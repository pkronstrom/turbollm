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

        // acquire() suspends; we cancel from a concurrent task to resolve it.
        let result = try await withThrowingTaskGroup(of: AcquirerResult.self) { group in
            group.addTask { try await acquirer.acquire() }
            group.addTask {
                // Small yield so acquire() has time to set up its continuation.
                try await Task.sleep(nanoseconds: 10_000_000)
                acquirer.cancel()
                // This task never produces a result; the acquire task does.
                throw CancellationError()
            }
            // Collect the first non-error result.
            for try await result in group {
                group.cancelAll()
                return result
            }
            throw AcquirerError.emptyOutput
        }

        XCTAssertEqual(result.paramName, "screenshots")
        XCTAssertEqual(result.value, "/tmp/x.png\n/tmp/x.png\n/tmp/x.png")
        XCTAssertNil(result.phase)
    }

    func test_acquire_with_no_triggers_returns_empty_string() async throws {
        let acquirer = ScreenshotManual(
            paramName: "screenshots",
            outputDir: FileManager.default.temporaryDirectory
        )

        let result = try await withThrowingTaskGroup(of: AcquirerResult.self) { group in
            group.addTask { try await acquirer.acquire() }
            group.addTask {
                try await Task.sleep(nanoseconds: 10_000_000)
                acquirer.cancel()
                throw CancellationError()
            }
            for try await result in group {
                group.cancelAll()
                return result
            }
            throw AcquirerError.emptyOutput
        }

        XCTAssertEqual(result.value, "")
    }
}
