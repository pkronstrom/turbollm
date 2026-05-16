import Foundation
import ScreenCaptureKit
import CoreImage
import AppKit

// MARK: - RecordScreenScope

enum RecordScreenScope: Equatable {
    case fullDisplay
    case region(CGRect)
    case activeWindow
}

// MARK: - Manifest types

struct ScreenFrameEntry: Codable {
    let path: String
    let tOffsetMs: Int
    enum CodingKeys: String, CodingKey {
        case path
        case tOffsetMs = "t_offset_ms"
    }
}

struct RecordScreenManifest: Codable {
    let frames: [ScreenFrameEntry]
    let durationMs: Int
    let droppedOvercap: Int
    let startOffsetMs: Int
    enum CodingKeys: String, CodingKey {
        case frames
        case durationMs = "duration_ms"
        case droppedOvercap = "dropped_overcap"
        case startOffsetMs = "start_offset_ms"
    }
}

// MARK: - RecordScreen

/// Implements the `record-screen` subcommand.
///
/// Captures screen frames via SCStream, downscales each to 16×16 grayscale,
/// computes dHash, and persists a full-resolution PNG keyframe when
/// Hamming distance ≥ threshold AND min-interval has elapsed AND cap not hit.
///
/// On SIGTERM/SIGINT, stops the stream, drains the PNG-write queue,
/// emits manifest JSON on stdout (returned as a String to the caller),
/// and deletes the activity file.
enum RecordScreen {

    // MARK: - Arg parsing

    struct Args {
        let outputDir: URL
        let scope: RecordScreenScope
        let threshold: Int
        let minIntervalMs: Int
        let maxKeyframes: Int
    }

    static func parseArgs(argv: [String]) -> Args? {
        var outputDir: URL? = nil
        var scopeStr = "full-display"
        var regionStr: String? = nil
        var threshold = 12
        var minIntervalMs = 1500
        var maxKeyframes = 200

        var i = 0
        while i < argv.count {
            switch argv[i] {
            case "--output-dir":
                i += 1; if i < argv.count { outputDir = URL(fileURLWithPath: argv[i], isDirectory: true) }
            case "--scope":
                i += 1; if i < argv.count { scopeStr = argv[i] }
            case "--region":
                i += 1; if i < argv.count { regionStr = argv[i] }
            case "--keyframe-threshold":
                i += 1; if i < argv.count, let v = Int(argv[i]) { threshold = v }
            case "--min-interval-ms":
                i += 1; if i < argv.count, let v = Int(argv[i]) { minIntervalMs = v }
            case "--max-keyframes":
                i += 1; if i < argv.count, let v = Int(argv[i]) { maxKeyframes = v }
            default: break
            }
            i += 1
        }

        guard let outputDir else { return nil }

        let scope: RecordScreenScope
        switch scopeStr {
        case "region":
            let rect = regionStr.flatMap(parseRegionString) ?? .zero
            scope = .region(rect)
        case "active-window":
            scope = .activeWindow
        default:
            scope = .fullDisplay
        }

        return Args(outputDir: outputDir, scope: scope,
                    threshold: threshold, minIntervalMs: minIntervalMs, maxKeyframes: maxKeyframes)
    }

    /// Parses `"x,y,w,h"` into a `CGRect`. Returns `nil` on parse failure.
    static func parseRegionString(_ s: String) -> CGRect? {
        let parts = s.split(separator: ",").compactMap { Double($0) }
        guard parts.count == 4 else { return nil }
        return CGRect(x: parts[0], y: parts[1], width: parts[2], height: parts[3])
    }

    // MARK: - Run

    /// Runs the record-screen subcommand.
    ///
    /// - Returns: The manifest JSON string (emitted on stdout by the caller).
    static func run(
        outputDir: URL,
        scope: RecordScreenScope,
        threshold: Int = 12,
        minIntervalMs: Int = 1500,
        maxKeyframes: Int = 200,
        parentId: String? = nil
    ) -> String {
        // Test seam: TURBO_RECORD_SCREEN_FAKE=1 → return synthetic empty manifest immediately.
        if ProcessInfo.processInfo.environment["TURBO_RECORD_SCREEN_FAKE"] == "1" {
            try? FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
            return syntheticEmptyManifest()
        }

        // Write activity file. Deleted on exit via defer.
        let activityURL = try? ActivityFile.writeActivity(label: "screen", parentId: parentId)
        defer { activityURL.map { ActivityFile.deleteActivity(at: $0) } }

        // Ensure output directory exists.
        try? FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)

        // Run the SCStream pipeline (blocks until SIGTERM/SIGINT).
        let manifest = runSCStreamPipeline(
            outputDir: outputDir,
            scope: scope,
            threshold: threshold,
            minIntervalMs: minIntervalMs,
            maxKeyframes: maxKeyframes,
            activityFileURL: activityURL
        )

        return encodeManifest(manifest)
    }

    // MARK: - Internal helpers

    static func syntheticEmptyManifest() -> String {
        return encodeManifest(RecordScreenManifest(
            frames: [],
            durationMs: 0,
            droppedOvercap: 0,
            startOffsetMs: computeStartOffsetMs()
        ))
    }

    /// Computes `start_offset_ms` from `TURBO_T0_NS` if set, otherwise returns 0.
    static func computeStartOffsetMs() -> Int {
        let nowNs = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
        if let t0Str = ProcessInfo.processInfo.environment["TURBO_T0_NS"],
           let t0Ns = UInt64(t0Str), nowNs >= t0Ns {
            return Int((nowNs - t0Ns) / 1_000_000)
        }
        return 0
    }

    static func encodeManifest(_ manifest: RecordScreenManifest) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        if let data = try? encoder.encode(manifest),
           let str = String(data: data, encoding: .utf8) {
            return str
        }
        return "{\"frames\":[],\"duration_ms\":0,\"dropped_overcap\":0,\"start_offset_ms\":0}"
    }

    // MARK: - SCStream pipeline (T-6)

    private static func runSCStreamPipeline(
        outputDir: URL,
        scope: RecordScreenScope,
        threshold: Int,
        minIntervalMs: Int,
        maxKeyframes: Int,
        activityFileURL: URL?
    ) -> RecordScreenManifest {
        // Resolve capture scope.
        let resolvedScope = resolveScope(scope)

        let startNs = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
        let startOffsetMs = computeStartOffsetMs()

        // State shared between the callback and stop logic.
        let decider = KeyframeDecider(threshold: threshold, minIntervalMs: minIntervalMs, maxKeyframes: maxKeyframes)
        var frames: [ScreenFrameEntry] = []
        var droppedOvercap = 0

        // PNG-write queue (serial, off the main thread).
        let pngQueue = DispatchQueue(label: "com.turbo.record-screen.png-writer", qos: .utility)

        // Semaphore used to wait for pngQueue drain on shutdown.
        let pngDrainGroup = DispatchGroup()

        // Stream output handler.
        let output = ScreenCaptureOutput(
            outputDir: outputDir,
            startNs: startNs,
            decider: decider,
            pngQueue: pngQueue,
            pngDrainGroup: pngDrainGroup,
            framesRef: &frames,
            droppedOvercapRef: &droppedOvercap,
            activityFileURL: activityFileURL
        )

        // Build SCStream.
        let semaphore = DispatchSemaphore(value: 0)
        var stream: SCStream? = nil

        Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                guard let display = content.displays.first else {
                    semaphore.signal(); return
                }
                let filter = buildContentFilter(display: display, scope: resolvedScope, content: content)
                let config = buildStreamConfig(scope: resolvedScope, display: display)
                let s = SCStream(filter: filter, configuration: config, delegate: nil)
                try s.addStreamOutput(output, type: .screen, sampleHandlerQueue: .global(qos: .userInteractive))
                try await s.startCapture()
                stream = s
                semaphore.signal()
            } catch {
                fputs("record-screen: SCStream setup failed: \(error)\n", stderr)
                semaphore.signal()
            }
        }
        semaphore.wait()

        // Poll for stop signal.
        while !isStopRequested() {
            RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
        }

        // Stop capture.
        let stopSemaphore = DispatchSemaphore(value: 0)
        if let s = stream {
            Task {
                try? await s.stopCapture()
                stopSemaphore.signal()
            }
            stopSemaphore.wait()
        }

        // Drain the PNG-write queue.
        pngDrainGroup.wait()

        let endNs = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
        let durationMs = Int((endNs - startNs) / 1_000_000)

        return RecordScreenManifest(
            frames: frames,
            durationMs: durationMs,
            droppedOvercap: droppedOvercap,
            startOffsetMs: startOffsetMs
        )
    }

    // MARK: - Scope resolution

    private static func resolveScope(_ scope: RecordScreenScope) -> RecordScreenScope {
        if case .activeWindow = scope {
            if let rect = RegionSelector.resolveActiveWindowRect() {
                return .region(rect)
            }
            return .fullDisplay
        }
        return scope
    }

    private static func buildContentFilter(
        display: SCDisplay,
        scope: RecordScreenScope,
        content: SCShareableContent
    ) -> SCContentFilter {
        return SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
    }

    private static func buildStreamConfig(scope: RecordScreenScope, display: SCDisplay) -> SCStreamConfiguration {
        let config = SCStreamConfiguration()
        config.capturesAudio = false
        config.minimumFrameInterval = CMTime(value: 1, timescale: 10) // ~10 fps
        config.pixelFormat = kCVPixelFormatType_32BGRA
        if case .region(let rect) = scope {
            config.sourceRect = rect
            config.width = max(1, Int(rect.width))
            config.height = max(1, Int(rect.height))
        } else {
            config.width = display.width
            config.height = display.height
        }
        return config
    }
}

// MARK: - KeyframeDecider (T-5)

enum KeyframeDecision {
    case persist
    case dropSimilar
    case dropMinInterval
    case dropOvercap
}

class KeyframeDecider {
    private let threshold: Int
    private let minIntervalMs: Int
    private let maxKeyframes: Int

    private var lastHash: UInt64? = nil
    private var lastKeyframeTimeMs: Int = Int.min
    private var persistedCount: Int = 0

    init(threshold: Int = 12, minIntervalMs: Int = 1500, maxKeyframes: Int = 200) {
        self.threshold = threshold
        self.minIntervalMs = minIntervalMs
        self.maxKeyframes = maxKeyframes
    }

    func decide(hash: UInt64, t_ms: Int) -> KeyframeDecision {
        // Check overcap first (hard stop).
        guard persistedCount < maxKeyframes else { return .dropOvercap }

        // First frame always persists.
        guard let last = lastHash else {
            accept(hash: hash, t_ms: t_ms)
            return .persist
        }

        // Min-interval gate.
        guard (t_ms - lastKeyframeTimeMs) >= minIntervalMs else { return .dropMinInterval }

        // Similarity gate.
        let dist = hammingDistance(hash, last)
        guard dist >= threshold else { return .dropSimilar }

        accept(hash: hash, t_ms: t_ms)
        return .persist
    }

    private func accept(hash: UInt64, t_ms: Int) {
        lastHash = hash
        lastKeyframeTimeMs = t_ms
        persistedCount += 1
    }
}

// MARK: - SCStream output handler

private class ScreenCaptureOutput: NSObject, SCStreamOutput {
    private let outputDir: URL
    private let startNs: UInt64
    private let decider: KeyframeDecider
    private let pngQueue: DispatchQueue
    private let pngDrainGroup: DispatchGroup
    private var framesRef: UnsafeMutablePointer<[ScreenFrameEntry]>
    private var droppedOvercapRef: UnsafeMutablePointer<Int>
    private let activityFileURL: URL?
    private var seenCount = 0
    private let lock = NSLock()

    init(outputDir: URL, startNs: UInt64,
         decider: KeyframeDecider,
         pngQueue: DispatchQueue,
         pngDrainGroup: DispatchGroup,
         framesRef: UnsafeMutablePointer<[ScreenFrameEntry]>,
         droppedOvercapRef: UnsafeMutablePointer<Int>,
         activityFileURL: URL?) {
        self.outputDir = outputDir
        self.startNs = startNs
        self.decider = decider
        self.pngQueue = pngQueue
        self.pngDrainGroup = pngDrainGroup
        self.framesRef = framesRef
        self.droppedOvercapRef = droppedOvercapRef
        self.activityFileURL = activityFileURL
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen else { return }
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        let nowNs = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
        let tOffsetMs = Int((nowNs - startNs) / 1_000_000)

        lock.lock()
        seenCount += 1
        let mySeenCount = seenCount
        lock.unlock()

        // Downscale to 16×16 grayscale for hashing.
        guard let hashValue = computeHash(from: pixelBuffer) else { return }

        let decision: KeyframeDecision
        lock.lock()
        decision = decider.decide(hash: hashValue, t_ms: tOffsetMs)
        lock.unlock()

        switch decision {
        case .dropSimilar, .dropMinInterval:
            return
        case .dropOvercap:
            lock.lock()
            droppedOvercapRef.pointee += 1
            lock.unlock()
            return
        case .persist:
            break
        }

        // Dispatch PNG write off-thread.
        pngDrainGroup.enter()
        let frameIndex = mySeenCount
        pngQueue.async { [weak self] in
            defer { self?.pngDrainGroup.leave() }
            guard let self else { return }
            let fileName = String(format: "%03d-T+%d.png", frameIndex, tOffsetMs)
            let fileURL = self.outputDir.appendingPathComponent(fileName)
            if let pngData = self.encodePNG(from: pixelBuffer) {
                try? pngData.write(to: fileURL)
                let entry = ScreenFrameEntry(path: fileURL.path, tOffsetMs: tOffsetMs)
                self.lock.lock()
                self.framesRef.pointee.append(entry)
                // Update activity file screen_frames.
                if let activityURL = self.activityFileURL {
                    self.updateActivityScreenFrames(at: activityURL, newPath: fileURL.path)
                }
                self.lock.unlock()
            }
        }
    }

    private func computeHash(from pixelBuffer: CVPixelBuffer) -> UInt64? {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        // Downscale to 16×16 and convert to grayscale.
        let scale = CGAffineTransform(scaleX: 16.0 / ciImage.extent.width,
                                      y: 16.0 / ciImage.extent.height)
        let scaled = ciImage.transformed(by: scale)
        let gray = scaled.applyingFilter("CIColorControls", parameters: [kCIInputSaturationKey: 0.0])

        let context = CIContext()
        var pixels = [UInt8](repeating: 0, count: 16 * 16)
        context.render(gray,
                       toBitmap: &pixels,
                       rowBytes: 16,
                       bounds: CGRect(x: 0, y: 0, width: 16, height: 16),
                       format: .L8,
                       colorSpace: CGColorSpaceCreateDeviceGray())
        return pixels.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
    }

    private func encodePNG(from pixelBuffer: CVPixelBuffer) -> Data? {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let context = CIContext()
        return context.pngRepresentation(of: ciImage,
                                          format: .RGBA8,
                                          colorSpace: CGColorSpaceCreateDeviceRGB())
    }

    private func updateActivityScreenFrames(at url: URL, newPath: String) {
        guard let data = try? Data(contentsOf: url),
              var dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        var frames = dict["screen_frames"] as? [String] ?? []
        frames.append(newPath)
        dict["screen_frames"] = frames
        if let updated = try? JSONSerialization.data(withJSONObject: dict) {
            try? updated.write(to: url)
        }
    }
}
