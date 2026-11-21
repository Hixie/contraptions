import AppKit

/// A window for editing the configuration file, and for starting and
/// stopping Codex sessions. Saving checks the settings, writes the file, and
/// tells `onSave`; settings that are wrong are not written, and the window
/// says why.
final class SettingsWindow: NSObject, NSWindowDelegate {
    var onSave: () -> Void = {}
    private let url: URL
    private let startsSessions: () -> Bool
    private let setStartsSessions: (Bool) -> Void
    private var window: NSWindow?
    private let address = NSTextField()
    private let owners = NSTextField()
    private let sessions = NSButton(checkboxWithTitle: "Start a Codex session for each failure on a default branch",
                                    target: nil, action: nil)
    private let folder = NSTextField()
    let prompt = NSTextView()
    /// Where the settings and their labels are laid out.
    private(set) var grid: NSGridView?
    private let problem = NSTextField(wrappingLabelWithString: "")

    init(url: URL, startsSessions: @escaping () -> Bool, setStartsSessions: @escaping (Bool) -> Void) {
        self.url = url
        self.startsSessions = startsSessions
        self.setStartsSessions = setStartsSessions
    }

    var isVisible: Bool {
        window?.isVisible ?? false
    }

    /// Shows the window, filled in from the file as it is now, unless it is
    /// showing already, with what has been typed into it.
    func show() {
        if let window, window.isVisible {
            NSApp.activate()
            window.makeKeyAndOrderFront(nil)
            return
        }
        let window = self.window ?? makeWindow()
        self.window = window
        let settings = (try? String(contentsOf: url, encoding: .utf8))
            .flatMap { try? Settings(text: $0) } ?? Settings()
        address.stringValue = settings.address
        owners.stringValue = settings.owners
        let codex = settings.codex ?? Settings.Codex()
        folder.stringValue = codex.folder
        prompt.string = codex.prompt
        sessions.state = startsSessions() ? .on : .off
        problem.stringValue = ""
        NSApp.activate()
        window.makeKeyAndOrderFront(nil)
    }

    /// Builds the window, without showing it.
    func makeWindow() -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 560),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = "cibar Settings"
        window.isReleasedWhenClosed = false
        window.delegate = self
        func label(_ text: String) -> NSTextField {
            let label = NSTextField(labelWithString: text)
            label.alignment = .right
            return label
        }
        func scrolling(_ textView: NSTextView, height: CGFloat) -> NSScrollView {
            textView.isRichText = false
            textView.font = .monospacedSystemFont(ofSize: NSFont.smallSystemFontSize, weight: .regular)
            textView.isAutomaticQuoteSubstitutionEnabled = false
            textView.isAutomaticDashSubstitutionEnabled = false
            textView.isAutomaticTextReplacementEnabled = false
            textView.autoresizingMask = [.width]
            let scroll = TextScrollView(showing: textView)
            scroll.hasVerticalScroller = true
            scroll.borderType = .bezelBorder
            scroll.heightAnchor.constraint(equalToConstant: height).isActive = true
            return scroll
        }
        address.placeholderString = "host:port"
        owners.placeholderString = "owners, separated by commas; empty follows every owner"
        folder.placeholderString = "~"
        let placeholders = NSTextField(wrappingLabelWithString:
            "In the request, {repository}, {branch}, {workflow}, {job}, {job_url}, {event}, {sha}, "
            + "{run_url}, {checks_url} and {report} are filled in. An empty request starts no session.")
        placeholders.textColor = .secondaryLabelColor
        problem.textColor = .systemRed
        let grid = NSGridView(views: [
            [label("Relay:"), address],
            [label("Owners:"), owners],
            [NSGridCell.emptyContentView, sessions],
            [label("Session folder:"), folder],
            [label("Request:"), scrolling(prompt, height: 260)],
            [NSGridCell.emptyContentView, placeholders],
            [NSGridCell.emptyContentView, problem],
        ])
        self.grid = grid
        grid.column(at: 0).xPlacement = .trailing
        grid.rowAlignment = .firstBaseline
        grid.columnSpacing = 8
        grid.rowSpacing = 10
        let save = NSButton(title: "Save", target: self, action: #selector(save))
        save.keyEquivalent = "\r"
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancel))
        cancel.keyEquivalent = "\u{1b}"
        let buttons = NSStackView(views: [cancel, save])
        let content = NSView()
        for view in [grid, buttons] {
            view.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(view)
        }
        NSLayoutConstraint.activate([
            grid.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            grid.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
            grid.topAnchor.constraint(equalTo: content.topAnchor, constant: 20),
            buttons.topAnchor.constraint(equalTo: grid.bottomAnchor, constant: 16),
            buttons.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
            buttons.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -20),
        ])
        window.contentView = content
        window.center()
        return window
    }

    @objc private func save() {
        var settings = Settings()
        settings.address = address.stringValue.trimmingCharacters(in: .whitespaces)
        settings.owners = owners.stringValue.trimmingCharacters(in: .whitespaces)
        settings.codex = Settings.Codex(
            folder: folder.stringValue.trimmingCharacters(in: .whitespaces),
            prompt: prompt.string)
        do {
            _ = try Configuration(settings, name: "the settings")
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try settings.text.write(to: url, atomically: true, encoding: .utf8)
        } catch {
            problem.stringValue = "\(error)"
            return
        }
        setStartsSessions(sessions.state == .on)
        window?.orderOut(nil)
        onSave()
    }

    @objc private func cancel() {
        window?.orderOut(nil)
    }
}

/// A scroll view around a text view, whose first baseline is that of the
/// text's first line, so that a label beside it lines up with that line.
private final class TextScrollView: NSScrollView {
    private let text: NSTextView

    init(showing text: NSTextView) {
        self.text = text
        super.init(frame: .zero)
        documentView = text
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("TextScrollView is not archived")
    }

    /// Measured as the text would be with nothing scrolled, so that the
    /// label stays put as the text scrolls.
    override var firstBaselineOffsetFromTop: CGFloat {
        guard let layout = text.layoutManager, let font = text.font else { return super.firstBaselineOffsetFromTop }
        let border = isFlipped ? contentView.frame.minY : bounds.height - contentView.frame.maxY
        return border + text.textContainerOrigin.y + layout.defaultBaselineOffset(for: font)
    }
}
