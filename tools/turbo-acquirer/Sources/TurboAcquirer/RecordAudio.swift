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
    case outputPathRequired
    /// Any other setup failure that isn't a TCC permission problem (a
    /// malformed AVAudioFormat, a rejected AudioUnitSetProperty call, an
    /// unknown --device-uid, …). Kept distinct from `permissionDenied` so
    /// callers/logs don't mislabel an internal error as "go grant this
    /// permission" when granting it wouldn't fix anything.
    case internalError(String)
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

    /// Overrides the microphone TCC check both scopes perform before touching
    /// `AVAudioEngine`. When set to `false`, `run(...)` throws
    /// `RecordAudioError.permissionDenied("Microphone")` without consulting
    /// `AVCaptureDevice.authorizationStatus(for:.audio)`. Tests set this and
    /// reset to `nil`.
    nonisolated(unsafe) static var microphonePermissionOverride: Bool?

    /// Retains the SCStream delegate for the system+mic stream's lifetime.
    /// `SCStream` does not document its `delegate` property as retaining —
    /// without this, the local `let delegate` in `startSystemPlusMic` would
    /// be deallocated the moment that function returns.
    nonisolated(unsafe) private static var systemAudioStreamDelegate: SCStreamDelegate?

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

        // Both scopes tap the microphone. When mic access is denied via TCC,
        // `AVAudioInputNode.installTap(...)` doesn't return an error or throw
        // a catchable Swift error — it raises an uncatchable ObjC exception
        // that crashes the process. Check authorization up front so a denial
        // surfaces as the existing `permissionDenied("Microphone")` error
        // instead. `.notDetermined` is left alone: that's the first-launch
        // case where the system itself prompts on first access.
        let micStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        let micGranted = microphonePermissionOverride ?? (micStatus != .denied && micStatus != .restricted)
        guard micGranted else {
            throw RecordAudioError.permissionDenied("Microphone")
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

    // MARK: - Manifest

    /// Computes `start_offset_ms` from `TURBO_T0_NS` if set, otherwise returns 0.
    /// Mirrors RecordScreen.computeStartOffsetMs() — both derive from the
    /// shared `computeStartOffsetMsFromT0()` helper (same time origin).
    static func computeStartOffsetMs() -> Int { computeStartOffsetMsFromT0() }

    /// Encodes a manifest JSON string: `{"path":"...","start_offset_ms":N}`.
    static func encodeManifest(path: String, startOffsetMs: Int) -> String {
        struct _M: Codable {
            let path: String
            let startOffsetMs: Int
            enum CodingKeys: String, CodingKey {
                case path
                case startOffsetMs = "start_offset_ms"
            }
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        if let data = try? encoder.encode(_M(path: path, startOffsetMs: startOffsetMs)),
           let str = String(data: data, encoding: .utf8) {
            return str
        }
        // Defensive fallback (path is a filesystem path — no special JSON chars expected).
        return "{\"path\":\"\(path)\",\"start_offset_ms\":\(startOffsetMs)}"
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
            let status = AudioUnitSetProperty(
                inputNode.audioUnit!,
                kAudioOutputUnitProperty_CurrentDevice,
                kAudioUnitScope_Global,
                0,
                &did,
                UInt32(MemoryLayout<AudioDeviceID>.size)
            )
            guard status == noErr else {
                throw RecordAudioError.internalError(
                    "failed to set input device to \"\(uid)\" (AudioUnitSetProperty status \(status))"
                )
            }
        }

        // Install tap with the input node's native hardware format — AVAudioEngine
        // rejects any other format here ("Input HW format and tap format not matching").
        // AVAudioFile.write(from:) handles format conversion internally when the
        // buffer's format differs from the file's format, so we don't need an
        // explicit AVAudioConverter. (An earlier explicit-converter implementation
        // signalled .endOfStream after the first input buffer, putting the
        // converter into a terminal state for subsequent tap callbacks — recordings
        // produced only ~156 ms of audio regardless of actual duration.)
        let nativeFormat = inputNode.outputFormat(forBus: 0)
        // A 0 Hz native format means the input node has no usable hardware
        // format — the same denied/unavailable-input state that can otherwise
        // crash installTap() with an uncatchable exception. Fail via a
        // catchable Swift error instead of reaching installTap() at all.
        guard nativeFormat.sampleRate > 0 else {
            throw RecordAudioError.permissionDenied("Microphone")
        }

        let micTapFailures = TapFailureTracker(label: "mic")
        inputNode.installTap(onBus: 0, bufferSize: 4096, format: nativeFormat) { buffer, _ in
            do {
                try audioFile.write(from: buffer)
                micTapFailures.record(success: true)
            } catch {
                micTapFailures.record(success: false)
            }
        }

        try engine.start()
    }

    // MARK: - System+mic path (ScreenCaptureKit + AVAudioEngine)

    /// SCStream audio format. Hardcoded values are constrained by
    /// `SCStreamConfiguration` (only specific sample rates / channel counts
    /// are accepted by ScreenCaptureKit's audio capture). 48 kHz stereo is
    /// the system's native rate and the well-tested config in the SCStream
    /// docs. AVAudioConverter in the bridge handles the conversion to the
    /// player's connection format.
    private static let scStreamSampleRate: Double = 48_000
    private static let scStreamChannelCount: UInt32 = 2

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

        // Mic → mixer bus 0 at native HW format (AVAudioEngine rejects any
        // other format for the input tap).
        let inputNode = engine.inputNode
        let nativeMicFormat = inputNode.outputFormat(forBus: 0)
        engine.connect(inputNode, to: mixer, fromBus: 0, toBus: 0, format: nativeMicFormat)

        // Player → mixer bus 1 at SCStream's native format. The bridge
        // converts each incoming CMSampleBuffer to this exact format before
        // scheduling onto the player. The mixer downsamples both inputs to
        // the tap format on its way out.
        guard let scPlayerFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: scStreamSampleRate,
            channels: scStreamChannelCount,
            interleaved: false
        ) else {
            // Not a permissions problem — the format parameters above are
            // fixed constants, so this can only fail from an AVFoundation
            // internal error, not from anything the user (or TCC) controls.
            throw RecordAudioError.internalError("could not construct AVAudioFormat for system audio capture")
        }
        engine.connect(player, to: mixer, fromBus: 0, toBus: 1, format: scPlayerFormat)

        // AVAudioEngine refuses to start any input chain that does not
        // ultimately reach `outputNode`. Wire the mixer to the main mixer
        // (→ outputNode) to satisfy the topology, and mute the main mixer
        // so the mic does not loop back through the speakers. The tap on
        // `mixer` still captures the full mix because it sits upstream of
        // the muted sink.
        engine.connect(mixer, to: engine.mainMixerNode, format: targetFormat)
        engine.mainMixerNode.outputVolume = 0

        // Tap mixer output → file. Sits upstream of the muted main mixer.
        let mixTapFailures = TapFailureTracker(label: "system+mic mix")
        mixer.installTap(onBus: 0, bufferSize: 4096, format: targetFormat) { buffer, _ in
            do {
                try audioFile.write(from: buffer)
                mixTapFailures.record(success: true)
            } catch {
                mixTapFailures.record(success: false)
            }
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
        // Pin SCStream's audio output to a known, supported format so the
        // bridge's AVAudioConverter has a deterministic source format. Without
        // these, SCStream emits at the system's current default and the
        // bridge has to discover the format from the first CMSampleBuffer.
        config.sampleRate = Int(scStreamSampleRate)
        config.channelCount = Int(scStreamChannelCount)

        // TODO(audit BUG-24): AVAudioEngine also offers an
        // `.engineConfigurationChangeNotification` for detecting mid-recording
        // device changes (unplugged mic, route change). Not wired up yet —
        // only the SCStream side (system audio) is guarded against silent
        // death below. Revisit if truncated system+mic recordings from a
        // route change turn up in practice.
        let delegate = AcquirerStreamDelegate(label: "record-audio")
        systemAudioStreamDelegate = delegate  // retain for the stream's lifetime
        let stream = SCStream(filter: filter, configuration: config, delegate: delegate)
        // Serial sample-handler queue: AVAudioConverter is not thread-safe and
        // SCStream can dispatch samples concurrently from a global queue.
        try stream.addStreamOutput(
            SCStreamOutputBridge(player: player, playerFormat: scPlayerFormat),
            type: .audio,
            sampleHandlerQueue: DispatchQueue(label: "com.turbollm.acquirer.scstream.audio")
        )
        try await stream.startCapture()
        return stream
    }
}

// MARK: - SCStream bridge

/// Bridges ScreenCaptureKit audio sample buffers to an AVAudioPlayerNode.
///
/// SCStream emits audio in whatever format `SCStreamConfiguration` requested
/// (typically 48 kHz stereo Float32). The player node is wired into the mixer
/// at a known fixed format. This bridge:
///
/// 1. Reads the actual ASBD from each CMSampleBuffer (defensive — works even
///    if SCStream's output format ever shifts).
/// 2. Copies PCM data into an AVAudioPCMBuffer in the SC-emitted format using
///    the supported `CMSampleBufferCopyPCMDataIntoAudioBufferList` API.
/// 3. Converts to the player's connection format via AVAudioConverter (held
///    once, rebuilt only if the source format changes).
/// 4. Schedules the converted buffer onto the player.
///
/// Must be invoked on a serial dispatch queue — AVAudioConverter is not
/// documented as thread-safe.
private final class SCStreamOutputBridge: NSObject, SCStreamOutput {
    private let player: AVAudioPlayerNode
    private let playerFormat: AVAudioFormat

    private var converter: AVAudioConverter?
    private var sourceFormat: AVAudioFormat?

    init(player: AVAudioPlayerNode, playerFormat: AVAudioFormat) {
        self.player = player
        self.playerFormat = playerFormat
        super.init()
    }

    func stream(_ stream: SCStream,
                didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio else { return }
        guard let inputBuffer = sampleBuffer.toAVAudioPCMBuffer() else { return }

        // Rebuild the converter on first buffer, or when the source format
        // changes mid-stream (defensive — SCStream config pins the format
        // but a future SCK change could relax that).
        if converter == nil || sourceFormat != inputBuffer.format {
            sourceFormat = inputBuffer.format
            converter = AVAudioConverter(from: inputBuffer.format, to: playerFormat)
        }
        guard let converter = converter else { return }

        // Output capacity: scale by sample-rate ratio + a small headroom for
        // resampler tail samples. The converter writes the actual frame count
        // into the buffer.
        let ratio = playerFormat.sampleRate / inputBuffer.format.sampleRate
        let outputCapacity = AVAudioFrameCount(ceil(Double(inputBuffer.frameLength) * ratio)) + 32
        guard let outputBuffer = AVAudioPCMBuffer(pcmFormat: playerFormat, frameCapacity: outputCapacity) else {
            return
        }

        var providedOnce = false
        var conversionError: NSError?
        let status = converter.convert(to: outputBuffer, error: &conversionError) { _, statusPtr in
            if providedOnce {
                statusPtr.pointee = .noDataNow
                return nil
            }
            providedOnce = true
            statusPtr.pointee = .haveData
            return inputBuffer
        }

        guard status == .haveData || status == .inputRanDry else { return }
        guard outputBuffer.frameLength > 0 else { return }
        player.scheduleBuffer(outputBuffer)
    }
}

// MARK: - CMSampleBuffer conversion helper

private extension CMSampleBuffer {
    /// Convert this audio CMSampleBuffer to an AVAudioPCMBuffer in the
    /// sample buffer's own native format. Returns nil if the sample buffer
    /// is not a PCM audio buffer or the copy fails.
    func toAVAudioPCMBuffer() -> AVAudioPCMBuffer? {
        guard let formatDescription = CMSampleBufferGetFormatDescription(self),
              let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription) else {
            return nil
        }
        var asbd = asbdPtr.pointee
        guard let format = AVAudioFormat(streamDescription: &asbd) else { return nil }

        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(self))
        guard frameCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            return nil
        }
        buffer.frameLength = frameCount

        // CMSampleBufferCopyPCMDataIntoAudioBufferList is the supported API
        // for materializing CMSampleBuffer audio into a host-managed buffer
        // list; it handles interleaved vs deinterleaved layouts automatically.
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            self,
            at: 0,
            frameCount: Int32(frameCount),
            into: buffer.mutableAudioBufferList
        )
        guard status == noErr else { return nil }
        return buffer
    }
}

// MARK: - Tap failure tracking

/// Tracks consecutive `AVAudioFile.write` failures for one tap callback.
/// A single dropped buffer is not worth stopping the recording over (the
/// original `try?` behavior), but a sustained run of failures — e.g. the
/// disk filling up — would otherwise silently produce an unusable WAV for
/// the rest of the recording. After `threshold` consecutive failures,
/// logs once and requests a stop so the process tears down instead of
/// spinning to the end of the recording for nothing.
final class TapFailureTracker {
    private let label: String
    private let threshold: Int
    private var consecutiveFailures = 0
    private var loggedThresholdHit = false

    init(label: String, threshold: Int = 50) {
        self.label = label
        self.threshold = threshold
    }

    func record(success: Bool) {
        if success {
            consecutiveFailures = 0
            return
        }
        consecutiveFailures += 1
        if consecutiveFailures >= threshold && !loggedThresholdHit {
            loggedThresholdHit = true
            fputs("record-audio: \(threshold) consecutive write failures on the \(label) tap (disk full?) — stopping\n", stderr)
            requestStop()
        }
    }
}
