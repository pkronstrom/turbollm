import XCTest
@testable import TurboAcquirer

final class SubcommandDispatchTests: XCTestCase {
    func test_dispatch_record_audio() {
        let result = App.dispatch(argv: ["turbo-acquirer", "record-audio"])
        XCTAssertEqual(result, "record-audio")
    }

    func test_dispatch_screenshot() {
        let result = App.dispatch(argv: ["turbo-acquirer", "screenshot"])
        XCTAssertEqual(result, "screenshot")
    }

    func test_dispatch_command() {
        let result = App.dispatch(argv: ["turbo-acquirer", "command"])
        XCTAssertEqual(result, "command")
    }

    func test_dispatch_permissions_state() {
        let result = App.dispatch(argv: ["turbo-acquirer", "permissions-state"])
        XCTAssertEqual(result, "permissions-state")
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
        let results = [
            App.dispatch(argv: ["turbo-acquirer", "record-audio"]),
            App.dispatch(argv: ["turbo-acquirer", "screenshot"]),
            App.dispatch(argv: ["turbo-acquirer", "command"]),
            App.dispatch(argv: ["turbo-acquirer", "permissions-state"]),
        ]
        let unique = Set(results)
        XCTAssertEqual(unique.count, 4, "All four handlers must return distinct results")
    }
}
