import AppKit

// pingbar: a menu bar indicator showing recent ping results to a fixed host
// as a 3x3 grid of colored circles. Each circle is one ping sample; the grid
// is a ring buffer, so the newest sample overwrites the oldest and the grid
// always shows the last nine results. Good samples update the grid without
// animation; poor or lost samples pulse as they land.

// MARK: - Configuration

/// The IPv4 address to ping.
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

/// The one's complement sum of big-endian 16-bit words used by ICMP.
func internetChecksum(_ bytes: [UInt8]) -> UInt16 {
    var sum = stride(from: 0, to: bytes.count, by: 2).reduce(UInt32(0)) { sum, index in
        let low = index + 1 < bytes.count ? bytes[index + 1] : 0
        return sum + (UInt32(bytes[index]) << 8 | UInt32(low))
    }
    while sum > 0xffff {
        sum = (sum & 0xffff) + (sum >> 16)
    }
    return ~UInt16(sum)
}

/// Sends one ICMP echo request at a time from an unprivileged ICMP datagram
/// socket, reporting each result on the main thread, with a pause between
/// samples. The socket is connected to the host, so it only receives packets
/// from the host, but those include replies to other processes' pings, each
/// with its IPv4 header in front, so a reply only counts when its identifier
/// and sequence number match the outstanding request. A request that cannot
/// be sent counts as lost once the deadline passes, like one that gets no
/// reply. All methods run on the main thread.
final class Pinger {
    var onSample: ((Sample) -> Void)?
    private let socket: Int32
    private let reader: DispatchSourceRead
    private let identifier = UInt16(truncatingIfNeeded: getpid())
    private var sequence: UInt16 = 0
    private var sentAt: DispatchTime?
    private var deadline: DispatchWorkItem?

    init() {
        let socket = Darwin.socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)
        guard socket >= 0 else {
            fatalError("could not open an ICMP socket: \(String(cString: strerror(errno)))")
        }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        guard inet_pton(AF_INET, host, &address.sin_addr) == 1 else {
            fatalError("\(host) is not an IPv4 address")
        }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(socket, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard connected == 0 else {
            fatalError("could not connect to \(host): \(String(cString: strerror(errno)))")
        }
        self.socket = socket
        reader = DispatchSource.makeReadSource(fileDescriptor: socket, queue: .main)
        reader.setEventHandler { [weak self] in
            self?.receive()
        }
        reader.resume()
    }

    func start() {
        send()
    }

    /// The identifier and sequence number fields, which a reply echoes back.
    private var echoFields: [UInt8] {
        [UInt8(identifier >> 8), UInt8(identifier & 0xff),
         UInt8(sequence >> 8), UInt8(sequence & 0xff)]
    }

    private func send() {
        sequence &+= 1
        var packet: [UInt8] = [8, 0, 0, 0] + echoFields  // type 8: echo request
        let checksum = internetChecksum(packet)
        packet[2] = UInt8(checksum >> 8)
        packet[3] = UInt8(checksum & 0xff)
        sentAt = .now()
        Darwin.send(socket, packet, packet.count, 0)
        let deadline = DispatchWorkItem { [weak self] in
            self?.finish(with: .lost)
        }
        self.deadline = deadline
        DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(pingDeadlineSeconds),
                                      execute: deadline)
    }

    private func receive() {
        var buffer = [UInt8](repeating: 0, count: 1500)
        let count = recv(socket, &buffer, buffer.count, 0)
        guard let sentAt, count > 0 else { return }
        let icmpStart = Int(buffer[0] & 0x0f) * 4
        guard count >= icmpStart + 8,
              buffer[icmpStart] == 0,  // type 0: echo reply
              buffer[icmpStart + 4 ..< icmpStart + 8].elementsEqual(echoFields) else {
            return
        }
        let nanoseconds = DispatchTime.now().uptimeNanoseconds - sentAt.uptimeNanoseconds
        finish(with: .reply(milliseconds: Double(nanoseconds) / 1_000_000))
    }

    private func finish(with sample: Sample) {
        deadline?.cancel()
        deadline = nil
        sentAt = nil
        onSample?(sample)
        DispatchQueue.main.asyncAfter(deadline: .now() + sampleGapSeconds) { [weak self] in
            self?.send()
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
        if intensity == 0 || overlays.isEmpty {
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

/// Cell layout inside the 18x18 point status image, measured from the top
/// left: 4 point circles on a 6 point pitch with a 1 point outer margin.
/// Slot 0 is the top left cell; slots fill left to right, then top to bottom.
func cellRect(_ slot: Int) -> NSRect {
    NSRect(x: 1 + CGFloat(slot % gridSide) * 6, y: 1 + CGFloat(slot / gridSide) * 6,
           width: 4, height: 4)
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var latestResultMenuItem: NSMenuItem!
    private var averageMenuItem: NSMenuItem!
    private var lostMenuItem: NSMenuItem!
    private var screenWarningMenuItem: NSMenuItem!
    private let pinger = Pinger()
    private let screenWarning = ScreenWarningController()
    private let preferences = UserDefaults(suiteName: preferencesDomain) ?? .standard
    private var screenWarningEnabled = true

    private var history: [Sample?] = Array(repeating: nil, count: historyLength)
    private var nextSlot = 0
    private var newestSlot: Int?

    /// The severities shown by the current status image.
    private var drawnSeverities: [Severity] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        preferences.register(defaults: [screenWarningEnabledPreferenceKey: true])
        screenWarningEnabled = preferences.bool(forKey: screenWarningEnabledPreferenceKey)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.toolTip = "Waiting for the first ping to \(host)"
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
        updateMenuItems()
        redraw()
        pinger.onSample = { [weak self] sample in
            self?.record(sample)
        }
        pinger.start()
    }

    private func record(_ sample: Sample) {
        let slot = nextSlot
        history[slot] = sample
        newestSlot = slot
        nextSlot = (slot + 1) % historyLength
        updateScreenWarning()
        switch sample {
        case .reply(let milliseconds):
            statusItem.button?.toolTip = String(format: "%@: %.1f ms", host, milliseconds)
        case .lost:
            statusItem.button?.toolTip = "\(host): no reply within \(pingDeadlineSeconds) s"
        }
        updateMenuItems()
        redraw()
        let newSeverity = severity(of: sample)
        if newSeverity != .good {
            pulse(slot: slot, color: color(for: newSeverity))
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

    private func redraw() {
        let severities = history.map(severity(of:))
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

    /// Covers the circle in the given slot with a circle that starts 1 point
    /// larger on every side and shrinks to the same size, then disappears.
    /// Core Animation runs the animation outside this process.
    private func pulse(slot: Int, color: NSColor) {
        guard let button = statusItem.button, let buttonLayer = button.layer,
              let imageRect = button.cell?.imageRect(forBounds: button.bounds) else {
            return
        }
        let circle = CAShapeLayer()
        circle.contentsScale = buttonLayer.contentsScale
        circle.frame = cellRect(slot).offsetBy(dx: imageRect.minX, dy: imageRect.minY)
        circle.path = CGPath(ellipseIn: circle.bounds, transform: nil)
        button.effectiveAppearance.performAsCurrentDrawingAppearance {
            circle.fillColor = color.cgColor
        }
        let shrink = CABasicAnimation(keyPath: "transform.scale")
        shrink.fromValue = 1.5
        shrink.toValue = 1
        shrink.duration = pulseDuration
        CATransaction.begin()
        CATransaction.setCompletionBlock {
            circle.removeFromSuperlayer()
        }
        buttonLayer.addSublayer(circle)
        circle.add(shrink, forKey: nil)
        CATransaction.commit()
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        updateMenuItems()
    }

    private func updateMenuItems() {
        if let newest = newestSlot, let sample = history[newest] {
            switch sample {
            case .reply(let milliseconds):
                latestResultMenuItem.title = String(format: "Last reply: %.1f ms", milliseconds)
            case .lost:
                latestResultMenuItem.title = "Last ping: no reply"
            }
        }
        latestResultMenuItem.isHidden = newestSlot == nil

        let replies = history.compactMap { sample -> Double? in
            if case .reply(let milliseconds)? = sample { return milliseconds }
            return nil
        }
        let recorded = history.compactMap { $0 }.count
        if !replies.isEmpty {
            let average = replies.reduce(0, +) / Double(replies.count)
            averageMenuItem.title = String(format: "Average: %.1f ms over %d replies",
                                           average, replies.count)
        }
        averageMenuItem.isHidden = replies.isEmpty

        let lost = recorded - replies.count
        if lost > 0 {
            lostMenuItem.title = "Lost: \(lost) of the last \(recorded)"
        }
        lostMenuItem.isHidden = lost == 0
        screenWarningMenuItem.state = screenWarningEnabled ? .on : .off
    }
}

// MARK: - One-off mode

/// Runs a single ping and prints the result; checks the sampling without
/// starting the menu bar app.
func runOnce() -> Never {
    let pinger = Pinger()
    pinger.onSample = { sample in
        switch sample {
        case .reply(let milliseconds):
            print(String(format: "reply from %@: %.1f ms", host, milliseconds))
            exit(0)
        case .lost:
            print("no reply from \(host) within \(pingDeadlineSeconds) s")
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
