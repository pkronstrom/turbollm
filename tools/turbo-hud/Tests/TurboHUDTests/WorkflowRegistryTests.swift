import XCTest
@testable import TurboHUD

final class WorkflowRegistryTests: XCTestCase {
    func test_decode_workflow_list_json() throws {
        let json = #"""
        [
          {
            "name": "transcribe-file",
            "description": "Transcribe an audio file",
            "command": "turbo transcribe \"{{file}}\"",
            "params": [
              {"name": "file", "type": "file", "extensions": ["m4a", "wav"]}
            ]
          },
          {
            "name": "summarize-existing",
            "description": "Summarize",
            "script": "summarize-to-obsidian",
            "args": ["{{audio}}", "{{title}}"],
            "params": [
              {"name": "title", "type": "string", "auto": "{{date:%Y-%m-%d}}"},
              {"name": "vault", "type": "directory", "default_env": "OBSIDIAN_VAULT"}
            ]
          }
        ]
        """#
        let data = json.data(using: .utf8)!
        let workflows = try JSONDecoder.workflow().decode([Workflow].self, from: data)
        XCTAssertEqual(workflows.count, 2)
        XCTAssertEqual(workflows[0].name, "transcribe-file")
        XCTAssertEqual(workflows[0].params.count, 1)
        XCTAssertEqual(workflows[0].params[0].type, "file")
        XCTAssertEqual(workflows[0].params[0].extensions ?? [], ["m4a", "wav"])
        XCTAssertEqual(workflows[1].script, "summarize-to-obsidian")
        XCTAssertEqual(workflows[1].args ?? [], ["{{audio}}", "{{title}}"])
        XCTAssertEqual(workflows[1].params[1].defaultEnv, "OBSIDIAN_VAULT")
    }

    func test_decode_workflow_without_params_key_defaults_to_empty() throws {
        let json = #"""
        [{"name": "no-params", "description": "Bare", "command": "echo"}]
        """#
        let data = json.data(using: .utf8)!
        let workflows = try JSONDecoder.workflow().decode([Workflow].self, from: data)
        XCTAssertEqual(workflows.count, 1)
        XCTAssertEqual(workflows[0].params.count, 0)
    }
}
