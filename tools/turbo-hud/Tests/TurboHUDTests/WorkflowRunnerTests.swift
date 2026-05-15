import XCTest
@testable import TurboHUD

final class WorkflowRunnerTests: XCTestCase {
    func test_resolveParams_sticky_wins_over_default() throws {
        let wf = sampleWorkflow(params: [
            WorkflowParam(name: "vault", type: "directory", mode: nil,
                          defaultValue: "/default/path", defaultEnv: nil,
                          auto: nil, options: nil, extensions: nil)
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
                          auto: nil, options: nil, extensions: nil)
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
                          auto: "{{date:%Y}}", options: nil, extensions: nil)
        ])
        let suite = "com.turbollm.hud.test.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let resolved = try WorkflowRunner.resolveParams(wf, settings: Settings(suiteName: suite))
        let f = DateFormatter(); f.dateFormat = "yyyy"
        XCTAssertEqual(resolved["title"], f.string(from: Date()))
    }

    private func sampleWorkflow(params: [WorkflowParam]) -> Workflow {
        Workflow(name: "wf", description: nil, command: "echo {{x}}", script: nil,
                 args: nil, env: nil, params: params)
    }
}
