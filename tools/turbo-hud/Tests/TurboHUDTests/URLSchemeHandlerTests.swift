import XCTest
@testable import TurboHUD

final class URLSchemeHandlerTests: XCTestCase {

    override func tearDown() {
        // Reset the test seam after every test so it doesn't leak.
        URLSchemeHandler.spawnOverride = nil
        super.tearDown()
    }

    // MARK: - parse() tests (unchanged behaviour)

    func test_parse_basic_run() {
        let url = URL(string: "turbohud://run/transcribe-file?file=/tmp/x.wav")!
        let parsed = URLSchemeHandler.parse(url)
        XCTAssertEqual(parsed?.workflowName, "transcribe-file")
        XCTAssertEqual(parsed?.params["file"], "/tmp/x.wav")
    }

    func test_parse_multiple_params() {
        let url = URL(string: "turbohud://run/wf?a=1&b=hello%20world")!
        let parsed = URLSchemeHandler.parse(url)
        XCTAssertEqual(parsed?.params["a"], "1")
        XCTAssertEqual(parsed?.params["b"], "hello world")
    }

    func test_parse_invalid_scheme_returns_nil() {
        let url = URL(string: "https://example.com")!
        XCTAssertNil(URLSchemeHandler.parse(url))
    }

    // MARK: - buildArgv() tests

    func test_buildArgv_single_param() {
        let url = URL(string: "turbohud://run/transcribe-file?file=/tmp/x.wav")!
        let parsed = URLSchemeHandler.parse(url)!
        let argv = URLSchemeHandler.buildArgv(from: parsed)
        XCTAssertEqual(argv, ["turbo", "workflows", "run", "transcribe-file",
                               "--param", "file=/tmp/x.wav"])
    }

    func test_buildArgv_no_params() {
        let url = URL(string: "turbohud://run/record-to-obsidian")!
        let parsed = URLSchemeHandler.parse(url)!
        let argv = URLSchemeHandler.buildArgv(from: parsed)
        XCTAssertEqual(argv, ["turbo", "workflows", "run", "record-to-obsidian"])
    }

    func test_buildArgv_multiple_params_sorted() {
        // Params should appear in sorted-key order for determinism.
        let url = URL(string: "turbohud://run/wf?z=last&a=first")!
        let parsed = URLSchemeHandler.parse(url)!
        let argv = URLSchemeHandler.buildArgv(from: parsed)
        XCTAssertEqual(argv, ["turbo", "workflows", "run", "wf",
                               "--param", "a=first",
                               "--param", "z=last"])
    }

    // MARK: - spawn() test seam

    func test_spawn_calls_override_with_correct_argv() {
        let url = URL(string: "turbohud://run/transcribe-file?file=/tmp/x.wav")!
        let parsed = URLSchemeHandler.parse(url)!
        let argv = URLSchemeHandler.buildArgv(from: parsed)

        var captured: [String]? = nil
        URLSchemeHandler.spawnOverride = { captured = $0 }

        URLSchemeHandler.spawn(argv: argv)

        XCTAssertEqual(captured, ["turbo", "workflows", "run", "transcribe-file",
                                   "--param", "file=/tmp/x.wav"])
    }

    func test_url_received_spawns_turbo_workflows_run() {
        // Full integration through the handler: parse URL → buildArgv → spawn override.
        var captured: [String]? = nil
        URLSchemeHandler.spawnOverride = { captured = $0 }

        // Simulate a URL arriving by going through the parsed → spawn path directly.
        let url = URL(string: "turbohud://run/transcribe-file?file=/tmp/x.wav")!
        guard let parsed = URLSchemeHandler.parse(url) else {
            XCTFail("parse returned nil")
            return
        }
        let argv = URLSchemeHandler.buildArgv(from: parsed)
        URLSchemeHandler.spawn(argv: argv)

        XCTAssertEqual(captured, ["turbo", "workflows", "run", "transcribe-file",
                                   "--param", "file=/tmp/x.wav"])
    }
}
