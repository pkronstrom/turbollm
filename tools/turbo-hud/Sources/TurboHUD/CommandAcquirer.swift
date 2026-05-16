import Foundation

/// A background-mode acquirer that runs a shell pipeline and returns trimmed stdout.
/// Empty stdout throws `AcquirerError.emptyOutput`.
/// Non-zero exit code throws `AcquirerError.underlying`.
final class CommandAcquirer: Acquirer {
    let mode: AcquirerMode = .background
    let paramName: String
    private let command: String

    init(paramName: String, command: String) {
        self.paramName = paramName
        self.command = command
    }

    func acquire() async throws -> AcquirerResult {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/sh")
        proc.arguments = ["-c", command]

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = Pipe()

        do {
            try proc.run()
        } catch {
            throw AcquirerError.underlying(error)
        }

        proc.waitUntilExit()

        guard proc.terminationStatus == 0 else {
            throw AcquirerError.underlying(
                CommandAcquirerError.nonZeroExit(Int(proc.terminationStatus))
            )
        }

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let raw = String(decoding: data, as: UTF8.self)
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !trimmed.isEmpty else {
            throw AcquirerError.emptyOutput
        }

        return AcquirerResult(paramName: paramName, value: trimmed, phase: nil)
    }

    func cancel() {
        // Command acquirers run synchronously to completion; cancel is a no-op.
    }
}

private enum CommandAcquirerError: Error {
    case nonZeroExit(Int)
}
