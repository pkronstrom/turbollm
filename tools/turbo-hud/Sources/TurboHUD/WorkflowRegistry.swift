import Foundation

/// Cache and callbacks are owned by the main queue.
final class WorkflowRegistry {
    private var cache: [Workflow] = []
    private var cacheExpiresAt: Date = .distantPast
    private let cacheTTL: TimeInterval = 0.5
    private var callbacks: [([Workflow], String?) -> Void] = []
    private var loading = false
    private let executable: String
    private let arguments: [String]
    private let timeout: TimeInterval
    static let subprocessTimeout: TimeInterval = 30

    init(executable: String = "/usr/bin/env",
         arguments: [String] = ["turbo", "workflows", "list", "--json"],
         timeout: TimeInterval = subprocessTimeout) {
        self.executable = executable
        self.arguments = arguments
        self.timeout = timeout
    }

    func current(completion: @escaping ([Workflow], String?) -> Void) {
        if Date() < cacheExpiresAt {
            completion(cache, nil)
            return
        }
        callbacks.append(completion)
        guard !loading else { return }
        loading = true
        DispatchQueue.global(qos: .userInitiated).async {
            let result = self.fetchFromCLI()
            DispatchQueue.main.async {
                if result.1 == nil {
                    self.cache = result.0
                    self.cacheExpiresAt = Date().addingTimeInterval(self.cacheTTL)
                }
                self.loading = false
                let waiting = self.callbacks
                self.callbacks.removeAll()
                for callback in waiting { callback(result.0, result.1) }
            }
        }
    }

    private func fetchFromCLI() -> ([Workflow], String?) {
        let result: BoundedSubprocess.Result
        do {
            result = try BoundedSubprocess.run(executable: executable, arguments: arguments, timeout: timeout)
        } catch {
            return ([], "turbo CLI not found in PATH")
        }
        if result.timedOut {
            return ([], "turbo workflows list --json timed out after \(Int(timeout))s")
        }
        if result.status != 0 {
            let error = String(data: result.stderr, encoding: .utf8) ?? ""
            return ([], "turbo workflows list --json failed: \(error.prefix(120))")
        }
        do {
            return (try JSONDecoder.workflow().decode([Workflow].self, from: result.stdout), nil)
        } catch {
            return ([], "Failed to decode workflows JSON: \(error)")
        }
    }
}
