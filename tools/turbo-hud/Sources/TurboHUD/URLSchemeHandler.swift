import AppKit
import Foundation

struct ParsedURL {
    let workflowName: String
    let params: [String: String]
}

enum URLSchemeHandler {
    /// Test seam: override to capture the argv that would be passed to Process.
    /// In production this is nil and `spawnProcess(_:)` is called instead.
    static var spawnOverride: (([String]) -> Void)? = nil

    static func parse(_ url: URL) -> ParsedURL? {
        guard url.scheme == "turbohud" else { return nil }
        guard url.host == "run" else { return nil }
        // The first non-empty path component is the workflow name.
        let trimmedPath = url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !trimmedPath.isEmpty else { return nil }
        let name = trimmedPath.split(separator: "/").first.map(String.init) ?? trimmedPath
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        var params: [String: String] = [:]
        for q in components?.queryItems ?? [] {
            if let v = q.value { params[q.name] = v }
        }
        return ParsedURL(workflowName: name, params: params)
    }

    /// Build the argv for `turbo workflows run <workflow> [--param k=v ...]`.
    static func buildArgv(from parsed: ParsedURL) -> [String] {
        var argv = ["turbo", "workflows", "run", parsed.workflowName]
        // Sort for deterministic ordering in tests.
        for key in parsed.params.keys.sorted() {
            argv.append("--param")
            argv.append("\(key)=\(parsed.params[key]!)")
        }
        return argv
    }

    /// Spawn `turbo workflows run …` detached. Uses `spawnOverride` if set (tests).
    static func spawn(argv: [String]) {
        if let override = spawnOverride {
            override(argv)
            return
        }
        spawnProcess(argv)
    }

    /// Install the AppleEvent handler. The handler spawns `turbo workflows run`
    /// for every `turbohud://run/<workflow>` URL received. Call from
    /// `applicationDidFinishLaunching`.
    static func install() {
        URLSchemeHandlerImpl.shared.onURL = { url in
            guard let parsed = URLSchemeHandler.parse(url) else { return }
            let argv = URLSchemeHandler.buildArgv(from: parsed)
            URLSchemeHandler.spawn(argv: argv)
        }
        URLSchemeHandlerImpl.registerEventHandler()
    }

    /// Legacy overload — kept for App.swift compatibility until B-G rewires it.
    /// Delegates to the new shell-out path and ignores the `onRun` closure.
    @available(*, deprecated, message: "Use install() — the handler now shells out to turbo workflows run directly")
    static func install(onRun: @escaping (ParsedURL) -> Void) {
        install()
    }
}

// MARK: - Private helpers

private func spawnProcess(_ argv: [String]) {
    guard !argv.isEmpty else { return }
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    proc.arguments = argv
    do {
        try proc.run()
        // Detached: do not waitUntilExit — the HUD returns immediately.
    } catch {
        NSLog("URLSchemeHandler: failed to spawn \(argv): \(error)")
    }
}

// MARK: - AppleEvent bridge

final class URLSchemeHandlerImpl: NSObject {
    static let shared = URLSchemeHandlerImpl()
    var onURL: ((URL) -> Void)?

    static func registerEventHandler() {
        NSAppleEventManager.shared().setEventHandler(
            shared,
            andSelector: #selector(handle(event:replyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }

    @objc func handle(event: NSAppleEventDescriptor, replyEvent: NSAppleEventDescriptor) {
        guard let urlString = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue,
              let url = URL(string: urlString) else { return }
        onURL?(url)
    }
}
