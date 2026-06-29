import XCTest
@testable import TurboHUD

/// Regression: the screen-recording region picker (`scope = region`) only opens
/// if `MenuTarget.shared.onOpenRegionPicker` is wired. MenuBuilder declares and
/// invokes that callback, but it was never assigned during app launch, so
/// selecting "region" was a silent no-op. `App.installRegionPicker()` performs
/// the wiring; this asserts it actually sets the callback.
final class AppTests: XCTestCase {
    override func tearDown() {
        MenuTarget.shared.onOpenRegionPicker = nil
        super.tearDown()
    }

    func test_installRegionPicker_wires_onOpenRegionPicker_callback() {
        MenuTarget.shared.onOpenRegionPicker = nil
        XCTAssertNil(MenuTarget.shared.onOpenRegionPicker)

        App.installRegionPicker()

        XCTAssertNotNil(
            MenuTarget.shared.onOpenRegionPicker,
            "App.installRegionPicker() must wire the region-picker callback so "
            + "selecting scope=region opens the picker instead of doing nothing."
        )
    }
}
