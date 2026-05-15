import XCTest
import CoreGraphics
@testable import TurboHUD

final class AudioRecorderTests: XCTestCase {

    // MARK: - T-5: Acquirer conformance

    func test_audioRecorder_conforms_to_Acquirer() {
        // Verify that AudioRecorder satisfies the Acquirer protocol at the type level.
        let rec: any Acquirer = AudioRecorder(
            paramName: "audio",
            scope: .micOnly,
            inputDeviceUID: nil,
            outputDir: URL(fileURLWithPath: NSTemporaryDirectory())
        )
        XCTAssertEqual(rec.mode, .primary)
        XCTAssertEqual(rec.paramName, "audio")
    }

    // MARK: - T-5: outputPath without sessionDir

    func test_outputPath_uses_TURBO_AUDIO_INBOX_when_set() {
        let env = ProcessInfo.processInfo.environment
        // We can't set env vars on ProcessInfo at runtime; instead, we verify
        // the structure of the fallback path (without the env var set).
        // If TURBO_AUDIO_INBOX is set in the current process, assert it's used.
        let wf = Workflow(name: "test", description: nil, command: nil,
                          script: nil, args: nil, env: nil, params: [])
        let url = AudioRecorder.outputPath(for: wf, sessionDir: nil)
        let inbox = env["TURBO_AUDIO_INBOX"]
        if let inbox = inbox {
            XCTAssertTrue(url.path.hasPrefix(inbox), "Expected path under TURBO_AUDIO_INBOX.")
        } else {
            let home = FileManager.default.homeDirectoryForCurrentUser.path
            XCTAssertTrue(url.path.hasPrefix(home + "/Recordings/turbo"),
                          "Expected path under ~/Recordings/turbo when TURBO_AUDIO_INBOX unset.")
        }
        XCTAssertEqual(url.pathExtension, "wav")
    }

    func test_outputPath_filename_matches_timestamp_pattern() {
        let wf = Workflow(name: "test", description: nil, command: nil,
                          script: nil, args: nil, env: nil, params: [])
        let url = AudioRecorder.outputPath(for: wf, sessionDir: nil)
        let filename = url.deletingPathExtension().lastPathComponent
        // Pattern: yyyy-MM-dd-HHmmss  e.g. "2026-01-15-185830"
        let pattern = #"^\d{4}-\d{2}-\d{2}-\d{6}$"#
        XCTAssertNotNil(filename.range(of: pattern, options: .regularExpression),
                        "Filename '\(filename)' should match yyyy-MM-dd-HHmmss.")
    }

    // MARK: - T-5: outputPath with sessionDir

    func test_outputPath_with_sessionDir_returns_audio_wav() {
        let sessionDir = URL(fileURLWithPath: "/tmp/turbo-session-abc123")
        let wf = Workflow(name: "test", description: nil, command: nil,
                          script: nil, args: nil, env: nil, params: [])
        let url = AudioRecorder.outputPath(for: wf, sessionDir: sessionDir)
        XCTAssertEqual(url.path, "/tmp/turbo-session-abc123/audio.wav")
    }

    // MARK: - T-6: system+mic init (permission-gated)

    func test_systemPlusMic_init_succeeds_when_screen_recording_granted() {
        guard CGPreflightScreenCaptureAccess() else {
            // Skip if Screen Recording is not granted in this test environment.
            return
        }
        // Just assert we can instantiate without crashing; we don't call acquire().
        let rec = AudioRecorder(
            paramName: "audio",
            scope: .systemPlusMic,
            inputDeviceUID: nil,
            outputDir: URL(fileURLWithPath: NSTemporaryDirectory())
        )
        XCTAssertEqual(rec.mode, .primary)
    }
}
