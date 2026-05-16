import AppKit
import Foundation
import Observation

@Observable
final class AppState {
    var activities: [Activity] = []
    var workflows: [Workflow] = []
    var workflowsError: String? = nil
    var activeSession: SessionState? = nil
}
