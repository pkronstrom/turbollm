import XCTest
@testable import TurboAcquirer

final class PermissionsTests: XCTestCase {

    func test_state_returns_PermissionState_without_crashing() {
        // The actual values depend on the runner's TCC grants, but the call
        // must not crash or hang.
        let state = Permissions.state()
        _ = state.microphone
        _ = state.screenRecording
    }

    func test_state_microphone_is_valid_enum_value() {
        let state = Permissions.state()
        let validValues: [String] = ["authorized", "denied", "notDetermined", "restricted"]
        XCTAssert(validValues.contains(state.microphoneString),
                  "Unexpected microphone status: \(state.microphoneString)")
    }

    func test_state_screenRecording_is_bool() {
        let state = Permissions.state()
        // Bool is always valid; just exercising the code path.
        _ = state.screenRecording as Bool
    }

    func test_state_is_cached_for_one_second() {
        // Two rapid calls within 1 s should return consistent values
        // (exercises the cached code path without crashing).
        let first = Permissions.state()
        let second = Permissions.state()
        XCTAssertEqual(first.microphone, second.microphone)
        XCTAssertEqual(first.screenRecording, second.screenRecording)
    }
}
