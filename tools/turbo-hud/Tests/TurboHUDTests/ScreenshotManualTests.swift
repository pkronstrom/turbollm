import XCTest
@testable import TurboHUD

final class ScreenshotManualTests: XCTestCase {
    func test_conforms_to_acquirer_as_trigger() {
        let acquirer = ScreenshotManual(
            paramName: "screenshots",
            outputDir: FileManager.default.temporaryDirectory
        )
        let a: any Acquirer = acquirer
        XCTAssertEqual(a.mode, .trigger)
        XCTAssertEqual(a.paramName, "screenshots")
    }

    func test_three_trigger_filePath_calls_produce_joined_value() async throws {
        let acquirer = ScreenshotManual(
            paramName: "screenshots",
            outputDir: FileManager.default.temporaryDirectory
        )

        acquirer.trigger(filePath: "/tmp/x.png")
        acquirer.trigger(filePath: "/tmp/x.png")
        acquirer.trigger(filePath: "/tmp/x.png")

        // Cancel from a detached task so its lifetime cannot race the acquire
        // task as siblings in a throwing group (where the cancellation throw
        // could propagate before the acquire result is yielded).
        Task.detached {
            try? await Task.sleep(nanoseconds: 10_000_000)
            acquirer.cancel()
        }

        let result = try await acquirer.acquire()

        XCTAssertEqual(result.paramName, "screenshots")
        XCTAssertEqual(result.value, "/tmp/x.png\n/tmp/x.png\n/tmp/x.png")
        XCTAssertNil(result.phase)
    }

    func test_acquire_with_no_triggers_returns_empty_string() async throws {
        let acquirer = ScreenshotManual(
            paramName: "screenshots",
            outputDir: FileManager.default.temporaryDirectory
        )

        Task.detached {
            try? await Task.sleep(nanoseconds: 10_000_000)
            acquirer.cancel()
        }

        let result = try await acquirer.acquire()

        XCTAssertEqual(result.value, "")
    }
}
