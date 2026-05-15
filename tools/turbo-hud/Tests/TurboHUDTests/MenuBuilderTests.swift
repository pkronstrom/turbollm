import XCTest
@testable import TurboHUD

final class MenuBuilderTests: XCTestCase {
    func test_renders_active_activities_section_then_workflows() {
        let state = AppState()
        state.activities = [
            Activity(id: "a1", kind: "workflow", label: "Running x",
                     icon: nil, color: nil, phase: nil,
                     startedAt: Date(), ownerPid: 1)
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
                                      options: nil, extensions: nil)
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
                                      options: nil, extensions: nil)
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

    func test_run_item_disabled_when_workflow_has_acquired_params() {
        let state = AppState()
        state.workflows = [
            Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil),
                        WorkflowParam(name: "title", type: "string", mode: nil,
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil),
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let runItem = menu.items.first(where: { $0.title == "rec" })?.submenu?.items.first
        XCTAssertNotNil(runItem)
        XCTAssertTrue(runItem!.title.contains("Plan 3"),
                      "Run item must explain why it's disabled when acquired params are present.")
        XCTAssertFalse(runItem!.isEnabled,
                       "Run item must be disabled when any param needs a Plan 3 acquirer — clicking would just fail with acquiredParamMissing.")
    }

    func test_acquired_param_types_render_disabled_with_plan3_hint() {
        let state = AppState()
        state.workflows = [
            Workflow(name: "rec", description: nil, command: nil, script: "s", args: nil, env: nil,
                     params: [
                        WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                                      defaultValue: nil, defaultEnv: nil, auto: nil,
                                      options: nil, extensions: nil)
                     ])
        ]
        let menu = MenuBuilder.build(state: state, settings: Settings(suiteName: "t.\(UUID().uuidString)"),
                                     onRunWorkflow: { _ in }, onEditParam: { _, _ in })
        let wfItem = menu.items.first(where: { $0.title == "rec" })
        let audioItem = wfItem?.submenu?.items.first(where: { $0.title.hasPrefix("audio:") })
        XCTAssertNotNil(audioItem)
        XCTAssertFalse(audioItem!.isEnabled, "Acquired param types must render disabled until Plan 3.")
        XCTAssertTrue(audioItem!.title.contains("Plan 3"), "Acquired param label must explain why it's not editable.")
    }
}
