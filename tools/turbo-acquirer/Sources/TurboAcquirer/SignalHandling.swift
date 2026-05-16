import Foundation

// MARK: - Stop flag

/// Set to true by the SIGTERM/SIGINT handler. Readable from any thread.
/// `nonisolated(unsafe)` suppresses Swift concurrency isolation warnings
/// for a C-level signal handler variable.
nonisolated(unsafe) private var stopFlag: Bool = false

/// Returns true if a stop signal (SIGTERM or SIGINT) has been received.
public func isStopRequested() -> Bool {
    stopFlag
}

/// Resets the stop flag. Intended for use in unit tests to restore
/// clean state between test cases.
public func resetStopFlag() {
    stopFlag = false
}

// MARK: - Handler installation

/// Installs POSIX signal handlers for SIGTERM and SIGINT that set the
/// shared `stopFlag`. Call once before starting the main work loop.
public func installStopSignalHandlers() {
    signal(SIGTERM) { _ in stopFlag = true }
    signal(SIGINT)  { _ in stopFlag = true }
}
