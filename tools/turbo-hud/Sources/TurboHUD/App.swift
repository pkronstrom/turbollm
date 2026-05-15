import AppKit

@main
class App: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    let state = AppState()
    var watcher: HudStateWatcher!
    var iconController: StatusIconController!
    let registry = WorkflowRegistry()
    let settings = Settings()

    static func main() {
        let app = NSApplication.shared
        let delegate = App()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        iconController = StatusIconController(statusItem: statusItem, state: state)

        let stateDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".turbollm").appendingPathComponent("state")
        watcher = HudStateWatcher(stateDir: stateDir)
        watcher.startWatching { [weak self] activities in
            self?.state.activities = activities
            self?.refreshMenu()
        }

        refreshMenu()
    }

    func refreshMenu() {
        let (workflows, error) = registry.current()
        state.workflows = workflows
        state.workflowsError = error
        statusItem.menu = MenuBuilder.build(state: state, settings: settings,
                                            onRunWorkflow: { [weak self] wf in
                                                self?.runWorkflow(wf)
                                            },
                                            onEditParam: { [weak self] wf, p in
                                                self?.editParam(wf, p)
                                            })
    }

    func runWorkflow(_ wf: Workflow) {
        Task.detached {
            _ = try? WorkflowRunner.run(wf, settings: self.settings)
        }
    }

    func editParam(_ wf: Workflow, _ p: WorkflowParam) {
        // Plan 2 Task 10 wires the popover. For now, no-op.
    }
}
