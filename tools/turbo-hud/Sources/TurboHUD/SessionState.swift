import Foundation

struct SessionState {
    let id: UUID
    let workflow: Workflow
    let startedAt: Date
    let phase: String
}
