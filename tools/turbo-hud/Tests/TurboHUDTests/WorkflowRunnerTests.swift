import XCTest
@testable import TurboHUD

final class WorkflowRunnerTests: XCTestCase {
    func test_resolveParams_sticky_wins_over_default() throws {
        let wf = sampleWorkflow(params: [
            WorkflowParam(name: "vault", type: "directory", mode: nil,
                          defaultValue: "/default/path", defaultEnv: nil,
                          auto: nil, options: nil, extensions: nil,
                          scope: nil, command: nil)
        ])
        let suite = "com.turbollm.hud.test.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let settings = Settings(suiteName: suite)
        settings.setParamValue(workflow: wf.name, param: "vault", value: "/sticky/path")
        let resolved = try WorkflowRunner.resolveParams(wf, settings: settings)
        XCTAssertEqual(resolved["vault"], "/sticky/path")
    }

    func test_resolveParams_falls_through_to_default() throws {
        let wf = sampleWorkflow(params: [
            WorkflowParam(name: "x", type: "string", mode: nil,
                          defaultValue: "fallback", defaultEnv: nil,
                          auto: nil, options: nil, extensions: nil,
                          scope: nil, command: nil)
        ])
        let suite = "com.turbollm.hud.test.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let resolved = try WorkflowRunner.resolveParams(wf, settings: Settings(suiteName: suite))
        XCTAssertEqual(resolved["x"], "fallback")
    }

    func test_resolveParams_auto_template() throws {
        let wf = sampleWorkflow(params: [
            WorkflowParam(name: "title", type: "string", mode: nil,
                          defaultValue: nil, defaultEnv: nil,
                          auto: "{{date:%Y}}", options: nil, extensions: nil,
                          scope: nil, command: nil)
        ])
        let suite = "com.turbollm.hud.test.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let resolved = try WorkflowRunner.resolveParams(wf, settings: Settings(suiteName: suite))
        let f = DateFormatter(); f.dateFormat = "yyyy"
        XCTAssertEqual(resolved["title"], f.string(from: Date()))
    }

    func test_resolveParams_skips_acquired_params_without_throwing() throws {
        let wf = sampleWorkflow(params: [
            WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                          defaultValue: nil, defaultEnv: nil, auto: nil,
                          options: nil, extensions: nil, scope: nil, command: nil),
            WorkflowParam(name: "title", type: "string", mode: nil,
                          defaultValue: "my-title", defaultEnv: nil, auto: nil,
                          options: nil, extensions: nil, scope: nil, command: nil),
        ])
        let suite = "com.turbollm.hud.test.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let resolved = try WorkflowRunner.resolveParams(wf, settings: Settings(suiteName: suite))
        // Acquired param is absent from the result (SessionController fills it in).
        XCTAssertNil(resolved["audio"], "Acquired params must be absent from resolveParams result.")
        XCTAssertEqual(resolved["title"], "my-title")
    }

    func test_resolveParams_command_acquirer_type_also_skipped() throws {
        let wf = sampleWorkflow(params: [
            WorkflowParam(name: "today", type: "command", mode: "background",
                          defaultValue: nil, defaultEnv: nil, auto: nil,
                          options: nil, extensions: nil, scope: nil, command: "date +%Y")
        ])
        let suite = "com.turbollm.hud.test.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let resolved = try WorkflowRunner.resolveParams(wf, settings: Settings(suiteName: suite))
        XCTAssertNil(resolved["today"])
    }

    private func sampleWorkflow(params: [WorkflowParam]) -> Workflow {
        Workflow(name: "wf", description: nil, command: "echo {{x}}", script: nil,
                 args: nil, env: nil, params: params)
    }
}
