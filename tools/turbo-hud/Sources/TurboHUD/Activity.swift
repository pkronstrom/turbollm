import Foundation

struct Activity: Codable, Identifiable, Equatable {
    let id: String
    let kind: String
    let label: String
    let icon: String?
    let color: String?
    let phase: String?
    let startedAt: Date
    let ownerPid: Int

    enum CodingKeys: String, CodingKey {
        case id, kind, label, icon, color, phase
        case startedAt = "started_at"
        case ownerPid = "owner_pid"
    }
}

extension JSONDecoder {
    static func activity() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }
}
