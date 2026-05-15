import Foundation

// MARK: - SessionError

enum SessionError: Error {
    case noPrimaryAcquirer
    case multiplePrimaryAcquirers
    case permissionDenied(String)
}

// MARK: - SessionController

@MainActor
final class SessionController {
    static let shared = SessionController()

    weak var appState: AppState?

    // Active acquirers for the current session; cleared on session end.
    private var activeAcquirers: [any Acquirer] = []
    private var sessionActive: Bool = false

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

        // Build acquirer instances for this session.
        let builtAcquirers = buildAcquirers(for: workflow)
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

        // Run primary + background acquirers concurrently.
        let primaryAcquirer = builtAcquirers.first(where: { $0.mode == .primary })!
        let backgroundAcquirers = builtAcquirers.filter { $0.mode == .background }

        var acquiredResults: [String: String] = [:]

        // Start all background acquirers concurrently then await the primary.
        await withTaskGroup(of: AcquirerResult?.self) { group in
            for bg in backgroundAcquirers {
                group.addTask {
                    return try? await bg.acquire()
                }
            }

            // Primary — drives session lifetime.
            group.addTask {
                return try? await primaryAcquirer.acquire()
            }

            for await result in group {
                if let r = result {
                    acquiredResults[r.paramName] = r.value
                }
            }
        }

        // Cancel any background acquirers that haven't stopped yet.
        for bg in backgroundAcquirers { bg.cancel() }
        activeAcquirers = []
        sessionActive = false

        // Publish session end.
        appState?.activeSession = nil

        // Delete activity file.
        try? FileManager.default.removeItem(at: activityURL)

        // Merge configured + acquired params and spawn the workflow.
        var params = configuredResolved
        for (k, v) in acquiredResults { params[k] = v }

        spawnWorkflow(workflow, params: params)
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

    private func buildAcquirers(for workflow: Workflow) -> [any Acquirer] {
        // Concrete acquirer types are created by the callers in C-C/C-D.
        // For now, return an empty list — SessionController tests inject MockAcquirer via
        // the test-only initialiser below.
        return []
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

        let primaryAcquirer = acquirers.first(where: { $0.mode == .primary })!
        let backgroundAcquirers = acquirers.filter { $0.mode == .background }

        var acquiredResults: [String: String] = [:]

        await withTaskGroup(of: AcquirerResult?.self) { group in
            for bg in backgroundAcquirers {
                group.addTask {
                    return try? await bg.acquire()
                }
            }
            group.addTask {
                return try? await primaryAcquirer.acquire()
            }
            for await result in group {
                if let r = result { acquiredResults[r.paramName] = r.value }
            }
        }

        for bg in backgroundAcquirers { bg.cancel() }
        activeAcquirers = []
        sessionActive = false

        appState?.activeSession = nil
        try? FileManager.default.removeItem(at: activityURL)
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
