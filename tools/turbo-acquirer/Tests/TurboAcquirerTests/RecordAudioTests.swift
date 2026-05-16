import XCTest
@testable import TurboAcquirer

final class RecordAudioTests: XCTestCase {

    // MARK: - Output path computation (T-9 Step 1)

    func test_outputPath_explicit_path_wins() {
        let url = RecordAudio.outputPath(explicitPath: "/tmp/explicit.wav")
        XCTAssertEqual(url.path, "/tmp/explicit.wav")
    }

    func test_outputPath_env_inbox_used_when_no_explicit_path() {
        setenv("TURBO_AUDIO_INBOX", "/tmp/audio-inbox", 1)
        defer { unsetenv("TURBO_AUDIO_INBOX") }

        let url = RecordAudio.outputPath(explicitPath: nil)
        XCTAssertTrue(url.path.hasPrefix("/tmp/audio-inbox"),
                      "When TURBO_AUDIO_INBOX is set, output must be inside it. Got: \(url.path)")
        XCTAssertTrue(url.lastPathComponent.hasSuffix(".wav"),
                      "Output filename must end with .wav")
    }

    func test_outputPath_fallback_uses_recordings_turbo() {
        // Remove inbox env var to test the default fallback.
        let savedInbox = ProcessInfo.processInfo.environment["TURBO_AUDIO_INBOX"]
        defer {
            if let saved = savedInbox { setenv("TURBO_AUDIO_INBOX", saved, 1) }
            else { unsetenv("TURBO_AUDIO_INBOX") }
        }
        unsetenv("TURBO_AUDIO_INBOX")

        let url = RecordAudio.outputPath(explicitPath: nil)
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        XCTAssertTrue(url.path.hasPrefix("\(home)/Recordings/turbo"),
                      "Default fallback must use ~/Recordings/turbo. Got: \(url.path)")
        XCTAssertTrue(url.lastPathComponent.hasSuffix(".wav"))
    }

    func test_outputPath_empty_explicit_path_falls_back() {
        unsetenv("TURBO_AUDIO_INBOX")
        let url = RecordAudio.outputPath(explicitPath: "")
        // Empty string should fall back (treated as nil).
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        XCTAssertTrue(url.path.hasPrefix("\(home)/Recordings/turbo"),
                      "Empty explicit path must fall back to ~/Recordings/turbo. Got: \(url.path)")
    }

    // MARK: - Argument parsing (T-9 Step 1)

    func test_parseArgs_default_scope_is_mic_only() {
        let (scope, _, _) = RecordAudio.parseArgs(argv: ["--output", "/tmp/out.wav"])
        XCTAssertEqual(scope, .micOnly)
    }

    func test_parseArgs_scope_system_plus_mic() {
        let (scope, _, _) = RecordAudio.parseArgs(argv: ["--scope", "system+mic", "--output", "/tmp/out.wav"])
        XCTAssertEqual(scope, .systemPlusMic)
    }

    func test_parseArgs_device_uid_parsed() {
        let (_, deviceUID, _) = RecordAudio.parseArgs(argv: ["--device-uid", "BuiltInMicrophone", "--output", "/tmp/out.wav"])
        XCTAssertEqual(deviceUID, "BuiltInMicrophone")
    }

    func test_parseArgs_output_path_parsed() {
        let (_, _, outputPath) = RecordAudio.parseArgs(argv: ["--output", "/custom/path.wav"])
        XCTAssertEqual(outputPath, "/custom/path.wav")
    }

    func test_parseArgs_empty_returns_defaults() {
        let (scope, deviceUID, outputPath) = RecordAudio.parseArgs(argv: [])
        XCTAssertEqual(scope, .micOnly)
        XCTAssertNil(deviceUID)
        XCTAssertNil(outputPath)
    }

    // MARK: - Tap-format contract assertion (T-9 Step 1)
    //
    /// Source-level guard for the T-33 smoke fix: the system+mic graph
    /// must terminate at `mainMixerNode` (which runs to outputNode), or
    /// AVAudioEngine refuses to start with kAudioUnitErr_FormatNotSupported
    /// (-10868) inside `AUGraphParser::InitializeActiveNodesInInputChain`.
    /// The `mainMixerNode.outputVolume = 0` line mutes the speaker output
    /// to prevent mic→speaker feedback. Without both lines, every
    /// `system+mic` recording exits 1 the moment `engine.start()` is called.
    func test_system_plus_mic_terminates_input_chain_at_muted_mainMixer() throws {
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/TurboAcquirer/RecordAudio.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)
        XCTAssertTrue(
            source.contains("engine.connect(mixer, to: engine.mainMixerNode"),
            "system+mic graph must wire mixer to mainMixerNode to satisfy AUGraph input-chain validation."
        )
        XCTAssertTrue(
            source.contains("engine.mainMixerNode.outputVolume = 0"),
            "Main mixer must be muted to prevent mic→speaker feedback."
        )
    }

    // We cannot start AVAudioEngine in a unit test environment (no real hardware I/O).
    // Instead, verify the architectural invariant in RecordAudio.swift: the code
    // must reference `inputNode.outputFormat(forBus: 0)` for the tap format, NOT a
    // hardcoded 16kHz format. We test this by reading the source and checking for the
    // correct pattern. This is a documentation test — it will catch a regression if
    // someone replaces the native-format tap with a hardcoded one.
    func test_record_audio_uses_native_hw_format_for_tap_not_hardcoded_16khz() throws {
        // Anti-regression source-level check per spec:
        // "The tap-install code path uses inputNode.outputFormat(forBus: 0) rather
        //  than a hardcoded format (the Plan-3 production bug; Phase 1's tests
        //  must catch a regression)."
        //
        // We can't drive AVAudioEngine in CI (no audio hardware), so we read the
        // RecordAudio.swift source file and assert on the tap-install patterns
        // directly. This catches the exact regression the spec warns about: a
        // future edit that "simplifies" startMicOnly to install the tap with
        // targetFormat (16 kHz mono) and re-introduces the NSException crash.
        let sourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()        // .../Tests/TurboAcquirerTests
            .deletingLastPathComponent()        // .../Tests
            .deletingLastPathComponent()        // .../turbo-acquirer
            .appendingPathComponent("Sources/TurboAcquirer/RecordAudio.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)

        // Must consult the input node's native hardware format for the tap.
        XCTAssertTrue(
            source.contains("inputNode.outputFormat(forBus: 0)"),
            "RecordAudio.swift must read the input node's native HW format via `inputNode.outputFormat(forBus: 0)`."
        )

        // The mic-only tap install line must use `format: nativeFormat`, not the
        // hardcoded 16 kHz `targetFormat`. We grep for the tap-install pattern
        // and require the literal `nativeFormat` reference within the same line.
        let lines = source.components(separatedBy: "\n")
        let tapInstalls = lines.filter { $0.contains("inputNode.installTap") }
        XCTAssertFalse(tapInstalls.isEmpty, "Expected at least one inputNode.installTap(...) call.")
        for line in tapInstalls {
            XCTAssertTrue(
                line.contains("format: nativeFormat"),
                "Tap install must use `format: nativeFormat`, never `targetFormat` directly. Offending line: \(line)"
            )
        }

        // And AVAudioConverter must be set up so the buffers reach the file in
        // the target 16 kHz mono format.
        XCTAssertTrue(
            source.contains("AVAudioConverter(from: nativeFormat, to: targetFormat)"),
            "RecordAudio.swift must convert native→target via AVAudioConverter."
        )
    }

    // MARK: - Fake mode test (T-9 Step 1 — ensures signal handlers installed before engine)

    func test_fake_mode_creates_output_file_and_returns() throws {
        setenv("TURBO_RECORD_AUDIO_FAKE", "1", 1)
        defer { unsetenv("TURBO_RECORD_AUDIO_FAKE") }

        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("record-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let outputURL = tmpDir.appendingPathComponent("out.wav")
        try RecordAudio.run(scope: .micOnly, deviceUID: nil, outputURL: outputURL, parentId: nil)

        XCTAssertTrue(FileManager.default.fileExists(atPath: outputURL.path),
                      "Fake mode must create the output file")
    }

    // MARK: - T-10: system+mic permission check

    /// Spec requirement: when Screen Recording is denied, `run(scope: .systemPlusMic, …)`
    /// throws `RecordAudioError.permissionDenied("Screen Recording")` *before*
    /// touching SCStream. We exercise that guard deterministically via the
    /// `screenRecordingPermissionOverride` test seam — no TCC dependency, no
    /// engine startup, no audio hardware required.
    func test_run_system_plus_mic_throws_permission_denied_when_seam_says_denied() throws {
        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("record-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }
        let outputURL = tmpDir.appendingPathComponent("out.wav")

        RecordAudio.screenRecordingPermissionOverride = false
        defer { RecordAudio.screenRecordingPermissionOverride = nil }

        do {
            try RecordAudio.run(
                scope: .systemPlusMic,
                deviceUID: nil,
                outputURL: outputURL,
                parentId: nil
            )
            XCTFail("Expected RecordAudioError.permissionDenied when seam reports denied")
        } catch let RecordAudioError.permissionDenied(reason) {
            XCTAssertEqual(reason, "Screen Recording")
        } catch {
            XCTFail("Expected RecordAudioError.permissionDenied; got \(error)")
        }

        // Output file must not have been created — the guard fires before any IO.
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: outputURL.path),
            "permissionDenied must short-circuit before creating the WAV."
        )
    }

    /// When the seam reports `true` (granted), mic-only still works as before — proving
    /// the seam only affects the system+mic preflight and does not leak into other paths.
    func test_screen_recording_seam_does_not_affect_mic_only_path() throws {
        setenv("TURBO_RECORD_AUDIO_FAKE", "1", 1)
        defer { unsetenv("TURBO_RECORD_AUDIO_FAKE") }
        RecordAudio.screenRecordingPermissionOverride = false  // would deny system+mic
        defer { RecordAudio.screenRecordingPermissionOverride = nil }

        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("record-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let outputURL = tmpDir.appendingPathComponent("out.wav")
        // Mic-only must succeed even though the override would deny system+mic.
        try RecordAudio.run(scope: .micOnly, deviceUID: nil, outputURL: outputURL, parentId: nil)
        XCTAssertTrue(FileManager.default.fileExists(atPath: outputURL.path))
    }
}
