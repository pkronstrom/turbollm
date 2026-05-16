import AppKit
import UniformTypeIdentifiers

enum ParamEditor {
    /// Retains the active popover so ARC does not release it before the user commits.
    static var currentPopover: NSPopover?

    // MARK: - T-18: Region picker logic

    /// Returns true when no region sticky exists for `param`, meaning the picker should open.
    /// Returns false if a region has already been captured (sticky present) — the existing
    /// value will be reused and the picker is skipped.
    static func shouldOpenRegionPicker(workflow: Workflow, param: WorkflowParam, settings: Settings) -> Bool {
        let regionKey = "\(workflow.name).\(param.name).region"
        return settings.rawString(forKey: regionKey) == nil
    }

    /// Formats a captured screen region as "x,y,w,h" for storage in UserDefaults.
    static func formatRegion(x: Int, y: Int, w: Int, h: Int) -> String {
        "\(x),\(y),\(w),\(h)"
    }

    /// Opens a transparent fullscreen NSWindow for region selection.
    ///
    /// The user drags a rectangle on screen; on mouseUp the region is written to
    /// `<wf>.<param>.region` in `Settings.defaultSuiteName` and the window closes.
    /// Pressing Esc reverts `<wf>.<param>.scope` to the value it had before the picker
    /// was opened (typically "full-display") and closes without writing a sticky.
    ///
    /// App.swift wires `MenuTarget.onOpenRegionPicker` to call this function.
    /// The NSWindow display path is exercised manually in T-31; the controller's
    /// commit/cancel logic is covered by unit tests.
    static func openRegionPicker(workflow: Workflow, param: WorkflowParam) {
        // Read the scope that was active before region was selected (for cancel revert).
        let scopeKey = "\(workflow.name).\(param.name).scope"
        let previousScope = UserDefaults(suiteName: Settings.defaultSuiteName)?
            .string(forKey: scopeKey) ?? "full-display"

        let controller = RegionPickerController(
            workflow: workflow, param: param, previousScope: previousScope
        )

        guard let screen = NSScreen.main else { return }

        let window = NSWindow(
            contentRect: screen.frame,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false,
            screen: screen
        )
        window.backgroundColor = NSColor.black.withAlphaComponent(0.3)
        window.isOpaque = false
        window.hasShadow = false
        window.level = .screenSaver
        window.ignoresMouseEvents = false
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]

        let pickerView = RegionPickerView(controller: controller, owningWindow: window)
        window.contentView = pickerView
        window.makeKeyAndOrderFront(nil)
    }

    /// Open an editor for `param`. On commit, calls `onCommit(newValue)`.
    static func open(workflow: Workflow, param: WorkflowParam, settings: Settings,
                     anchor: NSView, onCommit: @escaping (String?) -> Void) {
        // Use the same resolution order as WorkflowRunner so popover starting
        // values honor `auto` and `default_env`, not just sticky/default.
        let resolved = (try? WorkflowRunner.resolveParams(workflow, settings: settings)) ?? [:]
        let current = resolved[param.name] ?? ""

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
        popover.delegate = PopoverLifetimeDelegate.shared
        currentPopover = popover
        // Defer to next runloop tick so the closing menu doesn't dismiss the
        // popover before the user sees it.
        DispatchQueue.main.async {
            popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .maxX)
        }
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
        popover.delegate = PopoverLifetimeDelegate.shared
        currentPopover = popover
        DispatchQueue.main.async {
            popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .maxX)
        }
    }

    private static func showFile(extensions: [String]?, current: String,
                                 onCommit: @escaping (String?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        if let exts = extensions {
            panel.allowedContentTypes = exts.compactMap { UTType(filenameExtension: $0) }
        }
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

/// Clears ParamEditor.currentPopover when the popover closes so ARC can reclaim it.
final class PopoverLifetimeDelegate: NSObject, NSPopoverDelegate {
    static let shared = PopoverLifetimeDelegate()

    func popoverDidClose(_ notification: Notification) {
        ParamEditor.currentPopover = nil
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

// MARK: - T-fix-3: RegionPickerController (logic, unit-testable)

/// Controller for the region picker; handles commit and cancel without
/// coupling to the NSWindow lifecycle. Injecting a custom `UserDefaults`
/// makes the logic unit-testable without touching the production suite.
final class RegionPickerController {
    let workflow: Workflow
    let param: WorkflowParam
    /// Scope value to restore on cancel (e.g. "full-display").
    let previousScope: String

    private let defaults: UserDefaults

    init(workflow: Workflow,
         param: WorkflowParam,
         previousScope: String,
         defaults: UserDefaults = UserDefaults(suiteName: Settings.defaultSuiteName) ?? .standard) {
        self.workflow = workflow
        self.param = param
        self.previousScope = previousScope
        self.defaults = defaults
    }

    private var regionKey: String { "\(workflow.name).\(param.name).region" }
    private var scopeKey:  String { "\(workflow.name).\(param.name).scope" }

    /// Called when the user finishes dragging a rectangle.
    /// Persists the region sticky and leaves scope=region in place.
    func commit(rect: CGRect) {
        let regionStr = ParamEditor.formatRegion(
            x: Int(rect.origin.x), y: Int(rect.origin.y),
            w: Int(rect.size.width), h: Int(rect.size.height)
        )
        defaults.set(regionStr, forKey: regionKey)
    }

    /// Called when the user presses Esc.
    /// Reverts scope to the value it had before the picker was opened,
    /// leaving no region sticky (so next time scope=region is chosen the
    /// picker opens again).
    func cancel() {
        defaults.set(previousScope, forKey: scopeKey)
    }
}

// MARK: - T-fix-3: RegionPickerView (NSWindow content; manual-smoke only)

/// Transparent NSView that tracks a mouse drag and calls the controller
/// on commit (mouseUp) or cancel (Esc key).
///
/// NOTE: This class creates a live NSWindow — it is exercised in the T-31
/// manual smoke, not in unit tests.
final class RegionPickerView: NSView {
    private let controller: RegionPickerController
    private weak var owningWindow: NSWindow?

    private var dragStart: NSPoint = .zero
    private var dragRect: NSRect = .zero

    init(controller: RegionPickerController, owningWindow: NSWindow) {
        self.controller = controller
        self.owningWindow = owningWindow
        super.init(frame: .zero)
    }
    required init?(coder: NSCoder) { fatalError() }

    override var acceptsFirstResponder: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        // Semi-transparent overlay with a clear selection rectangle.
        NSColor.black.withAlphaComponent(0.3).setFill()
        bounds.fill()
        if dragRect != .zero {
            NSColor.white.withAlphaComponent(0.3).setFill()
            dragRect.fill()
            NSColor.white.setStroke()
            let path = NSBezierPath(rect: dragRect)
            path.lineWidth = 2
            path.stroke()
        }
    }

    override func mouseDown(with event: NSEvent) {
        dragStart = convert(event.locationInWindow, from: nil)
        dragRect = .zero
        needsDisplay = true
    }

    override func mouseDragged(with event: NSEvent) {
        let current = convert(event.locationInWindow, from: nil)
        dragRect = NSRect(
            x: min(dragStart.x, current.x),
            y: min(dragStart.y, current.y),
            width: abs(current.x - dragStart.x),
            height: abs(current.y - dragStart.y)
        )
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        guard dragRect.width > 4 && dragRect.height > 4 else {
            // Tiny / accidental click — treat as cancel.
            controller.cancel()
            owningWindow?.close()
            return
        }
        // Convert from view-flipped coordinates to screen coordinates.
        let screenRect = window?.convertToScreen(convert(dragRect, to: nil)) ?? dragRect
        controller.commit(rect: screenRect)
        owningWindow?.close()
    }

    override func keyDown(with event: NSEvent) {
        // Esc (keyCode 53) cancels the pick.
        if event.keyCode == 53 {
            controller.cancel()
            owningWindow?.close()
        }
    }
}
