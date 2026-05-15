import XCTest
@testable import TurboHUD

final class ActivityTests: XCTestCase {
    func test_decode_minimal_activity() throws {
        let json = #"""
        {
          "id": "workflow-abc123",
          "kind": "workflow",
          "label": "Running transcribe-file",
          "icon": "play",
          "color": "blue",
          "phase": null,
          "started_at": "2026-01-15T15:30:00Z",
          "owner_pid": 12345
        }
        """#
        let data = json.data(using: .utf8)!
        let activity = try JSONDecoder.activity().decode(Activity.self, from: data)
        XCTAssertEqual(activity.id, "workflow-abc123")
        XCTAssertEqual(activity.kind, "workflow")
        XCTAssertEqual(activity.label, "Running transcribe-file")
        XCTAssertEqual(activity.icon, "play")
        XCTAssertEqual(activity.color, "blue")
        XCTAssertNil(activity.phase)
        XCTAssertEqual(activity.ownerPid, 12345)
    }

    func test_decode_activity_with_nulls() throws {
        let json = #"""
        {"id":"x","kind":"external","label":"y","icon":null,"color":null,"phase":null,"started_at":"2026-01-15T15:30:00Z","owner_pid":1}
        """#
        let data = json.data(using: .utf8)!
        let activity = try JSONDecoder.activity().decode(Activity.self, from: data)
        XCTAssertNil(activity.icon)
        XCTAssertNil(activity.color)
    }
}
