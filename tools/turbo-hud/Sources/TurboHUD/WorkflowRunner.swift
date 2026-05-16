import Foundation

enum WorkflowRunner {
    /// Resolve configured param values: sticky → auto → default_env → default → "".
    /// Acquired params (audio-recording, screenshot-manual, command) are skipped —
    /// they are resolved by `turbo-acquirer` and the Python CLI at run time.
    /// Used by ParamEditor to display current sticky values in the edit popover.
    static func resolveParams(_ workflow: Workflow, settings: Settings) throws -> [String: String] {
        var resolved: [String: String] = [:]
        for p in workflow.params {
            // Acquired params are handled by turbo-acquirer at runtime; skip here.
            if ["audio-recording", "screenshot-manual", "command"].contains(p.type) { continue }
            if let sticky = settings.paramValue(workflow: workflow.name, param: p.name), !sticky.isEmpty {
                resolved[p.name] = sticky
                continue
            }
            if let auto = p.auto {
                resolved[p.name] = (try? Templates.expand(auto, params: resolved)) ?? ""
                continue
            }
            if let envName = p.defaultEnv, let envVal = ProcessInfo.processInfo.environment[envName], !envVal.isEmpty {
                resolved[p.name] = envVal
                continue
            }
            resolved[p.name] = p.defaultValue ?? ""
        }
        return resolved
    }

    /// Spawn `turbo workflows run <name> --param k=v...` as a foreground subprocess.
    /// Returns the exit code.
    @discardableResult
    static func run(_ workflow: Workflow, settings: Settings) throws -> Int32 {
        let resolved = try resolveParams(workflow, settings: settings)
        var args = ["turbo", "workflows", "run", workflow.name]
        for (k, v) in resolved {
            args.append("--param")
            args.append("\(k)=\(v)")
        }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = args
        try proc.run()
        proc.waitUntilExit()
        return proc.terminationStatus
    }
}
