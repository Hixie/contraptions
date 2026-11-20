import AppKit

// pingbar: a menu bar indicator showing recent ping results to a fixed host
// as a 3x3 grid of colored circles. Each circle is one ping sample; the grid
// is a ring buffer, so the newest sample overwrites the oldest and the grid
// always shows the last nine results. Good samples update the grid without
// animation; poor or lost samples pulse as they land.

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var latestResultMenuItem: NSMenuItem!
    private var averageMenuItem: NSMenuItem!
    private var lostMenuItem: NSMenuItem!
    private var screenWarningMenuItem: NSMenuItem!
    private let pinger = Pinger(host: host, gap: sampleGap, deadline: pingDeadline)
    private let screenWarning = ScreenWarningController()
    private let pulse = Pulse()
    private let preferences = UserDefaults(suiteName: preferencesDomain) ?? .standard
    private var screenWarningEnabled = true
    private var history = History()
    /// Whether the menu is open, in which case its lines follow new samples.
    private var menuIsOpen = false

    /// The severities shown by the current status image.
    private var drawnSeverities: [Severity] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        preferences.register(defaults: [screenWarningEnabledPreferenceKey: true])
        screenWarningEnabled = preferences.bool(forKey: screenWarningEnabledPreferenceKey)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let menu = NSMenu()
        menu.delegate = self
        menu.addItem(withTitle: "Ping to \(host)", action: nil, keyEquivalent: "")
        latestResultMenuItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")
        averageMenuItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")
        lostMenuItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")
        menu.addItem(.separator())
        screenWarningMenuItem = NSMenuItem(title: "Show Screen Vignette",
                                           action: #selector(toggleScreenWarning(_:)),
                                           keyEquivalent: "")
        screenWarningMenuItem.target = self
        menu.addItem(screenWarningMenuItem)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit pingbar",
                              action: #selector(NSApplication.terminate(_:)),
                              keyEquivalent: "q")
        quit.target = NSApp
        menu.addItem(quit)
        statusItem.menu = menu
        showHistory()
        pinger.onSample = { [weak self] sample in
            self?.record(sample)
        }
        pinger.runWhileDisplaysAwake { [weak self] in
            self?.forget()
        }
    }

    /// Forgets the samples while the displays sleep, since they would be
    /// stale by the time anyone could see them.
    private func forget() {
        history = History()
        showHistory()
    }

    private func record(_ sample: Sample) {
        let slot = history.record(sample)
        showHistory()
        let newSeverity = severity(of: sample)
        if newSeverity != .good, let button = statusItem.button, let buttonLayer = button.layer,
           let imageRect = button.cell?.imageRect(forBounds: button.bounds) {
            button.effectiveAppearance.performAsCurrentDrawingAppearance {
                pulse.play(in: buttonLayer,
                           frame: cellRect(slot).offsetBy(dx: imageRect.minX, dy: imageRect.minY),
                           color: color(for: newSeverity).cgColor)
            }
        }
    }

    private func showHistory() {
        updateScreenWarning()
        statusItem.button?.toolTip = history.toolTip(for: host)
        if menuIsOpen {
            updateMenuItems()
        }
        redraw()
    }

    private func updateScreenWarning() {
        screenWarning.update(redDotCount: screenWarningEnabled ? history.redCount : 0)
    }

    @objc private func toggleScreenWarning(_ sender: NSMenuItem) {
        screenWarningEnabled.toggle()
        preferences.set(screenWarningEnabled, forKey: screenWarningEnabledPreferenceKey)
        sender.state = screenWarningEnabled ? .on : .off
        updateScreenWarning()
    }

    private func redraw() {
        let severities = history.severities
        guard severities != drawnSeverities else { return }
        drawnSeverities = severities
        statusItem.button?.image = NSImage(size: NSSize(width: 18, height: 18), flipped: true) { _ in
            for (slot, severity) in severities.enumerated() {
                color(for: severity).setFill()
                NSBezierPath(ovalIn: cellRect(slot)).fill()
            }
            return true
        }
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        updateMenuItems()
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuIsOpen = true
    }

    func menuDidClose(_ menu: NSMenu) {
        menuIsOpen = false
    }

    private func updateMenuItems() {
        let lines: [(NSMenuItem, String?)] = [(latestResultMenuItem, history.latestText),
                                              (averageMenuItem, history.averageText),
                                              (lostMenuItem, history.lostText)]
        for (item, text) in lines {
            item.title = text ?? ""
            item.isHidden = text == nil
        }
        screenWarningMenuItem.state = screenWarningEnabled ? .on : .off
    }
}

// MARK: - One-off mode

/// Runs a single ping and prints the result; checks the sampling without
/// starting the menu bar app.
func runOnce() -> Never {
    let pinger = Pinger(host: host, gap: sampleGap, deadline: pingDeadline)
    pinger.onSample = { sample in
        switch sample {
        case .reply(let milliseconds):
            print(String(format: "reply from %@: %.1f ms", host, milliseconds))
            exit(0)
        case .lost:
            print("no reply from \(host)")
            exit(1)
        }
    }
    pinger.start()
    dispatchMain()
}

// MARK: - Entry point

if CommandLine.arguments.contains("--once") {
    runOnce()
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
