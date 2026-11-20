import AppKit

// Tests for everything in pingbar but main.swift. Run them with `make
// test`. They ping the loopback interface, send one ping to a documentation
// address that nothing answers, and put fully transparent windows on screen
// that ignore the mouse and never become active.

var failures = 0

func expect(_ condition: Bool, _ message: String, line: Int = #line) {
    if !condition {
        print("FAIL (line \(line)): \(message)")
        failures += 1
    }
}

/// Runs the main run loop until `done` returns true, checking each time the
/// loop has handled its work and is about to wait for more.
func runMainLoop(until done: @escaping () -> Bool) {
    let observer = CFRunLoopObserverCreateWithHandler(nil, CFRunLoopActivity.beforeWaiting.rawValue,
                                                      true, 0) { _, _ in
        if done() {
            CFRunLoopStop(CFRunLoopGetMain())
        }
    }
    CFRunLoopAddObserver(CFRunLoopGetMain(), observer, .defaultMode)
    if !done() {
        CFRunLoopRun()
    }
    CFRunLoopRemoveObserver(CFRunLoopGetMain(), observer, .defaultMode)
}

/// Runs the main run loop for the given time.
func runMainLoop(for interval: DispatchTimeInterval) {
    var over = false
    DispatchQueue.main.asyncAfter(deadline: .now() + interval) { over = true }
    runMainLoop { over }
}

// MARK: - Checksum

do {
    // An echo request with identifier 0x1234 and sequence number 1. Its
    // words sum to 0x1a35, whose complement is 0xe5ca.
    let request: [UInt8] = [8, 0, 0, 0, 0x12, 0x34, 0x00, 0x01]
    expect(internetChecksum(request) == 0xe5ca, "the checksum of an echo request is the complement of its sum")
    var stamped = request
    stamped[2] = 0xe5
    stamped[3] = 0xca
    expect(internetChecksum(stamped) == 0, "a packet carrying its own checksum checks to zero")
    stamped[5] ^= 1
    expect(internetChecksum(stamped) != 0, "a packet with a changed bit does not check to zero")
    expect(internetChecksum([0x01]) == 0xfeff, "an odd final byte is the high half of a word")
    expect(internetChecksum([0xff, 0xff, 0x00, 0x02]) == 0xfffd, "a carry out of 16 bits wraps around")
}

// MARK: - Severity and layout

expect(severity(of: nil) == .empty, "an empty slot has no severity")
expect(severity(of: .reply(milliseconds: 99.9)) == .good, "a reply under 100 ms is good")
expect(severity(of: .reply(milliseconds: 100)) == .poor, "a reply of 100 ms is poor")
expect(severity(of: .reply(milliseconds: 299.9)) == .poor, "a reply under 300 ms is poor")
expect(severity(of: .reply(milliseconds: 300)) == .bad, "a reply of 300 ms is bad")
expect(severity(of: .lost) == .bad, "a lost sample is bad")

expect(cellRect(0) == NSRect(x: 1, y: 1, width: 4, height: 4), "slot 0 is the top left cell")
expect(cellRect(5) == NSRect(x: 13, y: 7, width: 4, height: 4), "slot 5 ends the middle row")
expect(cellRect(8) == NSRect(x: 13, y: 13, width: 4, height: 4), "slot 8 is the bottom right cell")

// MARK: - History

do {
    var history = History()
    expect(history.toolTip(for: "h") == "Waiting for the first ping to h", "before any sample the tooltip waits")
    expect(history.latestText == nil && history.averageText == nil && history.lostText == nil,
           "before any sample the menu shows no results")
    expect(history.severities == Array(repeating: .empty, count: historyLength), "every slot starts empty")

    expect(history.record(.reply(milliseconds: 20)) == 0, "the first sample goes in slot 0")
    expect(history.record(.lost) == 1, "the second sample goes in slot 1")
    expect(history.record(.reply(milliseconds: 40)) == 2, "the third sample goes in slot 2")
    expect(history.toolTip(for: "h") == "h: 40.0 ms", "the tooltip gives the newest reply's time")
    expect(history.latestText == "Last reply: 40.0 ms", "the menu gives the newest reply's time")
    expect(history.averageText == "Average: 30.0 ms over 2 replies", "the average leaves out lost samples")
    expect(history.lostText == "Lost: 1 of the last 3", "the loss count covers the samples recorded")
    expect(history.redCount == 1, "a lost sample is red")

    _ = history.record(.lost)
    expect(history.toolTip(for: "h") == "h: no reply", "the tooltip says when the newest sample was lost")
    expect(history.latestText == "Last ping: no reply", "the menu says when the newest sample was lost")

    for _ in 4..<historyLength {
        _ = history.record(.reply(milliseconds: 500))
    }
    expect(history.redCount == 7, "slow replies are red as well as lost samples")
    expect(history.record(.reply(milliseconds: 10)) == 0, "the tenth sample overwrites the oldest")
    expect(history.severities[0] == .good && history.severities[1] == .bad,
           "overwriting the oldest sample leaves the others")
    expect(history.toolTip(for: "h") == "h: 10.0 ms", "the overwritten slot holds the newest sample")
    expect(history.averageText == "Average: 364.3 ms over 7 replies", "the average covers the last nine samples")
    expect(history.lostText == "Lost: 2 of the last 9", "the loss count covers the last nine samples")
}

// MARK: - Screen warning

expect(screenWarningIntensity(redDotCount: 0) == 0, "no red slots give no warning")
expect(screenWarningIntensity(redDotCount: 4) == 0, "four red slots give no warning")
expect(screenWarningIntensity(redDotCount: 5) == 0.2, "five red slots give a faint warning")
expect(screenWarningIntensity(redDotCount: 9) == 1, "nine red slots give the strongest warning")

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)

/// The opacity of the view's pixels at the given points, rendered at one
/// pixel per point.
func alphas(of view: NSView, at points: [CGPoint]) -> [UInt8] {
    let width = Int(view.bounds.width), height = Int(view.bounds.height)
    guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
          let layer = view.layer else {
        expect(false, "the view can be rendered")
        return []
    }
    layer.render(in: context)
    let pixels = context.data!.assumingMemoryBound(to: UInt8.self)
    return points.map { point in
        // The bitmap's first row is the top of the image.
        let row = height - 1 - Int(point.y)
        return pixels[row * context.bytesPerRow + Int(point.x) * 4 + 3]
    }
}

do {
    let view = ScreenWarningView(frame: NSRect(x: 0, y: 0, width: 400, height: 300))
    // The edge depth is 0.09 + 0.19 * intensity of the shorter side: 38.4
    // points at intensity 0.2, and 84 points at intensity 1.
    let edges = [CGPoint(x: 0, y: 150), CGPoint(x: 399, y: 150), CGPoint(x: 200, y: 0), CGPoint(x: 200, y: 299)]
    let center = CGPoint(x: 200, y: 150)
    let inward = CGPoint(x: 60, y: 150)
    expect(alphas(of: view, at: edges + [center]).allSatisfy { $0 == 0 }, "without a warning the view is clear")
    view.intensity = 0.2
    let faint = alphas(of: view, at: edges + [center, inward])
    expect(faint[0..<4].allSatisfy { $0 > 0 }, "a warning tints every edge: \(faint)")
    expect(faint[4] == 0 && faint[5] == 0, "a faint warning leaves the middle clear: \(faint)")
    view.intensity = 1
    let strong = alphas(of: view, at: edges + [center, inward])
    expect(zip(strong[0..<4], faint[0..<4]).allSatisfy { $0 > $1 }, "a stronger warning is more opaque: \(strong)")
    expect(strong[5] > 0, "a stronger warning reaches further in: \(strong)")
    expect(strong[4] == 0, "even the strongest warning leaves the middle clear: \(strong)")
}

/// Puts the view on screen, in a fully transparent panel, and has it draw.
func show(_ view: NSView) -> NSPanel {
    let panel = NSPanel(contentRect: view.frame, styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
    panel.contentView = view
    panel.backgroundColor = .clear
    panel.isOpaque = false
    panel.hasShadow = false
    panel.ignoresMouseEvents = true
    panel.isReleasedWhenClosed = false
    panel.alphaValue = 0
    panel.orderFrontRegardless()
    panel.displayIfNeeded()
    CATransaction.flush()
    return panel
}

/// A view that AppKit draws into an image.
final class FilledView: NSView {
    override var isOpaque: Bool { false }
    override func draw(_ dirtyRect: NSRect) {
        NSColor(white: 0, alpha: 0.2).setFill()
        bounds.fill()
    }
}

do {
    // A drawn view holds an image the size of its window, which for the
    // warning would be the size of a screen.
    let frame = NSRect(x: 0, y: 0, width: 400, height: 300)
    let warning = ScreenWarningView(frame: frame)
    warning.intensity = 1
    let filled = FilledView(frame: frame)
    let panels = [show(warning), show(filled)]
    expect(filled.layer?.contents != nil, "a drawn view on screen holds an image")
    let layers = [warning.layer] + (warning.layer?.sublayers ?? [])
    expect(layers.count == 5 && layers.allSatisfy { $0 != nil && $0?.contents == nil },
           "the warning on screen holds no image")
    panels.forEach { $0.close() }
}

// MARK: - Pulse

do {
    let host = CALayer()
    let content = CALayer()
    host.addSublayer(content)
    let pulse = Pulse()
    let first = CGRect(x: 1, y: 1, width: 4, height: 4)
    pulse.play(in: host, frame: first, color: NSColor.systemRed.cgColor)
    expect(host.sublayers?.count == 2, "a pulse adds one layer")
    let circle = host.sublayers?.last
    expect(circle?.frame == first, "the pulse covers the given circle")
    expect(circle?.opacity == 0, "the pulse's layer is transparent except while its animation runs")
    let animation = circle?.animation(forKey: "pulse") as? CAAnimationGroup
    expect(animation?.duration == pulseDuration, "the pulse lasts its configured time")
    expect(animation?.isRemovedOnCompletion == true, "the animation goes away when it ends")
    let parts = (animation?.animations ?? []).compactMap { $0 as? CABasicAnimation }
    expect(parts.contains { $0.keyPath == "opacity" && $0.toValue as? Float == 1 && $0.duration == pulseDuration },
           "the circle is opaque while the animation runs")
    expect(parts.contains { $0.keyPath == "transform.scale" && $0.fromValue as? Double == 1.5
                            && $0.toValue as? Double == 1 && $0.duration == pulseDuration },
           "the circle shrinks from half as large again to its own size")

    host.addSublayer(CALayer())
    let second = CGRect(x: 7, y: 13, width: 4, height: 4)
    pulse.play(in: host, frame: second, color: NSColor.systemOrange.cgColor)
    expect(host.sublayers?.count == 3, "a second pulse reuses the layer")
    expect(host.sublayers?.last === circle, "the pulse is above everything added since the last one")
    expect(circle?.frame == second, "the second pulse covers its own circle")
}

// MARK: - Pinger

do {
    let loopback = Pinger(host: "127.0.0.1", gap: .milliseconds(50), deadline: .seconds(10))
    var arrivals: [(sample: Sample, at: UInt64)] = []
    var stopAfter: Int?
    loopback.onSample = { sample in
        arrivals.append((sample, DispatchTime.now().uptimeNanoseconds))
        if arrivals.count == stopAfter {
            loopback.stop()
        } else {
            // Starting while running, as when the displays wake, changes nothing.
            loopback.start()
        }
    }
    loopback.start()
    loopback.start()
    expect(arrivals.isEmpty, "a ping that is sent waits for its reply")
    runMainLoop { arrivals.count >= 4 }
    expect(arrivals.allSatisfy { if case .reply = $0.sample { true } else { false } },
           "the loopback interface answers")
    let gaps = zip(arrivals.dropFirst(), arrivals).map { Double($0.at - $1.at) / 1_000_000 }
    // Each gap is the 50 ms pause plus a round trip, less however long the
    // test took to note the time; an extra ping would make it far shorter.
    expect(gaps.allSatisfy { $0 > 25 }, "starting while running neither adds pings nor cuts the pause short: \(gaps) ms")

    loopback.stop()
    runMainLoop(for: .milliseconds(300))
    expect(arrivals.count == 4, "no samples arrive while stopped")

    stopAfter = 6
    loopback.start()
    runMainLoop { arrivals.count >= 6 }
    runMainLoop(for: .milliseconds(300))
    expect(arrivals.count == 6, "stopping while a sample is reported prevents the next ping")

    stopAfter = nil
    loopback.start()
    loopback.stop()
    runMainLoop(for: .milliseconds(300))
    expect(arrivals.count == 6, "the reply to a ping outstanding when sampling stops is ignored")
}

do {
    // Nothing routes to 0.0.0.0, so the send fails.
    let unroutable = Pinger(host: "0.0.0.0", gap: .seconds(60), deadline: .seconds(60))
    var samples: [Sample] = []
    unroutable.onSample = { samples.append($0) }
    let started = DispatchTime.now().uptimeNanoseconds
    unroutable.start()
    expect(samples.isEmpty, "a lost sample is reported from the main queue, not from start()")
    runMainLoop { !samples.isEmpty }
    let elapsed = Double(DispatchTime.now().uptimeNanoseconds - started) / 1_000_000
    expect(samples.count == 1 && severity(of: samples.first) == .bad && elapsed < 1000,
           "a ping that cannot be sent counts as lost at once, not at the deadline: \(elapsed) ms")
    unroutable.stop()
}

do {
    // 192.0.2.1 is reserved for documentation, so nothing answers it.
    let silent = Pinger(host: "192.0.2.1", gap: .seconds(60), deadline: .milliseconds(100))
    var samples: [Sample] = []
    silent.onSample = { samples.append($0) }
    silent.start()
    runMainLoop { !samples.isEmpty }
    expect(severity(of: samples.first) == .bad, "a ping that gets no reply counts as lost")
    silent.stop()
}

do {
    // The notifications are posted only within this process. The first
    // tells the pinger the displays are awake, whether or not they are.
    let follower = Pinger(host: "127.0.0.1", gap: .milliseconds(50), deadline: .seconds(10))
    var count = 0
    var sleeps = 0
    follower.onSample = { _ in count += 1 }
    let observers = follower.runWhileDisplaysAwake { sleeps += 1 }
    let notifications = NSWorkspace.shared.notificationCenter
    notifications.post(name: NSWorkspace.screensDidWakeNotification, object: nil)
    runMainLoop { count >= 2 }
    notifications.post(name: NSWorkspace.screensDidSleepNotification, object: nil)
    expect(sleeps == 1, "the displays going to sleep is reported")
    let atSleep = count
    runMainLoop(for: .milliseconds(300))
    expect(count == atSleep, "no samples arrive while the displays sleep")
    notifications.post(name: NSWorkspace.screensDidWakeNotification, object: nil)
    runMainLoop { count >= atSleep + 2 }
    expect(sleeps == 1, "the displays waking is not reported as sleep")
    observers.forEach(notifications.removeObserver)
    follower.stop()
}

if failures > 0 {
    print("\(failures) failure(s)")
    exit(1)
}
print("all tests passed")
