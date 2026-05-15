import Foundation

final class WorkflowRegistry {
    private var cache: [Workflow] = []
    private var cacheExpiresAt: Date = .distantPast
    private let cacheTTL: TimeInterval = 0.5

    func current() -> ([Workflow], String?) {
        if Date() < cacheExpiresAt { return (cache, nil) }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["turbo", "workflows", "list", "--json"]
        let pipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = errPipe
        do {
            try proc.run()
        } catch {
            return ([], "turbo CLI not found in PATH")
        }
        proc.waitUntilExit()
        if proc.terminationStatus != 0 {
            let err = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            return ([], "turbo workflows list --json failed: \(err.prefix(120))")
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        do {
            let workflows = try JSONDecoder.workflow().decode([Workflow].self, from: data)
            cache = workflows
            cacheExpiresAt = Date().addingTimeInterval(cacheTTL)
            return (workflows, nil)
        } catch {
            return ([], "Failed to decode workflows JSON: \(error)")
        }
    }
}
