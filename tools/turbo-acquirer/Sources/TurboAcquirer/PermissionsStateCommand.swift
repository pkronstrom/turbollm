import Foundation
import AVFoundation

/// Implements the `permissions-state` subcommand.
///
/// Emits a JSON object to stdout describing TCC state:
///   {"microphone": "authorized" | "denied" | "notDetermined" | "restricted", "screenRecording": true | false}
///
/// Results are cached in `$TMPDIR/turbo-permissions-cache.json` with a 1-second TTL.
/// Because each invocation is a separate process, the cache is file-based.
enum PermissionsStateCommand {
    static let cacheURL: URL = {
        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
        return tmpDir.appendingPathComponent("turbo-permissions-cache.json")
    }()

    static let cacheTTLSeconds: TimeInterval = 1.0

    /// Runs the permissions-state subcommand: emits JSON to stdout, exits 0.
    static func run() -> String {
        let state = cachedOrFreshState()
        return jsonString(for: state)
    }

    // MARK: - Cache helpers

    static func cachedOrFreshState() -> PermissionState {
        // Check the file cache first.
        if let cached = readCache(), isRecent(cached.timestamp) {
            return cached.state
        }
        let fresh = Permissions.state()
        writeCache(state: fresh)
        return fresh
    }

    private struct CacheEntry {
        let state: PermissionState
        let timestamp: Date
    }

    private static func readCache() -> CacheEntry? {
        guard let data = try? Data(contentsOf: cacheURL),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let ts = json["timestamp"] as? TimeInterval,
              let microphone = json["microphone"] as? String,
              let screenRecording = json["screenRecording"] as? Bool else {
            return nil
        }
        let micStatus = avAuthorizationStatus(from: microphone)
        let state = PermissionState(microphone: micStatus, screenRecording: screenRecording)
        return CacheEntry(state: state, timestamp: Date(timeIntervalSince1970: ts))
    }

    private static func writeCache(state: PermissionState) {
        let entry: [String: Any] = [
            "timestamp": Date().timeIntervalSince1970,
            "microphone": state.microphoneString,
            "screenRecording": state.screenRecording
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: entry) else { return }
        try? data.write(to: cacheURL)
    }

    private static func isRecent(_ date: Date) -> Bool {
        Date().timeIntervalSince(date) < cacheTTLSeconds
    }

    // MARK: - JSON serialization

    static func jsonString(for state: PermissionState) -> String {
        let obj: [String: Any] = [
            "microphone": state.microphoneString,
            "screenRecording": state.screenRecording
        ]
        guard let data = try? JSONSerialization.data(
            withJSONObject: obj,
            options: [.sortedKeys]
        ),
        let str = String(data: data, encoding: .utf8) else {
            return #"{"microphone":"notDetermined","screenRecording":false}"#
        }
        return str
    }

    // MARK: - Reverse mapping (for cache read-back)

    private static func avAuthorizationStatus(from string: String) -> AVAuthorizationStatus {
        switch string {
        case "authorized":    return .authorized
        case "denied":        return .denied
        case "restricted":    return .restricted
        default:              return .notDetermined
        }
    }
}
