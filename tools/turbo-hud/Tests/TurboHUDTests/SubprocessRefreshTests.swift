import XCTest
@testable import TurboHUD

final class SubprocessRefreshTests: XCTestCase {
    func testDrainsBothPipesBeforeWaitingForExit() throws {
        let result = try BoundedSubprocess.run(
            executable: "/bin/sh",
            arguments: ["-c", "dd if=/dev/zero bs=65536 count=8 2>/dev/null; dd if=/dev/zero bs=65536 count=8 >&2 2>/dev/null"],
            timeout: 2)
        XCTAssertFalse(result.timedOut)
        XCTAssertEqual(result.status, 0)
        XCTAssertEqual(result.stdout.count, 524288)
        XCTAssertEqual(result.stderr.count, 524288)
    }

    func testTerminatesUncooperativeProcessWithinBound() throws {
        let start = Date()
        let result = try BoundedSubprocess.run(
            executable: "/bin/sh", arguments: ["-c", "trap '' TERM; while :; do :; done"], timeout: 0.1)
        XCTAssertTrue(result.timedOut)
        XCTAssertLessThan(Date().timeIntervalSince(start), 2)
    }

    func testRegistryCoalescesDelayedRequests() {
        let counter = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: counter) }
        let registry = WorkflowRegistry(executable: "/bin/sh", arguments: ["-c", "echo fetch >> \"$1\"; sleep 0.2; printf '[]'", "sh", counter.path], timeout: 2)
        let done = expectation(description: "all callers receive the fetch")
        done.expectedFulfillmentCount = 3
        for _ in 0..<3 {
            registry.current { workflows, error in
                XCTAssertTrue(Thread.isMainThread)
                XCTAssertNil(error)
                XCTAssertTrue(workflows.isEmpty)
                done.fulfill()
            }
        }
        wait(for: [done], timeout: 3)
        XCTAssertEqual(try? String(contentsOf: counter), "fetch\n")
    }

    func testPermissionRefreshReturnsCachedValueAndCoalescesDelayedSubprocess() {
        var runs = 0
        let cache = PermissionStateCache {
            runs += 1
            _ = try? BoundedSubprocess.run(executable: "/bin/sh", arguments: ["-c", "sleep 0.2"], timeout: 2)
            return PermissionState(microphone: .authorized, screenRecording: true)
        }
        let done = expectation(description: "refresh callbacks")
        done.expectedFulfillmentCount = 2
        let start = Date()
        for _ in 0..<2 {
            cache.refresh { state in
                XCTAssertTrue(Thread.isMainThread)
                XCTAssertEqual(state.microphone, .authorized)
                done.fulfill()
            }
        }
        XCTAssertEqual(cache.state.microphone, .notDetermined)
        XCTAssertLessThan(Date().timeIntervalSince(start), 0.1)
        wait(for: [done], timeout: 3)
        XCTAssertEqual(runs, 1)
        XCTAssertTrue(cache.state.screenRecording)
    }
}
