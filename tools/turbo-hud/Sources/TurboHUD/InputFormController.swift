import AppKit
import Foundation

/// Post-acquisition input form. Shows a popover for any configured param that
/// is still unset after the primary acquirer completes.
enum InputFormController {
    /// Retains the active popover so ARC does not release it.
    static var currentPopover: NSPopover?

    // MARK: - Pure logic

    /// Returns the subset of `workflow.params` that are configured (not acquired)
    /// AND have no resolved value from any source: sticky, auto, default_env,
    /// default, or prefilled (from acquirers).
    static func unsetParams(
        workflow: Workflow,
        prefilled: [String: String],
        settings: Settings
    ) -> [WorkflowParam] {
        let acquiredTypes: Set<String> = ["audio-recording", "screenshot-manual", "command"]

        return workflow.params.filter { param in
            // Skip acquired params — they're handled by acquirers.
            if acquiredTypes.contains(param.type) { return false }
            // Skip if prefilled by an acquirer.
            if let v = prefilled[param.name], !v.isEmpty { return false }
            // Skip if sticky value exists.
            if let sticky = settings.paramValue(workflow: workflow.name, param: param.name),
               !sticky.isEmpty { return false }
            // Skip if auto template resolves.
            if let auto = param.auto,
               let expanded = try? Templates.expand(auto, params: prefilled),
               !expanded.isEmpty { return false }
            // Skip if default_env is set.
            if let envName = param.defaultEnv,
               let envVal = ProcessInfo.processInfo.environment[envName],
               !envVal.isEmpty { return false }
            // Skip if literal default exists.
            if let def = param.defaultValue, !def.isEmpty { return false }
            // No resolution source — param is unset.
            return true
        }
    }

    // MARK: - UI

    /// If all params are resolved, returns `prefilled` immediately without showing UI.
    /// Otherwise shows an NSPopover with one text field per unset param.
    /// On commit, writes sticky values and returns the merged param map.
    /// On Esc / outside-click, throws `AcquirerError.userCancelled`.
    @MainActor
    static func show(
        workflow: Workflow,
        prefilled: [String: String],
        settings: Settings,
        anchor: NSView
    ) async throws -> [String: String] {
        let unset = unsetParams(workflow: workflow, prefilled: prefilled, settings: settings)
        if unset.isEmpty { return prefilled }

        return try await withCheckedThrowingContinuation { cont in
            let popover = NSPopover()
            let vc = InputFormVC(params: unset) { values in
                if let values {
                    // Write sticky values.
                    for (name, val) in values {
                        settings.setParamValue(workflow: workflow.name, param: name, value: val)
                    }
                    // Merge prefilled + new values.
                    var merged = prefilled
                    for (k, v) in values { merged[k] = v }
                    currentPopover = nil
                    cont.resume(returning: merged)
                } else {
                    currentPopover = nil
                    cont.resume(throwing: AcquirerError.userCancelled)
                }
            }
            popover.contentViewController = vc
            popover.behavior = .semitransient
            popover.delegate = InputFormLifetimeDelegate(onClose: {
                // Only fire userCancelled if we haven't already resolved.
            })
            currentPopover = popover
            DispatchQueue.main.async {
                popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .maxX)
            }
        }
    }
}

// MARK: - InputFormVC

private final class InputFormVC: NSViewController {
    private let params: [WorkflowParam]
    private let onCommit: ([String: String]?) -> Void
    private var fields: [String: NSTextField] = [:]

    init(params: [WorkflowParam], onCommit: @escaping ([String: String]?) -> Void) {
        self.params = params
        self.onCommit = onCommit
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError() }

    override func loadView() {
        let rowH: CGFloat = 28
        let labelW: CGFloat = 120
        let fieldW: CGFloat = 200
        let padding: CGFloat = 8
        let totalH = CGFloat(params.count) * (rowH + padding) + rowH + padding
        let view = NSView(frame: NSRect(x: 0, y: 0, width: labelW + fieldW + padding * 3, height: totalH))

        var y = totalH - rowH - padding
        for param in params {
            let label = NSTextField(labelWithString: "\(param.name):")
            label.frame = NSRect(x: padding, y: y, width: labelW, height: rowH)
            label.alignment = .right
            view.addSubview(label)

            let field = NSTextField(string: "")
            field.frame = NSRect(x: padding * 2 + labelW, y: y, width: fieldW, height: rowH)
            field.target = self
            field.action = #selector(commitAction)
            fields[param.name] = field
            view.addSubview(field)

            y -= rowH + padding
        }

        // Commit button
        let btn = NSButton(title: "OK", target: self, action: #selector(commitAction))
        btn.keyEquivalent = "\r"
        btn.frame = NSRect(x: view.frame.width - 80 - padding, y: padding, width: 80, height: rowH)
        view.addSubview(btn)

        self.view = view
    }

    @objc func commitAction() {
        var values: [String: String] = [:]
        for (name, field) in fields {
            values[name] = field.stringValue
        }
        onCommit(values)
        presentingViewController?.dismiss(self)
        if let popover = InputFormController.currentPopover {
            popover.performClose(nil)
        }
    }
}

// MARK: - InputFormLifetimeDelegate

private final class InputFormLifetimeDelegate: NSObject, NSPopoverDelegate {
    private let onClose: () -> Void

    init(onClose: @escaping () -> Void) {
        self.onClose = onClose
    }

    func popoverDidClose(_ notification: Notification) {
        onClose()
    }
}
