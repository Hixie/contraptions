import AppKit

// The pictures quotabar draws in the menu bar.

let glyphSize: CGFloat = 16
let glyphGap: CGFloat = 4

/// An account's picture: an outer ring that fills clockwise from the top as
/// the week passes, and an inner disc that fills clockwise as the quota is
/// used. A disc ahead of its ring means the quota is being used faster than
/// the week is passing. A digit in the middle gives how many banked resets
/// the account has that clear its weekly limit, or "+" for more than nine.
/// An account whose last check failed, or whose reset has passed since its
/// last good check, is drawn faintly, as a faint disc when no check has
/// succeeded yet; one that needs signing in again shows an exclamation mark.
func drawGlyph(_ status: Status?, now: Date, at origin: NSPoint) {
    let center = NSPoint(x: origin.x + glyphSize / 2, y: origin.y + glyphSize / 2)
    let faint = NSColor.black.withAlphaComponent(0.25)
    let stale = status?.problem != nil || status?.usage?.resets.map { $0 <= now } == true
    let solid = NSColor.black.withAlphaComponent(stale ? 0.5 : 1)
    func arc(radius: CGFloat, fraction: Double) -> NSBezierPath {
        let path = NSBezierPath()
        path.appendArc(withCenter: center, radius: radius, startAngle: 90,
                       endAngle: 90 - 360 * CGFloat(fraction), clockwise: true)
        path.lineWidth = 2
        return path
    }
    let disc = NSBezierPath(ovalIn: NSRect(x: center.x - 4.5, y: center.y - 4.5, width: 9, height: 9))
    faint.set()
    arc(radius: 7, fraction: 1).stroke()
    if status?.signInNeeded == true {
        NSColor.black.set()
        NSBezierPath(rect: NSRect(x: center.x - 1, y: center.y - 2, width: 2, height: 6)).fill()
        NSBezierPath(rect: NSRect(x: center.x - 1, y: center.y - 5, width: 2, height: 2)).fill()
        return
    }
    guard let usage = status?.usage else {
        if status?.problem != nil {
            disc.fill()
        }
        return
    }
    disc.fill()
    solid.set()
    if let resets = usage.resets {
        arc(radius: 7, fraction: weekElapsed(until: resets, now: now)).stroke()
    }
    let used = min(max(usage.percent / 100, 0), 1)
    if used >= 1 {
        disc.fill()
    } else if used > 0 {
        let pie = arc(radius: 4.5, fraction: used)
        pie.line(to: center)
        pie.close()
        pie.fill()
    }
    let banked = usage.bankedCount(at: now)
    if banked > 0 {
        drawDigit(banked > 9 ? "+" : "\(banked)", at: center)
    }
}

/// Draws `text` centred on `center` in reverse video against what is drawn
/// beneath it: clear over fully opaque parts, and darker over fainter ones.
func drawDigit(_ text: String, at center: NSPoint) {
    let font = NSFont.monospacedDigitSystemFont(ofSize: 7, weight: .bold)
    let string = NSAttributedString(string: text, attributes: [.font: font, .foregroundColor: NSColor.black])
    let size = string.size()
    let context = NSGraphicsContext.current!.cgContext
    context.saveGState()
    context.setBlendMode(.xor)
    string.draw(at: NSPoint(x: center.x - size.width / 2, y: center.y - size.height / 2))
    context.restoreGState()
}

/// The accounts' pictures side by side, with a warning after them: a bare
/// exclamation mark, which is not an account. With no accounts and no
/// warning, it is one empty circle.
func glyphImage(_ statuses: [Status?], now: Date, warning: Bool = false) -> NSImage {
    let circles = statuses.isEmpty && !warning ? [nil] : statuses
    let count = CGFloat(circles.count + (warning ? 1 : 0))
    let size = NSSize(width: count * glyphSize + (count - 1) * glyphGap, height: glyphSize)
    let image = NSImage(size: size, flipped: false) { _ in
        for (index, status) in circles.enumerated() {
            drawGlyph(status, now: now, at: NSPoint(x: CGFloat(index) * (glyphSize + glyphGap), y: 0))
        }
        if warning {
            let x = CGFloat(circles.count) * (glyphSize + glyphGap) + glyphSize / 2
            NSColor.black.set()
            NSBezierPath(rect: NSRect(x: x - 1.5, y: 5, width: 3, height: 10)).fill()
            NSBezierPath(rect: NSRect(x: x - 1.5, y: 0, width: 3, height: 3)).fill()
        }
        return true
    }
    image.isTemplate = true
    return image
}
