import AppKit
import Network

// quotabar: a menu bar indicator showing, for each Claude account, how much
// of its weekly quota is used and how much of the week is left.

/// The usage endpoint cannot tell quotabar when usage changes, so quotabar
/// asks this often.
let checkInterval: TimeInterval = 5 * 60

@MainActor
final class Monitor: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private lazy var watcher = Watcher(
        service: Service(), store: AccountStore(item: KeychainItem(service: "quotabar", account: "accounts")),
        open: { NSWorkspace.shared.open($0) }, changed: { [weak self] in self?.update() })
    private var timer: Timer?
    private let paths = NWPathMonitor()
    /// The sign-in note the menu showed when it last opened.
    private var shownNote: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        watcher.load()
        watcher.checkAll()
        paths.pathUpdateHandler = { path in
            MainActor.assumeIsolated { _ = self.watcher.setOnline(path.status != .unsatisfied) }
        }
        paths.start(queue: .main)
        // Each tick also redraws, so the rings advance even while a check
        // waits for the network.
        timer = Timer.scheduledTimer(withTimeInterval: checkInterval, repeats: true) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { _ in
            MainActor.assumeIsolated { self.tick() }
        }
    }

    private func tick() {
        watcher.checkAll()
        update()
    }

    private func update() {
        let picture = menuBarPicture(of: watcher.roster, loadProblem: watcher.loadProblem,
                                     saveProblem: watcher.saveProblem, now: Date(), in: .current)
        statusItem.button?.image = picture.image
        statusItem.button?.toolTip = picture.toolTip
    }

    func menuDidClose(_ menu: NSMenu) {
        watcher.dismissSignInNote(shownNote)
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        shownNote = watcher.signInNote
        fill(menu, from: watcher, now: Date(), in: .current, locale: .current) { NSApp.terminate(nil) }
    }

    /// Quitting waits for token refreshes under way to end and be saved.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        watcher.close()
        guard watcher.refreshing > 0 else {
            return .terminateNow
        }
        watcher.whenRefreshesEnd { NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}

// launchd restarts quotabar when it exits with a failure, so finding another
// quotabar running is a success. The lock is kept where macOS does not
// delete files to free space.
let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    .appendingPathComponent("quotabar")
do {
    try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
    guard try takeLock(at: support.appendingPathComponent("lock").path) else {
        print("quotabar is already running")
        exit(0)
    }
} catch {
    fputs("quotabar: \(error)\n", stderr)
    exit(1)
}

let app = NSApplication.shared
let monitor = MainActor.assumeIsolated { Monitor() }
app.delegate = monitor
app.setActivationPolicy(.accessory)
// launchd stops quotabar with SIGTERM, which then quits the way the Quit
// item does, waiting for token refreshes under way.
signal(SIGTERM, SIG_IGN)
let terminations = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
terminations.setEventHandler { NSApp.terminate(nil) }
terminations.resume()
app.run()
