import AppKit

func screenWarningIntensity(redDotCount: Int) -> CGFloat {
    guard redDotCount > screenWarningThreshold else { return 0 }
    let warningRange = historyLength - screenWarningThreshold
    return min(1, CGFloat(redDotCount - screenWarningThreshold) / CGFloat(warningRange))
}

/// A red-black gradient along each edge of a screen-sized view, deeper and
/// more opaque as the intensity rises. The gradients are Core Animation
/// layers, which the window server draws, so the view keeps no screen-sized
/// image of its own.
final class ScreenWarningView: NSView {
    /// The gradients along the left, right, bottom, and top edges.
    private let gradients = (0..<4).map { _ in CAGradientLayer() }

    var intensity: CGFloat = 0 {
        didSet {
            if intensity != oldValue {
                updateGradients()
            }
        }
    }

    override init(frame: NSRect) {
        super.init(frame: frame)
        // Each gradient starts at its screen edge, in its layer's unit square.
        let starts = [CGPoint(x: 0, y: 0.5), CGPoint(x: 1, y: 0.5),
                      CGPoint(x: 0.5, y: 0), CGPoint(x: 0.5, y: 1)]
        let root = CALayer()
        for (gradient, start) in zip(gradients, starts) {
            gradient.startPoint = start
            gradient.endPoint = CGPoint(x: 1 - start.x, y: 1 - start.y)
            gradient.locations = [0, 0.28, 1]
            root.addSublayer(gradient)
        }
        // Giving the view its layer before turning layers on makes the view
        // host the layer, so AppKit never draws the view into an image.
        layer = root
        wantsLayer = true
        updateGradients()
    }

    required init?(coder: NSCoder) {
        fatalError("ScreenWarningView is not loaded from a nib")
    }

    private func updateGradients() {
        let depth = intensity > 0 ? min(bounds.width, bounds.height) * (0.09 + 0.19 * intensity) : 0
        let frames = [
            CGRect(x: bounds.minX, y: bounds.minY, width: depth, height: bounds.height),
            CGRect(x: bounds.maxX - depth, y: bounds.minY, width: depth, height: bounds.height),
            CGRect(x: bounds.minX, y: bounds.minY, width: bounds.width, height: depth),
            CGRect(x: bounds.minX, y: bounds.maxY - depth, width: bounds.width, height: depth),
        ]
        let opacity = 0.11 + 0.34 * intensity
        let colors = [NSColor(srgbRed: 0.42, green: 0.0, blue: 0.01, alpha: opacity),
                      NSColor(srgbRed: 0.55, green: 0.0, blue: 0.01, alpha: opacity * 0.35),
                      NSColor(srgbRed: 0.55, green: 0.0, blue: 0.01, alpha: 0)].map(\.cgColor)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for (gradient, frame) in zip(gradients, frames) {
            gradient.frame = frame
            gradient.colors = colors
        }
        CATransaction.commit()
    }
}

final class ScreenWarningController: NSObject {
    private struct Overlay {
        let panel: NSPanel
        let warningView: ScreenWarningView
    }

    private var intensity: CGFloat = 0
    private var overlays: [Overlay] = []

    override init() {
        super.init()
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(screenParametersChanged),
            name: NSApplication.didChangeScreenParametersNotification,
            object: nil
        )
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    /// Shows the warning at the intensity for the given number of red slots,
    /// or hides it.
    func update(redDotCount: Int) {
        intensity = screenWarningIntensity(redDotCount: redDotCount)
        if (intensity > 0) == overlays.isEmpty {
            rebuildOverlays()
        } else {
            overlays.forEach { $0.warningView.intensity = intensity }
        }
    }

    @objc private func screenParametersChanged() {
        rebuildOverlays()
    }

    private func rebuildOverlays() {
        overlays.forEach {
            $0.panel.orderOut(nil)
            $0.panel.close()
        }
        overlays.removeAll()

        guard intensity > 0 else { return }

        for screen in NSScreen.screens {
            let contentRect = NSRect(origin: .zero, size: screen.frame.size)
            let warningView = ScreenWarningView(frame: contentRect)
            warningView.intensity = intensity

            let panel = NSPanel(contentRect: contentRect,
                                styleMask: [.borderless, .nonactivatingPanel],
                                backing: .buffered,
                                defer: false,
                                screen: screen)
            panel.contentView = warningView
            panel.backgroundColor = .clear
            panel.isOpaque = false
            panel.hasShadow = false
            panel.ignoresMouseEvents = true
            panel.hidesOnDeactivate = false
            panel.isMovable = false
            panel.isMovableByWindowBackground = false
            panel.isExcludedFromWindowsMenu = true
            panel.isReleasedWhenClosed = false
            panel.animationBehavior = .none
            panel.level = .statusBar
            var collectionBehavior: NSWindow.CollectionBehavior = [
                .canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary,
            ]
            if #available(macOS 13.0, *) {
                collectionBehavior.insert(.canJoinAllApplications)
            }
            panel.collectionBehavior = collectionBehavior
            panel.orderFrontRegardless()

            overlays.append(Overlay(panel: panel, warningView: warningView))
        }
    }
}
