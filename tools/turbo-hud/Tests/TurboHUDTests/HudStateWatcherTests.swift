import XCTest
import Foundation
@testable import TurboHUD

final class HudStateWatcherTests: XCTestCase {
    func test_loadCurrent_decodes_all_files_in_dir() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let f1 = dir.appendingPathComponent("activity-x.json")
        try #"{"id":"x","kind":"workflow","label":"L1","icon":null,"color":null,"phase":null,"started_at":"2026-01-15T10:00:00Z","owner_pid":\#(ProcessInfo.processInfo.processIdentifier)}"#.write(to: f1, atomically: true, encoding: .utf8)

        let watcher = HudStateWatcher(stateDir: dir)
        let activities = watcher.loadCurrent()
        XCTAssertEqual(activities.count, 1)
        XCTAssertEqual(activities[0].id, "x")
    }

    func test_loadCurrent_skips_malformed_json() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        try "not json".write(to: dir.appendingPathComponent("activity-bad.json"), atomically: true, encoding: .utf8)

        let watcher = HudStateWatcher(stateDir: dir)
        XCTAssertEqual(watcher.loadCurrent().count, 0)
    }

    func test_loadCurrent_purges_dead_pid_entries() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let f = dir.appendingPathComponent("activity-dead.json")
        try #"{"id":"dead","kind":"workflow","label":"L","icon":null,"color":null,"phase":null,"started_at":"2026-01-15T10:00:00Z","owner_pid":999999}"#.write(to: f, atomically: true, encoding: .utf8)

        let watcher = HudStateWatcher(stateDir: dir)
        _ = watcher.loadCurrent()
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.path))
    }

    func test_loadCurrent_purges_stale_entries_even_with_live_pid() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        // started_at well over 24h ago; owner_pid is the test runner's PID (alive).
        let oldDate = "2020-01-01T00:00:00Z"
        let f = dir.appendingPathComponent("activity-old.json")
        let pid = ProcessInfo.processInfo.processIdentifier
        try #"{"id":"old","kind":"workflow","label":"L","icon":null,"color":null,"phase":null,"started_at":"\#(oldDate)","owner_pid":\#(pid)}"#.write(to: f, atomically: true, encoding: .utf8)

        let watcher = HudStateWatcher(stateDir: dir)
        let live = watcher.loadCurrent()
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.path))
        XCTAssertEqual(live.count, 0)
    }

    func test_loadCurrent_kills_children_of_dead_owner() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        // Spawn a long-running sleep subprocess to act as the child process.
        let sleepProc = Process()
        sleepProc.executableURL = URL(fileURLWithPath: "/bin/sleep")
        sleepProc.arguments = ["60"]
        try sleepProc.run()
        let childPid = Int(sleepProc.processIdentifier)

        // Write an activity file with a dead owner PID and the sleep process as a child.
        let f = dir.appendingPathComponent("activity-orphan.json")
        try #"{"id":"orphan","kind":"workflow","label":"L","icon":null,"color":null,"phase":null,"started_at":"2026-01-15T10:00:00Z","owner_pid":999999,"children":[\#(childPid)]}"#.write(to: f, atomically: true, encoding: .utf8)

        let watcher = HudStateWatcher(stateDir: dir)
        _ = watcher.loadCurrent()

        // Activity file must be deleted.
        XCTAssertFalse(FileManager.default.fileExists(atPath: f.path))

        // Sleep subprocess must die within 2 seconds after receiving SIGTERM.
        let deadline = Date().addingTimeInterval(2.0)
        while Date() < deadline {
            // kill(pid, 0) returns -1 with ESRCH when the process no longer exists.
            if kill(pid_t(childPid), 0) == -1 && errno == ESRCH {
                break
            }
            Thread.sleep(forTimeInterval: 0.05)
        }
        XCTAssertEqual(kill(pid_t(childPid), 0), -1, "Child process should be dead after SIGTERM")
    }

    // MARK: - T-17: acquirerActivity extraction

    func test_acquirerActivity_returns_nil_when_no_acquirer_activities() throws {
        let activities: [Activity] = [
            Activity(id: "w1", kind: "workflow", label: "Running x",
                     icon: nil, color: nil, phase: nil,
                     startedAt: Date(), ownerPid: Int(ProcessInfo.processInfo.processIdentifier),
                     children: [], parentId: nil)
        ]
        let result = HudStateWatcher.acquirerActivity(from: activities)
        XCTAssertNil(result, "acquirerActivity should be nil when no acquirer activities exist")
    }

    func test_acquirerActivity_returns_acquirer_kind_activity() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let pid = ProcessInfo.processInfo.processIdentifier
        let f = dir.appendingPathComponent("activity-acq.json")
        try #"{"id":"acq1","kind":"acquirer","label":"audio (mic-only)","icon":null,"color":null,"phase":null,"started_at":"2026-01-15T08:00:00Z","owner_pid":\#(pid)}"#
            .write(to: f, atomically: true, encoding: .utf8)

        let watcher = HudStateWatcher(stateDir: dir)
        let activities = watcher.loadCurrent()
        let acquirer = HudStateWatcher.acquirerActivity(from: activities)
        XCTAssertNotNil(acquirer, "acquirerActivity should find the acquirer-kind activity")
        XCTAssertEqual(acquirer?.id, "acq1")
        XCTAssertEqual(acquirer?.label, "audio (mic-only)")
    }

    func test_acquirerActivity_picks_newest_when_multiple_acquirers() throws {
        let older = Activity(id: "old", kind: "acquirer", label: "audio (mic-only)",
                             icon: nil, color: nil, phase: nil,
                             startedAt: Date().addingTimeInterval(-10),
                             ownerPid: 1, children: [], parentId: nil)
        let newer = Activity(id: "new", kind: "acquirer", label: "screenshot",
                             icon: nil, color: nil, phase: nil,
                             startedAt: Date(),
                             ownerPid: 2, children: [], parentId: nil)
        let result = HudStateWatcher.acquirerActivity(from: [older, newer])
        XCTAssertEqual(result?.id, "new", "Newest-started acquirer wins when multiple are present")
    }

    func test_loadCurrent_does_not_kill_children_of_live_owner() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        // Spawn a child subprocess.
        let sleepProc = Process()
        sleepProc.executableURL = URL(fileURLWithPath: "/bin/sleep")
        sleepProc.arguments = ["60"]
        try sleepProc.run()
        let childPid = Int(sleepProc.processIdentifier)
        defer { sleepProc.terminate() }

        // Write an activity file with a live owner PID (the current process) and the child.
        let livePid = ProcessInfo.processInfo.processIdentifier
        let f = dir.appendingPathComponent("activity-live.json")
        try #"{"id":"live","kind":"workflow","label":"L","icon":null,"color":null,"phase":null,"started_at":"2026-01-15T10:00:00Z","owner_pid":\#(livePid),"children":[\#(childPid)]}"#.write(to: f, atomically: true, encoding: .utf8)

        let watcher = HudStateWatcher(stateDir: dir)
        _ = watcher.loadCurrent()

        // Activity file must NOT be deleted (owner is alive and not stale).
        XCTAssertTrue(FileManager.default.fileExists(atPath: f.path))

        // Child must still be alive.
        XCTAssertEqual(kill(pid_t(childPid), 0), 0, "Child process should still be alive")
    }
}
