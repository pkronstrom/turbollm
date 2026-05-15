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
}
