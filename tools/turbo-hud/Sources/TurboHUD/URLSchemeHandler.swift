import AppKit

struct ParsedURL {
    let workflowName: String
    let params: [String: String]
}

enum URLSchemeHandler {
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

    /// Install the AppleEvent handler. Call from `applicationDidFinishLaunching`.
    static func install(onRun: @escaping (ParsedURL) -> Void) {
        URLSchemeHandlerImpl.shared.onRun = onRun
        NSAppleEventManager.shared().setEventHandler(
            URLSchemeHandlerImpl.shared,
            andSelector: #selector(URLSchemeHandlerImpl.handle(event:replyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }
}

final class URLSchemeHandlerImpl: NSObject {
    static let shared = URLSchemeHandlerImpl()
    var onRun: ((ParsedURL) -> Void)?

    @objc func handle(event: NSAppleEventDescriptor, replyEvent: NSAppleEventDescriptor) {
        guard let urlString = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue,
              let url = URL(string: urlString),
              let parsed = URLSchemeHandler.parse(url) else { return }
        onRun?(parsed)
    }
}
