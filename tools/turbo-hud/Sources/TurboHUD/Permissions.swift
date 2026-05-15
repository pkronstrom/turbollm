import AVFoundation
import CoreGraphics

enum PermissionKind {
    case microphone
    case screenRecording
}

struct PermissionState {
    var microphone: AVAuthorizationStatus
    var screenRecording: Bool
}

enum Permissions {
    // Cache: avoids TCC roundtrip on every menu build (1-second TTL).
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
