import XCTest
@testable import TurboHUD

final class InputFormControllerTests: XCTestCase {
    private func makeSettings() -> Settings {
        Settings(suiteName: "com.turbollm.hud.test.\(UUID().uuidString)")
    }

    private func makeParam(
        name: String,
        type: String = "string",
        mode: String? = nil,
        defaultValue: String? = nil,
        defaultEnv: String? = nil,
        auto: String? = nil
    ) -> WorkflowParam {
        WorkflowParam(
            name: name, type: type, mode: mode,
            defaultValue: defaultValue, defaultEnv: defaultEnv,
            auto: auto, options: nil, extensions: nil,
            scope: nil, command: nil
        )
    }

    private func makeWorkflow(params: [WorkflowParam]) -> Workflow {
        Workflow(name: "wf", description: nil, command: nil, script: nil,
                 args: nil, env: nil, params: params)
    }

    func test_sticky_value_present_not_unset() {
        let settings = makeSettings()
        let wf = makeWorkflow(params: [makeParam(name: "note")])
        settings.setParamValue(workflow: "wf", param: "note", value: "sticky-value")
        let unset = InputFormController.unsetParams(workflow: wf, prefilled: [:], settings: settings)
        XCTAssertTrue(unset.isEmpty)
    }

    func test_default_env_set_not_unset() {
        let settings = makeSettings()
        // HOME is always set in test environment.
        let wf = makeWorkflow(params: [makeParam(name: "vault", defaultEnv: "HOME")])
        let unset = InputFormController.unsetParams(workflow: wf, prefilled: [:], settings: settings)
        XCTAssertTrue(unset.isEmpty, "param with default_env=HOME should be resolved")
    }

    func test_literal_default_not_unset() {
        let settings = makeSettings()
        let wf = makeWorkflow(params: [makeParam(name: "format", defaultValue: "markdown")])
        let unset = InputFormController.unsetParams(workflow: wf, prefilled: [:], settings: settings)
        XCTAssertTrue(unset.isEmpty)
    }

    func test_no_resolution_source_is_unset() {
        let settings = makeSettings()
        let wf = makeWorkflow(params: [makeParam(name: "note")])
        let unset = InputFormController.unsetParams(workflow: wf, prefilled: [:], settings: settings)
        XCTAssertEqual(unset.count, 1)
        XCTAssertEqual(unset[0].name, "note")
    }

    func test_acquired_param_excluded_from_unset() {
        let settings = makeSettings()
        let wf = makeWorkflow(params: [makeParam(name: "audio", type: "audio-recording", mode: "primary")])
        let unset = InputFormController.unsetParams(workflow: wf, prefilled: [:], settings: settings)
        XCTAssertTrue(unset.isEmpty, "acquired params should never appear in unsetParams")
    }

    func test_prefilled_by_acquirer_not_unset() {
        let settings = makeSettings()
        let wf = makeWorkflow(params: [makeParam(name: "note")])
        let unset = InputFormController.unsetParams(
            workflow: wf,
            prefilled: ["note": "some value from acquirer"],
            settings: settings
        )
        XCTAssertTrue(unset.isEmpty)
    }

    func test_auto_template_resolves_not_unset() {
        let settings = makeSettings()
        // "{{env:HOME}}" should resolve since HOME is set.
        let wf = makeWorkflow(params: [makeParam(name: "title", auto: "{{env:HOME}}")])
        if ProcessInfo.processInfo.environment["HOME"] != nil {
            let unset = InputFormController.unsetParams(workflow: wf, prefilled: [:], settings: settings)
            XCTAssertTrue(unset.isEmpty, "auto template should resolve when env var is present")
        }
    }

    func test_mixed_params_only_unset_returned() {
        let settings = makeSettings()
        settings.setParamValue(workflow: "wf", param: "sticky_p", value: "v")
        let params = [
            makeParam(name: "sticky_p"),
            makeParam(name: "unset_p"),
            makeParam(name: "defaulted_p", defaultValue: "x"),
            makeParam(name: "audio", type: "audio-recording", mode: "primary"),
        ]
        let wf = makeWorkflow(params: params)
        let unset = InputFormController.unsetParams(workflow: wf, prefilled: [:], settings: settings)
        XCTAssertEqual(unset.count, 1)
        XCTAssertEqual(unset[0].name, "unset_p")
    }
}
