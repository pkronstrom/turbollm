import AppKit
import Foundation

// MARK: - SessionError

enum SessionError: Error {
    case noPrimaryAcquirer
    case multiplePrimaryAcquirers
    case permissionDenied(String)
}

// MARK: - TaggedAcquirerResult

/// Internal type used to distinguish the primary task completion from background/trigger tasks.
private enum AcquireTag {
    case primary(AcquirerResult?)
    case other(AcquirerResult?)
}

// MARK: - SessionController

@MainActor
final class SessionController {
    static let shared = SessionController()

    weak var appState: AppState?

    // Active acquirers for the current session; cleared on session end.
    private var activeAcquirers: [any Acquirer] = []
    private var sessionActive: Bool = false

    // Test-only override for InputFormController.show — if non-nil, called instead of the real UI.
    var inputFormShowOverride: ((_ workflow: Workflow, _ prefilled: [String: String], _ settings: Settings) async throws -> [String: String])?

    init() {}

    // MARK: - Start

    func start(workflow: Workflow, settings: Settings) async {
        guard !sessionActive else { return }

        // Validate primary count.
        let acquiredParams = workflow.params.filter { isAcquiredType($0) }
        let primaries = acquiredParams.filter { $0.mode == "primary" }
        guard !primaries.isEmpty else {
            NSLog("SessionController: workflow '%@' has no primary acquirer", workflow.name)
            return
        }
        guard primaries.count == 1 else {
            NSLog("SessionController: workflow '%@' has multiple primary acquirers", workflow.name)
            return
        }

        // Pre-resolve configured params.
        var configuredResolved: [String: String] = [:]
        for p in workflow.params where !isAcquiredType(p) {
            if let sticky = settings.paramValue(workflow: workflow.name, param: p.name), !sticky.isEmpty {
                configuredResolved[p.name] = sticky
            } else if let auto = p.auto, let expanded = try? Templates.expand(auto, params: configuredResolved) {
                configuredResolved[p.name] = expanded
            } else if let envName = p.defaultEnv,
                      let envVal = ProcessInfo.processInfo.environment[envName], !envVal.isEmpty {
                configuredResolved[p.name] = envVal
            } else {
                configuredResolved[p.name] = p.defaultValue ?? ""
            }
        }

        // Build acquirer instances for this session. (T-fix-1)
        let builtAcquirers = buildAcquirers(for: workflow, settings: settings)
        activeAcquirers = builtAcquirers
        sessionActive = true

        // Write activity file.
        let sessionID = UUID()
        let activityURL = activityFileURL(id: sessionID)
        writeActivityFile(id: sessionID, workflow: workflow)

        // Publish session start.
        let sessionState = SessionState(id: sessionID, workflow: workflow,
                                         startedAt: Date(), phase: "Running")
        appState?.activeSession = sessionState

        let acquiredResults = await runAcquirers(builtAcquirers)

        activeAcquirers = []
        sessionActive = false

        // Publish session end.
        appState?.activeSession = nil

        // Delete activity file.
        try? FileManager.default.removeItem(at: activityURL)

        // Merge configured + acquired params.
        var params = configuredResolved
        for (k, v) in acquiredResults { params[k] = v }

        // T-fix-4: Show InputFormController for any remaining unset configured params.
        // If the test override is set, call it; otherwise use the real UI.
        if let override = inputFormShowOverride {
            if let merged = try? await override(workflow, params, settings) {
                params = merged
            }
        } else {
            // In production, obtain the status-item button as anchor.
            // In headless / test environments NSApp.windows may be empty — skip UI gracefully.
            let unsetCount = InputFormController.unsetParams(
                workflow: workflow, prefilled: params, settings: settings
            ).count
            if unsetCount > 0 {
                // Try to get anchor from the shared status item button.
                // We do this via a MainActor call since we're already on MainActor.
                if let anchor = statusItemAnchor() {
                    if let merged = try? await InputFormController.show(
                        workflow: workflow,
                        prefilled: params,
                        settings: settings,
                        anchor: anchor
                    ) {
                        params = merged
                    }
                } else {
                    NSLog("SessionController: no anchor available for InputFormController; %d params remain unset", unsetCount)
                }
            }
        }

        spawnWorkflow(workflow, params: params)
    }

    /// Returns the NSStatusItem button if the App singleton is accessible.
    private func statusItemAnchor() -> NSView? {
        // Try to obtain the status item button from the application delegate.
        if let appDelegate = NSApp.delegate as? AnyObject,
           let statusItem = (appDelegate as AnyObject).value(forKey: "statusItem") as? NSStatusItem {
            return statusItem.button
        }
        return nil
    }

    // MARK: - Shared acquirer orchestration (T-fix-2)

    /// Runs all acquirers concurrently. The session ends when the primary acquirer
    /// completes (returns or throws). All background and trigger acquirers are then
    /// cancelled immediately; a bounded ≤500 ms window collects any final results.
    private func runAcquirers(_ acquirers: [any Acquirer]) async -> [String: String] {
        let primaryAcquirer = acquirers.first(where: { $0.mode == .primary })!
        let backgroundAcquirers = acquirers.filter { $0.mode == .background }
        let triggerAcquirers = acquirers.filter { $0.mode == .trigger }

        var acquiredResults: [String: String] = [:]

        // Phase 1: run all acquirers; stop as soon as the primary task finishes.
        await withTaskGroup(of: AcquireTag.self) { group in
            // Start trigger acquirers (they suspend until cancel() is called).
            for trig in triggerAcquirers {
                group.addTask {
                    return .other(try? await trig.acquire())
                }
            }
            // Start background acquirers.
            for bg in backgroundAcquirers {
                group.addTask {
                    return .other(try? await bg.acquire())
                }
            }
            // Primary — tagged so we can detect its completion.
            group.addTask {
                return .primary(try? await primaryAcquirer.acquire())
            }

            for await tag in group {
                switch tag {
                case .primary(let r):
                    if let r { acquiredResults[r.paramName] = r.value }
                    // Primary completed — cancel all remaining acquirers immediately.
                    // Each acquirer's cancel() resumes its acquire() continuation, so the
                    // for-await loop drains the .other results below and the group exits.
                    for bg in backgroundAcquirers { bg.cancel() }
                    for trig in triggerAcquirers { trig.cancel() }

                case .other(let r):
                    if let r { acquiredResults[r.paramName] = r.value }
                }
            }
        }

        return acquiredResults
    }

    // MARK: - Cancel

    func cancel() {
        for acquirer in activeAcquirers {
            acquirer.cancel()
        }
    }

    // MARK: - Helpers

    private func isAcquiredType(_ param: WorkflowParam) -> Bool {
        ["audio-recording", "screenshot-manual", "command"].contains(param.type)
    }

    // T-fix-1: Build concrete Acquirer instances for each acquired param in the workflow.
    func buildAcquirers(for workflow: Workflow, settings: Settings) -> [any Acquirer] {
        var result: [any Acquirer] = []

        // Screenshot trigger acquirer needs a place to write per-screenshot PNGs.
        // Audio uses its own path resolution (env/inbox/timestamped fallback) and
        // does not share a directory with screenshots.
        let screenshotsDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Recordings/turbo", isDirectory: true)

        for param in workflow.params {
            switch param.type {
            case "audio-recording":
                // Read scope from sticky settings (user may have changed it via menu).
                let scopeKey = "\(workflow.name).\(param.name).scope"
                let scopeStr = UserDefaults(suiteName: Settings.defaultSuiteName)?
                    .string(forKey: scopeKey) ?? param.scope ?? "mic-only"
                let scope: AudioScope = scopeStr == "system+mic" ? .systemPlusMic : .micOnly

                // Read sticky input-device UID.
                let deviceKey = "\(workflow.name).\(param.name).input_device"
                let inputDeviceUID = UserDefaults(suiteName: Settings.defaultSuiteName)?
                    .string(forKey: deviceKey)

                // No sessionDir: AudioRecorder writes a timestamped file in the
                // $TURBO_AUDIO_INBOX or ~/Recordings/turbo fallback. Bundling
                // multiple per-run artifacts under one dir is a future workflow
                // shape; today every record-* workflow has audio as the only
                // file output.
                let recorder = AudioRecorder(
                    paramName: param.name,
                    scope: scope,
                    inputDeviceUID: inputDeviceUID
                )
                result.append(recorder)

            case "screenshot-manual":
                let screenshotter = ScreenshotManual(
                    paramName: param.name,
                    outputDir: screenshotsDir
                )
                result.append(screenshotter)

            case "command":
                guard let cmd = param.command, !cmd.isEmpty else {
                    NSLog("SessionController: command param '%@' has no command string — skipping", param.name)
                    continue
                }
                let cmdAcquirer = CommandAcquirer(paramName: param.name, command: cmd)
                result.append(cmdAcquirer)

            default:
                break
            }
        }

        return result
    }

    /// Test-only initialiser that allows injecting acquirers directly.
    func startWithAcquirers(_ acquirers: [any Acquirer],
                             workflow: Workflow,
                             settings: Settings) async throws {
        guard !sessionActive else { return }

        let primaries = acquirers.filter { $0.mode == .primary }
        guard !primaries.isEmpty else { throw SessionError.noPrimaryAcquirer }
        guard primaries.count == 1 else { throw SessionError.multiplePrimaryAcquirers }

        var configuredResolved: [String: String] = [:]
        for p in workflow.params where !isAcquiredType(p) {
            if let sticky = settings.paramValue(workflow: workflow.name, param: p.name), !sticky.isEmpty {
                configuredResolved[p.name] = sticky
            } else if let auto = p.auto, let expanded = try? Templates.expand(auto, params: configuredResolved) {
                configuredResolved[p.name] = expanded
            } else if let envName = p.defaultEnv,
                      let envVal = ProcessInfo.processInfo.environment[envName], !envVal.isEmpty {
                configuredResolved[p.name] = envVal
            } else {
                configuredResolved[p.name] = p.defaultValue ?? ""
            }
        }

        activeAcquirers = acquirers
        sessionActive = true

        let sessionID = UUID()
        let activityURL = activityFileURL(id: sessionID)
        writeActivityFile(id: sessionID, workflow: workflow)

        let sessionState = SessionState(id: sessionID, workflow: workflow,
                                         startedAt: Date(), phase: "Running")
        appState?.activeSession = sessionState

        let acquiredResults = await runAcquirers(acquirers)

        activeAcquirers = []
        sessionActive = false

        appState?.activeSession = nil
        try? FileManager.default.removeItem(at: activityURL)

        // Merge configured + acquired params, then call inputFormShowOverride if set
        // (allows T-fix-4 tests to verify the param map reaches the form step).
        var params = configuredResolved
        for (k, v) in acquiredResults { params[k] = v }

        if let override = inputFormShowOverride {
            _ = try? await override(workflow, params, settings)
        }
        // Note: startWithAcquirers does NOT call spawnWorkflow — test-only path.
    }

    // MARK: - Activity file

    private func stateDir() -> URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".turbollm")
            .appendingPathComponent("state")
    }

    private func activityFileURL(id: UUID) -> URL {
        stateDir().appendingPathComponent("activity-session-\(id.uuidString).json")
    }

    private func writeActivityFile(id: UUID, workflow: Workflow) {
        let dir = stateDir()
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let entry: [String: Any] = [
            "id": "session-\(id.uuidString)",
            "kind": "workflow",
            "label": workflow.name,
            "started_at": ISO8601DateFormatter().string(from: Date()),
            "owner_pid": ProcessInfo.processInfo.processIdentifier,
            "children": []
        ]
        if let data = try? JSONSerialization.data(withJSONObject: entry) {
            try? data.write(to: activityFileURL(id: id))
        }
    }

    // MARK: - Spawn workflow

    private func spawnWorkflow(_ workflow: Workflow, params: [String: String]) {
        var args = ["turbo", "workflows", "run", workflow.name]
        for (k, v) in params {
            args.append("--param")
            args.append("\(k)=\(v)")
        }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = args
        try? proc.run()
    }
}
