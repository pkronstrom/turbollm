import Foundation

final class Settings {
    /// The stable UserDefaults suite name shared across all Settings instances.
    /// If this constant ever changes, every user loses their sticky params —
    /// don't change it without a migration story.
    static let defaultSuiteName = "com.turbollm.hud"

    private let defaults: UserDefaults

    init(suiteName: String = Settings.defaultSuiteName) {
        self.defaults = UserDefaults(suiteName: suiteName) ?? .standard
    }

    private func key(workflow: String, param: String) -> String {
        "workflow.\(workflow).param.\(param)"
    }

    func paramValue(workflow: String, param: String) -> String? {
        defaults.string(forKey: key(workflow: workflow, param: param))
    }

    func setParamValue(workflow: String, param: String, value: String?) {
        let k = key(workflow: workflow, param: param)
        if let value, !value.isEmpty {
            defaults.set(value, forKey: k)
        } else {
            defaults.removeObject(forKey: k)
        }
    }
}
