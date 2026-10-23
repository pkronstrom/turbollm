import Foundation

/// Owned by the main queue; only the loader runs in the background.
final class PermissionStateCache {
    private(set) var state = PermissionState(microphone: .notDetermined, screenRecording: false)
    private var expiresAt = Date.distantPast
    private var callbacks: [(PermissionState) -> Void] = []
    private var loading = false
    private let loader: () -> PermissionState

    init(loader: @escaping () -> PermissionState) { self.loader = loader }

    func refresh(completion: @escaping (PermissionState) -> Void) {
        if Date() < expiresAt {
            completion(state)
            return
        }
        callbacks.append(completion)
        guard !loading else { return }
        loading = true
        DispatchQueue.global(qos: .utility).async {
            let fresh = self.loader()
            DispatchQueue.main.async {
                self.state = fresh
                self.expiresAt = Date().addingTimeInterval(1)
                self.loading = false
                let waiting = self.callbacks
                self.callbacks.removeAll()
                for callback in waiting { callback(fresh) }
            }
        }
    }
}
