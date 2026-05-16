import XCTest
@testable import TurboAcquirer

final class SubcommandDispatchTests: XCTestCase {
    func test_dispatch_record_audio() {
        // Use fake mode so dispatch doesn't start real AVAudioEngine (no hardware in tests).
        setenv("TURBO_RECORD_AUDIO_FAKE", "1", 1)
        defer { unsetenv("TURBO_RECORD_AUDIO_FAKE") }
        setenv("TURBO_AUDIO_INBOX", NSTemporaryDirectory(), 1)
        defer { unsetenv("TURBO_AUDIO_INBOX") }

        let result = App.dispatch(argv: ["turbo-acquirer", "record-audio"])
        // In fake mode, returns the output WAV path (a .wav file in TURBO_AUDIO_INBOX).
        XCTAssertTrue(result.hasSuffix(".wav"),
                      "record-audio dispatch in fake mode must return a .wav path, got: \(result)")
    }

    func test_dispatch_screenshot() {
        // Use fake-success mode so dispatch doesn't spawn real screencapture or call exit.
        setenv("TURBO_SCREENCAPTURE_FAKE", "success", 1)
        defer { unsetenv("TURBO_SCREENCAPTURE_FAKE") }

        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("dispatch-screenshot-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let result = App.dispatch(argv: ["turbo-acquirer", "screenshot", "--output-dir", tmpDir.path])
        // In fake-success mode, the result is a PNG path string.
        XCTAssertTrue(result.hasSuffix(".png"),
                      "screenshot dispatch in fake-success mode must return a .png path, got: \(result)")
    }

    func test_dispatch_command() {
        // Real handler requires --shell; provide it so dispatch routes without calling exit.
        let result = App.dispatch(argv: ["turbo-acquirer", "command", "--shell", "echo command"])
        XCTAssertEqual(result, "command",
                       "command subcommand with --shell 'echo command' must echo 'command'")
    }

    func test_dispatch_permissions_state() {
        let result = App.dispatch(argv: ["turbo-acquirer", "permissions-state"])
        // Real handler returns JSON with microphone + screenRecording keys.
        XCTAssertTrue(result.contains("microphone"),
                      "permissions-state must return JSON with microphone key, got: \(result)")
        XCTAssertTrue(result.contains("screenRecording"),
                      "permissions-state must return JSON with screenRecording key, got: \(result)")
    }

    func test_dispatch_unknown_returns_usage() {
        let result = App.dispatch(argv: ["turbo-acquirer", "unknown-subcommand"])
        XCTAssert(result.contains("Usage") || result.contains("unknown"),
                  "Expected usage/unknown message, got: \(result)")
    }

    func test_dispatch_no_subcommand_returns_usage() {
        let result = App.dispatch(argv: ["turbo-acquirer"])
        XCTAssert(result.contains("Usage") || result.contains("usage"),
                  "Expected usage message, got: \(result)")
    }

    func test_subcommands_are_distinct() {
        // Use fake modes so dispatch doesn't start real engines or screencapture.
        setenv("TURBO_RECORD_AUDIO_FAKE", "1", 1)
        defer { unsetenv("TURBO_RECORD_AUDIO_FAKE") }
        setenv("TURBO_SCREENCAPTURE_FAKE", "success", 1)
        defer { unsetenv("TURBO_SCREENCAPTURE_FAKE") }

        // Use an inbox dir so record-audio writes to a known prefix.
        let audioInbox = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("audio-distinct-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: audioInbox, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: audioInbox) }
        setenv("TURBO_AUDIO_INBOX", audioInbox.path, 1)
        defer { unsetenv("TURBO_AUDIO_INBOX") }

        let screenshotDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("screenshot-distinct-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: screenshotDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: screenshotDir) }

        let results = [
            App.dispatch(argv: ["turbo-acquirer", "record-audio"]),
            App.dispatch(argv: ["turbo-acquirer", "screenshot", "--output-dir", screenshotDir.path]),
            App.dispatch(argv: ["turbo-acquirer", "command", "--shell", "echo __cmd_sentinel__"]),
            App.dispatch(argv: ["turbo-acquirer", "permissions-state"]),
        ]
        let unique = Set(results)
        XCTAssertEqual(unique.count, 4, "All four handlers must return distinct results")
    }
}
