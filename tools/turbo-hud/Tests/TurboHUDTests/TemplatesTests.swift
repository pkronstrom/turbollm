import XCTest
@testable import TurboHUD

final class TemplatesTests: XCTestCase {
    func test_substitutes_param() throws {
        let out = try Templates.expand("hello {{name}}", params: ["name": "world"])
        XCTAssertEqual(out, "hello world")
    }

    func test_substitutes_env() throws {
        setenv("TEST_VAR", "envval", 1)
        defer { unsetenv("TEST_VAR") }
        let out = try Templates.expand("x={{env:TEST_VAR}}", params: [:])
        XCTAssertEqual(out, "x=envval")
    }

    func test_missing_env_is_empty() throws {
        unsetenv("MISSING_VAR_XYZ")
        let out = try Templates.expand("[{{env:MISSING_VAR_XYZ}}]", params: [:])
        XCTAssertEqual(out, "[]")
    }

    func test_date_uses_strftime() throws {
        let out = try Templates.expand("{{date:%Y}}", params: [:])
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy"
        XCTAssertEqual(out, formatter.string(from: Date()))
    }

    func test_missing_param_throws() {
        XCTAssertThrowsError(try Templates.expand("{{missing}}", params: ["other": "x"]))
    }
}
