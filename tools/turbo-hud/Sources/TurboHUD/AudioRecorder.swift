import AVFoundation
import CoreAudio
import Foundation
import ScreenCaptureKit

// MARK: - Audio scope

enum AudioScope {
    case micOnly
    case systemPlusMic
}

// MARK: - AudioRecorder

/// Acquirer that records audio to a .wav file. Supports mic-only
/// (`AVAudioEngine` input node tap) and system+mic (ScreenCaptureKit
/// audio mixed with mic via AVAudioMixerNode).
final class AudioRecorder: Acquirer {

    // MARK: Acquirer conformance

    let mode: AcquirerMode = .primary
    let paramName: String

    // MARK: Private state

    private let scope: AudioScope
    private let inputDeviceUID: String?
    /// Session directory shared with other acquirers in the same workflow run.
    /// When set, the recording is written here as `audio.wav` so all per-run
    /// artifacts cluster together. When nil, the recording lands in the
    /// user-visible inbox/fallback with a timestamped filename so successive
    /// runs do not clobber each other.
    private let sessionDir: URL?

    private let engine = AVAudioEngine()
    private var audioFile: AVAudioFile?
    private var continuation: CheckedContinuation<AcquirerResult, Error>?

    // For system+mic: SCStream machinery
    private var scStream: SCStream?
    private var mixerNode: AVAudioMixerNode?
    private var playerNode: AVAudioPlayerNode?

    // MARK: Init

    init(paramName: String, scope: AudioScope, inputDeviceUID: String?, sessionDir: URL? = nil) {
        self.paramName = paramName
        self.scope = scope
        self.inputDeviceUID = inputDeviceUID
        self.sessionDir = sessionDir
    }

    // MARK: - Output path (pure, testable)

    /// Compute the output file URL for this recorder.
    /// - Parameters:
    ///   - workflow: not used currently (reserved for future per-workflow naming)
    ///   - sessionDir: if non-nil, writes `<sessionDir>/audio.wav`. Otherwise
    ///     uses `$TURBO_AUDIO_INBOX` or `$HOME/Recordings/turbo/`.
    static func outputPath(for workflow: Workflow, sessionDir: URL?) -> URL {
        if let dir = sessionDir {
            return dir.appendingPathComponent("audio.wav")
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

    // MARK: - Acquirer

    func acquire() async throws -> AcquirerResult {
        // When the caller bundles multiple acquirers under one session dir,
        // write audio.wav inside it. Otherwise the path is timestamped, so
        // successive runs do not overwrite a single audio.wav.
        let outURL = Self.outputPath(for: Workflow(
            name: paramName, description: nil, command: nil,
            script: nil, args: nil, env: nil, params: []
        ), sessionDir: sessionDir)
        try FileManager.default.createDirectory(
            at: outURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        // Prepare AVAudioFile.
        let format = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!
        audioFile = try AVAudioFile(forWriting: outURL, settings: format.settings)

        switch scope {
        case .micOnly:
            try startMicOnly(format: format)
        case .systemPlusMic:
            try await startSystemPlusMic(format: format)
        }

        return try await withCheckedThrowingContinuation { cont in
            self.continuation = cont
        }
    }

    func cancel() {
        stopAndFlush(result: .success(
            AcquirerResult(
                paramName: paramName,
                value: audioFile?.url.path ?? "",
                phase: nil
            )
        ))
    }

    // MARK: - Mic-only path

    private func startMicOnly(format: AVAudioFormat) throws {
        let inputNode = engine.inputNode

        // Optionally set input device via AudioUnit property.
        if let uid = inputDeviceUID,
           let deviceID = AudioSourcePicker.deviceID(forUID: uid) {
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
        // Convert each buffer to the target 16 kHz mono format before writing.
        let nativeFormat = inputNode.outputFormat(forBus: 0)
        guard let converter = AVAudioConverter(from: nativeFormat, to: format) else {
            throw AcquirerError.underlying(NSError(
                domain: "TurboHUD.AudioRecorder",
                code: -1,
                userInfo: [NSLocalizedDescriptionKey: "Could not create AVAudioConverter from \(nativeFormat) to \(format)"]
            ))
        }

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: nativeFormat) { [weak self] buffer, _ in
            guard let self, let file = self.audioFile else { return }
            // Allocate an output buffer sized for the converted sample count.
            let ratio = format.sampleRate / nativeFormat.sampleRate
            let outCapacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded())
            guard let outBuffer = AVAudioPCMBuffer(
                pcmFormat: format,
                frameCapacity: max(outCapacity, 1)
            ) else { return }

            var error: NSError?
            var supplied = false
            let status = converter.convert(to: outBuffer, error: &error) { _, inputStatus in
                if supplied {
                    inputStatus.pointee = .endOfStream
                    return nil
                }
                supplied = true
                inputStatus.pointee = .haveData
                return buffer
            }

            guard status != .error, outBuffer.frameLength > 0 else { return }
            try? file.write(from: outBuffer)
        }

        try engine.start()
    }

    // MARK: - System+mic path (ScreenCaptureKit + AVAudioEngine)

    private func startSystemPlusMic(format: AVAudioFormat) async throws {
        guard CGPreflightScreenCaptureAccess() else {
            throw AcquirerError.permissionDenied("Screen Recording")
        }

        let mixer = AVAudioMixerNode()
        let player = AVAudioPlayerNode()
        mixerNode = mixer
        playerNode = player

        engine.attach(mixer)
        engine.attach(player)

        // Connect mic input → mixer bus 0. Use the input node's native hardware
        // format on the mic side; the mixer down-mixes/down-samples to the tap
        // format on its output side. Forcing a non-native format here triggers
        // "Input HW format and tap format not matching".
        let inputNode = engine.inputNode
        let nativeMicFormat = inputNode.outputFormat(forBus: 0)
        engine.connect(inputNode, to: mixer, format: nativeMicFormat)
        // Connect player → mixer bus 1.
        engine.connect(player, to: mixer, format: format)
        // Tap mixer output → file.
        mixer.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            guard let self, let file = self.audioFile else { return }
            try? file.write(from: buffer)
        }

        try engine.start()
        player.play()

        // Start SCStream for system audio.
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let display = content.displays.first
        let filter = SCContentFilter(display: display!, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true

        let stream = SCStream(filter: filter, configuration: config, delegate: nil)
        scStream = stream
        try stream.addStreamOutput(SCStreamOutputBridge(player: player, format: format),
                                   type: .audio, sampleHandlerQueue: .global())
        try await stream.startCapture()
    }

    // MARK: - Stop

    private func stopAndFlush(result: Result<AcquirerResult, Error>) {
        Task { @MainActor in
            // Stop SCStream if active.
            if let stream = scStream {
                try? await stream.stopCapture()
                scStream = nil
            }

            // Remove tap and stop engine.
            engine.inputNode.removeTap(onBus: 0)
            mixerNode?.removeTap(onBus: 0)
            engine.stop()
            audioFile = nil

            continuation?.resume(with: result)
            continuation = nil
        }
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
        let status = CMBlockBufferGetDataPointer(blockBuffer, atOffset: 0, lengthAtOffsetOut: nil,
                                                 totalLengthOut: &totalLength, dataPointerOut: &dataPointer)
        guard status == kCMBlockBufferNoErr, let src = dataPointer else { return nil }

        // Copy samples to float32 buffer (assuming f32 from SCStream).
        if let floatChannelData = buffer.floatChannelData {
            let byteCount = min(totalLength, Int(buffer.frameCapacity) * MemoryLayout<Float>.size)
            memcpy(floatChannelData[0], src, byteCount)
        }
        return buffer
    }
}
