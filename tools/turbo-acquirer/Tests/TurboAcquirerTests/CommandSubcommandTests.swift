import XCTest
@testable import TurboAcquirer

final class CommandSubcommandTests: XCTestCase {

    func test_echo_hello_returns_hello_exit0() {
        let result = CommandSubcommand.run(shellCommand: "echo hello")
        switch result {
        case .success(let stdout):
            XCTAssertEqual(stdout, "hello")
        case .failure(let stderr, let exitCode):
            XCTFail("Expected success but got failure: stderr=\(stderr), exitCode=\(exitCode)")
        }
    }

    func test_true_empty_stdout_returns_emptyOutput_error() {
        let result = CommandSubcommand.run(shellCommand: "true")
        switch result {
        case .success(let stdout):
            XCTFail("Expected failure(emptyOutput) but got success: stdout=\(stdout)")
        case .failure(let stderr, let exitCode):
            XCTAssertTrue(stderr.contains("emptyOutput"),
                          "stderr must contain 'emptyOutput', got: \(stderr)")
            XCTAssertNotEqual(exitCode, 0, "Exit code must be non-zero for empty stdout")
        }
    }

    func test_exit7_returns_nonzero() {
        let result = CommandSubcommand.run(shellCommand: "exit 7")
        switch result {
        case .success(let stdout):
            XCTFail("Expected failure but got success: stdout=\(stdout)")
        case .failure(_, let exitCode):
            XCTAssertNotEqual(exitCode, 0, "Non-zero subprocess exit must propagate as non-zero")
        }
    }

    func test_stderr_forwarded_on_subprocess_failure() {
        let result = CommandSubcommand.run(shellCommand: "echo 'oops' >&2; exit 1")
        switch result {
        case .success(let stdout):
            XCTFail("Expected failure but got success: stdout=\(stdout)")
        case .failure(let stderr, _):
            XCTAssertTrue(stderr.contains("oops"),
                          "stderr from subprocess must be forwarded, got: \(stderr)")
        }
    }

    func test_multiline_stdout_trimmed() {
        let result = CommandSubcommand.run(shellCommand: "printf 'hello\\n\\n'")
        switch result {
        case .success(let stdout):
            XCTAssertEqual(stdout, "hello", "Trailing whitespace/newlines must be trimmed")
        case .failure(let stderr, let exitCode):
            XCTFail("Expected success but got failure: stderr=\(stderr), exitCode=\(exitCode)")
        }
    }

    func test_parse_shell_arg_extracts_command() {
        let argv = ["--shell", "echo foo"]
        XCTAssertEqual(CommandSubcommand.parseShellArg(argv: argv), "echo foo")
    }

    func test_parse_shell_arg_missing_returns_nil() {
        let argv: [String] = []
        XCTAssertNil(CommandSubcommand.parseShellArg(argv: argv))
    }
}
