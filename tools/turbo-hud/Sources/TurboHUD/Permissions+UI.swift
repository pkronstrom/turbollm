import AppKit

// MARK: - Test-only opener injection

extension Permissions {
    /// Override this in unit tests to intercept `openSystemSettings` calls
    /// without actually opening System Settings during `swift test`.
    static var openerOverride: ((URL) -> Void)? = nil

    static func openSystemSettings(for kind: PermissionKind) {
        let url = settingsURL(for: kind)
        if let override = openerOverride {
            override(url)
        } else {
            NSWorkspace.shared.open(url)
        }
    }
}

// MARK: - NSMenuItem helpers

extension Permissions {
    /// Returns an NSMenuItem for the "Grant <permission>…" row in a workflow
    /// submenu. The item is enabled and, when clicked, opens the correct pane
    /// in System Settings. Extracted here to keep MenuBuilder UI-logic free.
    static func grantMenuItem(for kind: PermissionKind) -> NSMenuItem {
        let title: String
        switch kind {
        case .microphone:
            title = "⚠ Grant Microphone access…"
        case .screenRecording:
            title = "⚠ Grant Screen Recording access…"
        }
        let item = NSMenuItem(
            title: title,
            action: #selector(PermissionsMenuTarget.openSettings(_:)),
            keyEquivalent: ""
        )
        let target = PermissionsMenuTarget(kind: kind)
        // NSMenuItem does not retain its target, so we embed the target in
        // representedObject to keep it alive for the lifetime of the menu.
        item.representedObject = target
        item.target = target
        item.isEnabled = true
        return item
    }
}

// MARK: - Action target

/// Thin NSObject target that dispatches the menu-item action to Permissions.
final class PermissionsMenuTarget: NSObject {
    let kind: PermissionKind

    init(kind: PermissionKind) {
        self.kind = kind
    }

    @objc func openSettings(_ sender: NSMenuItem) {
        Permissions.openSystemSettings(for: kind)
    }
}
