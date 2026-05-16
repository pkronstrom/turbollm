import Foundation

struct Activity: Identifiable, Equatable {
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
}

extension Activity: Codable {
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
