import Foundation

enum TemplatesError: Error, CustomStringConvertible {
    case undefinedParam(String)
    var description: String {
        if case let .undefinedParam(name) = self { return "undefined param '\(name)' in template" }
        return "TemplatesError"
    }
}

enum Templates {
    /// Mirror of Python `workflows.expand_template`: replaces `{{date:FMT}}`,
    /// `{{env:VAR}}`, `{{param-name}}` tokens in `template`. Throws on
    /// missing param references.
    static func expand(_ template: String, params: [String: String]) throws -> String {
        let regex = try NSRegularExpression(pattern: "\\{\\{([^}]+)\\}\\}", options: [])
        let nsTemplate = template as NSString
        var result = ""
        var cursor = 0
        let matches = regex.matches(in: template, options: [], range: NSRange(location: 0, length: nsTemplate.length))
        for match in matches {
            let prefix = nsTemplate.substring(with: NSRange(location: cursor, length: match.range.location - cursor))
            result += prefix
            let token = nsTemplate.substring(with: match.range(at: 1)).trimmingCharacters(in: .whitespaces)
            if token.hasPrefix("date:") {
                let fmt = String(token.dropFirst("date:".count))
                result += formattedDate(fmt: fmt)
            } else if token.hasPrefix("env:") {
                let varName = String(token.dropFirst("env:".count))
                result += ProcessInfo.processInfo.environment[varName] ?? ""
            } else if let v = params[token] {
                result += v
            } else {
                throw TemplatesError.undefinedParam(token)
            }
            cursor = match.range.location + match.range.length
        }
        result += nsTemplate.substring(from: cursor)
        return result
    }

    private static func formattedDate(fmt: String) -> String {
        // Use strftime so format-string semantics match the Python side exactly.
        var t = time(nil)
        var tm = tm()
        localtime_r(&t, &tm)
        var buf = [CChar](repeating: 0, count: 256)
        let n = strftime(&buf, 256, fmt, &tm)
        // strftime returns 0 when the result would not fit; do NOT trust buf.
        guard n > 0 else { return "" }
        return String(cString: buf, encoding: .utf8) ?? ""
    }
}
