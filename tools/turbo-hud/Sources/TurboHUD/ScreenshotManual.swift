import Foundation

/// A trigger-mode acquirer that collects screenshots via `screencapture -i`.
/// Each call to `trigger()` captures one screenshot and appends its file path.
/// `acquire()` suspends until `cancel()` is called, then returns all collected
/// paths joined by newlines.
final class ScreenshotManual: Acquirer {
    let mode: AcquirerMode = .trigger
    let paramName: String
    private let outputDir: URL

    private var collectedPaths: [String] = []
    private var continuation: CheckedContinuation<AcquirerResult, Error>?
    private let lock = NSLock()

    init(paramName: String, outputDir: URL) {
        self.paramName = paramName
        self.outputDir = outputDir
    }

    /// Runs `screencapture -i` and appends the resulting file path.
    func trigger() async throws -> String {
        let timestamp = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "")
            .replacingOccurrences(of: "-", with: "")
        let filename = "screenshot-\(timestamp).png"
        let filePath = outputDir.appendingPathComponent(filename).path

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        proc.arguments = ["-i", filePath]
        try proc.run()
        proc.waitUntilExit()

        lock.lock()
        collectedPaths.append(filePath)
        lock.unlock()

        return filePath
    }

    /// Test-only overload: appends a pre-formed path without spawning a subprocess.
    func trigger(filePath: String) {
        lock.lock()
        collectedPaths.append(filePath)
        lock.unlock()
    }

    func acquire() async throws -> AcquirerResult {
        return try await withCheckedThrowingContinuation { cont in
            lock.lock()
            continuation = cont
            lock.unlock()
        }
    }

    func cancel() {
        lock.lock()
        let paths = collectedPaths
        let cont = continuation
        continuation = nil
        lock.unlock()

        let value = paths.joined(separator: "\n")
        cont?.resume(returning: AcquirerResult(paramName: paramName, value: value, phase: nil))
    }
}
