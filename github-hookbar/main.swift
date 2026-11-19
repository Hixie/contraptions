import AppKit
import Network
import os

// github-hookbar: a menu bar indicator that keeps a connection open to a relay of
// GitHub webhook deliveries, runs a command for each pull request that a
// chosen author opens, and keeps a list of those pull requests until they
// are closed. It can also follow a stream of server-sent events and keep a
// value from them in a file.

// MARK: - Constants

let defaultConfigurationPath = "~/.config/github-hookbar/config.ini"
let pullRequestListPath = NSHomeDirectory() + "/.cache/github-prs.tsv"

/// How many handled pull requests the menu lists.
let recentLength = 10

/// GitHub caps webhook payloads at 25 MB, and the request around one adds a
/// few kilobytes of headers.
let maximumRequestLength = 32 * 1024 * 1024

/// How much of a command's output the menu keeps, from the end.
let keptOutputLength = 2000

let logTimeFormatter = ISO8601DateFormatter()

/// The system log keeps every line, however github-hookbar was started;
/// `/usr/bin/log show --predicate 'subsystem == "local.github-hookbar"'`
/// lists them.
let systemLog = Logger(subsystem: "local.github-hookbar", category: "events")

func log(_ message: String) {
    systemLog.notice("\(message, privacy: .public)")
    let stamp = logTimeFormatter.string(from: Date())
    // Once the terminal github-hookbar was started from has gone, writing
    // fails, and the line is dropped.
    fputs("\(stamp) \(message)\n", stderr)
}

// MARK: - The relay connection

/// One connection to the relay, which sends each delivery as a frame and
/// nothing in between. A frame is the length of an HTTP request, as a 32-bit
/// big-endian integer, followed by the request exactly as the relay received
/// it. It reports when the connection is up, each delivery, and once that
/// the connection has ended; a frame that the end of the connection cuts
/// short is not a delivery. All methods and callbacks run on the main
/// thread.
final class RelayConnection: Connection {
    var onReady: (() -> Void)?
    var onDelivery: ((Delivery) -> Void)?
    var onEnded: ((String) -> Void)?
    private let connection: NWConnection
    private var buffer = Data()
    private var ended = false

    init(_ configuration: Configuration) {
        // The relay sends nothing while no deliveries arrive, so keepalive
        // probes are what notice a connection that died silently, for
        // example while the computer slept.
        let tcp = NWProtocolTCP.Options()
        tcp.enableKeepalive = true
        tcp.keepaliveIdle = 60
        tcp.keepaliveInterval = 10
        tcp.keepaliveCount = 3
        connection = NWConnection(host: NWEndpoint.Host(configuration.host),
                                  port: NWEndpoint.Port(integerLiteral: configuration.port),
                                  using: NWParameters(tls: nil, tcp: tcp))
    }

    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                if self?.ended == false {
                    self?.onReady?()
                }
            case .waiting(let error), .failed(let error):
                self?.end(describe(error))
            default:
                break
            }
        }
        connection.start(queue: .main)
        receive()
    }

    func cancel() {
        ended = true
        connection.cancel()
    }

    private func end(_ reason: String) {
        guard !ended else { return }
        cancel()
        onEnded?(reason)
    }

    private func receive() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) {
            [weak self] data, _, isComplete, error in
            guard let self, !ended else { return }
            if let data {
                buffer.append(data)
                readFrames()
            }
            if ended {
                return
            } else if let error {
                end(describe(error))
            } else if isComplete {
                end("the relay closed the connection")
            } else {
                receive()
            }
        }
    }
}

extension RelayConnection {
    /// Takes every complete frame out of the buffer. Consumed bytes are
    /// removed rather than sliced off, so that their storage is freed.
    private func readFrames() {
        while !ended, buffer.count >= 4 {
            let length = buffer.prefix(4).reduce(0) { $0 << 8 | Int($1) }
            guard length <= maximumRequestLength else {
                end("the relay sent a frame of \(length) bytes")
                return
            }
            let frameEnd = buffer.startIndex + 4 + length
            guard buffer.endIndex >= frameEnd else { return }
            let request = buffer[(buffer.startIndex + 4)..<frameEnd]
            if let delivery = Delivery(request) {
                onDelivery?(delivery)
            } else {
                log("ignoring a frame from the relay that is not an HTTP request")
            }
            buffer.removeSubrange(..<frameEnd)
        }
    }
}

func describe(_ error: NWError) -> String {
    switch error {
    case .posix(let code):
        return String(cString: strerror(code.rawValue))
    case .dns(let code):
        return "could not look up the relay's address (\(code))"
    default:
        return error.localizedDescription
    }
}

// MARK: - Deliveries

/// One delivery: the headers of the HTTP request that carried it, and its
/// body, exactly as GitHub sent it.
struct Delivery {
    /// The header values, by header name in lowercase. Proxies on the way
    /// can change the case of header names.
    let headers: [String: String]
    let body: Data

    /// Splits an HTTP request into its headers and body.
    init?(_ request: Data) {
        guard let blank = request.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        var headers: [String: String] = [:]
        // The first line is the request line, not a header. The values are
        // bytes, read as Latin-1, which every byte is.
        for line in request[..<blank.lowerBound].split(separator: UInt8(ascii: "\n")).dropFirst() {
            let text = String(String.UnicodeScalarView(
                (line.last == UInt8(ascii: "\r") ? line.dropLast() : line).map { Unicode.Scalar($0) }))
            let parts = text.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            if parts.count == 2 {
                headers[parts[0].lowercased()] = parts[1].trimmingCharacters(in: .whitespaces)
            }
        }
        self.headers = headers
        body = Data(request[blank.upperBound...])
    }
}

// MARK: - Running the command

/// One run of the command for one pull request.
final class Run {
    let event: PullRequestEvent
    /// The exit status and the output, once the command has finished.
    var outcome: (status: Int32, output: String)?

    init(_ event: PullRequestEvent) {
        self.event = event
    }

    var summary: String {
        "\(event.repository.fullName)#\(event.pullRequest.number) \(event.pullRequest.title)"
    }
}

func loginShellPath() -> String {
    if let entry = getpwuid(getuid()), let shell = entry.pointee.pw_shell {
        let path = String(cString: shell)
        if !path.isEmpty {
            return path
        }
    }
    return "/bin/sh"
}

/// Runs a script in the user's shell, as an interactive login shell so that
/// it sees the environment a terminal sees, with the given arguments as $1,
/// $2, and so on. Calls back on the main thread with the exit status and the
/// end of the output, standard output and standard error together.
///
/// The output goes to a file whose name is removed as soon as it is made, so
/// a background process that the script leaves running, still holding the
/// file, does not keep the run from finishing.
func runInLoginShell(_ script: String, arguments: [String],
                     completion: @escaping (Int32, String) -> Void) {
    let output: FileHandle
    do {
        let (file, path) = try makeTemporaryFile()
        unlink(path)
        output = file
    } catch {
        completion(-1, "\(error)")
        return
    }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: loginShellPath())
    process.arguments = ["-ilc", script, "github-hookbar"] + arguments
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = output
    process.standardError = output
    process.terminationHandler = { process in
        let text = (try? output.seek(toOffset: 0)).flatMap { try? output.readToEnd() }
            .map { String(decoding: $0, as: UTF8.self) } ?? ""
        DispatchQueue.main.async {
            completion(process.terminationStatus, String(text.suffix(keptOutputLength)))
        }
    }
    do {
        try process.run()
    } catch {
        completion(-1, "could not start \(loginShellPath()): \(error.localizedDescription)")
    }
}

/// Makes a new, empty file that only this user can read, and opens it.
func makeTemporaryFile() throws -> (FileHandle, String) {
    var template = Array("\(NSTemporaryDirectory())github-hookbar.XXXXXX".utf8CString)
    let descriptor = mkstemp(&template)
    guard descriptor >= 0 else {
        throw Problem(description: "could not make a temporary file: "
                      + String(cString: strerror(errno)))
    }
    return (FileHandle(fileDescriptor: descriptor, closeOnDealloc: true), String(cString: template))
}

/// Runs a script that asks GitHub something with the gh command, and reads
/// what it found with `read`. The login shell can print to standard output
/// as it starts, so the script writes what it found to the file named by its
/// first argument, before `arguments`.
func askGitHub<Found>(_ script: String, _ arguments: [String],
                      reading read: @escaping (String) throws -> Found,
                      completion: @escaping (Result<Found, Error>) -> Void) {
    let path: String
    do {
        path = try makeTemporaryFile().1
    } catch {
        completion(.failure(error))
        return
    }
    runInLoginShell(script, arguments: [path] + arguments) { status, output in
        defer { unlink(path) }
        completion(Result {
            guard status == 0 else {
                throw Problem(description: "gh exited with status \(status)\n\(output)")
            }
            return try read(String(contentsOfFile: path, encoding: .utf8))
        })
    }
}

/// The application's main menu, which an app that lives in the menu bar
/// still needs for the standard editing commands to reach text fields.
func makeMainMenu() -> NSMenu {
    let main = NSMenu()
    let application = NSMenu()
    application.addItem(withTitle: "Quit github-hookbar",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    let edit = NSMenu(title: "Edit")
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
    edit.addItem(.separator())
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
    edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
    edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)),
                 keyEquivalent: "a")
    edit.addItem(.separator())
    edit.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)),
                 keyEquivalent: "w")
    for submenu in [application, edit] {
        main.addItem(withTitle: "", action: nil, keyEquivalent: "").submenu = submenu
    }
    return main
}

// MARK: - Menu bar app

/// What the indicator shows.
enum Indicator: Equatable {
    case connecting
    /// Connected to the relay, with whether the last delivery arrived late.
    case connected(late: Bool)
    case disconnected
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, NSViewToolTipOwner {
    private let configurationURL: URL
    private var configuration: Configuration?
    private let relay = PersistentConnection()
    private var stream: (connection: PersistentConnection, file: StreamValueFile)?
    private let train = TrainController()
    private var recent: [Run] = []
    /// The pull requests the command has run for.
    private var ranFor: Set<URL> = []
    /// How long after GitHub made it the last delivery on this connection
    /// arrived, when its delivery ID says when that was.
    private var lastDeliveryAge: TimeInterval?
    private var missed = MissedDeliveries()
    private var dimming = Dimming()
    private var indicator: Indicator?
    private let pullRequestList = PullRequestList(path: pullRequestListPath)
    /// Why a change to the pull request list failed, if one has since the
    /// last refresh that succeeded.
    private var pullRequestListProblem: String?
    /// Why the last attempt to catch up after reconnecting failed, if it did.
    private var catchUpProblem: String?
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private let settingsWindow: SettingsWindow

    private let timeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        return formatter
    }()

    init(configurationURL: URL) {
        self.configurationURL = configurationURL
        settingsWindow = SettingsWindow(url: configurationURL)
        super.init()
        relay.onChange = { [weak self] in
            guard let self else { return }
            if let since = missed.changed(to: relay.state) {
                catchUp(since: since)
            }
            changed(relayName, relay.state)
        }
        settingsWindow.onSave = { [weak self] in
            self?.connect()
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = makeMainMenu()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.menu = menu
        menu.delegate = self
        // A layer, so that a delivery can dim the indicator.
        statusItem.button?.wantsLayer = true
        // The tooltip says how late the last delivery arrived, which changes
        // with every delivery, so its text is made only when it is shown,
        // over the whole indicator, whatever size the menu bar makes it.
        NotificationCenter.default.addObserver(
            self, selector: #selector(placeToolTip),
            name: NSView.frameDidChangeNotification, object: statusItem.button)
        placeToolTip()
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(didWake),
            name: NSWorkspace.didWakeNotification, object: nil)
        connect()
        if !FileManager.default.fileExists(atPath: configurationURL.path) {
            settingsWindow.show()
        }
    }

    /// Reads the configuration again and opens new connections.
    @objc private func connect() {
        let configuration: Configuration
        do {
            configuration = try Configuration(contentsOf: configurationURL)
        } catch {
            self.configuration = nil
            relay.stop("\(error)")
            // The stream is kept, so that settings that come back unchanged
            // keep its record of writing the file.
            stream?.connection.stop("the settings cannot be read")
            return
        }
        self.configuration = configuration
        relay.start { [weak self] in
            self?.lastDeliveryAge = nil
            let connection = RelayConnection(configuration)
            connection.onDelivery = { delivery in
                self?.received(delivery)
            }
            return connection
        }
        guard let settings = configuration.stream else {
            return stopStream("the settings no longer name it")
        }
        // A stream whose settings have not changed is opened again, but
        // keeps its record of writing the file, and the wait it has reached.
        if stream?.file.settings != settings {
            stopStream("the settings changed")
            let connection = PersistentConnection()
            connection.onChange = { [weak self, weak connection] in
                guard let self, let connection else { return }
                changed(streamName(settings), connection.state)
            }
            stream = (connection, StreamValueFile(settings))
        }
        if let stream {
            let file = stream.file
            stream.connection.start { [weak self] in
                let connection = EventStreamConnection(url: settings.url, path: settings.path)
                connection.onValue = { value in
                    self?.streamed(value, into: file)
                }
                return connection
            }
        }
    }

    private func stopStream(_ reason: String) {
        let stopped = stream
        stream = nil
        stopped?.connection.stop(reason)
    }

    @objc private func didWake() {
        if configuration == nil {
            connect()
        } else {
            relay.wake()
            stream?.connection.wake()
        }
    }

    /// Writes a value from the event stream to the file, and sends a train
    /// carrying it when it is new.
    private func streamed(_ value: String, into file: StreamValueFile) {
        file.take(value) { [weak self] change in
            guard let self, let change else { return }
            log("\(change)")
            updateMenu()
            if case .wrote(_, let value) = change {
                train.run(value)
            }
        }
    }

    /// Sends a train carrying the latest value from the event stream, or
    /// saying that there is none.
    @objc private func sendTrain() {
        if !train.run(stream?.file.latest ?? "") {
            train.run("No message just now")
        }
    }

    /// Dims the indicator briefly for every delivery, notes how late it
    /// arrived, and then handles it.
    private func received(_ delivery: Delivery) {
        missed.heard(at: Date())
        lastDeliveryAge = delivery.headers["x-github-delivery"].flatMap(creationTime(ofDelivery:))
            .map { Date().timeIntervalSince($0) }
        // Each delivery replaces the animation with one from the indicator's
        // opacity at that moment.
        let steps = dimming.deliver(at: CACurrentMediaTime())
        let duration = steps[steps.count - 1].time
        let animation = CAKeyframeAnimation(keyPath: "opacity")
        animation.values = steps.map(\.opacity)
        animation.keyTimes = steps.map { NSNumber(value: $0.time / duration) }
        animation.duration = duration
        statusItem.button?.layer?.add(animation, forKey: "pulse")
        updateIndicator()
        handle(delivery)
    }

    private func handle(_ delivery: Delivery) {
        guard delivery.headers["x-github-event"] == "pull_request" else { return }
        let event: PullRequestEvent
        do {
            event = try PullRequestEvent(delivery.body)
        } catch {
            log("ignoring a pull_request delivery that could not be read: \(error)")
            return
        }
        handle(event, from: "pull_request delivery "
               + (delivery.headers["x-github-delivery"] ?? "without an ID"))
    }

    /// Changes the pull request list and runs the command as an event says,
    /// naming where the event came from in the log.
    private func handle(_ event: PullRequestEvent, from source: String) {
        guard let configuration else { return }
        let url = event.pullRequest.htmlUrl
        let described = "\(source) for \(url.absoluteString), \(event.action) by "
            + event.pullRequest.user.login
        if let change = event.listChange(configuration) {
            log("\(described): \(change)")
            pullRequestList.make(change) { [weak self] error in
                self?.pullRequestListChanged(error)
            }
        }
        guard event.matches(configuration) else {
            log("\(described): not one the configuration runs the command for")
            return
        }
        let item = Run(event)
        ranFor.insert(url)
        log("\(described): running the command")
        recent = Array(([item] + recent).prefix(recentLength))
        updateMenu()
        runInLoginShell(configuration.command + " \"$1\"",
                        arguments: [url.absoluteString]) { [weak self] status, output in
            item.outcome = (status, output)
            // The system log cuts a message off after about a kilobyte, so
            // each line of output is a log line of its own.
            let lines = output.split(whereSeparator: \.isNewline)
            log("the command for \(url.absoluteString) exited with status \(status)"
                + (lines.isEmpty ? " and printed nothing" : " and printed:"))
            for line in lines {
                log("  | \(line)")
            }
            self?.updateMenu()
        }
    }

    /// Replaces the pull request list with the open pull requests the
    /// configuration tracks, as GitHub's search finds them.
    @objc private func refreshPullRequestList() {
        guard let search = trackedPullRequestSearch else { return }
        log("refreshing the pull request list from GitHub's search for \(search)")
        pullRequestList.refresh({
            askGitHub(searchScript, [search], reading: trackedPullRequests(fromSearchOutput:),
                      completion: $0)
        }) { [weak self] error in
            if error == nil {
                log("refreshed the pull request list")
                self?.pullRequestListProblem = nil
            }
            self?.pullRequestListChanged(error)
        }
        updateMenu()
    }

    /// Refreshes the pull request list, and runs the command for the pull
    /// requests the configuration chooses that were opened since a time,
    /// unless it has already run for them. When looking fails, the next
    /// connection looks again.
    private func catchUp(since: Date) {
        refreshPullRequestList()
        guard let configuration, configuration.actions.contains("opened"),
              let query = openedPullRequestsQuery(authors: configuration.authors,
                                                  owners: configuration.orgs) else {
            return
        }
        log("looking for pull requests opened since \(logTimeFormatter.string(from: since))")
        askGitHub(catchUpScript, [query, openedPullRequestsFilter(since: since)],
                  reading: openedEvents(fromCatchUpOutput:)) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let events):
                catchUpProblem = nil
                for event in events where !ranFor.contains(event.pullRequest.htmlUrl) {
                    handle(event, from: "a pull request found after reconnecting")
                }
            case .failure(let error):
                catchUpProblem = "\(error)"
                log("could not look for pull requests opened while not connected: \(error)")
                missed.notLooked(from: since)
            }
            updateMenu()
        }
    }

    private var trackedPullRequestSearch: String? {
        configuration.flatMap { openPullRequestSearch(authors: $0.authors, owners: $0.orgs) }
    }

    /// Notes a change to the pull request list that failed. One that
    /// succeeded leaves the problem with an earlier change in place, since
    /// only a refresh makes up for that.
    private func pullRequestListChanged(_ error: Error?) {
        if let error {
            pullRequestListProblem = "\(error)"
            log("could not change the pull request list: \(error)")
        }
        updateMenu()
    }

    private var relayName: String {
        configuration.map { "the relay at \($0.relay)" } ?? "the relay"
    }

    private func streamName(_ settings: StreamSettings) -> String {
        "the event stream at \(settings.publicURL)"
    }

    private func changed(_ name: String, _ state: ConnectionState) {
        switch state {
        case .connecting:
            break
        case .connected:
            log("connected to \(name)")
        case .disconnected(let reason, _):
            log("not connected to \(name): \(reason)")
        }
        updateIndicator()
        updateMenu()
    }

    /// Shows the state in the indicator. While connected, the indicator is
    /// orange when the last delivery arrived late. Setting the image redraws
    /// the indicator, so it is set only when what it shows changes.
    private func updateIndicator() {
        let shown: Indicator
        switch relay.state {
        case .connecting:
            shown = .connecting
        case .connected:
            shown = .connected(late: lastDeliveryAge.map(isLate) == true)
        case .disconnected:
            shown = .disconnected
        }
        guard shown != indicator else { return }
        indicator = shown
        let symbol: String
        switch shown {
        case .connecting: symbol = "ellipsis.circle"
        case .connected: symbol = "arrow.triangle.pull"
        case .disconnected: symbol = "exclamationmark.triangle"
        }
        var image = NSImage(systemSymbolName: symbol, accessibilityDescription: "github-hookbar")
        // The menu bar draws a template image in its own color, so a colored
        // indicator is an ordinary image drawn in that color.
        if shown == .connected(late: true) {
            image = image?.withSymbolConfiguration(.init(paletteColors: [.systemOrange]))
            image?.isTemplate = false
        }
        statusItem.button?.image = image
    }

    @objc private func placeToolTip() {
        guard let button = statusItem.button else { return }
        button.removeAllToolTips()
        button.addToolTip(button.bounds, owner: self, userData: nil)
    }

    func view(_ view: NSView, stringForToolTip tag: NSView.ToolTipTag, point: NSPoint,
              userData data: UnsafeMutableRawPointer?) -> String {
        statusLines.joined(separator: "\n")
    }

    private var statusLines: [String] {
        var lines = describe(relay.state, of: relayName)
        if case .connected = relay.state, let lastDeliveryAge {
            lines.append(String(format: "The last delivery arrived %.1f seconds after "
                                + "GitHub made it", lastDeliveryAge))
        }
        if let stream {
            let file = stream.file.settings.file
            lines += describe(stream.connection.state, of: streamName(stream.file.settings))
            if let problem = stream.file.problem {
                lines.append("Could not write the value file: \(problem)")
            } else if let writtenAt = stream.file.writtenAt {
                lines.append("Wrote \(file) at " + timeFormatter.string(from: writtenAt))
            }
        }
        return lines
    }

    private func describe(_ state: ConnectionState, of name: String) -> [String] {
        switch state {
        case .connecting:
            return ["Connecting to \(name)…"]
        case .connected(let since):
            return ["Connected to \(name) since " + timeFormatter.string(from: since)]
        case .disconnected(let reason, let retryAt):
            return ["Not connected to \(name): \(reason)"]
                + (retryAt.map { ["Trying again at " + timeFormatter.string(from: $0)] } ?? [])
        }
    }

    /// Refreshes the menu as it opens, so that it says how late the last
    /// delivery arrived without being rebuilt for every delivery.
    func menuNeedsUpdate(_ menu: NSMenu) {
        updateMenu()
    }

    private func updateMenu() {
        menu.removeAllItems()
        for line in statusLines {
            menu.addItem(withTitle: line, action: nil, keyEquivalent: "")
        }
        if let configuration {
            menu.addItem(withTitle: "Command: \(configuration.command)", action: nil,
                         keyEquivalent: "")
        }
        for case let (title, problem?) in [("Could not change the pull request list: ",
                                            pullRequestListProblem),
                                           ("Could not catch up after reconnecting: ",
                                            catchUpProblem)] {
            let item = menu.addItem(withTitle: title + problem.prefix { !$0.isNewline },
                                    action: nil, keyEquivalent: "")
            item.toolTip = problem
        }
        if !recent.isEmpty {
            menu.addItem(.separator())
            for (index, item) in recent.enumerated() {
                let menuItem = NSMenuItem(title: item.summary,
                                          action: #selector(openPullRequest(_:)), keyEquivalent: "")
                menuItem.target = self
                menuItem.tag = index
                switch item.outcome {
                case nil:
                    menuItem.image = NSImage(systemSymbolName: "clock",
                                             accessibilityDescription: "running")
                    menuItem.toolTip = "The command is running."
                case let (status, output)?:
                    menuItem.image = NSImage(
                        systemSymbolName: status == 0 ? "checkmark" : "xmark",
                        accessibilityDescription: status == 0 ? "succeeded" : "failed")
                    menuItem.toolTip = "The command exited with status \(status).\n\(output)"
                }
                menu.addItem(menuItem)
            }
        }
        menu.addItem(.separator())
        let reconnect = NSMenuItem(title: "Reconnect", action: #selector(connect),
                                   keyEquivalent: "r")
        reconnect.target = self
        menu.addItem(reconnect)
        // The train is behind Option, since waiting for a new value is no way
        // to see what it looks like. It is an alternate of the item above,
        // which AppKit swaps in while Option is held. An alternate has to
        // follow its twin and carry the same key equivalent, differing only in
        // the modifiers, which gives it Command-Option-R.
        let sendTrain = NSMenuItem(title: "Send the Train", action: #selector(sendTrain),
                                   keyEquivalent: "r")
        sendTrain.keyEquivalentModifierMask = [.command, .option]
        sendTrain.isAlternate = true
        sendTrain.target = self
        sendTrain.toolTip = "Send a train carrying the latest value from the event stream now."
        menu.addItem(sendTrain)
        // An item without an action is disabled.
        let refresh = NSMenuItem(
            title: pullRequestList.isRefreshing
                ? "Refreshing Pull Request List…" : "Refresh Pull Request List",
            action: trackedPullRequestSearch == nil ? nil : #selector(refreshPullRequestList),
            keyEquivalent: "")
        refresh.target = self
        if configuration != nil {
            refresh.toolTip = trackedPullRequestSearch == nil
                ? "Refreshing needs the settings to name authors or repository owners."
                : "Replaces \(pullRequestList.path) with the open pull requests the settings "
                    + "track. Choosing it while a refresh runs starts again."
        }
        menu.addItem(refresh)
        let settings = NSMenuItem(title: "Settings…", action: #selector(showSettings),
                                  keyEquivalent: ",")
        settings.target = self
        menu.addItem(settings)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit github-hookbar",
                              action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quit.target = NSApp
        menu.addItem(quit)
    }

    @objc private func openPullRequest(_ sender: NSMenuItem) {
        NSWorkspace.shared.open(recent[sender.tag].event.pullRequest.htmlUrl)
    }

    @objc private func showSettings() {
        settingsWindow.show()
    }
}

// MARK: - Entry point

var configurationPath = defaultConfigurationPath
var arguments = CommandLine.arguments.dropFirst()
while let argument = arguments.popFirst() {
    if argument == "--config", let path = arguments.popFirst() {
        configurationPath = path
    } else {
        fputs("usage: github-hookbar [--config PATH]\n", stderr)
        exit(2)
    }
}

// The indicator outlives the terminal it was started from, and quits from its
// menu. Catching the signals, rather than ignoring them, leaves them at their
// defaults in the commands it runs.
signal(SIGHUP) { _ in }
signal(SIGPIPE) { _ in }

let app = NSApplication.shared
let delegate = AppDelegate(configurationURL: URL(
    fileURLWithPath: NSString(string: configurationPath).expandingTildeInPath))
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
