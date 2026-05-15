import Foundation

final class WorkflowRegistry {
    private var cache: [Workflow] = []
    private var cacheExpiresAt: Date = .distantPast
    private let cacheTTL: TimeInterval = 0.5

    /// Returns cached result immediately if fresh; otherwise fetches on a background
    /// queue and delivers the result via `completion` on the main queue.
    func current(completion: @escaping ([Workflow], String?) -> Void) {
        if Date() < cacheExpiresAt {
            completion(cache, nil)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let result = self.fetchFromCLI()
            DispatchQueue.main.async {
                if case let (workflows, nil) = result {
                    self.cache = workflows
                    self.cacheExpiresAt = Date().addingTimeInterval(self.cacheTTL)
                }
                completion(result.0, result.1)
            }
        }
    }

    static let subprocessTimeout: TimeInterval = 30

    private func fetchFromCLI() -> ([Workflow], String?) {
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
        let deadline = DispatchTime.now() + Self.subprocessTimeout
        let done = DispatchSemaphore(value: 0)
        proc.terminationHandler = { _ in done.signal() }
        if done.wait(timeout: deadline) == .timedOut {
            proc.terminate()
            _ = done.wait(timeout: .now() + 2)
            return ([], "turbo workflows list --json timed out after \(Int(Self.subprocessTimeout))s")
        }
        if proc.terminationStatus != 0 {
            let err = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            return ([], "turbo workflows list --json failed: \(err.prefix(120))")
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        do {
            let workflows = try JSONDecoder.workflow().decode([Workflow].self, from: data)
            return (workflows, nil)
        } catch {
            return ([], "Failed to decode workflows JSON: \(error)")
        }
    }
}
