import AppKit

/// Symbols made so far, since the indicator asks for the same few over and
/// over.
private var symbols: [String: NSImage] = [:]

/// An SF Symbol, drawn in the menu bar's own colour, or in `color`. A
/// filled symbol, such as a tick in a filled circle, draws its mark in white
/// on `color`.
func symbol(_ name: String, _ description: String, color: NSColor? = nil) -> NSImage? {
    let key = "\(name) \(description) \(color.map { "\($0)" } ?? "")"
    if let image = symbols[key] {
        return image
    }
    var image = NSImage(systemSymbolName: name, accessibilityDescription: description)
    if let color {
        let colors = name.hasSuffix(".fill") ? [NSColor.white, color] : [color]
        image = image?.withSymbolConfiguration(.init(paletteColors: colors))
    }
    symbols[key] = image
    return image
}

/// What the indicator shows: a green tick when every workflow on every
/// default branch is green, or a red cross, followed by how many jobs last
/// failed on default branches, when one is red, or a warning triangle when
/// cibar is not connected to the relay; then how many jobs are running; then,
/// when no Codex sessions start, a pause sign.
struct Indicator: Equatable {
    var connected: Bool
    var defaultBranchesRed: Bool
    var running: Int
    var failing: Int
    var paused: Bool

    func show(on button: NSButton) {
        if !connected {
            button.image = symbol("exclamationmark.triangle", "not connected")
        } else if defaultBranchesRed {
            button.image = symbol("xmark.octagon.fill", "a default branch is red", color: .systemRed)
        } else {
            button.image = symbol("checkmark.circle.fill", "default branches are green", color: .systemGreen)
        }
        button.imagePosition = .imageLeft
        let font = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .medium)
        let title = NSMutableAttributedString()
        func add(_ text: String, color: NSColor? = nil) {
            var attributes: [NSAttributedString.Key: Any] = [.font: font]
            attributes[.foregroundColor] = color
            title.append(NSAttributedString(string: text, attributes: attributes))
        }
        func add(symbol name: String, _ description: String) {
            guard let image = NSImage(systemSymbolName: name, accessibilityDescription: description)?
                .withSymbolConfiguration(.init(pointSize: font.pointSize * 0.7, weight: .bold)) else { return }
            let attachment = NSTextAttachment()
            attachment.image = image
            attachment.bounds = NSRect(x: 0, y: (font.capHeight - image.size.height) / 2,
                                       width: image.size.width, height: image.size.height)
            title.append(NSAttributedString(attachment: attachment))
        }
        if connected && failing > 0 {
            add(" \(failing)", color: .systemRed)
        }
        add(connected && failing > 0 ? "  " : " ")
        add(symbol: "play.fill", "running")
        add(connected ? "\u{2009}\(running)" : "\u{2009}–")
        if paused {
            add("  ")
            add(symbol: "pause.fill", "Codex sessions paused")
        }
        button.attributedTitle = title
        button.toolTip = (connected
            ? "\(running) jobs running, \(failing) failing on default branches" : "not connected")
            + (paused ? "; Codex sessions paused" : "")
    }
}
