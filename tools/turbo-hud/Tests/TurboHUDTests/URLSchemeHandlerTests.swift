import XCTest
@testable import TurboHUD

final class URLSchemeHandlerTests: XCTestCase {
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
}
