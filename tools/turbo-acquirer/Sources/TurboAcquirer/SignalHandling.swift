import Foundation

// MARK: - Stop flag

/// Set to non-zero when a stop signal (SIGTERM/SIGINT) is received, or when
/// the capture backend itself dies mid-recording (see `requestStop()`). This
/// is a cross-thread flag, not a signal-context one: the `DispatchSource`
/// event handler below runs on a normal GCD queue (see `installStopSignalHandlers`
/// doc), never inside the signal handler itself, so ordinary Swift state would
/// be just as safe here. `sig_atomic_t` is kept as the storage type anyway —
/// it's still a trivial, lock-free word-sized value, which is all a poll loop
/// reading it via `isStopRequested()` needs.
nonisolated(unsafe) private var stopFlag: sig_atomic_t = 0

/// Retains the dispatch signal sources for the process lifetime (a released
/// source stops delivering).
nonisolated(unsafe) private var signalSources: [DispatchSourceSignal] = []

/// Guards `installStopSignalHandlers()` against being called more than once
/// per process — a second call would append duplicate `DispatchSourceSignal`s,
/// each firing (and each scheduling its own force-exit backstop) per signal.
nonisolated(unsafe) private var handlersInstalled = false

/// Test seam: the hard force-exit backstop. Production terminates the process
/// with a non-zero status (distinguishable from a clean, graceful exit);
/// unit tests override it with a no-op so raising a signal doesn't kill the
/// test runner.
nonisolated(unsafe) public var stopForceExit: @Sendable () -> Void = { _exit(2) }

/// Returns true if a stop signal (SIGTERM or SIGINT) has been received.
public func isStopRequested() -> Bool {
    stopFlag != 0
}

/// Resets the stop flag. Intended for use in unit tests to restore
/// clean state between test cases.
public func resetStopFlag() {
    stopFlag = 0
}

/// Forces the poll loop to treat this as a stop request even though no
/// SIGTERM/SIGINT arrived — used when the capture backend itself dies mid-
/// recording (SCStream/AVAudioEngine reporting an unexpected termination) so
/// the process still tears down and emits a manifest instead of hanging.
public func requestStop() {
    stopFlag = 1
}

// MARK: - Handler installation

/// Installs SIGTERM/SIGINT handling that stops the recording. Safe to call
/// more than once — only the first call installs handlers.
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
/// in the normal case. The force-exit backstop only fires if teardown is itself
/// wedged inside a system framework — you can't gracefully unwind a hung SCK/AVF
/// call, so guarantee termination instead. The backstop's clock starts at
/// signal arrival, not at "teardown looks stuck", so it needs enough headroom
/// that a healthy-but-slow teardown (e.g. draining a large PNG queue) finishes
/// first; 10s comfortably covers that case while still bounding a truly wedged
/// process. It exits with status 2 (not 0) so the parent can tell a hard-kill
/// apart from a clean, complete exit.
/// ponytail: hard force-exit backstop over an event-driven "is teardown stuck"
/// rewrite; revisit if wedged-setup contention becomes common rather than rare.
public func installStopSignalHandlers() {
    guard !handlersInstalled else { return }
    handlersInstalled = true
    for sig in [SIGTERM, SIGINT] {
        signal(sig, SIG_IGN)  // disable default disposition; the source observes via kqueue
        let source = DispatchSource.makeSignalSource(signal: sig, queue: .global())
        source.setEventHandler {
            stopFlag = 1
            DispatchQueue.global().asyncAfter(deadline: .now() + 10) { stopForceExit() }
        }
        source.resume()
        signalSources.append(source)
    }
}
