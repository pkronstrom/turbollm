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
    // No in-process cache: every CLI invocation is a fresh, short-lived
    // process (milliseconds), so a TTL cache here can never be hit across
    // calls — it only ever serves its own single read. See
    // `PermissionsStateCommand` for the actual cross-invocation cache
    // (file-based, since state doesn't survive process exit).
    static func state() -> PermissionState {
        PermissionState(
            microphone: AVCaptureDevice.authorizationStatus(for: .audio),
            screenRecording: CGPreflightScreenCaptureAccess()
        )
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
