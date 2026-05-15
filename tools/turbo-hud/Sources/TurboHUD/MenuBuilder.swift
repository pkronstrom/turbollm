import AppKit
import AVFoundation

enum MenuBuilder {
    private static let acquiredTypes: Set<String> = ["audio-recording", "screenshot-manual", "command"]

    static func build(state: AppState,
                      settings: Settings,
                      onRunWorkflow: @escaping (Workflow) -> Void,
                      onEditParam: @escaping (Workflow, WorkflowParam) -> Void) -> NSMenu {
        let menu = NSMenu()
        // Our actions target a custom NSObject (MenuTarget) that's not in the
        // responder chain, so AppKit's auto-validation grays out items it can't
        // resolve. Disable auto-enable here and on every submenu we attach.
        menu.autoenablesItems = false

        // Stop row — appears when a session is active.
        if let session = state.activeSession {
            let stopItem = NSMenuItem(
                title: "⏹ Stop recording \(session.workflow.name)",
                action: #selector(MenuTarget.stopSession(_:)),
                keyEquivalent: ""
            )
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
            let permState = Permissions.state()
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
        let currentScope = settings.paramValue(workflow: workflow.name, param: "\(param.name).scope")
            ?? param.scope ?? "mic-only"
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

    @objc func runWorkflow(_ sender: NSMenuItem) {
        guard let wf = sender.representedObject as? Workflow else { return }
        onRun?(wf)
    }

    @objc func editParam(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? WorkflowParamBinding else { return }
        onEdit?(b.workflow, b.param)
    }

    @objc func stopSession(_ sender: NSMenuItem) {
        Task { @MainActor in
            SessionController.shared.cancel()
        }
    }

    @objc func selectScope(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? ScopeBinding else { return }
        UserDefaults(suiteName: Settings.defaultSuiteName)?
            .set(b.scope, forKey: "\(b.workflow.name).\(b.param.name).scope")
    }

    @objc func selectInputDevice(_ sender: NSMenuItem) {
        guard let b = sender.representedObject as? DeviceBinding else { return }
        UserDefaults(suiteName: Settings.defaultSuiteName)?
            .set(b.device.uid, forKey: "\(b.workflow.name).\(b.param.name).input_device")
    }
}
