import Foundation

// MARK: - Activity (mirrors tools/turbo-hud/Sources/TurboHUD/Activity.swift)
// Swift packages cannot share types across packages without a shared library.
// This struct is intentionally duplicated and kept in sync with the HUD's Activity.

struct Activity: Codable {
    let id: String
    let kind: String
    let label: String
    let icon: String?
    let color: String?
    let phase: String?
    let startedAt: Date
    let ownerPid: Int
    let children: [Int]
    let parentId: String?

    enum CodingKeys: String, CodingKey {
        case id, kind, label, icon, color, phase, children
        case startedAt = "started_at"
        case ownerPid = "owner_pid"
        case parentId = "parent_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try c.decode(String.self, forKey: .kind)
        label = try c.decode(String.self, forKey: .label)
        icon = try c.decodeIfPresent(String.self, forKey: .icon)
        color = try c.decodeIfPresent(String.self, forKey: .color)
        phase = try c.decodeIfPresent(String.self, forKey: .phase)
        startedAt = try c.decode(Date.self, forKey: .startedAt)
        ownerPid = try c.decode(Int.self, forKey: .ownerPid)
        children = try c.decodeIfPresent([Int].self, forKey: .children) ?? []
        parentId = try c.decodeIfPresent(String.self, forKey: .parentId)
    }
}

extension JSONDecoder {
    static func activity() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }
}

// MARK: - ActivityFile

/// Manages the acquirer process's own activity file in `~/.turbollm/state/`.
///
/// Usage pattern:
///   let url = try ActivityFile.writeActivity(label: "audio (mic-only)", parentId: parentId)
///   defer { ActivityFile.deleteActivity(at: url) }
enum ActivityFile {
    static let stateDir: URL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".turbollm")
        .appendingPathComponent("state")

    /// Writes `activity-<UUID>.json` to `~/.turbollm/state/` and returns the file URL.
    ///
    /// - Parameters:
    ///   - label: Human-readable label describing the acquirer's work (e.g. "audio (mic-only)").
    ///   - parentId: The workflow activity's UUID string, read from `$TURBO_WORKFLOW_ID` by the
    ///               caller. `nil` when the acquirer is invoked standalone (no parent workflow).
    /// - Returns: The URL of the written activity file.
    /// - Throws: If the state directory cannot be created or the file cannot be written.
    @discardableResult
    static func writeActivity(label: String, parentId: String?) throws -> URL {
        let fm = FileManager.default
        try fm.createDirectory(at: stateDir, withIntermediateDirectories: true)

        let uuid = UUID().uuidString
        let fileURL = stateDir.appendingPathComponent("activity-\(uuid).json")

        var entry: [String: Any] = [
            "id": uuid,
            "kind": "acquirer",
            "label": label,
            "started_at": ISO8601DateFormatter().string(from: Date()),
            "owner_pid": ProcessInfo.processInfo.processIdentifier,
            "children": []
        ]
        if let parentId {
            entry["parent_id"] = parentId
        }

        let data = try JSONSerialization.data(withJSONObject: entry)
        try data.write(to: fileURL)
        return fileURL
    }

    /// Deletes an activity file previously written by `writeActivity`.
    ///
    /// Ignores errors (the file may already be absent if a crash occurred).
    static func deleteActivity(at url: URL) {
        try? FileManager.default.removeItem(at: url)
    }
}
