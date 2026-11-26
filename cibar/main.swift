import AppKit
import SwiftUI

// cibar: a menu bar indicator that follows every GitHub Actions job in the
// repositories whose webhook deliveries reach a relay, flies a plane when a
// workflow that a push started turns red or green, and starts a Codex
// session to fix each failure.

let supportFolder = URL(fileURLWithPath: NSHomeDirectory())
    .appendingPathComponent("Library/Application Support/\(applicationName)", isDirectory: true)
let stateURL = supportFolder.appendingPathComponent("state.json")
/// What cibar did about each job that still fails on a default branch.
let recordsURL = supportFolder.appendingPathComponent("sessions.json")

/// The application's main menu, which an app that lives in the menu bar
/// still needs for the standard editing commands to reach the settings
/// window's text fields.
func makeMainMenu() -> NSMenu {
    let main = NSMenu()
    let application = NSMenu()
    application.addItem(withTitle: "Quit \(applicationName)",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    let edit = NSMenu(title: "Edit")
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
    edit.addItem(.separator())
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
    edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
    edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
    edit.addItem(.separator())
    edit.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
    for submenu in [application, edit] {
        main.addItem(withTitle: "", action: nil, keyEquivalent: "").submenu = submenu
    }
    return main
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let configurationURL: URL
    private let bannerOnly: Bool
    private var configuration: Configuration?
    private var configurationProblem: String?
    private let relay = PersistentConnection()
    private var watch: Watch
    /// The deliveries that arrive while cibar reads from GitHub's API, to be
    /// taken in again once what it reads has been; nil while it is not.
    private var deliveriesWhileRefreshing: [Delivery]?
    /// The deliveries of the last minute, with when each arrived.
    private var recentDeliveries: [(arrived: Date, delivery: Delivery)] = []
    /// Whether cibar is reading from GitHub's API, or about to, whether it
    /// is waiting for the login shell to give it a token to, and whether it
    /// has been asked to again since it started.
    private var refreshing = false
    private var refreshProgress: RefreshProgress?
    private var awaitingToken = false
    private var refreshAgain = false
    /// Whether cibar reads from GitHub's API once the connection to the
    /// relay delivers something, which a relay that accepts connections but
    /// cannot deliver never does, so that it does not read over and over.
    private var refreshOnDelivery = false
    /// The token for GitHub's API, found once, since the login shell is slow
    /// to ask, when it is first needed.
    private var tokenLookup: Task<String?, Never>?
    /// Why cibar could not last read from GitHub's API.
    private var refreshProblem: String?
    /// The self-hosted runners of each owner, as GitHub's API last listed
    /// them, whether cibar is reading them, and why it last could not.
    private var runners: [String: [Runner]] = [:]
    private var largerRunners: [String: [LargerRunner]] = [:]
    private var readingRunners = false
    private var readRunnersAgain = false
    private var runnersProblem: String?
    private let flyby = FlybyController()
    /// How long after GitHub made them the latest deliveries on this
    /// connection arrived, when their delivery IDs say when that was.
    private var lateness = Lateness()
    /// Why the last delivery of an event cibar follows could not be read.
    private var deliveryProblem: String?
    private var statusItem: NSStatusItem!
    private var panel: DashboardPanel!
    private var dashboard: DashboardModel!
    /// Whether the dashboard is to be brought up to date once what is being
    /// taken in now has been.
    private var dashboardPending = false
    private var savedRevision: Int
    private var writer: StateWriter!
    private var records: [CodexRecord] = []
    /// The jobs whose Codex sessions are being started.
    private var sending: Set<JobKey> = []
    private var settingsWindow: SettingsWindow!
    /// What the indicator shows, so that it is redrawn only when that changes.
    private var shown: Indicator?

    private var includesPullRequests: Bool {
        get { UserDefaults.standard.bool(forKey: "includesPullRequests") }
        set { UserDefaults.standard.set(newValue, forKey: "includesPullRequests") }
    }

    private var showsRunners: Bool {
        get { UserDefaults.standard.bool(forKey: "showsRunners") }
        set { UserDefaults.standard.set(newValue, forKey: "showsRunners") }
    }

    private var startsSessions: Bool {
        get { UserDefaults.standard.bool(forKey: "startsSessions") }
        set { UserDefaults.standard.set(newValue, forKey: "startsSessions") }
    }

    init(configurationURL: URL, bannerOnly: Bool) {
        self.configurationURL = configurationURL
        self.bannerOnly = bannerOnly
        UserDefaults.standard.register(defaults: ["startsSessions": true])
        watch = Watch(restoring: bannerOnly ? WatchState() : readState(from: stateURL))
        records = bannerOnly ? [] : readState(from: recordsURL, or: [Readable<CodexRecord>]()).compactMap(\.element)
        savedRevision = watch.revision
        super.init()
        settingsWindow = SettingsWindow(
            url: configurationURL, startsSessions: { [weak self] in self?.startsSessions ?? false },
            setStartsSessions: { [weak self] in self?.setStartsSessions($0) })
        settingsWindow.onSave = { [weak self] in self?.reconfigure() }
        writer = StateWriter(
            snapshot: { [weak self] in
                guard let self else { return WatchState() }
                savedRevision = watch.revision
                return watch.state
            },
            write: { write($0, to: stateURL) })
        relay.onChange = { [weak self] in
            self?.relayChanged()
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = makeMainMenu()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        updateIndicator()
        if bannerOnly {
            // A status item made a moment ago has not been placed in the bar
            // yet, so the flights wait for the turn of the run loop in which
            // it is.
            DispatchQueue.main.async { [self] in
                var landed = 0
                for (text, trim) in [("cibar is red", flybyRed), ("cibar is green", flybyRacingGreen)] {
                    flyby.show(text, trim: trim, from: statusItem.button) {
                        landed += 1
                        if landed == 2 { NSApp.terminate(nil) }
                    }
                }
            }
            return
        }
        dashboard = DashboardModel(state: dashboardState(), actions: DashboardActions(
            setSessions: { [weak self] in self?.setStartsSessions($0) },
            send: { [weak self] in self?.send($0) },
            showSettings: { [weak self] in
                self?.closeDashboard()
                self?.settingsWindow.show()
            },
            flyBanners: { [weak self] in self?.flyBanners() },
            quit: { NSApp.terminate(nil) },
            refresh: { [weak self] in self?.refresh() },
            setIncludesPullRequests: { [weak self] in
                self?.includesPullRequests = $0
                self?.changed()
            },
            setShowsRunners: { [weak self] in
                self?.showsRunners = $0
                if $0 {
                    self?.readRunners()
                }
                self?.changed()
            },
            open: { [weak self] in
                self?.closeDashboard()
                NSWorkspace.shared.open($0)
            },
            close: { [weak self] in self?.panel.cancelOperation(nil) }))
        // The dashboard closes when the indicator is clicked again, when
        // Escape is pressed, and when anything else is clicked, which takes
        // the keyboard from it.
        let content = makeDashboard(dashboard, width: dashboardSize(screenHeight: 0).width)
        panel = DashboardPanel(showing: content)
        panel.onTitleBar = { content.titleBar = $0 }
        panel.delegate = self
        panel.onEscape = { [weak self] in self?.closeDashboard() }
        panel.onTearOff = { [weak self] in self?.statusItem.button?.highlight(false) }
        statusItem.button?.target = self
        statusItem.button?.action = #selector(toggleDashboard)
        // On release, as other panels in the menu bar open, so that the
        // indicator's own highlighting, which lasts while it is pressed, is
        // over before cibar highlights it for as long as the dashboard shows.
        statusItem.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(didWake), name: NSWorkspace.didWakeNotification, object: nil)
        connect()
        if !FileManager.default.fileExists(atPath: configurationURL.path) {
            settingsWindow.show()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        guard !bannerOnly else { return }
        writer.flush()
    }

    // MARK: The relay

    /// Reads the configuration again and opens a new connection to the
    /// relay.
    @objc private func connect() {
        do {
            let configuration = try Configuration(contentsOf: configurationURL)
            self.configuration = configuration
            configurationProblem = nil
            watch.owners = configuration.owners
            relay.start { [weak self] in
                let connection = RelayConnection(host: configuration.host, port: configuration.port)
                connection.onDelivery = { self?.received($0) }
                return connection
            }
        } catch {
            configuration = nil
            configurationProblem = "\(error)"
            log("cannot use the configuration: \(error)")
            relay.stop("the configuration cannot be used")
        }
        save()
    }

    /// Takes the settings as saved, opening a new connection to the relay,
    /// which reads from GitHub again, only when they change which relay to
    /// connect to or whose deliveries count.
    private func reconfigure() {
        guard let configuration, let saved = try? Configuration(contentsOf: configurationURL),
              saved.connectsLike(configuration) else {
            connect()
            return
        }
        self.configuration = saved
        changed()
    }

    @objc private func didWake() {
        relay.wake()
    }

    private func relayChanged() {
        switch relay.state {
        case .connected:
            log("connected to the relay")
        case .connecting:
            break
        case .disconnected(let reason, _):
            log("not connected to the relay: \(reason)")
        }
        // Jobs that finish while cibar is not connected are never
        // delivered, so which jobs are running is not known from before the
        // connection is up.
        if case .connected = relay.state {
            // What happened while cibar was not connected is read afresh.
            refreshOnDelivery = true
        } else {
            watch.forgetActiveJobs()
            lateness = Lateness()
        }
        changed()
    }

    private func received(_ delivery: Delivery) {
        deliveriesWhileRefreshing?.append(delivery)
        if refreshOnDelivery {
            refreshOnDelivery = false
            refresh()
        }
        let now = Date()
        recentDeliveries.removeAll { now.timeIntervalSince($0.arrived) > 60 }
        recentDeliveries.append((now, delivery))
        if let created = delivery.created {
            let now = Date()
            lateness.record(now.timeIntervalSince(created), at: now)
        }
        let changes: Changes
        do {
            changes = try watch.handle(event: delivery.event, body: delivery.body, at: delivery.created ?? Date())
            deliveryProblem = nil
        } catch {
            deliveryProblem = "A \(delivery.event) delivery could not be read: \(error)"
            log("a \(delivery.event) delivery could not be read: \(error)")
            changed()
            return
        }
        for turn in changes.turns {
            log("\(turn.workflowBranch.title) turned \(turn.red ? "red" : "green") in \(turn.runURL)")
        }
        let (sessions, planes) = responses(to: changes, codex: configuration?.codex, paused: !startsSessions,
                                           records: records)
        respond(to: sessions)
        for (text, red) in planes {
            flyby.show(text, trim: red ? flybyRed : flybyRacingGreen, from: statusItem.button)
        }
        if watch.revision != savedRevision {
            save()
        }
        changed()
    }

    /// Saves what cibar knows, forgetting what it did about each job that
    /// has since passed, or been forgotten.
    private func save() {
        writer.save()
        let failing = records.filter { record in
            watch.failures(on: record.job.workflowBranch).contains { $0.name == record.job.job }
        }
        if failing != records {
            records = failing
            write(records, to: recordsURL)
        }
    }

    /// Starts a session for each failure that calls for one, and notes why
    /// each other did not.
    private func respond(to sessions: [(job: JobKey, response: Response)]) {
        for (job, response) in sessions {
            switch response {
            case .start(let request):
                startSession(request, for: job)
            case .skip(let reason):
                log("not sending \(job.workflowBranch.title): \(describe(job: job.job)) to Codex: \(reason)")
                remember(.notSent(reason: reason), for: job)
            }
        }
    }

    private func remember(_ outcome: CodexRecord.Outcome, for job: JobKey) {
        records.removeAll { $0.job == job }
        records.append(CodexRecord(job: job, at: Date(), outcome: outcome))
        write(records, to: recordsURL)
        changed()
    }

    /// Starts a Codex session for a failing job that someone sent by hand,
    /// unless one is already starting for it.
    private func send(_ job: JobKey) {
        guard !sending.contains(job),
              let request = handSentRequest(for: job, in: watch, codex: configuration?.codex, records: records) else {
            return
        }
        log("sending \(request.target) to Codex by hand")
        startSession(request, for: job)
    }

    /// Starts a Codex session for `job`, off the main thread, as that takes
    /// a few seconds, and remembers when, and which thread it is.
    private func startSession(_ request: SessionRequest, for job: JobKey) {
        guard let tool = installedCodexTool() else {
            log("could not start a Codex session for \(request.target): the Codex app is not installed")
            remember(.notSent(reason: "Codex not installed"), for: job)
            return
        }
        sending.insert(job)
        changed()
        Thread { [weak self] in
            let started = Result {
                try startCodexSession(tool: tool, request) { url in
                    DispatchQueue.main.async { wakeInCodex(url) }
                }
            }
            DispatchQueue.main.async { self?.sessionStarted(request, for: job, started) }
        }.start()
    }

    private func sessionStarted(_ request: SessionRequest, for job: JobKey, _ started: Result<String, Error>) {
        sending.remove(job)
        switch started {
        case .success(let thread):
            log("started the Codex session \(thread) for \(request.target)")
            remember(.sent(thread: thread), for: job)
        case .failure(let error):
            log("could not start a Codex session for \(request.target): \(error)")
            remember(.notSent(reason: "Codex failed to start"), for: job)
        }
    }

    // MARK: The indicator

    /// Brings the indicator up to date, and the dashboard, if it shows,
    /// once the deliveries that have arrived together have all been taken
    /// in, rather than once for each.
    private func changed() {
        updateIndicator()
        guard panel.isVisible, !dashboardPending else { return }
        dashboardPending = true
        DispatchQueue.main.async { [weak self] in
            self?.dashboardPending = false
            self?.showDashboardState()
        }
    }

    private func updateIndicator() {
        guard let button = statusItem.button else { return }
        let connected: Bool
        if case .connected = relay.state { connected = true } else { connected = bannerOnly }
        let indicator = Indicator(connected: connected, defaultBranchesRed: watch.defaultBranchesRed,
                                  running: watch.runningCount(defaultBranchesOnly: !includesPullRequests),
                                  failing: watch.failingJobCount,
                                  paused: !startsSessions || configuration?.codex == nil)
        guard indicator != shown else { return }
        shown = indicator
        indicator.show(on: button)
    }

    private func setStartsSessions(_ starts: Bool) {
        startsSessions = starts
        log(starts ? "Codex sessions resumed" : "Codex sessions paused")
        changed()
    }

    // MARK: The dashboard

    @objc private func toggleDashboard() {
        // A torn-off dashboard comes to the front rather than closing.
        if panel.isTornOff {
            panel.orderFrontRegardless()
            panel.makeKey()
            return
        }
        if panel.isVisible {
            closeDashboard()
            return
        }
        guard let button = statusItem.button, let bar = button.window, let screen = bar.screen else { return }
        if showsRunners {
            readRunners()
        }
        showDashboardState()
        // Placed once, as it opens, so that the indicator changing width
        // while it shows does not move it.
        let indicator = bar.convertToScreen(button.convert(button.bounds, to: nil))
        panel.setFrame(dashboardFrame(below: indicator,
                                      size: dashboardSize(screenHeight: screen.visibleFrame.height),
                                      within: screen.visibleFrame),
                       display: true)
        panel.orderFrontRegardless()
        panel.makeKey()
        button.highlight(true)
    }

    /// Closes the dashboard, unless it has been torn off into a window of its
    /// own, which stays until it is closed.
    @objc private func closeDashboard() {
        guard !panel.isTornOff else { return }
        panel.orderOut(nil)
        statusItem.button?.highlight(false)
    }

    /// Closes the dashboard when anything else is clicked, which takes the
    /// keyboard from it, unless that is the indicator, whose click closes it.
    func windowDidResignKey(_ notification: Notification) {
        guard !panel.isTornOff, let button = statusItem.button, let bar = button.window,
              !bar.convertToScreen(button.convert(button.bounds, to: nil)).contains(NSEvent.mouseLocation) else {
            return
        }
        closeDashboard()
    }

    private func dashboardState() -> DashboardState {
        DashboardState(
            watch: watch, at: Date(),
            connection: DashboardState.Connection(relay.state, settingsProblem: configurationProblem),
            lateness: lateness,
            deliveryProblem: deliveryProblem, refreshing: refreshing, refreshProgress: refreshProgress,
            refreshProblem: awaitingToken ? "Waiting for the login shell to give a GitHub token" : refreshProblem,
            sessions: configuration?.codex == nil ? nil : startsSessions, codex: configuration?.codex,
            records: records, sending: sending, includesPullRequests: includesPullRequests, showsRunners: showsRunners,
            runners: runners.filter { watch.owners.isEmpty || watch.owners.contains($0.key) }
                .sorted { $0.key < $1.key }.flatMap(\.value),
            larger: largerRunners.filter { watch.owners.isEmpty || watch.owners.contains($0.key) }
                .sorted { $0.key < $1.key }.flatMap(\.value),
            runnersProblem: runnersProblem)
    }

    /// Brings the dashboard up to date.
    private func showDashboardState() {
        let state = dashboardState()
        guard state != dashboard.state || !panel.isVisible else { return }
        // Only what animates itself does: animating everything would
        // have the dashboard drawn afresh, all of it, for as long as
        // deliveries keep coming. And nothing does as the dashboard opens,
        // which shows how things are, not how they have changed since it
        // last showed.
        var transaction = Transaction()
        transaction.disablesAnimations = !panel.isVisible
        withTransaction(transaction) {
            dashboard.state = state
        }
    }

    // MARK: GitHub's API

    /// Reads what is going on from GitHub's API, and starts afresh from
    /// that, forgetting what no longer is: workflows that will never run
    /// again, and jobs whose ends were missed. What cibar knew of
    /// repositories it does not read is kept. Deliveries that arrive
    /// meanwhile, and in the minute before, in case GitHub's answers lag,
    /// are taken in as usual, and again once what was read has been, since
    /// they may be newer. Asked again while reading, it reads again after.
    private func refresh() {
        guard !refreshing else {
            refreshAgain = true
            return
        }
        guard let configuration else { return }
        refreshing = true
        awaitingToken = true
        changed()
        let owners = owners(of: configuration)
        // Quiet repositories are read when they are red, so that what made
        // them so can be found to be gone.
        let red = Set(watch.redWorkflowBranches.map(\.repository))
        // So that each failure can be sent to Codex by hand.
        let missing = watch.unknownFailingRuns
        let lookup = token()
        Task { @MainActor [weak self] in
            // The token is found before deliveries are set aside, so that
            // they do not pile up while the login shell runs.
            let token = await lookup.value
            guard let self else { return }
            awaitingToken = false
            guard let token else {
                refreshed(.failure(GitHubProblem(description:
                    "no GitHub token was found in GH_TOKEN, GITHUB_TOKEN, or gh auth token")))
                return
            }
            let since = Date() - 60
            deliveriesWhileRefreshing = recentDeliveries.filter { $0.arrived >= since }.map(\.delivery)
            changed()
            let result = await Result {
                try await GitHubAPI(token: token).prime(owners: Array(owners), alsoReading: red, missing: missing,
                                                        now: Date()) {
                    [weak self] done, total in
                    Task { @MainActor in self?.advanceRefresh(RefreshProgress(done: done, total: total)) }
                }
            }
            refreshed(result)
        }
    }

    /// Says how far the read has got, which the reports of it, each sent
    /// on its own, may not say in order.
    private func advanceRefresh(_ progress: RefreshProgress) {
        guard refreshing, progress.done >= refreshProgress?.done ?? 0 else { return }
        refreshProgress = progress
        changed()
    }

    private func refreshed(_ result: Result<PrimedState, Error>) {
        let deliveries = deliveriesWhileRefreshing ?? []
        deliveriesWhileRefreshing = nil
        refreshing = false
        refreshProgress = nil
        switch result {
        case .failure(let error):
            refreshProblem = "Could not read from GitHub: \(error)"
            log("could not read from GitHub: \(error)")
            // The token may have changed.
            tokenLookup = nil
        case .success(let read):
            refreshProblem = read.problems.isEmpty ? nil
                : "Could not read all of GitHub: " + read.problems.joined(separator: "; ")
            for problem in read.problems {
                log("could not read from GitHub: \(problem)")
            }
            let (fresh, failures) = Watch.refreshed(from: read, previous: watch, deliveries: deliveries)
            watch = fresh
            if case .connected = relay.state {} else {
                watch.forgetActiveJobs()
            }
            if showsRunners {
                readRunners()
            }
            log("read \(read.primed.count) runs and jobs of \(read.repositories.count) repositories from GitHub, "
                + "and \(deliveries.count) deliveries again")
            // What was read starts nothing, since sessions and planes are
            // for what happens while cibar listens; but a failure delivered
            // while reading, of a run cibar had not yet heard of, did.
            respond(to: responses(to: Changes(turns: [], failures: failures), codex: configuration?.codex,
                                  paused: !startsSessions, records: records).sessions)
            save()
        }
        if refreshAgain {
            refreshAgain = false
            refresh()
        }
        changed()
    }

    /// The owners whose repositories cibar reads: those the settings name,
    /// or, with none, those of the repositories it knows.
    private func owners(of configuration: Configuration) -> Set<String> {
        configuration.owners.isEmpty ? Set(watch.defaultBranches.keys.map(ownerOf)) : configuration.owners
    }

    /// The token for GitHub's API, looked up once, until a read fails.
    private func token() -> Task<String?, Never> {
        let lookup = tokenLookup ?? Task.detached { gitHubToken() }
        tokenLookup = lookup
        return lookup
    }

    /// Reads the owners' self-hosted runners from GitHub's API, for the
    /// runners board, as it opens, and after each read of the rest while it
    /// shows, since no delivery says when a runner comes, goes, or loses its
    /// connection. Asked to read again while reading, it reads again after.
    private func readRunners() {
        guard !readingRunners else {
            readRunnersAgain = true
            return
        }
        guard let configuration else { return }
        readingRunners = true
        let owners = owners(of: configuration)
        let lookup = token()
        Task { @MainActor [weak self] in
            guard let token = await lookup.value else {
                self?.readingRunners = false
                self?.readRunnersAgain = false
                return
            }
            let result = await Result { try await GitHubAPI(token: token).runners(of: Array(owners)) }
            guard let self else { return }
            readingRunners = false
            switch result {
            case .success(let read):
                runners.merge(read.runners) { $1 }
                largerRunners.merge(read.larger) { $1 }
                runnersProblem = read.problems.isEmpty ? nil
                    : "Could not read all the runners: " + read.problems.joined(separator: "; ")
            case .failure(let error):
                runnersProblem = "Could not read the runners: \(error)"
                log("could not read the runners from GitHub: \(error)")
                // The token may have changed.
                tokenLookup = nil
            }
            if readRunnersAgain {
                readRunnersAgain = false
                readRunners()
            }
            changed()
        }
    }

    private func flyBanners() {
        flyby.show("cibar is red", trim: flybyRed, from: statusItem.button)
        flyby.show("cibar is green", trim: flybyRacingGreen, from: statusItem.button)
    }
}

// MARK: - Entry point

var configurationPath = defaultConfigurationPath
var bannerOnly = false
var arguments = CommandLine.arguments.dropFirst()
while let argument = arguments.popFirst() {
    if argument == "--config", let path = arguments.popFirst() {
        configurationPath = path
    } else if argument == "--banner" {
        bannerOnly = true
    } else {
        fputs("usage: cibar [--config PATH] [--banner]\n", stderr)
        exit(2)
    }
}

// Only one copy follows the relay, so that each failure starts one session.
// A second copy waits for the first to quit, rather than exiting, so that
// launchd does not start it again and again.
if !bannerOnly {
    try? FileManager.default.createDirectory(at: supportFolder, withIntermediateDirectories: true)
    let path = supportFolder.appendingPathComponent("lock").path
    let lock = open(path, O_RDWR | O_CREAT, 0o600)
    guard lock >= 0 else {
        fputs("cibar: cannot open \(path): \(String(cString: strerror(errno)))\n", stderr)
        exit(1)
    }
    if flock(lock, LOCK_EX | LOCK_NB) != 0 {
        log("another copy of cibar is running; waiting for it to quit")
        while flock(lock, LOCK_EX) != 0 {
            guard errno == EINTR else {
                fputs("cibar: cannot lock \(path): \(String(cString: strerror(errno)))\n", stderr)
                exit(1)
            }
        }
    }
}

// The indicator outlives the terminal it was started from, and quits from its
// dashboard. Catching the signals, rather than ignoring them, leaves them at
// their defaults in the programs it runs. launchd stops cibar with SIGTERM,
// and a terminal with SIGINT or SIGQUIT; each quits it the way the dashboard
// does, so that what it knows is saved.
signal(SIGHUP) { _ in }
signal(SIGPIPE) { _ in }
let terminationSources = [SIGTERM, SIGINT, SIGQUIT].map { number in
    signal(number) { _ in }
    let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
    source.setEventHandler { NSApp.terminate(nil) }
    source.resume()
    return source
}

let app = NSApplication.shared
let delegate = AppDelegate(
    configurationURL: URL(fileURLWithPath: NSString(string: configurationPath).expandingTildeInPath),
    bannerOnly: bannerOnly)
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
