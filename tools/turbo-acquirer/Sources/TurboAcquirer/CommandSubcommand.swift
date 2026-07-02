import Foundation

/// Result type for CommandSubcommand.run(), used as a test seam.
enum CommandResult {
    case success(stdout: String)
    case failure(stderr: String, exitCode: Int32)
}

/// Implements the `command` subcommand.
///
/// Spawns `/bin/sh -c <cmd>`, captures its stdout (trimmed of trailing whitespace),
/// and emits it on the acquirer's stdout. Empty stdout causes non-zero exit with
/// `emptyOutput` stderr. Non-zero subprocess exit causes non-zero exit with the
/// subprocess's stderr forwarded.
enum CommandSubcommand {

    /// Runs `/bin/sh -c <shellCommand>` and returns a `CommandResult`.
    ///
    /// This is the test seam — tests call this directly rather than going through dispatch.
    static func run(shellCommand: String) -> CommandResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", shellCommand]

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        do {
            try process.run()
        } catch {
            return .failure(stderr: "Failed to launch /bin/sh: \(error)", exitCode: 1)
        }

        // Drain both pipes concurrently BEFORE waitUntilExit(). A pipe's OS
        // buffer is ~64KB; if the child writes more than that to stdout or
        // stderr before we ever read it, the child blocks on write(2) while
        // we block on waitUntilExit() — a permanent deadlock. Reading on
        // background queues lets the child keep writing while we still wait
        // for it to exit.
        let drainGroup = DispatchGroup()
        var stdoutData = Data()
        var stderrData = Data()
        drainGroup.enter()
        DispatchQueue.global(qos: .utility).async {
            stdoutData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
            drainGroup.leave()
        }
        drainGroup.enter()
        DispatchQueue.global(qos: .utility).async {
            stderrData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
            drainGroup.leave()
        }
        drainGroup.wait()

        process.waitUntilExit()

        let stdoutString = String(data: stdoutData, encoding: .utf8) ?? ""
        let stderrString = String(data: stderrData, encoding: .utf8) ?? ""

        let trimmedStdout = stdoutString.trimmingCharacters(in: .whitespacesAndNewlines)
        let exitCode = process.terminationStatus

        // Non-zero subprocess exit: propagate stderr.
        if exitCode != 0 {
            let errorMessage = stderrString.trimmingCharacters(in: .whitespacesAndNewlines)
            return .failure(
                stderr: errorMessage.isEmpty ? "Subprocess exited with code \(exitCode)" : errorMessage,
                exitCode: exitCode
            )
        }

        // Empty stdout: emptyOutput error.
        if trimmedStdout.isEmpty {
            return .failure(stderr: "emptyOutput", exitCode: 1)
        }

        return .success(stdout: trimmedStdout)
    }

    /// Parses argv for the `command` subcommand: expects `--shell <cmd>`.
    /// Returns the shell command string, or nil if not found.
    static func parseShellArg(argv: [String]) -> String? {
        guard let idx = argv.firstIndex(of: "--shell"), idx + 1 < argv.count else {
            return nil
        }
        return argv[idx + 1]
    }
}
