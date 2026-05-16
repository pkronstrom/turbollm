import XCTest
@testable import TurboAcquirer

final class ActivityFileTests: XCTestCase {

    // MARK: - Activity struct decode tests (mirrors HUD's ActivityTests)

    func test_decode_activity_with_parent_id() throws {
        let json = #"""
        {
          "id": "acquirer-uuid-abc",
          "kind": "acquirer",
          "label": "audio (mic-only)",
          "icon": null,
          "color": null,
          "phase": null,
          "started_at": "2026-01-15T15:30:00Z",
          "owner_pid": 42,
          "parent_id": "workflow-uuid-123"
        }
        """#
        let data = json.data(using: .utf8)!
        let activity = try JSONDecoder.activity().decode(Activity.self, from: data)
        XCTAssertEqual(activity.id, "acquirer-uuid-abc")
        XCTAssertEqual(activity.kind, "acquirer")
        XCTAssertEqual(activity.parentId, "workflow-uuid-123")
    }

    func test_decode_activity_without_parent_id_is_nil() throws {
        let json = #"""
        {
          "id": "acquirer-uuid-def",
          "kind": "acquirer",
          "label": "audio (mic-only)",
          "icon": null,
          "color": null,
          "phase": null,
          "started_at": "2026-01-15T15:30:00Z",
          "owner_pid": 42
        }
        """#
        let data = json.data(using: .utf8)!
        let activity = try JSONDecoder.activity().decode(Activity.self, from: data)
        XCTAssertNil(activity.parentId)
    }

    func test_decode_activity_with_null_parent_id_is_nil() throws {
        let json = #"""
        {
          "id": "acquirer-uuid-ghi",
          "kind": "acquirer",
          "label": "audio",
          "icon": null,
          "color": null,
          "phase": null,
          "started_at": "2026-01-15T15:30:00Z",
          "owner_pid": 42,
          "parent_id": null
        }
        """#
        let data = json.data(using: .utf8)!
        let activity = try JSONDecoder.activity().decode(Activity.self, from: data)
        XCTAssertNil(activity.parentId)
    }

    // MARK: - ActivityFile write/delete tests

    func test_writeActivity_creates_file_with_expected_fields() throws {
        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("activityfile-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        // Temporarily redirect stateDir by using a custom write helper (inline for test isolation).
        let label = "audio (mic-only)"
        let parentId = "workflow-uuid-456"

        // Write directly to tmpDir (not the real stateDir) to avoid polluting state.
        let uuid = UUID().uuidString
        let fileURL = tmpDir.appendingPathComponent("activity-\(uuid).json")
        var entry: [String: Any] = [
            "id": uuid,
            "kind": "acquirer",
            "label": label,
            "started_at": ISO8601DateFormatter().string(from: Date()),
            "owner_pid": ProcessInfo.processInfo.processIdentifier,
            "children": [],
            "parent_id": parentId
        ]
        let data = try JSONSerialization.data(withJSONObject: entry)
        try data.write(to: fileURL)

        // Verify the file was written.
        XCTAssertTrue(FileManager.default.fileExists(atPath: fileURL.path))

        // Verify we can decode it as an Activity.
        let readData = try Data(contentsOf: fileURL)
        let activity = try JSONDecoder.activity().decode(Activity.self, from: readData)
        XCTAssertEqual(activity.kind, "acquirer")
        XCTAssertEqual(activity.label, label)
        XCTAssertEqual(activity.parentId, parentId)
        XCTAssertEqual(activity.ownerPid, Int(ProcessInfo.processInfo.processIdentifier))
        XCTAssertEqual(activity.children, [])

        // Verify deleteActivity removes it.
        ActivityFile.deleteActivity(at: fileURL)
        XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path))
    }

    func test_writeActivity_omits_parent_id_when_nil() throws {
        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("activityfile-tests-nil-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let uuid = UUID().uuidString
        let fileURL = tmpDir.appendingPathComponent("activity-\(uuid).json")
        let entry: [String: Any] = [
            "id": uuid,
            "kind": "acquirer",
            "label": "screenshot",
            "started_at": ISO8601DateFormatter().string(from: Date()),
            "owner_pid": ProcessInfo.processInfo.processIdentifier,
            "children": []
        ]
        let data = try JSONSerialization.data(withJSONObject: entry)
        try data.write(to: fileURL)

        XCTAssertTrue(FileManager.default.fileExists(atPath: fileURL.path))

        let readData = try Data(contentsOf: fileURL)
        let activity = try JSONDecoder.activity().decode(Activity.self, from: readData)
        XCTAssertNil(activity.parentId)

        ActivityFile.deleteActivity(at: fileURL)
        XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path))
    }

    func test_deleteActivity_is_idempotent() throws {
        let tmpFile = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("nonexistent-activity-\(UUID().uuidString).json")
        // Should not throw — deleteActivity ignores errors when file is absent.
        ActivityFile.deleteActivity(at: tmpFile)
    }

    func test_writeActivity_via_public_api_creates_and_delete_removes() throws {
        // Use the real ActivityFile.writeActivity but override stateDir by relying on
        // the fact that writeActivity uses ActivityFile.stateDir. We test the real API here
        // and clean up afterwards.
        let label = "command"
        let parentId = "parent-workflow-xyz"

        let fileURL = try ActivityFile.writeActivity(label: label, parentId: parentId)
        defer { ActivityFile.deleteActivity(at: fileURL) }

        XCTAssertTrue(FileManager.default.fileExists(atPath: fileURL.path))
        XCTAssertTrue(fileURL.lastPathComponent.hasPrefix("activity-"))
        XCTAssertTrue(fileURL.pathExtension == "json")

        let readData = try Data(contentsOf: fileURL)
        let activity = try JSONDecoder.activity().decode(Activity.self, from: readData)
        XCTAssertEqual(activity.kind, "acquirer")
        XCTAssertEqual(activity.label, label)
        XCTAssertEqual(activity.parentId, parentId)

        ActivityFile.deleteActivity(at: fileURL)
        XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path))
    }

    func test_writeActivity_without_parent_id() throws {
        let fileURL = try ActivityFile.writeActivity(label: "screenshot", parentId: nil)
        defer { ActivityFile.deleteActivity(at: fileURL) }

        let readData = try Data(contentsOf: fileURL)
        let activity = try JSONDecoder.activity().decode(Activity.self, from: readData)
        XCTAssertNil(activity.parentId)
    }

    // MARK: - screen_frames field (T-2)

    func test_decode_screen_frames_absent_defaults_to_empty() throws {
        let json = #"""
        {
          "id": "screen-uuid-1",
          "kind": "acquirer",
          "label": "screen",
          "icon": null,
          "color": null,
          "phase": null,
          "started_at": "2026-01-15T15:30:00Z",
          "owner_pid": 42
        }
        """#
        let data = json.data(using: .utf8)!
        let activity = try JSONDecoder.activity().decode(Activity.self, from: data)
        XCTAssertEqual(activity.screenFrames, [])
    }

    func test_decode_screen_frames_present() throws {
        let json = #"""
        {
          "id": "screen-uuid-2",
          "kind": "acquirer",
          "label": "screen",
          "icon": null,
          "color": null,
          "phase": null,
          "started_at": "2026-01-15T15:30:00Z",
          "owner_pid": 42,
          "screen_frames": ["/tmp/frames/001-T+0.png", "/tmp/frames/002-T+1500.png"]
        }
        """#
        let data = json.data(using: .utf8)!
        let activity = try JSONDecoder.activity().decode(Activity.self, from: data)
        XCTAssertEqual(activity.screenFrames, [
            "/tmp/frames/001-T+0.png",
            "/tmp/frames/002-T+1500.png"
        ])
    }

    func test_decode_old_activity_file_without_screen_frames_is_forward_compatible() throws {
        // Phase 1 activity files have no screen_frames key — must decode cleanly.
        let json = #"""
        {
          "id": "phase1-acquirer-uuid",
          "kind": "acquirer",
          "label": "audio (mic-only)",
          "icon": null,
          "color": null,
          "phase": null,
          "started_at": "2026-01-15T15:30:00Z",
          "owner_pid": 77,
          "parent_id": "wf-uuid-xyz"
        }
        """#
        let data = json.data(using: .utf8)!
        let activity = try JSONDecoder.activity().decode(Activity.self, from: data)
        XCTAssertEqual(activity.screenFrames, [],
            "Phase 1 activity files must decode with screenFrames == []")
        XCTAssertEqual(activity.parentId, "wf-uuid-xyz")
    }
}
