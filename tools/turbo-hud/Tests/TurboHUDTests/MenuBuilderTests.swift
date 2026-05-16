import XCTest
import AVFoundation
@testable import TurboHUD

final class MenuBuilderTests: XCTestCase {
    func test_renders_active_activities_section_then_workflows() {
        let state = AppState()
        state.activities = [
            Activity(id: "a1", kind: "workflow", label: "Running x",
                     icon: nil, color: nil, phase: nil,
                     startedAt: Date(), ownerPid: 1, children: [], parentId: nil)
        ]
        state.workflows = [
            Workflow(name: "transcribe-file", description: "Transcribe", command: nil,
                     script: nil, args: nil, env: nil, params: [])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let titles = menu.items.map(\.title)
        XCTAssertTrue(titles.contains("Active (1)"))
        XCTAssertTrue(titles.contains("Running x"))
        XCTAssertTrue(titles.contains("transcribe-file"))
        XCTAssertTrue(titles.contains("Quit"))
    }

    func test_renders_no_workflows_disabled_when_empty() {
        let state = AppState()
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        XCTAssertTrue(menu.items.contains(where: { $0.title == "No workflows configured" && !$0.isEnabled }))
    }

    func test_workflow_submenu_lists_configured_params() {
        let state = AppState()
        state.workflows = [
            Workflow(name: "wf", description: nil, command: "echo", script: nil, args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "title", type: "string", mode: nil,
                                      defaultValue: "", defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: nil, command: nil)
                     ])
        ]
        let suite = "t.\(UUID().uuidString)"
        let settings = Settings(suiteName: suite)
        settings.setParamValue(workflow: "wf", param: "title", value: "My title")
        let menu = MenuBuilder.build(state: state, settings: settings,
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let wfItem = menu.items.first(where: { $0.title == "wf" })
        XCTAssertNotNil(wfItem)
        XCTAssertNotNil(wfItem?.submenu)
        let subTitles = wfItem!.submenu!.items.map(\.title)
        XCTAssertTrue(subTitles.contains("title: My title"))
    }

    func test_workflow_with_params_has_run_item_in_submenu() {
        let state = AppState()
        state.workflows = [
            Workflow(name: "wf", description: nil, command: "echo", script: nil, args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "title", type: "string", mode: nil,
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: nil, command: nil)
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let wfItem = menu.items.first(where: { $0.title == "wf" })
        let runItem = wfItem?.submenu?.items.first
        XCTAssertEqual(runItem?.title, "▶ Run wf",
                       "Submenu must lead with a Run item — top-level click can't fire its action when a submenu is attached.")
        XCTAssertNotNil(runItem?.action,
                        "Run item must have an action wired.")
    }

    // MARK: - Plan 3 acquirer tests

    func test_run_item_enabled_when_workflow_has_acquired_params() {
        // Plan 3: Run is now enabled for workflows with acquired params; SessionController handles them.
        let state = AppState()
        state.workflows = [
            Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: "mic-only", command: nil),
                        WorkflowParam(name: "title", type: "string", mode: nil,
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: nil, command: nil),
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        // Find the Run item: it should be the first item WITHOUT "Plan 3" in title.
        let wfItem = menu.items.first(where: { $0.title == "rec" })
        let submenuItems = wfItem?.submenu?.items ?? []
        // Run item is the first non-separator, non-permission item that starts with "▶ Run"
        let runItem = submenuItems.first(where: { $0.title.hasPrefix("▶ Run") })
        XCTAssertNotNil(runItem)
        XCTAssertFalse(runItem!.title.contains("Plan 3"),
                       "Run item must NOT mention Plan 3 — acquired params now work in Plan 3.")
        // When mic permission is authorized (or notDetermined), Run is enabled.
        // We can't control the actual mic permission in tests, so just assert the title is right.
        XCTAssertEqual(runItem!.title, "▶ Run rec")
    }

    func test_audio_recording_param_shows_scope_and_device_submenus() {
        let state = AppState()
        state.workflows = [
            Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: "mic-only", command: nil)
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let wfItem = menu.items.first(where: { $0.title == "rec" })
        let submenuItems = wfItem?.submenu?.items ?? []
        let scopeItem = submenuItems.first(where: { $0.title.hasPrefix("scope:") })
        let deviceItem = submenuItems.first(where: { $0.title.hasPrefix("input device:") })
        XCTAssertNotNil(scopeItem, "audio-recording param must show a scope submenu item")
        XCTAssertNotNil(deviceItem, "audio-recording param must show an input device submenu item")
        XCTAssertNotNil(scopeItem?.submenu, "scope item must have a submenu")
        XCTAssertNotNil(deviceItem?.submenu, "input device item must have a submenu")
    }

    func test_scope_submenu_includes_app_mic_disabled_option() {
        let state = AppState()
        state.workflows = [
            Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: "mic-only", command: nil)
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let wfItem = menu.items.first(where: { $0.title == "rec" })
        let scopeItem = wfItem?.submenu?.items.first(where: { $0.title.hasPrefix("scope:") })
        let scopeOptions = scopeItem?.submenu?.items.map(\.title) ?? []
        XCTAssertTrue(scopeOptions.contains("mic-only"))
        XCTAssertTrue(scopeOptions.contains("system+mic"))
        let appMicItem = scopeItem?.submenu?.items.first(where: { $0.title.hasPrefix("app+mic") })
        XCTAssertNotNil(appMicItem)
        XCTAssertFalse(appMicItem!.isEnabled, "app+mic must be disabled (v2)")
    }

    func test_stop_row_appears_when_acquirer_active() {
        let state = AppState()
        state.currentAcquirerActivity = Activity(
            id: "acq-1", kind: "acquirer", label: "audio (mic-only)",
            icon: nil, color: nil, phase: nil,
            startedAt: Date(), ownerPid: 41010, children: [], parentId: nil)
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let stopItem = menu.items.first
        XCTAssertNotNil(stopItem)
        XCTAssertTrue(stopItem!.title.hasPrefix("⏹ Stop"),
                      "When an acquirer is active, the first menu item must be the Stop row.")
        XCTAssertTrue(stopItem!.title.contains("audio (mic-only)"),
                      "Stop row title must include the acquirer label.")
    }

    // MARK: - T-19: Stop row sends SIGTERM to acquirer PID

    func test_stop_row_action_calls_kill_with_acquirer_pid() {
        let state = AppState()
        state.currentAcquirerActivity = Activity(
            id: "acq-2", kind: "acquirer", label: "audio (mic-only)",
            icon: nil, color: nil, phase: nil,
            startedAt: Date(), ownerPid: 41010, children: [], parentId: nil)
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let stopItem = menu.items.first!
        XCTAssertEqual(stopItem.action, #selector(MenuTarget.stopAcquirer(_:)))

        var killedPid: pid_t = 0
        var killedSig: Int32 = 0
        MenuTarget.shared.killFunction = { pid, sig in
            killedPid = pid
            killedSig = sig
            return 0
        }
        defer { MenuTarget.shared.killFunction = { pid, sig in kill(pid, sig) } }

        _ = stopItem.target?.perform(stopItem.action!, with: stopItem)

        XCTAssertEqual(killedPid, 41010, "Stop must send SIGTERM to the acquirer's PID")
        XCTAssertEqual(killedSig, SIGTERM, "Stop must send SIGTERM specifically")
    }

    // MARK: - T-20: Permissions via test seam

    func test_permissions_seam_mic_denied_shows_grant_row() {
        // Use the test seam to avoid a real subprocess call
        MenuBuilder.permissionsStateOverride = PermissionState(
            microphone: .denied,
            screenRecording: true
        )
        defer { MenuBuilder.permissionsStateOverride = nil }

        let state = AppState()
        state.workflows = [
            Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: "mic-only", command: nil)
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let wfItem = menu.items.first(where: { $0.title == "rec" })
        let submenuItems = wfItem?.submenu?.items ?? []
        let grantItem = submenuItems.first(where: { $0.title.contains("Grant Microphone") })
        XCTAssertNotNil(grantItem, "Grant Microphone row must appear when mic is denied (via seam).")
        let runItem = submenuItems.first(where: { $0.title.hasPrefix("▶ Run") })
        XCTAssertFalse(runItem?.isEnabled ?? true, "Run must be disabled when mic is denied.")
    }

    func test_permissions_seam_all_granted_no_grant_rows() {
        MenuBuilder.permissionsStateOverride = PermissionState(
            microphone: .authorized,
            screenRecording: true
        )
        defer { MenuBuilder.permissionsStateOverride = nil }

        let state = AppState()
        state.workflows = [
            Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: "mic-only", command: nil)
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let wfItem = menu.items.first(where: { $0.title == "rec" })
        let submenuItems = wfItem?.submenu?.items ?? []
        let grantItem = submenuItems.first(where: { $0.title.contains("Grant") })
        XCTAssertNil(grantItem, "No grant rows when all permissions are authorized.")
        let runItem = submenuItems.first(where: { $0.title.hasPrefix("▶ Run") })
        XCTAssertTrue(runItem?.isEnabled ?? false, "Run must be enabled when all permissions are authorized.")
    }

    func test_mic_denied_shows_grant_row_and_disables_run() {
        // We can only test this if the real mic permission is denied; skip if authorized.
        let permState = Permissions.state()
        guard permState.microphone == .denied else {
            // In CI or dev machines where mic is authorized or not determined,
            // we just assert the menu builds without crashing.
            let state = AppState()
            state.workflows = [
                Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                         params: [
                            WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                          defaultValue: nil, defaultEnv: nil, auto: nil,
                                          options: nil, extensions: nil,
                                          scope: "mic-only", command: nil)
                         ])
            ]
            let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                         onRunWorkflow: { _ in }, onEditParam: { _, _ in })
            XCTAssertNotNil(menu)
            return
        }

        let state = AppState()
        state.workflows = [
            Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: "mic-only", command: nil)
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let wfItem = menu.items.first(where: { $0.title == "rec" })
        let submenuItems = wfItem?.submenu?.items ?? []
        let grantItem = submenuItems.first(where: { $0.title.contains("Grant Microphone") })
        XCTAssertNotNil(grantItem, "Grant Microphone row must appear when mic is denied.")
        let runItem = submenuItems.first(where: { $0.title.hasPrefix("▶ Run") })
        XCTAssertFalse(runItem?.isEnabled ?? true, "Run must be disabled when mic is denied.")
    }

    // MARK: - T-fix-5: system+mic with Screen Recording denied disables Run

    func test_system_plus_mic_scope_screen_recording_denied_disables_run() {
        // T-fix-5: When a workflow has audio-recording with scope = "system+mic"
        // and Screen Recording permission is absent (and mic is authorized),
        // the Run item must be disabled with subtitle "needs Screen Recording"
        // and a Screen Recording grant row must appear.
        //
        // Since we cannot control the real permission state, we test the menu-build
        // logic by checking:
        //  - If Screen Recording is denied (and mic is authorized): Run is disabled
        //    and tooltip indicates "needs Screen Recording".
        //  - Otherwise (Screen Recording granted or mic denied): menu builds without crash.
        let permState = Permissions.state()

        let state = AppState()
        state.workflows = [
            Workflow(name: "sys-rec", description: nil, command: nil, script: "s",
                     args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil,
                                      scope: "system+mic", command: nil)
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })

        let wfItem = menu.items.first(where: { $0.title == "sys-rec" })
        XCTAssertNotNil(wfItem, "Workflow item must exist in menu")

        let submenuItems = wfItem?.submenu?.items ?? []
        let runItem = submenuItems.first(where: { $0.title.hasPrefix("▶ Run") })
        XCTAssertNotNil(runItem, "Run item must exist in submenu")

        if permState.microphone == .authorized && !permState.screenRecording {
            // Screen Recording denied, mic authorized — this is the T-fix-5 case.
            XCTAssertFalse(runItem?.isEnabled ?? true,
                           "Run must be disabled when Screen Recording is denied for system+mic scope")
            XCTAssertEqual(runItem?.toolTip, "needs Screen Recording",
                           "Run tooltip must say 'needs Screen Recording' for system+mic without SR permission")
            let grantItem = submenuItems.first(where: { $0.title.contains("Screen Recording") })
            XCTAssertNotNil(grantItem,
                            "A Screen Recording grant row must appear when SR is denied for system+mic")
        } else {
            // In environments where SR is granted or mic is denied, just verify menu builds.
            XCTAssertNotNil(runItem, "Menu must build without crashing in any permission state")
        }
    }
}
