import Foundation

struct Workflow: Codable, Identifiable, Equatable {
    let name: String
    let description: String?
    let command: String?
    let script: String?
    let args: [String]?
    let env: [String: String]?
    let params: [WorkflowParam]

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, description, command, script, args, env, params
    }

    // Custom decoder so `params` defaults to [] when the JSON omits the key —
    // Plan 1's `turbo workflows list --json` may emit workflows without a
    // `params` entry when the TOML stanza has no `[[workflows.X.params]]`.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        description = try c.decodeIfPresent(String.self, forKey: .description)
        command = try c.decodeIfPresent(String.self, forKey: .command)
        script = try c.decodeIfPresent(String.self, forKey: .script)
        args = try c.decodeIfPresent([String].self, forKey: .args)
        env = try c.decodeIfPresent([String: String].self, forKey: .env)
        params = try c.decodeIfPresent([WorkflowParam].self, forKey: .params) ?? []
    }

    // Explicit memberwise init for tests and direct construction.
    init(name: String, description: String?, command: String?, script: String?,
         args: [String]?, env: [String: String]?, params: [WorkflowParam]) {
        self.name = name
        self.description = description
        self.command = command
        self.script = script
        self.args = args
        self.env = env
        self.params = params
    }
}

struct WorkflowParam: Codable, Equatable {
    let name: String
    let type: String
    let mode: String?
    let defaultValue: String?
    let defaultEnv: String?
    let auto: String?
    let options: [String]?
    let extensions: [String]?
    let scope: String?     // for audio-recording: "system+mic" | "mic-only" | "app+mic"
    let command: String?   // for command acquirer: shell pipeline string

    enum CodingKeys: String, CodingKey {
        case name, type, mode, options, extensions, auto, scope, command
        case defaultValue = "default"
        case defaultEnv = "default_env"
    }
}

extension JSONDecoder {
    static func workflow() -> JSONDecoder { JSONDecoder() }
}
