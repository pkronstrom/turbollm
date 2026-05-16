import AppKit
import UserNotifications

@main
class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
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

        // Refresh menu state when the user picks a scope/device from a submenu.
        MenuTarget.shared.onSettingsChanged = { [weak self] in
            self?.refreshMenu()
        }

        let stateDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".turbollm").appendingPathComponent("state")
        watcher = HudStateWatcher(stateDir: stateDir)
        watcher.startWatching { [weak self] activities, acquirerActivity in
            self?.state.activities = activities
            self?.state.currentAcquirerActivity = acquirerActivity
            self?.refreshMenu()
        }

        // T-21/B-G: URLSchemeHandler now shells out to turbo workflows run directly.
        URLSchemeHandler.install()

        // T-24: Post a one-time migration notification when turbo-acquirer
        // hasn't been granted TCC permissions yet (microphone = notDetermined).
        postMigrationNoticeIfNeeded()

        refreshMenu()
    }

    // MARK: - T-24: TCC migration notification

    /// UserDefaults flag key — set after the migration notification is posted once.
    static let migrationNoticeFlagKey = "migration_notice_shown_v1"

    /// Posts a UNUserNotificationCenter notification the first time the HUD
    /// launches after the two-binary architecture is deployed, iff
    /// `turbo-acquirer permissions-state` reports `microphone: "notDetermined"`.
    /// Sets the `migration_notice_shown_v1` flag so subsequent launches skip this.
    func postMigrationNoticeIfNeeded() {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: App.migrationNoticeFlagKey) else { return }

        // Check permissions-state asynchronously to avoid blocking launch.
        DispatchQueue.global(qos: .utility).async {
            let state = MenuBuilder.permissionsState()
            guard state.microphone == .notDetermined else {
                // Already granted (or denied) — no migration notice needed.
                UserDefaults.standard.set(true, forKey: App.migrationNoticeFlagKey)
                return
            }
            // Request notification authorization if not yet granted, then post.
            let center = UNUserNotificationCenter.current()
            center.requestAuthorization(options: [.alert]) { granted, _ in
                guard granted else {
                    UserDefaults.standard.set(true, forKey: App.migrationNoticeFlagKey)
                    return
                }
                let content = UNMutableNotificationContent()
                content.title = "Turbo: Recording permissions update"
                content.body = "Turbo's recording backend moved to a new binary. You'll be asked to grant Microphone (and Screen Recording, if you use system+mic scope) on first recording."
                let request = UNNotificationRequest(
                    identifier: "com.turbollm.migration-notice-v1",
                    content: content,
                    trigger: nil   // deliver immediately
                )
                center.add(request) { _ in }
                UserDefaults.standard.set(true, forKey: App.migrationNoticeFlagKey)
            }
        }
    }

    func refreshMenu() {
        registry.current { [weak self] workflows, error in
            guard let self else { return }
            self.state.workflows = workflows
            self.state.workflowsError = error
            self.rebuildMenuFromState()
        }
    }

    /// Synchronous rebuild from the workflows already cached in state. Used by
    /// menuNeedsUpdate so the user sees current scope / permission / sticky
    /// values without waiting on a registry round-trip.
    private func rebuildMenuFromState() {
        let menu = MenuBuilder.build(state: self.state, settings: self.settings,
                                      onRunWorkflow: { [weak self] wf in
                                          self?.runWorkflow(wf)
                                      },
                                      onEditParam: { [weak self] wf, p in
                                          self?.editParam(wf, p)
                                      })
        // Re-set delegate every rebuild — MenuBuilder returns a new NSMenu.
        menu.delegate = self
        self.statusItem.menu = menu
    }

    // MARK: - NSMenuDelegate

    /// Called by macOS before the menu (or any submenu) is displayed. We use
    /// it on the top-level menu only to rebuild from cached state so changes
    /// made while the menu was closed (scope/device selection, granted
    /// permissions) show up immediately.
    func menuNeedsUpdate(_ menu: NSMenu) {
        guard menu === statusItem.menu else { return }
        rebuildMenuFromState()
    }

    /// T-21: Shell out to `turbo workflows run <name>` unconditionally.
    /// All param resolution (configured, acquired) is now performed by the CLI.
    func runWorkflow(_ wf: Workflow) {
        var args = ["turbo", "workflows", "run", wf.name]
        // Pass any pre-resolved sticky params so the CLI can skip prompts.
        for p in wf.params where p.type != "audio-recording" && p.type != "screenshot-manual" && p.type != "command" {
            if let sticky = settings.paramValue(workflow: wf.name, param: p.name), !sticky.isEmpty {
                args.append("--param")
                args.append("\(p.name)=\(sticky)")
            }
        }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = args
        try? proc.run()
    }

    func editParam(_ wf: Workflow, _ p: WorkflowParam) {
        guard let anchor = statusItem.button else { return }
        ParamEditor.open(workflow: wf, param: p, settings: settings, anchor: anchor) { [weak self] value in
            guard let self else { return }
            self.settings.setParamValue(workflow: wf.name, param: p.name, value: value)
            self.refreshMenu()
        }
    }
}
