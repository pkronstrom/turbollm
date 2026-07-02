import Foundation
import ScreenCaptureKit

/// Minimal `SCStreamDelegate` that makes mid-recording stream death visible
/// instead of silent.
///
/// Both `record-screen` and `record-audio --scope system+mic` previously
/// passed `delegate: nil` to `SCStream(...)`. If the stream dies on its own
/// (e.g. the captured display disconnects, or ScreenCaptureKit's daemon
/// crashes/restarts) there was nothing to notice: the poll loop only watches
/// `isStopRequested()`, so a recording would just hang producing no more
/// frames/samples until an external SIGTERM arrived. This delegate logs the
/// failure to stderr and calls `requestStop()` so the normal teardown path
/// (flush + emit manifest + exit) runs instead.
final class AcquirerStreamDelegate: NSObject, SCStreamDelegate {
    private let label: String

    init(label: String) {
        self.label = label
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("\(label): SCStream stopped unexpectedly: \(error)\n", stderr)
        requestStop()
    }
}
