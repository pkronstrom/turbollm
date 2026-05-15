import AppKit

enum ParamEditor {
    /// Open an editor for `param`. On commit, calls `onCommit(newValue)`.
    static func open(workflow: Workflow, param: WorkflowParam, settings: Settings,
                     anchor: NSView, onCommit: @escaping (String?) -> Void) {
        let current = settings.paramValue(workflow: workflow.name, param: param.name)
            ?? param.defaultValue ?? ""

        switch param.type {
        case "string":
            showText(anchor: anchor, current: current, multiline: false, onCommit: onCommit)
        case "text":
            showText(anchor: anchor, current: current, multiline: true, onCommit: onCommit)
        case "enum":
            showEnum(anchor: anchor, current: current, options: param.options ?? [], onCommit: onCommit)
        case "file":
            showFile(extensions: param.extensions, current: current, onCommit: onCommit)
        case "directory":
            showDirectory(current: current, onCommit: onCommit)
        default:
            return
        }
    }

    private static func showText(anchor: NSView, current: String, multiline: Bool,
                                 onCommit: @escaping (String?) -> Void) {
        let popover = NSPopover()
        let vc = TextEditorVC(current: current, multiline: multiline) { value in
            onCommit(value)
            popover.performClose(nil)
        }
        popover.contentViewController = vc
        popover.behavior = .transient
        popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .maxX)
    }

    private static func showEnum(anchor: NSView, current: String, options: [String],
                                 onCommit: @escaping (String?) -> Void) {
        let popover = NSPopover()
        let vc = EnumEditorVC(current: current, options: options) { value in
            onCommit(value)
            popover.performClose(nil)
        }
        popover.contentViewController = vc
        popover.behavior = .transient
        popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .maxX)
    }

    private static func showFile(extensions: [String]?, current: String,
                                 onCommit: @escaping (String?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if let exts = extensions { panel.allowedFileTypes = exts }
        if !current.isEmpty { panel.directoryURL = URL(fileURLWithPath: current).deletingLastPathComponent() }
        if panel.runModal() == .OK, let url = panel.url {
            onCommit(url.path)
        }
    }

    private static func showDirectory(current: String, onCommit: @escaping (String?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if !current.isEmpty { panel.directoryURL = URL(fileURLWithPath: current) }
        if panel.runModal() == .OK, let url = panel.url {
            onCommit(url.path)
        }
    }
}

final class TextEditorVC: NSViewController {
    private let initial: String
    private let multiline: Bool
    private let onCommit: (String) -> Void
    private var field: NSTextField!
    private var textView: NSTextView!

    init(current: String, multiline: Bool, onCommit: @escaping (String) -> Void) {
        self.initial = current; self.multiline = multiline; self.onCommit = onCommit
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError() }

    override func loadView() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: multiline ? 120 : 28))
        if multiline {
            let scroll = NSScrollView(frame: view.bounds)
            scroll.hasVerticalScroller = true
            textView = NSTextView(frame: scroll.bounds)
            textView.string = initial
            scroll.documentView = textView
            view.addSubview(scroll)
        } else {
            field = NSTextField(string: initial)
            field.frame = view.bounds
            field.target = self
            field.action = #selector(commit)
            view.addSubview(field)
        }
        self.view = view
    }

    @objc func commit() {
        let value = multiline ? textView.string : field.stringValue
        onCommit(value)
    }
}

final class EnumEditorVC: NSViewController {
    private let options: [String]
    private let initial: String
    private let onCommit: (String) -> Void
    private var popUp: NSPopUpButton!

    init(current: String, options: [String], onCommit: @escaping (String) -> Void) {
        self.options = options; self.initial = current; self.onCommit = onCommit
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError() }

    override func loadView() {
        let view = NSView(frame: NSRect(x: 0, y: 0, width: 220, height: 28))
        popUp = NSPopUpButton(frame: view.bounds, pullsDown: false)
        popUp.addItems(withTitles: options)
        popUp.selectItem(withTitle: initial)
        popUp.target = self
        popUp.action = #selector(commit)
        view.addSubview(popUp)
        self.view = view
    }

    @objc func commit() {
        if let selected = popUp.selectedItem?.title { onCommit(selected) }
    }
}
