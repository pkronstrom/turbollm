import AppKit

enum MenuBuilder {
    static func build(state: AppState,
                      settings: Settings,
                      onRunWorkflow: @escaping (Workflow) -> Void,
                      onEditParam: @escaping (Workflow, WorkflowParam) -> Void) -> NSMenu {
        let menu = NSMenu()
        // Our actions target a custom NSObject (MenuTarget) that's not in the
        // responder chain, so AppKit's auto-validation grays out items it can't
        // resolve. Disable auto-enable here and on every submenu we attach.
        menu.autoenablesItems = false

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
                    let hasAcquiredParam = wf.params.contains { ["audio-recording", "screenshot-manual", "command"].contains($0.type) }
                    let runTitle = hasAcquiredParam
                        ? "▶ Run \(wf.name) (needs Plan 3 acquirers)"
                        : "▶ Run \(wf.name)"
                    let runItem = NSMenuItem(title: runTitle, action: #selector(MenuTarget.runWorkflow(_:)), keyEquivalent: "")
                    runItem.representedObject = wf
                    runItem.target = MenuTarget.shared
                    if hasAcquiredParam {
                        runItem.action = nil
                        runItem.isEnabled = false
                    }
                    submenu.addItem(runItem)
                    submenu.addItem(NSMenuItem.separator())

                    for p in wf.params {
                        let current = settings.paramValue(workflow: wf.name, param: p.name) ?? p.defaultValue ?? ""
                        let isAcquired = ["audio-recording", "screenshot-manual", "command"].contains(p.type)
                        let label: String
                        if isAcquired {
                            label = "\(p.name): (acquired \(p.type) — Plan 3)"
                        } else {
                            label = "\(p.name): \(current.isEmpty ? "(unset)" : current)"
                        }
                        let subItem = NSMenuItem(title: label, action: nil, keyEquivalent: "")
                        if isAcquired {
                            subItem.isEnabled = false
                        } else {
                            subItem.representedObject = WorkflowParamBinding(workflow: wf, param: p)
                            subItem.target = MenuTarget.shared
                            subItem.action = #selector(MenuTarget.editParam(_:))
                        }
                        submenu.addItem(subItem)
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
}

struct WorkflowParamBinding {
    let workflow: Workflow
    let param: WorkflowParam
}

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
}
