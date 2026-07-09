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

// MARK: - Menu bar app

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let pinger = Pinger()

    private var history: [Sample?] = Array(repeating: nil, count: historyLength)
    private var nextSlot = 0
    private var newestSlot: Int?

    private var pulseTimer: Timer?
    private var pulseStart: Date?

    func applicationDidFinishLaunching(_ notification: Notification) {
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
