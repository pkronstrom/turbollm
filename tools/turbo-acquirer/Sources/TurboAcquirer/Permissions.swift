import AVFoundation
import CoreGraphics

enum PermissionKind {
    case microphone
    case screenRecording
}

struct PermissionState {
    var microphone: AVAuthorizationStatus
    var screenRecording: Bool

    /// Returns the microphone status as one of the four JSON-ready strings
    /// defined in the acquirer-cli spec: authorized, denied, notDetermined, restricted.
    var microphoneString: String {
        switch microphone {
        case .authorized:      return "authorized"
        case .denied:          return "denied"
        case .notDetermined:   return "notDetermined"
        case .restricted:      return "restricted"
        @unknown default:      return "notDetermined"
        }
    }
}

enum Permissions {
    // Cache: avoids TCC roundtrip on every invocation (1-second TTL).
    private static var cachedState: PermissionState?
    private static var lastPollDate: Date?

    static func state() -> PermissionState {
        let now = Date()
        if let cached = cachedState, let lastPoll = lastPollDate,
           now.timeIntervalSince(lastPoll) < 1.0 {
            return cached
        }
        let fresh = PermissionState(
            microphone: AVCaptureDevice.authorizationStatus(for: .audio),
            screenRecording: CGPreflightScreenCaptureAccess()
        )
        cachedState = fresh
        lastPollDate = now
        return fresh
    }

    static func settingsURL(for kind: PermissionKind) -> URL {
        switch kind {
        case .microphone:
            return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!
        case .screenRecording:
            return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!
        }
    }
}
