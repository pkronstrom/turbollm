import AppKit
import ApplicationServices
import Foundation

// MARK: - RegionSelector

/// Resolves screen-space bounds for `--scope active-window`.
///
/// The frontmost application and its focused window are queried once at recording
/// start via the Accessibility API. The result is a `CGRect` in screen coordinates
/// (origin at top-left of the primary display, y increases downward — matching
/// `SCStream`'s content filter coordinate space).
enum RegionSelector {

    // MARK: Test seam

    /// Override in tests to inject a fake frontmost-window query.
    /// When nil, the real NSWorkspace + AX query is used.
    static var queryFunction: (() -> CGRect?)? = nil

    // MARK: Public API

    /// Returns the screen-space rect of the frontmost application's focused window.
    ///
    /// - Returns: A `CGRect` in screen coordinates, or `nil` if the frontmost application
    ///   has no accessible focused window or the AX query fails.
    static func resolveActiveWindowRect() -> CGRect? {
        if let override = queryFunction {
            return override()
        }
        return queryActiveWindowRect()
    }

    // MARK: - Private implementation

    private static func queryActiveWindowRect() -> CGRect? {
        guard let app = NSWorkspace.shared.frontmostApplication else { return nil }

        let axApp = AXUIElementCreateApplication(app.processIdentifier)

        var windowValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            axApp,
            kAXFocusedWindowAttribute as CFString,
            &windowValue
        ) == .success, let windowValue else { return nil }

        let axWindow = windowValue as! AXUIElement // swiftlint:disable:this force_cast

        var positionValue: CFTypeRef?
        var sizeValue: CFTypeRef?

        guard AXUIElementCopyAttributeValue(
            axWindow,
            kAXPositionAttribute as CFString,
            &positionValue
        ) == .success,
        AXUIElementCopyAttributeValue(
            axWindow,
            kAXSizeAttribute as CFString,
            &sizeValue
        ) == .success,
        let positionValue,
        let sizeValue else { return nil }

        var position = CGPoint.zero
        var size = CGSize.zero

        guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &position),
              AXValueGetValue(sizeValue as! AXValue, .cgSize, &size) else { return nil }

        return CGRect(origin: position, size: size)
    }
}
