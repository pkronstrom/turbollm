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
        default:
            return "unknown subcommand: \(subcommand)\nUsage: turbo-acquirer <subcommand> [options]\nSubcommands: record-audio, screenshot, command, permissions-state"
        }
    }

    // MARK: - Stub handlers (replaced in later clusters)

    static func handleRecordAudio(argv: [String]) -> String {
        "record-audio"
    }

    static func handleScreenshot(argv: [String]) -> String {
        "screenshot"
    }

    static func handleCommand(argv: [String]) -> String {
        "command"
    }

    static func handlePermissionsState(argv: [String]) -> String {
        "permissions-state"
    }
}
