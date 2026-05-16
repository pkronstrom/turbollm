import Foundation

/// Result type for ScreenshotSubcommand.run(), used as a test seam.
enum ScreenshotResult {
    case success(path: String)
    case cancelled
    case error(message: String)
}

/// Implements the `screenshot` subcommand.
///
/// Invokes `/usr/sbin/screencapture -i <outputDir>/<timestamp>.png` to let the user
/// select a region/window. Detects cancellation by file-existence check after the process
/// exits. Writes an activity file for the duration of the capture and deletes it on exit.
///
/// Test seam: set `TURBO_SCREENCAPTURE_FAKE=success` or `TURBO_SCREENCAPTURE_FAKE=cancel`
/// in the environment to bypass the real screencapture invocation.
enum ScreenshotSubcommand {

    static let screencapturePath = "/usr/sbin/screencapture"

    /// Runs the screenshot subcommand.
    ///
    /// - Parameters:
    ///   - outputDir: Directory to place the PNG file.
    ///   - parentId: Optional workflow activity ID for linking.
    /// - Returns: A `ScreenshotResult` describing outcome.
    static func run(outputDir: URL, parentId: String?) -> ScreenshotResult {
        // Write activity file; delete it on exit.
        let activityURL = try? ActivityFile.writeActivity(
            label: "screenshot",
            parentId: parentId
        )
        defer {
            if let url = activityURL {
                ActivityFile.deleteActivity(at: url)
            }
        }

        // Generate timestamped output filename.
        let timestamp = timestampString()
        let outputPath = outputDir.appendingPathComponent("\(timestamp).png").path

        // Check for fake env var (test seam).
        let fakeMode = ProcessInfo.processInfo.environment["TURBO_SCREENCAPTURE_FAKE"]
        switch fakeMode {
        case "success":
            // In fake-success mode, create an empty file to simulate screencapture writing it.
            FileManager.default.createFile(atPath: outputPath, contents: Data(), attributes: nil)
            return .success(path: outputPath)
        case "cancel":
            // In fake-cancel mode, do NOT create the file (simulates user pressing Esc).
            return .cancelled
        default:
            break
        }

        // Real screencapture invocation.
        return runScreencapture(outputPath: outputPath)
    }

    /// Parses argv for the `screenshot` subcommand: expects `--output-dir <dir>`.
    /// Returns the output dir URL, or nil if not found.
    static func parseOutputDir(argv: [String]) -> URL? {
        guard let idx = argv.firstIndex(of: "--output-dir"), idx + 1 < argv.count else {
            return nil
        }
        return URL(fileURLWithPath: argv[idx + 1], isDirectory: true)
    }

    // MARK: - Private helpers

    private static func runScreencapture(outputPath: String) -> ScreenshotResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: screencapturePath)
        // -i: interactive region picker
        process.arguments = ["-i", outputPath]

        do {
            try process.run()
        } catch {
            return .error(message: "Failed to launch screencapture: \(error)")
        }

        process.waitUntilExit()

        // Detect cancellation by file-existence check: screencapture does not write
        // the output file if the user presses Esc.
        if FileManager.default.fileExists(atPath: outputPath) {
            return .success(path: outputPath)
        } else {
            return .cancelled
        }
    }

    private static func timestampString() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd-HHmmss"
        return formatter.string(from: Date())
    }
}
