import AppKit
import Network
import os

// github-hookbar: a menu bar indicator that keeps a connection open to a relay of
// GitHub webhook deliveries, runs a command for each pull request that a
// chosen author opens, and keeps a list of those pull requests until they
// are closed.

// MARK: - Constants

let defaultConfigurationPath = "~/.config/github-hookbar/config.ini"
let pullRequestListPath = NSHomeDirectory() + "/.cache/github-prs.tsv"

/// After the connection fails, the first attempt to connect again waits this
/// long, and each failure after that doubles the wait, up to the maximum.
let firstRetryDelay: TimeInterval = 1
let maximumRetryDelay: TimeInterval = 64

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
final class RelayConnection {
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
                self?.onReady?()
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
        for line in request[..<blank.lowerBound].split(separator: Data("\r\n".utf8)).dropFirst() {
            let text = String(String.UnicodeScalarView(line.map { Unicode.Scalar($0) }))
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

/// Asks GitHub, with the gh command, for the pull requests a search finds.
/// The login shell can print to standard output as it starts, so the pull
/// requests go to a file of their own.
func lookUpPullRequests(_ search: String,
                        completion: @escaping (Result<[TrackedPullRequest], Error>) -> Void) {
    let path: String
    do {
        path = try makeTemporaryFile().1
    } catch {
        completion(.failure(error))
        return
    }
    runInLoginShell(searchScript, arguments: [path, search]) { status, output in
        defer { unlink(path) }
        completion(Result {
            guard status == 0 else {
                throw Problem(description: "the search exited with status \(status)\n\(output)")
            }
            return try trackedPullRequests(fromSearchOutput: String(contentsOfFile: path,
                                                                    encoding: .utf8))
        })
    }
}

// MARK: - Settings window

/// A window with a field for each setting, which edits the configuration
/// file in place: saving rewrites only the lines of the settings that the
/// window shows, so the file's comments and layout stay as they were.
final class SettingsWindow: NSObject {
    var onSave: (() -> Void)?
    private let url: URL
    private let window: NSWindow
    private let fields: [NSTextField]
    private let message = NSTextField(wrappingLabelWithString: "")
    /// What the fields held when the window opened. Saving writes only the
    /// fields that have changed since, so it keeps whatever else changed in
    /// the file meanwhile.
    private var shown: [String] = []

    init(url: URL) {
        self.url = url
        fields = settings.map { setting in
            let field = NSTextField()
            field.placeholderString = setting.placeholder
            field.widthAnchor.constraint(equalToConstant: 360).isActive = true
            return field
        }
        window = NSWindow(contentRect: .zero, styleMask: [.titled, .closable],
                          backing: .buffered, defer: true)
        super.init()
        window.title = "github-hookbar Settings"
        window.isReleasedWhenClosed = false
        let grid = NSGridView(views: zip(settings, fields).map { setting, field in
            [NSTextField(labelWithString: setting.label), field]
        })
        grid.column(at: 0).xPlacement = .trailing
        grid.rowAlignment = .firstBaseline
        message.textColor = .systemRed
        message.preferredMaxLayoutWidth = 480
        let editFile = NSButton(title: "Edit File…", target: self, action: #selector(openFile))
        let cancel = NSButton(title: "Cancel", target: window,
                              action: #selector(NSWindow.performClose(_:)))
        cancel.keyEquivalent = "\u{1b}"
        let save = NSButton(title: "Save", target: self, action: #selector(save))
        save.keyEquivalent = "\r"
        let buttons = NSStackView(views: [editFile, NSView(), cancel, save])
        let stack = NSStackView(views: [grid, message, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.translatesAutoresizingMaskIntoConstraints = false
        buttons.widthAnchor.constraint(equalTo: grid.widthAnchor).isActive = true
        let content = NSView()
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 20),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -20),
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
        ])
        window.contentView = content
        window.initialFirstResponder = fields[0]
        window.center()
    }

    /// Brings the window to the front, filling the fields from the file as
    /// it is now unless the window is already open.
    func show() {
        if !window.isVisible {
            var values: [String: String] = [:]
            message.stringValue = ""
            do {
                if let text = try contents() {
                    let file = try INIFile(text)
                    values = file.values
                    _ = try Configuration(file)
                }
            } catch {
                message.stringValue = "\(error)"
            }
            message.isHidden = message.stringValue.isEmpty
            for (setting, field) in zip(settings, fields) {
                field.stringValue = values[setting.name] ?? ""
            }
            shown = fields.map(\.stringValue)
        }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    /// The file's text, or nothing when there is no file yet.
    private func contents() throws -> String? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try String(contentsOf: url, encoding: .utf8)
    }

    @objc private func save() {
        do {
            var file = try INIFile(try contents() ?? "")
            for (index, setting) in settings.enumerated()
            where fields[index].stringValue != shown[index] {
                try file.set(setting.name, to: fields[index].stringValue
                    .components(separatedBy: .newlines).joined(separator: " ")
                    .trimmingCharacters(in: .whitespaces))
            }
            _ = try Configuration(file)
            // Writing through a symbolic link keeps the link.
            let target = url.resolvingSymlinksInPath()
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try file.text.write(to: target, atomically: true, encoding: .utf8)
        } catch {
            message.stringValue = "\(error)"
            message.isHidden = false
            return
        }
        window.close()
        onSave?()
    }

    @objc private func openFile() {
        NSWorkspace.shared.open(url)
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

enum ConnectionState {
    case connecting
    case connected(since: Date)
    case disconnected(reason: String, retryAt: Date?)
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let configurationURL: URL
    private var configuration: Configuration?
    private var connection: RelayConnection?
    private var state = ConnectionState.disconnected(reason: "not started", retryAt: nil)
    private var retryDelay = firstRetryDelay
    private var retry: DispatchWorkItem?
    private var recent: [Run] = []
    /// How long after GitHub made it the last delivery on this connection
    /// arrived, when its delivery ID says when that was.
    private var lastDeliveryAge: TimeInterval?
    private var dimming = Dimming()
    private let pullRequestList = PullRequestList(path: pullRequestListPath)
    /// Why a change to the pull request list failed, if one has since the
    /// last refresh that succeeded.
    private var pullRequestListProblem: String?
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
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(didWake),
            name: NSWorkspace.didWakeNotification, object: nil)
        connect()
        if !FileManager.default.fileExists(atPath: configurationURL.path) {
            settingsWindow.show()
        }
    }

    /// Reads the configuration again and opens a new connection to the relay.
    @objc private func connect() {
        retry?.cancel()
        retry = nil
        connection?.cancel()
        connection = nil
        let configuration: Configuration
        do {
            configuration = try Configuration(contentsOf: configurationURL)
        } catch {
            self.configuration = nil
            show(.disconnected(reason: "\(error)", retryAt: nil))
            return
        }
        self.configuration = configuration
        lastDeliveryAge = nil
        let connection = RelayConnection(configuration)
        connection.onReady = { [weak self] in
            self?.show(.connected(since: Date()))
        }
        connection.onDelivery = { [weak self] delivery in
            self?.received(delivery)
        }
        connection.onEnded = { [weak self] reason in
            self?.connectionEnded(reason)
        }
        self.connection = connection
        show(.connecting)
        connection.start()
    }

    /// Schedules the next attempt to connect. A connection that lasted longer
    /// than the wait before it counts as a success, and starts the waits
    /// again from the first; one that ended sooner, as one does when a proxy
    /// in front of the relay accepts it and then finds the relay down, makes
    /// the next wait longer.
    private func connectionEnded(_ reason: String) {
        connection = nil
        if case .connected(let since) = state, Date().timeIntervalSince(since) > retryDelay {
            retryDelay = firstRetryDelay
        }
        let retry = DispatchWorkItem { [weak self] in
            self?.connect()
        }
        self.retry = retry
        DispatchQueue.main.asyncAfter(deadline: .now() + retryDelay, execute: retry)
        show(.disconnected(reason: reason, retryAt: Date() + retryDelay))
        retryDelay = min(retryDelay * 2, maximumRetryDelay)
    }

    @objc private func didWake() {
        if case .disconnected = state {
            connect()
        }
    }

    /// Dims the indicator briefly for every delivery, notes how late it
    /// arrived, and then handles it.
    private func received(_ delivery: Delivery) {
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
        guard let configuration else { return }
        guard delivery.headers["x-github-event"] == "pull_request" else { return }
        let event: PullRequestEvent
        do {
            event = try PullRequestEvent(delivery.body)
        } catch {
            log("ignoring a pull_request delivery that could not be read: \(error)")
            return
        }
        let url = event.pullRequest.htmlUrl
        let described = "pull_request delivery \(delivery.headers["x-github-delivery"] ?? "without an ID")"
            + " for \(url.absoluteString), \(event.action) by \(event.pullRequest.user.login)"
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
        pullRequestList.refresh({ lookUpPullRequests(search, completion: $0) }) { [weak self] error in
            if error == nil {
                log("refreshed the pull request list")
                self?.pullRequestListProblem = nil
            }
            self?.pullRequestListChanged(error)
        }
        updateMenu()
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

    private func show(_ state: ConnectionState) {
        self.state = state
        switch state {
        case .connecting:
            break
        case .connected:
            log("connected to \(configuration?.relay ?? "the relay")")
        case .disconnected(let reason, _):
            log("not connected: \(reason)")
        }
        updateIndicator()
        updateMenu()
    }

    /// Shows the state in the indicator and its tooltip. While connected,
    /// the indicator is orange when the last delivery arrived late.
    private func updateIndicator() {
        let symbol: String
        var tint: NSColor?
        switch state {
        case .connecting:
            symbol = "ellipsis.circle"
        case .connected:
            symbol = "arrow.triangle.pull"
            if let lastDeliveryAge, isLate(lastDeliveryAge) {
                tint = .systemOrange
            }
        case .disconnected:
            symbol = "exclamationmark.triangle"
        }
        var image = NSImage(systemSymbolName: symbol, accessibilityDescription: "github-hookbar")
        // The menu bar draws a template image in its own color, so a colored
        // indicator is an ordinary image drawn in that color.
        if let tint {
            image = image?.withSymbolConfiguration(.init(paletteColors: [tint]))
            image?.isTemplate = false
        }
        statusItem.button?.image = image
        statusItem.button?.toolTip = statusLines.joined(separator: "\n")
    }

    private var statusLines: [String] {
        switch state {
        case .connecting:
            return ["Connecting to \(configuration?.relay ?? "the relay")…"]
        case .connected(let since):
            var lines = ["Connected to \(configuration?.relay ?? "the relay") since "
                         + timeFormatter.string(from: since)]
            if let lastDeliveryAge {
                lines.append(String(format: "The last delivery arrived %.1f seconds after "
                                    + "GitHub made it", lastDeliveryAge))
            }
            return lines
        case .disconnected(let reason, let retryAt):
            var lines = ["Not connected: \(reason)"]
            if let retryAt {
                lines.append("Trying again at " + timeFormatter.string(from: retryAt))
            }
            return lines
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
        if let pullRequestListProblem {
            let item = menu.addItem(withTitle: "Could not change the pull request list: "
                                    + pullRequestListProblem.prefix { !$0.isNewline },
                                    action: nil, keyEquivalent: "")
            item.toolTip = pullRequestListProblem
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
if configurationPath.hasPrefix("~/") {
    configurationPath = NSHomeDirectory() + configurationPath.dropFirst()
}
let delegate = AppDelegate(configurationURL: URL(fileURLWithPath: configurationPath))
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
