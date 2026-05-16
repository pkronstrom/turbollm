import AppKit
import AVFoundation
import CoreGraphics
import Foundation

// MARK: - Permissions types (absorbed from deleted Permissions.swift + Permissions+UI.swift)

enum PermissionKind {
    case microphone
    case screenRecording
}

struct PermissionState {
    var microphone: AVAuthorizationStatus
    var screenRecording: Bool
}

/// Thin helpers for TCC state queries and grant menu items.
/// Permissions.swift and Permissions+UI.swift were deleted in T-22;
/// the types they provided are inlined here (T-27).
enum Permissions {
    private static var cachedState: PermissionState?
    private static var lastPollDate: Date?

    static var openerOverride: ((URL) -> Void)? = nil

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

    static func openSystemSettings(for kind: PermissionKind) {
        let url = settingsURL(for: kind)
        if let override = openerOverride {
            override(url)
        } else {
            NSWorkspace.shared.open(url)
        }
    }

    static func grantMenuItem(for kind: PermissionKind) -> NSMenuItem {
        let title: String
        switch kind {
        case .microphone:        title = "⚠ Grant Microphone access…"
        case .screenRecording:   title = "⚠ Grant Screen Recording access…"
        }
        let item = NSMenuItem(
            title: title,
            action: #selector(PermissionsMenuTarget.openSettings(_:)),
            keyEquivalent: ""
        )
        let target = PermissionsMenuTarget(kind: kind)
        item.representedObject = target
        item.target = target
        item.isEnabled = true
        return item
    }
}

/// Action target for grant-permission menu items.
final class PermissionsMenuTarget: NSObject {
    let kind: PermissionKind

    init(kind: PermissionKind) {
        self.kind = kind
    }

    @objc func openSettings(_ sender: NSMenuItem) {
        switch kind {
        case .microphone:
            AVCaptureDevice.requestAccess(for: .audio) { _ in }
        case .screenRecording:
            _ = CGRequestScreenCaptureAccess()
        }
        Permissions.openSystemSettings(for: kind)
    }
}

// MARK: - MenuBuilder

enum MenuBuilder {
    private static let acquiredTypes: Set<String> = ["audio-recording", "screenshot-manual", "command"]

    // MARK: - T-20: permissions via subprocess

    /// Test seam: override to avoid spawning turbo-acquirer during unit tests.
    static var permissionsStateOverride: PermissionState? = nil

    /// Reads TCC state by running `turbo-acquirer permissions-state`.
    /// Falls back to direct TCC query if the binary is not found.
    /// Results are cached inside `turbo-acquirer` itself (1-second file TTL).
    static func permissionsState() -> PermissionState {
        if let override = permissionsStateOverride {
            return override
        }
        return runPermissionsStateSubprocess() ?? Permissions.state()
    }

    private static func runPermissionsStateSubprocess() -> PermissionState? {
        // Locate turbo-acquirer: prefer ~/.local/bin (sidecar symlink), then PATH.
        let candidates = [
            FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".local/bin/turbo-acquirer").path,
            "/usr/local/bin/turbo-acquirer",
        ]
        guard let binaryPath = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            return nil
        }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: binaryPath)
        proc.arguments = ["permissions-state"]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = Pipe()
        do {
            try proc.run()
            proc.waitUntilExit()
        } catch {
            return nil
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return parsePermissionsJSON(data)
    }

    private static func parsePermissionsJSON(_ data: Data) -> PermissionState? {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let micString = json["microphone"] as? String,
              let screenRecording = json["screenRecording"] as? Bool else {
            return nil
        }
        let mic: AVAuthorizationStatus
        switch micString {
        case "authorized":   mic = .authorized
        case "denied":       mic = .denied
        case "restricted":   mic = .restricted
        default:             mic = .notDetermined
        }
        return PermissionState(microphone: mic, screenRecording: screenRecording)
    }

    // MARK: - Menu build

    static func build(state: AppState,
                      settings: Settings,
                      onRunWorkflow: @escaping (Workflow) -> Void,
                      onEditParam: @escaping (Workflow, WorkflowParam) -> Void) -> NSMenu {
        let menu = NSMenu()
        // Our actions target a custom NSObject (MenuTarget) that's not in the
        // responder chain, so AppKit's auto-validation grays out items it can't
        // resolve. Disable auto-enable here and on every submenu we attach.
        menu.autoenablesItems = false

        // Stop row — appears when an acquirer is active.
        if let acquirer = state.currentAcquirerActivity {
            let stopItem = NSMenuItem(
                title: "⏹ Stop \(acquirer.label)",
                action: #selector(MenuTarget.stopAcquirer(_:)),
                keyEquivalent: ""
            )
            stopItem.representedObject = NSNumber(value: acquirer.ownerPid)
            stopItem.target = MenuTarget.shared
            stopItem.isEnabled = true
            menu.addItem(stopItem)
            menu.addItem(NSMenuItem.separator())
        }

        // Active activities section
        if !state.activities.isEmpty {
            let header = NSMenuItem(title: "Active (\(state.activities.count))", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
            for a in state.activities {
                let item = NSMenuItem(title: a.label, action: nil, keyEquivalent: "")
                item.isEnabled = false
                menu.addItem(item)
            }
            menu.addItem(NSMenuItem.separator())
        }

        // Workflows section
        MenuTarget.shared.onRun = onRunWorkflow
        MenuTarget.shared.onEdit = onEditParam
        if let err = state.workflowsError {
            let item = NSMenuItem(title: err, action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        } else if state.workflows.isEmpty {
            let item = NSMenuItem(title: "No workflows configured", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        } else {
            let permState = permissionsState()
            for wf in state.workflows {
                let item = NSMenuItem(title: wf.name, action: nil, keyEquivalent: "")
                item.toolTip = wf.description
                item.representedObject = wf
                item.target = MenuTarget.shared
                item.action = #selector(MenuTarget.runWorkflow(_:))

                if !wf.params.isEmpty {
                    // AppKit ignores `action` on items that also have a submenu — clicking
                    // such an item only opens the submenu. So the top-level click can't
                    // run the workflow; we prepend a "▶ Run" item inside the submenu.
                    let submenu = NSMenu()
                    submenu.autoenablesItems = false

                    let hasAudioParam = wf.params.contains { $0.type == "audio-recording" }
                    let needsMic = hasAudioParam &&
                        permState.microphone != .authorized && permState.microphone != .notDetermined
                    let hasSystemMicScope = hasAudioParam && wf.params.contains {
                        $0.type == "audio-recording" && $0.scope == "system+mic"
                    }
                    let needsScreenRecording = hasSystemMicScope && !permState.screenRecording &&
                        permState.microphone == .authorized

                    // Permission grant rows (above Run when permission denied).
                    if needsMic {
                        submenu.addItem(Permissions.grantMenuItem(for: .microphone))
                    }
                    if needsScreenRecording {
                        submenu.addItem(Permissions.grantMenuItem(for: .screenRecording))
                    }

                    // Run row
                    let runItem = NSMenuItem(
                        title: "▶ Run \(wf.name)",
                        action: #selector(MenuTarget.runWorkflow(_:)),
                        keyEquivalent: ""
                    )
                    runItem.representedObject = wf
                    runItem.target = MenuTarget.shared
                    if needsMic {
                        runItem.action = nil
                        runItem.isEnabled = false
                        runItem.toolTip = "needs Microphone access"
                    } else if needsScreenRecording {
                        // T-fix-5: system+mic scope requires Screen Recording permission.
                        runItem.action = nil
                        runItem.isEnabled = false
                        runItem.toolTip = "needs Screen Recording"
                    }
                    submenu.addItem(runItem)
                    submenu.addItem(NSMenuItem.separator())

                    // Param rows
                    for p in wf.params {
                        if p.type == "audio-recording" {
                            // Scope submenu
                            submenu.addItem(makeScopeSubmenuItem(param: p, workflow: wf, settings: settings))
                            // Input device submenu
                            submenu.addItem(makeDeviceSubmenuItem(param: p, workflow: wf, settings: settings))
                            submenu.addItem(NSMenuItem.separator())
                        } else if p.type == "screen-recording" {
                            // T-17: Scope submenu for screen-recording params.
                            submenu.addItem(makeScreenScopeSubmenuItem(param: p, workflow: wf, settings: settings))
                            submenu.addItem(NSMenuItem.separator())
                        } else if acquiredTypes.contains(p.type) {
                            // Other acquired types: read-only row
                            let label = "\(p.name): (recording)"
                            let subItem = NSMenuItem(title: label, action: nil, keyEquivalent: "")
                            subItem.isEnabled = false
                            submenu.addItem(subItem)
                        } else {
                            // Configured param: editable row
                            let current = settings.paramValue(workflow: wf.name, param: p.name) ?? p.defaultValue ?? ""
                            let label = "\(p.name): \(current.isEmpty ? "(unset)" : current)"
                            let subItem = NSMenuItem(title: label, action: nil, keyEquivalent: "")
                            subItem.representedObject = WorkflowParamBinding(workflow: wf, param: p)
                            subItem.target = MenuTarget.shared
                            subItem.action = #selector(MenuTarget.editParam(_:))
                            submenu.addItem(subItem)
                        }
                    }
                    item.submenu = submenu
                }
                menu.addItem(item)
            }
        }

        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        return menu
    }

    // MARK: - Scope submenu

    private static func makeScopeSubmenuItem(param: WorkflowParam, workflow: Workflow, settings: Settings) -> NSMenuItem {
        // Read using the same raw-key format that selectScope writes and that
        // SessionController.buildAcquirers reads. (Earlier code routed display
        // through Settings.paramValue, which prepends "workflow.…param.…" and
        // does not match the writer — clicks looked silently ignored.)
        let scopeKey = "\(workflow.name).\(param.name).scope"
        let currentScope = UserDefaults(suiteName: Settings.defaultSuiteName)?
            .string(forKey: scopeKey) ?? param.scope ?? "mic-only"
        let item = NSMenuItem(title: "scope: \(currentScope) ▸", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false
        for scope in ["system+mic", "mic-only"] {
            let si = NSMenuItem(title: scope, action: #selector(MenuTarget.selectScope(_:)), keyEquivalent: "")
            si.representedObject = ScopeBinding(workflow: workflow, param: param, scope: scope)
            si.target = MenuTarget.shared
            si.state = currentScope == scope ? .on : .off
            si.isEnabled = true
            sub.addItem(si)
        }
        // app+mic is rendered but disabled (v2)
        let appMicItem = NSMenuItem(title: "app+mic (v2 — disabled)", action: nil, keyEquivalent: "")
        appMicItem.isEnabled = false
        sub.addItem(appMicItem)
        item.submenu = sub
        return item
    }

    // MARK: - Input device submenu

    private static func makeDeviceSubmenuItem(param: WorkflowParam, workflow: Workflow, settings: Settings) -> NSMenuItem {
        let deviceKey = "\(workflow.name).\(param.name).input_device"
        let storedUID = UserDefaults(suiteName: Settings.defaultSuiteName)?.string(forKey: deviceKey)

        let devices = AudioSourcePicker.inputDevices()
        let defaultID = AudioSourcePicker.defaultInputDeviceID()
        let defaultUID = devices.first(where: { $0.id == defaultID })?.uid

        let currentUID = storedUID ?? defaultUID
        let currentName = devices.first(where: { $0.uid == currentUID })?.name
            ?? (storedUID.map { "Unknown (\($0))" } ?? "Default")

        let item = NSMenuItem(title: "input device: \(currentName) ▸", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false
        for device in devices {
            let si = NSMenuItem(
                title: device.name,
                action: #selector(MenuTarget.selectInputDevice(_:)),
                keyEquivalent: ""
            )
            si.representedObject = DeviceBinding(workflow: workflow, param: param, device: device)
            si.target = MenuTarget.shared
            si.state = device.uid == currentUID ? .on : .off
            si.isEnabled = true
            sub.addItem(si)
        }
        item.submenu = sub
        return item
    }

    // MARK: - Screen scope submenu (T-17, T-19)

    private static let screenScopeValues = ["full-display", "region", "active-window"]

    private static func makeScreenScopeSubmenuItem(
        param: WorkflowParam, workflow: Workflow, settings: Settings
    ) -> NSMenuItem {
        let scopeKey = "\(workflow.name).\(param.name).scope"
        let currentScope = settings.rawString(forKey: scopeKey) ?? "full-display"
        let item = NSMenuItem(title: "scope: \(currentScope) ▸", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        sub.autoenablesItems = false

        for scope in screenScopeValues {
            let si = NSMenuItem(
                title: scope,
                action: #selector(MenuTarget.selectScreenScope(_:)),
                keyEquivalent: ""
            )
            si.representedObject = ScreenScopeBinding(workflow: workflow, param: param, scope: scope)
            si.target = MenuTarget.shared
            si.state = currentScope == scope ? .on : .off
            si.isEnabled = true
            sub.addItem(si)
        }

        // T-19: "Re-pick region…" visible when scope=region and sticky exists.
        if currentScope == "region" {
            let regionKey = "\(workflow.name).\(param.name).region"
            if settings.rawString(forKey: regionKey) != nil {
                sub.addItem(NSMenuItem.separator())
                let repickItem = NSMenuItem(
                    title: "Re-pick region…",
                    action: #selector(MenuTarget.repickRegion(_:)),
                    keyEquivalent: ""
                )
                repickItem.representedObject = ScreenScopeBinding(
                    workflow: workflow, param: param, scope: "region"
                )
                repickItem.target = MenuTarget.shared
                repickItem.isEnabled = true
                sub.addItem(repickItem)
            }
        }

        item.submenu = sub
        return item
    }
}

// MARK: - Binding types

struct WorkflowParamBinding {
    let workflow: Workflow
    let param: WorkflowParam
}

struct ScopeBinding {
    let workflow: Workflow
    let param: WorkflowParam
    let scope: String
}

struct ScreenScopeBinding {
    let workflow: Workflow
    let param: WorkflowParam
    let scope: String
}

struct DeviceBinding {
    let workflow: Workflow
    let param: WorkflowParam
    let device: AudioDeviceInfo
}

// MARK: - MenuTarget

final class MenuTarget: NSObject {
    static let shared = MenuTarget()
    var onRun: ((Workflow) -> Void)?
    var onEdit: ((Workflow, WorkflowParam) -> Void)?
    /// Invoked when a sticky setting changes via the menu (scope, input device).
    /// App.swift wires this to refreshMenu so the title and checkmarks reflect
    /// the new state next time the menu opens.
    var onSettingsChanged: (() -> Void)?

    @objc func runWorkflow(_ sender: NSMenuItem) {
        guard let wf = sender.representedObject as? Workflow else { return }
        onRun?(wf)
    }

    @objc func editParam(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? WorkflowParamBinding else { return }
        onEdit?(b.workflow, b.param)
    }

    /// T-19 test seam: allows tests to intercept the kill call.
    var killFunction: (pid_t, Int32) -> Int32 = { pid, sig in kill(pid, sig) }

    @objc func stopAcquirer(_ sender: NSMenuItem) {
        guard let pidNumber = sender.representedObject as? NSNumber else { return }
        _ = killFunction(pid_t(pidNumber.intValue), SIGTERM)
    }

    @objc func selectScope(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? ScopeBinding else { return }
        UserDefaults(suiteName: Settings.defaultSuiteName)?
            .set(b.scope, forKey: "\(b.workflow.name).\(b.param.name).scope")
        onSettingsChanged?()
    }

    @objc func selectInputDevice(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? DeviceBinding else { return }
        UserDefaults(suiteName: Settings.defaultSuiteName)?
            .set(b.device.uid, forKey: "\(b.workflow.name).\(b.param.name).input_device")
        onSettingsChanged?()
    }

    /// T-17: Callback wired by App.swift to open the region picker when scope=region is selected
    /// and no region sticky exists yet.
    var onOpenRegionPicker: ((Workflow, WorkflowParam) -> Void)?

    /// T-17: Invoked when the user selects a screen-recording scope option.
    @objc func selectScreenScope(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? ScreenScopeBinding else { return }
        let ud = UserDefaults(suiteName: Settings.defaultSuiteName)
        ud?.set(b.scope, forKey: "\(b.workflow.name).\(b.param.name).scope")
        if b.scope == "region" {
            let regionKey = "\(b.workflow.name).\(b.param.name).region"
            if ud?.string(forKey: regionKey) == nil {
                onOpenRegionPicker?(b.workflow, b.param)
            }
        }
        onSettingsChanged?()
    }

    /// T-19: Invoked when the user selects "Re-pick region…".
    @objc func repickRegion(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? ScreenScopeBinding else { return }
        let ud = UserDefaults(suiteName: Settings.defaultSuiteName)
        ud?.removeObject(forKey: "\(b.workflow.name).\(b.param.name).region")
        onOpenRegionPicker?(b.workflow, b.param)
        onSettingsChanged?()
    }

}
