import AppKit
import Observation

/// Re-tracks @Observable AppState. `withObservationTracking` is one-shot —
/// after the onChange fires once, observation is gone. To get continuous
/// updates we must re-call the tracker inside onChange. This is the
/// canonical pattern for Swift 5.9+ @Observable.
final class StatusIconController {
    private let statusItem: NSStatusItem
    private let state: AppState

    init(statusItem: NSStatusItem, state: AppState) {
        self.statusItem = statusItem
        self.state = state
        observe()
    }

    private func observe() {
        withObservationTracking({
            // Touch the properties we want to observe. update() reads them.
            self.update()
        }, onChange: { [weak self] in
            // onChange fires off the main queue. Re-render on main and re-observe.
            DispatchQueue.main.async {
                self?.observe()
            }
        })
    }

    func update() {
        let count = state.activities.count
        if count == 0 {
            statusItem.button?.title = "🐢"
            statusItem.button?.toolTip = "Turbo HUD — idle"
        } else if count == 1 {
            statusItem.button?.title = "🟢"
            statusItem.button?.toolTip = state.activities[0].label
        } else {
            statusItem.button?.title = "🟢\(count)"
            statusItem.button?.toolTip = state.activities.map(\.label).joined(separator: " · ")
        }
    }
}
