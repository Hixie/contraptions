import AppKit

// pingbar: a menu bar indicator showing recent ping results to a fixed host
// as a 3x3 grid of colored circles. Each circle is one ping sample; the grid
// is a ring buffer, so the newest sample overwrites the oldest and the grid
// always shows the last nine results. Good samples update the grid without
// animation; poor or lost samples pulse as they land.

// MARK: - Configuration

let host = "8.8.8.8"

/// Pause between the end of one ping and the start of the next.
let sampleGapSeconds: TimeInterval = 2.0

/// How long ping waits for a reply before the sample counts as lost.
let pingDeadlineSeconds = 2

/// Replies faster than this render green.
let goodThresholdMs = 100.0

/// Replies faster than this (but at least goodThresholdMs) render orange;
/// slower replies and lost pings render red.
let poorThresholdMs = 300.0

let gridSide = 3
let historyLength = gridSide * gridSide

/// How long the pulse animation on a newly landed poor or lost sample runs.
let pulseDuration: TimeInterval = 0.8

/// The screen-edge warning appears once more than this many grid slots are red.
let screenWarningThreshold = 4

let preferencesDomain = "local.pingbar"
let screenWarningEnabledPreferenceKey = "screenWarningEnabled"

// MARK: - Samples

enum Sample {
    case reply(milliseconds: Double)
    case lost
}

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

// MARK: - Ping sampling

let pingPath = "/sbin/ping"
let pingArguments = ["-n", "-c", "1", "-t", String(pingDeadlineSeconds), host]

func parsePing(exitStatus: Int32, output: Data) -> Sample {
    guard exitStatus == 0, let text = String(data: output, encoding: .utf8) else {
        return .lost
    }
    // A reply line looks like:
    // 64 bytes from 8.8.8.8: icmp_seq=0 ttl=117 time=13.418 ms
    guard let range = text.range(of: #"time=([0-9.]+) ms"#, options: .regularExpression) else {
        return .lost
    }
    let value = text[range].dropFirst("time=".count).dropLast(" ms".count)
    guard let milliseconds = Double(String(value)) else {
        return .lost
    }
    return .reply(milliseconds: milliseconds)
}

/// Runs one ping at a time, reporting each result on the main thread, with a
/// pause between samples. All methods run on the main thread.
final class Pinger {
    var onSample: ((Sample) -> Void)?
    private var current: Process?

    func start() {
        launch()
    }

    private func launch() {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: pingPath)
        process.arguments = pingArguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] finished in
            let output = pipe.fileHandleForReading.readDataToEndOfFile()
            let sample = parsePing(exitStatus: finished.terminationStatus, output: output)
            DispatchQueue.main.async {
                self?.finish(with: sample)
            }
        }
        current = process
        do {
            try process.run()
        } catch {
            current = nil
            finish(with: .lost)
        }
    }

    private func finish(with sample: Sample) {
        current = nil
        onSample?(sample)
        DispatchQueue.main.asyncAfter(deadline: .now() + sampleGapSeconds) { [weak self] in
            self?.launch()
        }
    }
}

// MARK: - Screen warning

func screenWarningIntensity(redDotCount: Int) -> CGFloat {
    guard redDotCount > screenWarningThreshold else { return 0 }
    let warningRange = historyLength - screenWarningThreshold
    return min(1, CGFloat(redDotCount - screenWarningThreshold) / CGFloat(warningRange))
}

private final class ScreenWarningView: NSView {
    var intensity: CGFloat = 0 {
        didSet {
            if intensity != oldValue {
                needsDisplay = true
            }
        }
    }

    override var isOpaque: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        guard intensity > 0, let context = NSGraphicsContext.current?.cgContext else {
            return
        }

        let edgeDepth = min(bounds.width, bounds.height) * (0.09 + 0.19 * intensity)
        let edgeOpacity = 0.11 + 0.34 * intensity
        let outerColor = NSColor(srgbRed: 0.42, green: 0.0, blue: 0.01,
                                 alpha: edgeOpacity)
        let shoulderColor = NSColor(srgbRed: 0.55, green: 0.0, blue: 0.01,
                                    alpha: edgeOpacity * 0.35)
        let clearColor = NSColor(srgbRed: 0.55, green: 0.0, blue: 0.01, alpha: 0)
        let colors = [outerColor.cgColor, shoulderColor.cgColor, clearColor.cgColor]
        guard let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                                        colors: colors as CFArray,
                                        locations: [0, 0.28, 1]) else {
            return
        }

        draw(gradient, in: NSRect(x: bounds.minX, y: bounds.minY,
                                  width: edgeDepth, height: bounds.height),
             from: CGPoint(x: bounds.minX, y: bounds.midY),
             to: CGPoint(x: bounds.minX + edgeDepth, y: bounds.midY),
             using: context)
        draw(gradient, in: NSRect(x: bounds.maxX - edgeDepth, y: bounds.minY,
                                  width: edgeDepth, height: bounds.height),
             from: CGPoint(x: bounds.maxX, y: bounds.midY),
             to: CGPoint(x: bounds.maxX - edgeDepth, y: bounds.midY),
             using: context)
        draw(gradient, in: NSRect(x: bounds.minX, y: bounds.minY,
                                  width: bounds.width, height: edgeDepth),
             from: CGPoint(x: bounds.midX, y: bounds.minY),
             to: CGPoint(x: bounds.midX, y: bounds.minY + edgeDepth),
             using: context)
        draw(gradient, in: NSRect(x: bounds.minX, y: bounds.maxY - edgeDepth,
                                  width: bounds.width, height: edgeDepth),
             from: CGPoint(x: bounds.midX, y: bounds.maxY),
             to: CGPoint(x: bounds.midX, y: bounds.maxY - edgeDepth),
             using: context)
    }

    private func draw(_ gradient: CGGradient, in rect: NSRect,
                      from start: CGPoint, to end: CGPoint,
                      using context: CGContext) {
        context.saveGState()
        context.clip(to: rect)
        context.drawLinearGradient(gradient, start: start, end: end, options: [])
        context.restoreGState()
    }
}

private final class ScreenWarningController: NSObject {
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

    func update(redDotCount: Int) {
        intensity = screenWarningIntensity(redDotCount: redDotCount)
        guard intensity > 0 else {
            overlays.forEach { $0.panel.orderOut(nil) }
            return
        }

        if overlays.isEmpty {
            rebuildOverlays()
            return
        }

        overlays.forEach {
            $0.warningView.intensity = intensity
            $0.panel.orderFrontRegardless()
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

// MARK: - Menu bar app

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let pinger = Pinger()
    private let screenWarning = ScreenWarningController()
    private let preferences = UserDefaults(suiteName: preferencesDomain) ?? .standard
    private var screenWarningEnabled = true

    private var history: [Sample?] = Array(repeating: nil, count: historyLength)
    private var nextSlot = 0
    private var newestSlot: Int?

    private var pulseTimer: Timer?
    private var pulseStart: Date?

    func applicationDidFinishLaunching(_ notification: Notification) {
        preferences.register(defaults: [screenWarningEnabledPreferenceKey: true])
        screenWarningEnabled = preferences.bool(forKey: screenWarningEnabledPreferenceKey)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.toolTip = "Waiting for the first ping to \(host)"
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        redraw(pulseProgress: nil)
        pinger.onSample = { [weak self] sample in
            self?.record(sample)
        }
        pinger.start()
    }

    private func record(_ sample: Sample) {
        history[nextSlot] = sample
        newestSlot = nextSlot
        nextSlot = (nextSlot + 1) % historyLength
        updateScreenWarning()
        switch sample {
        case .reply(let milliseconds):
            statusItem.button?.toolTip = String(format: "%@: %.1f ms", host, milliseconds)
        case .lost:
            statusItem.button?.toolTip = "\(host): no reply within \(pingDeadlineSeconds) s"
        }
        if severity(of: sample) == .good {
            stopPulse()
            redraw(pulseProgress: nil)
        } else {
            startPulse()
        }
    }

    private func updateScreenWarning() {
        let redDotCount = history.reduce(into: 0) { count, sample in
            if severity(of: sample) == .bad {
                count += 1
            }
        }
        screenWarning.update(redDotCount: screenWarningEnabled ? redDotCount : 0)
    }

    @objc private func toggleScreenWarning(_ sender: NSMenuItem) {
        screenWarningEnabled.toggle()
        preferences.set(screenWarningEnabled, forKey: screenWarningEnabledPreferenceKey)
        sender.state = screenWarningEnabled ? .on : .off
        updateScreenWarning()
    }

    private func startPulse() {
        pulseTimer?.invalidate()
        pulseStart = Date()
        let timer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            self?.pulseTick()
        }
        RunLoop.main.add(timer, forMode: .common)
        pulseTimer = timer
        pulseTick()
    }

    private func pulseTick() {
        guard let start = pulseStart else { return }
        let progress = Date().timeIntervalSince(start) / pulseDuration
        if progress >= 1.0 {
            stopPulse()
            redraw(pulseProgress: nil)
        } else {
            redraw(pulseProgress: progress)
        }
    }

    private func stopPulse() {
        pulseTimer?.invalidate()
        pulseTimer = nil
        pulseStart = nil
    }

    /// Cell layout inside the 18x18 point status image: 4 point circles on a
    /// 6 point pitch with a 1 point outer margin. Slot 0 is the top left
    /// cell; slots fill left to right, then top to bottom.
    private func redraw(pulseProgress: Double?) {
        let snapshot = history
        let newest = newestSlot
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: false) { _ in
            for slot in 0..<historyLength {
                let column = slot % gridSide
                let row = slot / gridSide
                var rect = NSRect(x: 1 + CGFloat(column) * 6,
                                  y: 13 - CGFloat(row) * 6,
                                  width: 4,
                                  height: 4)
                if slot == newest, let progress = pulseProgress {
                    let grow = CGFloat(1.0 - progress)
                    rect = rect.insetBy(dx: -grow, dy: -grow)
                }
                color(for: severity(of: snapshot[slot])).setFill()
                NSBezierPath(ovalIn: rect).fill()
            }
            return true
        }
        statusItem.button?.image = image
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        menu.addItem(withTitle: "Ping to \(host)", action: nil, keyEquivalent: "")
        if let newest = newestSlot, let sample = history[newest] {
            switch sample {
            case .reply(let milliseconds):
                menu.addItem(withTitle: String(format: "Last reply: %.1f ms", milliseconds),
                             action: nil, keyEquivalent: "")
            case .lost:
                menu.addItem(withTitle: "Last ping: no reply", action: nil, keyEquivalent: "")
            }
        }
        let replies = history.compactMap { sample -> Double? in
            if case .reply(let milliseconds)? = sample { return milliseconds }
            return nil
        }
        let recorded = history.compactMap { $0 }.count
        if !replies.isEmpty {
            let average = replies.reduce(0, +) / Double(replies.count)
            menu.addItem(withTitle: String(format: "Average: %.1f ms over %d replies",
                                           average, replies.count),
                         action: nil, keyEquivalent: "")
        }
        let lost = recorded - replies.count
        if lost > 0 {
            menu.addItem(withTitle: "Lost: \(lost) of the last \(recorded)",
                         action: nil, keyEquivalent: "")
        }
        menu.addItem(.separator())
        let screenWarningItem = NSMenuItem(title: "Show Screen Vignette",
                                           action: #selector(toggleScreenWarning(_:)),
                                           keyEquivalent: "")
        screenWarningItem.target = self
        screenWarningItem.state = screenWarningEnabled ? .on : .off
        menu.addItem(screenWarningItem)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit pingbar",
                              action: #selector(NSApplication.terminate(_:)),
                              keyEquivalent: "q")
        quit.target = NSApp
        menu.addItem(quit)
    }
}

// MARK: - One-off mode

/// Runs a single ping and prints the result; checks the sampling and parsing
/// without starting the menu bar app.
func runOnce() -> Never {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: pingPath)
    process.arguments = pingArguments
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
    } catch {
        print("could not launch \(pingPath): \(error.localizedDescription)")
        exit(1)
    }
    let output = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    switch parsePing(exitStatus: process.terminationStatus, output: output) {
    case .reply(let milliseconds):
        print(String(format: "reply from %@: %.1f ms", host, milliseconds))
        exit(0)
    case .lost:
        print("no reply from \(host) within \(pingDeadlineSeconds) s")
        exit(1)
    }
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
