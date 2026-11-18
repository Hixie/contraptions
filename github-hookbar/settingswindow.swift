import AppKit

// The settings window.

/// A window with a field for each setting, which edits the configuration
/// file in place: saving rewrites only the lines of the settings that the
/// window shows, so the file's comments and layout stay as they were.
final class SettingsWindow: NSObject {
    var onSave: (() -> Void)?
    private let url: URL
    private let window: NSWindow
    private let fields: [NSTextField]
    private let message = NSTextField(wrappingLabelWithString: "")
    /// What the fields held when the window opened. Saving writes only the
    /// fields that have changed since, so it keeps whatever else changed in
    /// the file meanwhile.
    private var shown: [String] = []

    init(url: URL) {
        self.url = url
        let grid: NSGridView
        (grid, fields) = settingsGrid()
        window = NSWindow(contentRect: .zero, styleMask: [.titled, .closable],
                          backing: .buffered, defer: true)
        super.init()
        window.title = "github-hookbar Settings"
        window.isReleasedWhenClosed = false
        message.textColor = .systemRed
        message.preferredMaxLayoutWidth = 480
        let editFile = NSButton(title: "Edit File…", target: self, action: #selector(openFile))
        let cancel = NSButton(title: "Cancel", target: window,
                              action: #selector(NSWindow.performClose(_:)))
        cancel.keyEquivalent = "\u{1b}"
        let save = NSButton(title: "Save", target: self, action: #selector(save))
        save.keyEquivalent = "\r"
        let buttons = NSStackView(views: [editFile, NSView(), cancel, save])
        let stack = NSStackView(views: [grid, message, buttons])
        stack.setCustomSpacing(20, after: grid)
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.translatesAutoresizingMaskIntoConstraints = false
        buttons.widthAnchor.constraint(equalTo: grid.widthAnchor).isActive = true
        let content = NSView()
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 20),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -20),
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
        ])
        window.contentView = content
        window.initialFirstResponder = fields[0]
        window.center()
    }

    /// Brings the window to the front, filling the fields from the file as
    /// it is now unless the window is already open.
    func show() {
        if !window.isVisible {
            var values: [String: String] = [:]
            var problem = ""
            do {
                if let text = try contents() {
                    let file = try INIFile(text)
                    values = file.values
                    _ = try Configuration(file)
                }
            } catch {
                problem = "\(error)"
            }
            say(problem)
            for (setting, field) in zip(settings, fields) {
                field.stringValue = values[setting.name] ?? ""
            }
            shown = fields.map(\.stringValue)
        }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    /// Shows a problem below the settings, or none when it is empty, and fits
    /// the window to what it then holds, so that no space is left where a
    /// problem was.
    private func say(_ problem: String) {
        message.stringValue = problem
        message.isHidden = problem.isEmpty
        if let content = window.contentView {
            window.setContentSize(content.fittingSize)
        }
    }

    /// The file's text, or nothing when there is no file yet.
    private func contents() throws -> String? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try String(contentsOf: url, encoding: .utf8)
    }

    @objc private func save() {
        do {
            var file = try INIFile(try contents() ?? "")
            for (index, setting) in settings.enumerated()
            where fields[index].stringValue != shown[index] {
                try file.set(setting.name, to: fields[index].stringValue
                    .components(separatedBy: .newlines).joined(separator: " ")
                    .trimmingCharacters(in: .whitespaces))
            }
            _ = try Configuration(file)
            // Writing through a symbolic link keeps the link.
            let target = url.resolvingSymlinksInPath()
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try file.text.write(to: target, atomically: true, encoding: .utf8)
        } catch {
            say("\(error)")
            return
        }
        window.close()
        onSave?()
    }

    @objc private func openFile() {
        NSWorkspace.shared.open(url)
    }
}

/// The settings, in their groups, and a field for each setting, in the
/// order of `settings`. Each group has a heading and a sentence saying what
/// it is for, and each field has its label beside it and what it holds
/// below it.
func settingsGrid() -> (NSGridView, [NSTextField]) {
    var rows: [[NSView]] = []
    var fields: [NSTextField] = []
    var headings: [Int] = []
    var notes: [Int] = []
    for group in settingGroups {
        let heading = NSTextField(labelWithString: group.title)
        heading.font = .boldSystemFont(ofSize: NSFont.systemFontSize)
        headings.append(rows.count)
        notes.append(rows.count + 1)
        rows += [[heading], [note(group.help, width: 480)]]
        for setting in group.settings {
            let field = NSTextField()
            field.placeholderString = setting.placeholder
            field.widthAnchor.constraint(equalToConstant: 360).isActive = true
            fields.append(field)
            notes.append(rows.count + 1)
            rows += [[NSTextField(labelWithString: setting.label), field],
                     [NSGridCell.emptyContentView, note(setting.help, width: 360)]]
        }
    }
    let grid = NSGridView(views: rows)
    grid.column(at: 0).xPlacement = .trailing
    grid.rowAlignment = .firstBaseline
    for index in headings {
        grid.row(at: index).topPadding = index == 0 ? 0 : 18
        // The heading and the group's note each span both columns.
        for row in [grid.row(at: index), grid.row(at: index + 1)] {
            row.mergeCells(in: NSRange(location: 0, length: 2))
            row.cell(at: 0).xPlacement = .leading
        }
    }
    for index in notes {
        grid.row(at: index).bottomPadding = 4
    }
    return (grid, fields)
}

/// Text that explains something, in a small font and the secondary color.
func note(_ text: String, width: CGFloat) -> NSTextField {
    let note = NSTextField(wrappingLabelWithString: text)
    note.font = .systemFont(ofSize: NSFont.smallSystemFontSize)
    note.textColor = .secondaryLabelColor
    note.preferredMaxLayoutWidth = width
    return note
}
