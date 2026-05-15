import XCTest
@testable import TurboHUD

final class SettingsTests: XCTestCase {
    func test_round_trip() {
        let suite = "com.turbollm.hud.test.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let s = Settings(suiteName: suite)
        XCTAssertNil(s.paramValue(workflow: "wf", param: "p"))
        s.setParamValue(workflow: "wf", param: "p", value: "hello")
        XCTAssertEqual(s.paramValue(workflow: "wf", param: "p"), "hello")
        s.setParamValue(workflow: "wf", param: "p", value: nil)
        XCTAssertNil(s.paramValue(workflow: "wf", param: "p"))
    }

    func test_default_suite_name_is_stable() {
        // If this string ever changes, every user loses their sticky params.
        // Update this assertion plus a migration plan together, never alone.
        XCTAssertEqual(Settings.defaultSuiteName, "com.turbollm.hud")
    }
}
