import Foundation

// Entry point — call App.main() from top-level so @main is not needed
// (Swift requires @main to be in a non-main.swift file for executable targets).
App.main()

struct App {
    static func main() {
        let argv = CommandLine.arguments
        let output = dispatch(argv: argv)
        print(output)
    }

    /// Test seam: parses argv and routes to the appropriate handler.
    /// Returns the handler's output string. Production entry point calls
    /// this and prints the result; tests call this directly.
    static func dispatch(argv: [String]) -> String {
        guard argv.count >= 2 else {
            return "Usage: turbo-acquirer <subcommand> [options]\nSubcommands: record-audio, screenshot, command, permissions-state"
        }
        let subcommand = argv[1]
        switch subcommand {
        case "record-audio":
            return handleRecordAudio(argv: Array(argv.dropFirst(2)))
        case "screenshot":
            return handleScreenshot(argv: Array(argv.dropFirst(2)))
        case "command":
            return handleCommand(argv: Array(argv.dropFirst(2)))
        case "permissions-state":
            return handlePermissionsState(argv: Array(argv.dropFirst(2)))
        case "record-screen":
            return handleRecordScreen(argv: Array(argv.dropFirst(2)))
        default:
            return "unknown subcommand: \(subcommand)\nUsage: turbo-acquirer <subcommand> [options]\nSubcommands: record-audio, screenshot, command, permissions-state, record-screen"
        }
    }

    // MARK: - Stub handlers (replaced in later clusters)

    static func handleRecordAudio(argv: [String]) -> String {
        let (scope, deviceUID, outputPathStr) = RecordAudio.parseArgs(argv: argv)
        let outputURL = RecordAudio.outputPath(explicitPath: outputPathStr)
        let parentId = ProcessInfo.processInfo.environment["TURBO_WORKFLOW_ID"]
        // Capture start offset before the blocking run() so it reflects when
        // recording began, not when it ended.
        let startOffsetMs = RecordAudio.computeStartOffsetMs()
        do {
            try RecordAudio.run(scope: scope, deviceUID: deviceUID, outputURL: outputURL, parentId: parentId)
            return RecordAudio.encodeManifest(path: outputURL.path, startOffsetMs: startOffsetMs)
        } catch RecordAudioError.permissionDenied(let resource) {
            fputs("permissionDenied(\"\(resource)\")\n", stderr)
            exit(1)
        } catch {
            fputs("\(error)\n", stderr)
            exit(1)
        }
    }

    static func handleScreenshot(argv: [String]) -> String {
        guard let outputDir = ScreenshotSubcommand.parseOutputDir(argv: argv) else {
            fputs("error: --output-dir <dir> is required\n", stderr)
            exit(1)
        }
        let parentId = ProcessInfo.processInfo.environment["TURBO_WORKFLOW_ID"]
        switch ScreenshotSubcommand.run(outputDir: outputDir, parentId: parentId) {
        case .success(let path):
            return path
        case .cancelled:
            fputs("userCancelled\n", stderr)
            exit(1)
        case .error(let msg):
            fputs("\(msg)\n", stderr)
            exit(1)
        }
    }

    static func handleCommand(argv: [String]) -> String {
        guard let cmd = CommandSubcommand.parseShellArg(argv: argv) else {
            fputs("error: --shell <cmd> is required\n", stderr)
            exit(1)
        }
        switch CommandSubcommand.run(shellCommand: cmd) {
        case .success(let stdout):
            return stdout
        case .failure(let errMsg, let code):
            fputs("\(errMsg)\n", stderr)
            exit(code != 0 ? code : 1)
        }
    }

    static func handlePermissionsState(argv: [String]) -> String {
        PermissionsStateCommand.run()
    }

    static func handleRecordScreen(argv: [String]) -> String {
        guard let args = RecordScreen.parseArgs(argv: argv) else {
            fputs("error: --output-dir <dir> is required\n", stderr)
            exit(1)
        }
        let parentId = ProcessInfo.processInfo.environment["TURBO_WORKFLOW_ID"]
        return RecordScreen.run(
            outputDir: args.outputDir,
            scope: args.scope,
            threshold: args.threshold,
            minIntervalMs: args.minIntervalMs,
            maxKeyframes: args.maxKeyframes,
            parentId: parentId
        )
    }
}
