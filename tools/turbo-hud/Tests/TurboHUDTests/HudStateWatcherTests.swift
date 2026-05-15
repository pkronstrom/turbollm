import XCTest
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
}
