import XCTest
@testable import TurboAcquirer

final class ScreenshotSubcommandTests: XCTestCase {

    var tmpDir: URL!

    override func setUp() {
        super.setUp()
        tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("screenshot-tests-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        // Ensure TURBO_SCREENCAPTURE_FAKE is not set by prior tests
        // (setenv is not available here — rely on individual tests setting up fake mode
        //  by modifying the env or using the fake-mode path directly in tests).
    }

    override func tearDown() {
        super.tearDown()
        try? FileManager.default.removeItem(at: tmpDir)
    }

    // MARK: - Fake mode tests (unit-testable without real screencapture)

    func test_fake_success_returns_success_and_creates_file() {
        // Inject fake mode via env override — setenv so ProcessInfo picks it up.
        setenv("TURBO_SCREENCAPTURE_FAKE", "success", 1)
        defer { unsetenv("TURBO_SCREENCAPTURE_FAKE") }

        let result = ScreenshotSubcommand.run(outputDir: tmpDir, parentId: nil)

        switch result {
        case .success(let path):
            XCTAssertTrue(FileManager.default.fileExists(atPath: path),
                          "Success result must point to an existing file")
            XCTAssertTrue(path.hasSuffix(".png"), "Output path must have .png extension")
            XCTAssertTrue(path.hasPrefix(tmpDir.path), "Output file must be inside output dir")
        case .cancelled:
            XCTFail("Expected success but got cancelled")
        case .error(let msg):
            XCTFail("Expected success but got error: \(msg)")
        }
    }

    func test_fake_cancel_returns_cancelled() {
        setenv("TURBO_SCREENCAPTURE_FAKE", "cancel", 1)
        defer { unsetenv("TURBO_SCREENCAPTURE_FAKE") }

        let result = ScreenshotSubcommand.run(outputDir: tmpDir, parentId: nil)

        switch result {
        case .cancelled:
            break  // expected
        case .success(let path):
            XCTFail("Expected cancelled but got success at path: \(path)")
        case .error(let msg):
            XCTFail("Expected cancelled but got error: \(msg)")
        }
    }

    func test_fake_success_activity_file_deleted_after_run() {
        setenv("TURBO_SCREENCAPTURE_FAKE", "success", 1)
        defer { unsetenv("TURBO_SCREENCAPTURE_FAKE") }

        // Check that the state dir has no lingering activity files after the run.
        let stateDir = ActivityFile.stateDir
        let filesBefore = (try? FileManager.default.contentsOfDirectory(
            atPath: stateDir.path
        ).filter { $0.hasPrefix("activity-") }) ?? []

        _ = ScreenshotSubcommand.run(outputDir: tmpDir, parentId: nil)

        let filesAfter = (try? FileManager.default.contentsOfDirectory(
            atPath: stateDir.path
        ).filter { $0.hasPrefix("activity-") }) ?? []

        // Count should be equal (activity file written and then deleted).
        XCTAssertEqual(filesBefore.count, filesAfter.count,
                       "Activity file must be deleted after screenshot completes")
    }

    func test_fake_cancel_activity_file_deleted_after_run() {
        setenv("TURBO_SCREENCAPTURE_FAKE", "cancel", 1)
        defer { unsetenv("TURBO_SCREENCAPTURE_FAKE") }

        let stateDir = ActivityFile.stateDir
        let filesBefore = (try? FileManager.default.contentsOfDirectory(
            atPath: stateDir.path
        ).filter { $0.hasPrefix("activity-") }) ?? []

        _ = ScreenshotSubcommand.run(outputDir: tmpDir, parentId: nil)

        let filesAfter = (try? FileManager.default.contentsOfDirectory(
            atPath: stateDir.path
        ).filter { $0.hasPrefix("activity-") }) ?? []

        XCTAssertEqual(filesBefore.count, filesAfter.count,
                       "Activity file must be deleted even when screenshot is cancelled")
    }

    // MARK: - Arg parsing

    func test_parse_output_dir_extracts_dir() {
        let argv = ["--output-dir", "/tmp/screenshots"]
        let url = ScreenshotSubcommand.parseOutputDir(argv: argv)
        XCTAssertEqual(url?.path, "/tmp/screenshots")
    }

    func test_parse_output_dir_missing_returns_nil() {
        let argv: [String] = []
        XCTAssertNil(ScreenshotSubcommand.parseOutputDir(argv: argv))
    }
}
