import Foundation

// MARK: - Stop flag

/// Set to non-zero when a stop signal (SIGTERM/SIGINT) is received. `sig_atomic_t`
/// is the only type whose reads/writes are async-signal-safe; the poll loops read
/// it via `isStopRequested()`.
nonisolated(unsafe) private var stopFlag: sig_atomic_t = 0

/// Retains the dispatch signal sources for the process lifetime (a released
/// source stops delivering).
nonisolated(unsafe) private var signalSources: [DispatchSourceSignal] = []

/// Test seam: the hard force-exit backstop. Production terminates the process;
/// unit tests override it with a no-op so raising a signal doesn't kill the
/// test runner.
nonisolated(unsafe) public var stopForceExit: @Sendable () -> Void = { _exit(0) }

/// Returns true if a stop signal (SIGTERM or SIGINT) has been received.
public func isStopRequested() -> Bool {
    stopFlag != 0
}

/// Resets the stop flag. Intended for use in unit tests to restore
/// clean state between test cases.
public func resetStopFlag() {
    stopFlag = 0
}

// MARK: - Handler installation

/// Installs SIGTERM/SIGINT handling that stops the recording.
///
/// Uses a GCD `DispatchSource` signal source rather than a C `signal()` handler
/// for three reasons:
///   1. The event handler runs on a normal queue, not in signal context, so it
///      can safely touch Swift state (a C handler mutating Swift state is UB).
///   2. It fires on a background queue — independent of the main thread — so a
///      recording still stops when the main thread is parked in `.wait()` during
///      async ScreenCaptureKit setup.
///   3. As a `SIG_IGN` + kqueue source it isn't clobbered by AppKit's own
///      SIGTERM handling once the window picker / flash initialize NSApplication.
///
/// The graceful path (poll loop sees the flag → flush WAV/manifest → exit) wins
/// in the normal case. The `_exit(0)` backstop only fires if teardown is itself
/// wedged inside a system framework — you can't gracefully unwind a hung SCK/AVF
/// call, so guarantee termination instead.
/// ponytail: hard `_exit` backstop over an event-driven rewrite; revisit if
/// wedged-setup contention becomes common rather than rare.
public func installStopSignalHandlers() {
    for sig in [SIGTERM, SIGINT] {
        signal(sig, SIG_IGN)  // disable default disposition; the source observes via kqueue
        let source = DispatchSource.makeSignalSource(signal: sig, queue: .global())
        source.setEventHandler {
            stopFlag = 1
            DispatchQueue.global().asyncAfter(deadline: .now() + 3) { stopForceExit() }
        }
        source.resume()
        signalSources.append(source)
    }
}
