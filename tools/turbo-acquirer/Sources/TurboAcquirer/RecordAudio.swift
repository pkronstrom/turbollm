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
            guard CGPreflightScreenCaptureAccess() else {
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

        switch scope {
        case .micOnly:
            try startMicOnly(engine: engine, audioFile: audioFile,
                             deviceUID: deviceUID, targetFormat: targetFormat)
        case .systemPlusMic:
            // system+mic requires async SCStream setup — run on a background thread.
            let group = DispatchGroup()
            var startError: Error?
            group.enter()
            Task {
                do {
                    try await startSystemPlusMic(engine: engine, audioFile: audioFile,
                                                  targetFormat: targetFormat)
                } catch {
                    startError = error
                }
                group.leave()
            }
            group.wait()
            if let error = startError { throw error }
        }

        // Poll for stop signal at 100 ms intervals.
        while !isStopRequested() {
            Thread.sleep(forTimeInterval: 0.1)
        }

        // Graceful stop: remove taps, stop engine, flush file.
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

    private static func startSystemPlusMic(
        engine: AVAudioEngine,
        audioFile: AVAudioFile,
        targetFormat: AVAudioFormat
    ) async throws {
        let mixer = AVAudioMixerNode()
        let player = AVAudioPlayerNode()

        engine.attach(mixer)
        engine.attach(player)

        // Connect mic input → mixer bus 0. Use the input node's native hardware
        // format on the mic side; the mixer down-mixes/resamples to the target format.
        let inputNode = engine.inputNode
        let nativeMicFormat = inputNode.outputFormat(forBus: 0)
        engine.connect(inputNode, to: mixer, format: nativeMicFormat)
        // Connect player → mixer bus 1.
        engine.connect(player, to: mixer, format: targetFormat)
        // Tap mixer output → file.
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
