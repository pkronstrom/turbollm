import AppKit

enum MenuBuilder {
    static func build(state: AppState,
                      settings: Settings,
                      onRunWorkflow: @escaping (Workflow) -> Void,
                      onEditParam: @escaping (Workflow, WorkflowParam) -> Void) -> NSMenu {
        let menu = NSMenu()

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
                // Stash the workflow in `representedObject`; action handler reads it.
                item.representedObject = wf
                item.target = MenuTarget.shared
                item.action = #selector(MenuTarget.runWorkflow(_:))
                MenuTarget.shared.onRun = onRunWorkflow

                if !wf.params.isEmpty {
                    let submenu = NSMenu()
                    for p in wf.params {
                        let current = settings.paramValue(workflow: wf.name, param: p.name) ?? p.defaultValue ?? ""
                        let label = "\(p.name): \(current.isEmpty ? "(unset)" : current)"
                        let subItem = NSMenuItem(title: label, action: nil, keyEquivalent: "")
                        subItem.representedObject = WorkflowParamBinding(workflow: wf, param: p)
                        subItem.target = MenuTarget.shared
                        subItem.action = #selector(MenuTarget.editParam(_:))
                        MenuTarget.shared.onEdit = onEditParam
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
