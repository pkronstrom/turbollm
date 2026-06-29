import XCTest
@testable import TurboHUD

/// The region picker draws in AppKit view coordinates (origin bottom-left,
/// y-up), but the acquirer's SCStream `sourceRect` is display-local with origin
/// top-left, y-down. `displayLocalRect` performs that flip. A wrong flip put
/// the captured region in the wrong place.
final class RegionPickerTests: XCTestCase {
    func test_displayLocalRect_flips_y_to_top_left_origin() {
        // Drag near the bottom-left of a 1000pt-tall display.
        let drag = NSRect(x: 100, y: 200, width: 300, height: 150)
        let result = RegionPickerView.displayLocalRect(drag: drag, viewHeight: 1000)

        // Top-left y = viewHeight - drag.maxY = 1000 - 350 = 650.
        XCTAssertEqual(result, NSRect(x: 100, y: 650, width: 300, height: 150))
    }

    func test_displayLocalRect_full_height_drag_maps_to_zero_origin() {
        // A drag spanning the full height should land at y=0 (top of display).
        let drag = NSRect(x: 0, y: 0, width: 500, height: 1000)
        let result = RegionPickerView.displayLocalRect(drag: drag, viewHeight: 1000)
        XCTAssertEqual(result, NSRect(x: 0, y: 0, width: 500, height: 1000))
    }
}
