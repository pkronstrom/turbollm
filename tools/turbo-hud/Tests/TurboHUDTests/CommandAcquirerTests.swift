import XCTest
@testable import TurboHUD

final class CommandAcquirerTests: XCTestCase {
    func test_echo_hello_returns_hello() async throws {
        let acquirer = CommandAcquirer(paramName: "x", command: "echo hello")
        let result = try await acquirer.acquire()
        XCTAssertEqual(result.value, "hello")
        XCTAssertEqual(result.paramName, "x")
    }

    func test_true_with_no_output_throws_emptyOutput() async throws {
        let acquirer = CommandAcquirer(paramName: "x", command: "true")
        do {
            _ = try await acquirer.acquire()
            XCTFail("Expected AcquirerError.emptyOutput")
        } catch AcquirerError.emptyOutput {
            // expected
        }
    }

    func test_nonzero_exit_throws_underlying() async throws {
        let acquirer = CommandAcquirer(paramName: "x", command: "exit 1")
        do {
            _ = try await acquirer.acquire()
            XCTFail("Expected AcquirerError.underlying")
        } catch AcquirerError.underlying {
            // expected
        }
    }

    func test_multiword_pipeline_works() async throws {
        let acquirer = CommandAcquirer(paramName: "x", command: "echo hello | tr 'a-z' 'A-Z'")
        let result = try await acquirer.acquire()
        XCTAssertEqual(result.value, "HELLO")
    }
}
