import AppKit

enum Severity {
    case empty   // no sample recorded in this slot yet
    case good
    case poor
    case bad
}

func severity(of sample: Sample?) -> Severity {
    switch sample {
    case nil:
        return .empty
    case .lost?:
        return .bad
    case .reply(let milliseconds)?:
        if milliseconds < goodThresholdMs { return .good }
        if milliseconds < poorThresholdMs { return .poor }
        return .bad
    }
}

func color(for severity: Severity) -> NSColor {
    switch severity {
    case .empty: return .tertiaryLabelColor
    case .good: return .systemGreen
    case .poor: return .systemOrange
    case .bad: return .systemRed
    }
}

/// Cell layout inside the 18x18 point status image, measured from the top
/// left: 4 point circles on a 6 point pitch with a 1 point outer margin.
/// Slot 0 is the top left cell; slots fill left to right, then top to bottom.
func cellRect(_ slot: Int) -> NSRect {
    NSRect(x: 1 + CGFloat(slot % gridSide) * 6, y: 1 + CGFloat(slot / gridSide) * 6,
           width: 4, height: 4)
}

/// The last `historyLength` samples, one per grid slot. It is a ring
/// buffer: each new sample overwrites the oldest.
struct History {
    private(set) var samples: [Sample?] = Array(repeating: nil, count: historyLength)
    private var nextSlot = 0
    private var newestSlot: Int?

    /// Records a sample in the slot of the oldest one, and returns that slot.
    mutating func record(_ sample: Sample) -> Int {
        let slot = nextSlot
        samples[slot] = sample
        newestSlot = slot
        nextSlot = (slot + 1) % historyLength
        return slot
    }

    var severities: [Severity] {
        samples.map(severity(of:))
    }

    var redCount: Int {
        severities.filter { $0 == .bad }.count
    }

    private var newest: Sample? {
        newestSlot.flatMap { samples[$0] }
    }

    private var replies: [Double] {
        samples.compactMap { sample in
            if case .reply(let milliseconds)? = sample { return milliseconds }
            return nil
        }
    }

    func toolTip(for host: String) -> String {
        switch newest {
        case nil:
            return "Waiting for the first ping to \(host)"
        case .reply(let milliseconds)?:
            return String(format: "%@: %.1f ms", host, milliseconds)
        case .lost?:
            return "\(host): no reply"
        }
    }

    /// The menu's line about the newest sample, if there is one.
    var latestText: String? {
        switch newest {
        case nil:
            return nil
        case .reply(let milliseconds)?:
            return String(format: "Last reply: %.1f ms", milliseconds)
        case .lost?:
            return "Last ping: no reply"
        }
    }

    /// The menu's line about the average reply time, if there are replies.
    var averageText: String? {
        let replies = replies
        guard !replies.isEmpty else { return nil }
        return String(format: "Average: %.1f ms over %d replies",
                      replies.reduce(0, +) / Double(replies.count), replies.count)
    }

    /// The menu's line about lost samples, if there are any.
    var lostText: String? {
        let recorded = samples.compactMap { $0 }.count
        let lost = recorded - replies.count
        return lost > 0 ? "Lost: \(lost) of the last \(recorded)" : nil
    }
}

/// Covers a circle of the grid with a circle that starts 1 point larger on
/// every side and shrinks to the same size, then disappears. Core Animation
/// runs the animation outside this process. One layer serves every pulse; it
/// is fully transparent except while its animation runs, so nothing needs
/// to remove it afterwards.
final class Pulse {
    private let circle = CAShapeLayer()

    init() {
        circle.opacity = 0
    }

    func play(in host: CALayer, frame: CGRect, color: CGColor) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        // Moves the circle above anything added to the host since the last
        // pulse.
        host.addSublayer(circle)
        circle.contentsScale = host.contentsScale
        circle.frame = frame
        circle.path = CGPath(ellipseIn: circle.bounds, transform: nil)
        circle.fillColor = color
        let shrink = CABasicAnimation(keyPath: "transform.scale")
        shrink.fromValue = 1.5
        shrink.toValue = 1
        shrink.duration = pulseDuration
        let show = CABasicAnimation(keyPath: "opacity")
        show.fromValue = 1
        show.toValue = 1
        show.duration = pulseDuration
        let pulse = CAAnimationGroup()
        pulse.animations = [shrink, show]
        pulse.duration = pulseDuration
        circle.add(pulse, forKey: "pulse")
        CATransaction.commit()
    }
}
