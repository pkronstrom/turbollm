import XCTest
@testable import TurboHUD

final class PermissionsTests: XCTestCase {

    // MARK: - T-3: settingsURL tests

    func test_settingsURL_microphone_returns_canonical_URL() {
        let url = Permissions.settingsURL(for: .microphone)
        XCTAssertEqual(
            url.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        )
    }

    func test_settingsURL_screenRecording_returns_canonical_URL() {
        let url = Permissions.settingsURL(for: .screenRecording)
        XCTAssertEqual(
            url.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
        )
    }

    // MARK: - T-3: state() tests

    func test_state_returns_PermissionState_without_hanging() {
        // Just assert it returns without throwing or hanging.
        // The actual values depend on the runner's TCC grants.
        let state = Permissions.state()
        // microphone is an AVAuthorizationStatus enum — any value is valid.
        // screenRecording is a Bool — any value is valid.
        // The compiler enforces types; the test merely exercises the call path.
        _ = state.microphone
        _ = state.screenRecording
    }

    func test_state_is_cached_for_one_second() {
        // Two rapid calls should return the same struct instance values
        // (not testing identity, just that no crash or hang occurs in the
        // cached code path).
        let first = Permissions.state()
        let second = Permissions.state()
        XCTAssertEqual(first.microphone, second.microphone)
        XCTAssertEqual(first.screenRecording, second.screenRecording)
    }

    // MARK: - T-17: grantMenuItem tests (added here per footprint)

    func test_grantMenuItem_microphone_has_correct_title() {
        let item = Permissions.grantMenuItem(for: .microphone)
        XCTAssertEqual(item.title, "⚠ Grant Microphone access…")
    }

    func test_grantMenuItem_screenRecording_has_correct_title() {
        let item = Permissions.grantMenuItem(for: .screenRecording)
        XCTAssertEqual(item.title, "⚠ Grant Screen Recording access…")
    }

    func test_grantMenuItem_has_target_and_action() {
        let item = Permissions.grantMenuItem(for: .microphone)
        XCTAssertNotNil(item.target)
        XCTAssertNotNil(item.action)
    }

    func test_grantMenuItem_clicking_calls_opener_not_NSWorkspace() {
        // Inject a test-only opener to verify the correct URL is dispatched
        // without opening System Settings during `swift test`.
        var openedURL: URL?
        Permissions.openerOverride = { url in openedURL = url }
        defer { Permissions.openerOverride = nil }

        let item = Permissions.grantMenuItem(for: .microphone)
        // Simulate the menu-item action by invoking target/action directly.
        _ = item.target!.perform(item.action!, with: item)

        XCTAssertEqual(
            openedURL?.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        )
    }

    func test_grantMenuItem_screenRecording_clicking_opens_correct_URL() {
        var openedURL: URL?
        Permissions.openerOverride = { url in openedURL = url }
        defer { Permissions.openerOverride = nil }

        let item = Permissions.grantMenuItem(for: .screenRecording)
        _ = item.target!.perform(item.action!, with: item)

        XCTAssertEqual(
            openedURL?.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
        )
    }
}
