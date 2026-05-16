import AVFoundation
import CoreAudio
import Foundation
import ScreenCaptureKit

// MARK: - AudioScope

enum AudioScope: Equatable {
    case micOnly
    case systemPlusMic
}

// MARK: - RecordAudioError

enum RecordAudioError: Error {
    case permissionDenied(String)
    case converterInitFailed
    case outputPathRequired
}

// MARK: - RecordAudio

/// Implements the `record-audio` subcommand.
///
/// Supports two scopes:
/// - `.micOnly`: taps `AVAudioEngine`'s input node with native HW format, converts to 16 kHz mono.
/// - `.systemPlusMic`: mixes ScreenCaptureKit system audio with mic via `AVAudioMixerNode`.
///
/// Polls `isStopRequested()` at 100 ms intervals; on SIGTERM/SIGINT, stops the engine,
/// flushes the file, prints the output path to stdout, and exits 0.
///
/// Writes an activity file before `engine.start()` and deletes it on exit via `defer`.
enum RecordAudio {

    // MARK: - Test seam

    /// Overrides the Screen Recording permission check used by the `system+mic`
    /// path. When set to `false`, `run(scope: .systemPlusMic, …)` throws
    /// `RecordAudioError.permissionDenied("Screen Recording")` without
    /// consulting the real TCC subsystem. Tests set this and reset to `nil`.
    nonisolated(unsafe) static var screenRecordingPermissionOverride: Bool?

    // MARK: - Entry point

    /// Runs the record-audio subcommand.
    ///
    /// - Parameters:
    ///   - scope: Recording scope (mic-only or system+mic).
    ///   - deviceUID: Optional CoreAudio input device UID.
    ///   - outputURL: File URL to write the WAV.
    ///   - parentId: Optional workflow activity ID (from `$TURBO_WORKFLOW_ID`).
    static func run(scope: AudioScope, deviceUID: String?, outputURL: URL, parentId: String?) throws {
        // Test seam: TURBO_RECORD_AUDIO_FAKE=1 creates an empty WAV and returns immediately.
        if ProcessInfo.processInfo.environment["TURBO_RECORD_AUDIO_FAKE"] == "1" {
            try FileManager.default.createDirectory(
                at: outputURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            FileManager.default.createFile(atPath: outputURL.path, contents: Data(), attributes: nil)
            return
        }

        // System+mic requires Screen Recording permission.
        if scope == .systemPlusMic {
            let granted = screenRecordingPermissionOverride ?? CGPreflightScreenCaptureAccess()
            guard granted else {
                throw RecordAudioError.permissionDenied("Screen Recording")
            }
        }

        // Write activity file; delete it on exit (RAII via defer).
        let activityURL = try ActivityFile.writeActivity(
            label: scope == .micOnly ? "audio (mic-only)" : "audio (system+mic)",
            parentId: parentId
        )
        defer { ActivityFile.deleteActivity(at: activityURL) }

        // Ensure output directory exists.
        try FileManager.default.createDirectory(
            at: outputURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        // Target: 16 kHz mono PCM.
        let targetFormat = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!

        // Prepare AVAudioFile.
        let audioFile = try AVAudioFile(forWriting: outputURL, settings: targetFormat.settings)

        // Install signal handlers before starting the engine.
        installStopSignalHandlers()

        let engine = AVAudioEngine()
        // Retain SCStream for the full life of the recording. ScreenCaptureKit
        // does not document `startCapture()` as keeping its receiver alive, and
        // ARC will deallocate a stream the moment its last strong reference
        // disappears — losing system audio mid-recording without surfacing an
        // error. Holding it here covers both the async setup path and graceful
        // teardown.
        var systemAudioStream: SCStream?

        switch scope {
        case .micOnly:
            try startMicOnly(engine: engine, audioFile: audioFile,
                             deviceUID: deviceUID, targetFormat: targetFormat)
        case .systemPlusMic:
            // system+mic requires async SCStream setup — run on a background thread.
            let group = DispatchGroup()
            var startError: Error?
            var producedStream: SCStream?
            group.enter()
            Task {
                do {
                    producedStream = try await startSystemPlusMic(
                        engine: engine,
                        audioFile: audioFile,
                        targetFormat: targetFormat
                    )
                } catch {
                    startError = error
                }
                group.leave()
            }
            group.wait()
            if let error = startError { throw error }
            systemAudioStream = producedStream
        }

        // Poll for stop signal at 100 ms intervals.
        while !isStopRequested() {
            Thread.sleep(forTimeInterval: 0.1)
        }

        // Graceful stop: stop the SCStream first (if any), then remove taps and
        // stop the engine so the WAV is flushed cleanly.
        if let stream = systemAudioStream {
            let stopGroup = DispatchGroup()
            stopGroup.enter()
            Task {
                try? await stream.stopCapture()
                stopGroup.leave()
            }
            stopGroup.wait()
        }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }

    // MARK: - Output path (pure, testable)

    /// Computes the output file URL for the recording.
    ///
    /// Priority order:
    /// 1. `--output <path>` explicit arg (passed in as `explicitPath`)
    /// 2. `$TURBO_AUDIO_INBOX` env var directory + timestamped filename
    /// 3. `~/Recordings/turbo/` + timestamped filename
    static func outputPath(explicitPath: String?) -> URL {
        if let path = explicitPath, !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        let baseDir: URL
        if let inbox = ProcessInfo.processInfo.environment["TURBO_AUDIO_INBOX"] {
            baseDir = URL(fileURLWithPath: inbox, isDirectory: true)
        } else {
            let home = FileManager.default.homeDirectoryForCurrentUser
            baseDir = home.appendingPathComponent("Recordings/turbo", isDirectory: true)
        }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd-HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let timestamp = formatter.string(from: Date())
        return baseDir.appendingPathComponent("\(timestamp).wav")
    }

    // MARK: - Arg parsing

    /// Parses argv for the `record-audio` subcommand.
    /// Returns `(scope, deviceUID?, outputPath?)`.
    static func parseArgs(argv: [String]) -> (scope: AudioScope, deviceUID: String?, outputPath: String?) {
        var scope: AudioScope = .micOnly
        var deviceUID: String? = nil
        var outputPath: String? = nil

        var i = 0
        while i < argv.count {
            switch argv[i] {
            case "--scope":
                if i + 1 < argv.count {
                    scope = argv[i + 1] == "system+mic" ? .systemPlusMic : .micOnly
                    i += 2
                } else { i += 1 }
            case "--device-uid":
                if i + 1 < argv.count {
                    deviceUID = argv[i + 1]
                    i += 2
                } else { i += 1 }
            case "--output":
                if i + 1 < argv.count {
                    outputPath = argv[i + 1]
                    i += 2
                } else { i += 1 }
            default:
                i += 1
            }
        }
        return (scope, deviceUID, outputPath)
    }

    // MARK: - Mic-only path

    private static func startMicOnly(
        engine: AVAudioEngine,
        audioFile: AVAudioFile,
        deviceUID: String?,
        targetFormat: AVAudioFormat
    ) throws {
        let inputNode = engine.inputNode

        // Optionally set input device via AudioUnit property.
        if let uid = deviceUID, let deviceID = AudioSourcePicker.deviceID(forUID: uid) {
            var did = deviceID
            AudioUnitSetProperty(
                inputNode.audioUnit!,
                kAudioOutputUnitProperty_CurrentDevice,
                kAudioUnitScope_Global,
                0,
                &did,
                UInt32(MemoryLayout<AudioDeviceID>.size)
            )
        }

        // Install tap with the input node's native hardware format — AVAudioEngine
        // rejects any other format here ("Input HW format and tap format not matching").
        // Use AVAudioConverter to down-convert each buffer to 16 kHz mono.
        let nativeFormat = inputNode.outputFormat(forBus: 0)
        guard let converter = AVAudioConverter(from: nativeFormat, to: targetFormat) else {
            throw RecordAudioError.converterInitFailed
        }

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: nativeFormat) { buffer, _ in
            let ratio = targetFormat.sampleRate / nativeFormat.sampleRate
            let outCapacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded())
            guard let outBuffer = AVAudioPCMBuffer(
                pcmFormat: targetFormat,
                frameCapacity: max(outCapacity, 1)
            ) else { return }

            var supplied = false
            let status = converter.convert(to: outBuffer, error: nil) { _, inputStatus in
                if supplied {
                    inputStatus.pointee = .endOfStream
                    return nil
                }
                supplied = true
                inputStatus.pointee = .haveData
                return buffer
            }

            guard status != .error, outBuffer.frameLength > 0 else { return }
            try? audioFile.write(from: outBuffer)
        }

        try engine.start()
    }

    // MARK: - System+mic path (ScreenCaptureKit + AVAudioEngine)

    /// Returns the SCStream so the caller can retain it for the full
    /// recording lifetime and stop it during graceful teardown.
    private static func startSystemPlusMic(
        engine: AVAudioEngine,
        audioFile: AVAudioFile,
        targetFormat: AVAudioFormat
    ) async throws -> SCStream {
        let mixer = AVAudioMixerNode()
        let player = AVAudioPlayerNode()

        engine.attach(mixer)
        engine.attach(player)

        // Two sources fanning into one mixer require explicit input bus
        // indices — `engine.connect(src, to: mixer, format:)` defaults to
        // mixer.bus 0 for every call, so the second connection would
        // silently clobber the first.
        let inputNode = engine.inputNode
        let nativeMicFormat = inputNode.outputFormat(forBus: 0)
        engine.connect(inputNode, to: mixer, fromBus: 0, toBus: 0, format: nativeMicFormat)
        engine.connect(player, to: mixer, fromBus: 0, toBus: 1, format: targetFormat)

        // AVAudioEngine refuses to start any input chain that does not
        // ultimately reach `outputNode` — the AUGraph validator throws
        // kAudioUnitErr_FormatNotSupported (-10868) inside
        // `AUGraphParser::InitializeActiveNodesInInputChain` with no
        // useful detail beyond "input chain not connected". Wire the
        // mixer to the main mixer (→ outputNode) to satisfy the
        // topology, and mute the main mixer so the mic does not loop
        // back through the speakers. The tap on `mixer` still captures
        // the full mix because it sits upstream of the muted sink.
        // Manual smoke (T-33) caught this in CLI context; the original
        // HUD code suffered the same bug but presumably hadn't been
        // exercised against `system+mic` since the b22c9cd fix landed.
        engine.connect(mixer, to: engine.mainMixerNode, format: targetFormat)
        engine.mainMixerNode.outputVolume = 0

        // Tap mixer output → file. Sits upstream of the muted main mixer.
        mixer.installTap(onBus: 0, bufferSize: 4096, format: targetFormat) { buffer, _ in
            try? audioFile.write(from: buffer)
        }

        try engine.start()
        player.play()

        // Start SCStream for system audio.
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw RecordAudioError.permissionDenied("Screen Recording (no displays)")
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true

        let stream = SCStream(filter: filter, configuration: config, delegate: nil)
        try stream.addStreamOutput(
            SCStreamOutputBridge(player: player, format: targetFormat),
            type: .audio,
            sampleHandlerQueue: .global()
        )
        try await stream.startCapture()
        return stream
    }
}

// MARK: - SCStream bridge

/// Bridges ScreenCaptureKit sample buffers to an AVAudioPlayerNode.
private final class SCStreamOutputBridge: NSObject, SCStreamOutput {
    private let player: AVAudioPlayerNode
    private let format: AVAudioFormat

    init(player: AVAudioPlayerNode, format: AVAudioFormat) {
        self.player = player
        self.format = format
    }

    func stream(_ stream: SCStream,
                didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio else { return }
        guard let pcmBuffer = sampleBuffer.asPCMBuffer(format: format) else { return }
        player.scheduleBuffer(pcmBuffer)
    }
}

// MARK: - CMSampleBuffer conversion helper

private extension CMSampleBuffer {
    func asPCMBuffer(format: AVAudioFormat) -> AVAudioPCMBuffer? {
        guard let blockBuffer = CMSampleBufferGetDataBuffer(self) else { return nil }
        let frameCount = CMSampleBufferGetNumSamples(self)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frameCount)) else {
            return nil
        }
        buffer.frameLength = buffer.frameCapacity

        var totalLength = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(
            blockBuffer, atOffset: 0, lengthAtOffsetOut: nil,
            totalLengthOut: &totalLength, dataPointerOut: &dataPointer
        )
        guard status == kCMBlockBufferNoErr, let src = dataPointer else { return nil }

        if let floatChannelData = buffer.floatChannelData {
            let byteCount = min(totalLength, Int(buffer.frameCapacity) * MemoryLayout<Float>.size)
            memcpy(floatChannelData[0], src, byteCount)
        }
        return buffer
    }
}
