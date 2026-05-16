import AppKit
import Foundation
import Observation

@Observable
final class AppState {
    var activities: [Activity] = []
    var workflows: [Workflow] = []
    var workflowsError: String? = nil
    var currentAcquirerActivity: Activity? = nil
    /// Legacy: kept for SessionController compile compatibility; deleted with SessionController in B-G.
    var activeSession: SessionState? = nil
}
