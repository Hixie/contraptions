import AppKit
import Network

// github-hookbar: a menu bar indicator that keeps a connection open to a relay of
// GitHub webhook deliveries, and runs a command for each pull request that a
// chosen author opens.

// MARK: - Constants

let defaultConfigurationPath = "~/.config/github-hookbar/config.ini"

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

func log(_ message: String) {
    let stamp = logTimeFormatter.string(from: Date())
    // Once the terminal github-hookbar was started from has gone, writing
    // fails, and the line is dropped.
    fputs("\(stamp) \(message)\n", stderr)
}

// MARK: - Configuration

struct Problem: Error, CustomStringConvertible {
    let description: String
}

/// The settings the configuration file holds, each named by its section and
/// key, with how the settings window shows it.
let settings: [(name: String, label: String, placeholder: String)] = [
    ("relay.address", "Relay:", "host:port"),
    ("github.authors", "Authors:", "everyone"),
    ("github.orgs", "Repository owners:", "anyone"),
    ("github.actions", "Actions:", "opened"),
    ("command.run", "Command:", "open"),
]

/// What one line of INI text is: a "[section]" header, a "key = value"
/// setting, nothing (a blank line, or a comment starting with "#" or ";"),
/// or none of those.
enum INILine {
    case section(String)
    case setting(key: String, value: String)
    case nothing
    case invalid

    init(_ line: String) {
        let line = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if line.isEmpty || line.hasPrefix("#") || line.hasPrefix(";") {
            self = .nothing
        } else if line.hasPrefix("["), line.hasSuffix("]") {
            self = .section(line.dropFirst().dropLast().trimmingCharacters(in: .whitespaces)
                .lowercased())
        } else if let equals = line.firstIndex(of: "=") {
            self = .setting(key: line[..<equals].trimmingCharacters(in: .whitespaces).lowercased(),
                            value: line[line.index(after: equals)...]
                                .trimmingCharacters(in: .whitespaces))
        } else {
            self = .invalid
        }
    }
}

/// INI text, with the line each setting is on. Settings are named
/// "section.key", and each may be set only once. Changing a setting changes
/// only its own line, so the rest of the text stays as it was.
struct INIFile {
    /// The settings, in the order the text sets them.
    private(set) var names: [String] = []
    private(set) var values: [String: String] = [:]
    private var lines: [String]
    private var lineOfSetting: [String: Int] = [:]
    /// Where a new setting in each section goes: after the section's last
    /// setting, or after its header when it has none.
    private var endOfSection: [String: Int] = [:]

    init(_ text: String) throws {
        lines = text.components(separatedBy: "\n")
        if lines.last == "" {
            lines.removeLast()
        }
        var section = ""
        for (index, line) in lines.enumerated() {
            switch INILine(line) {
            case .section(let name):
                section = name
                endOfSection[name] = index + 1
            case .setting(let key, let value):
                let name = "\(section).\(key)"
                guard values[name] == nil else {
                    throw Problem(description: "\(name) is set more than once")
                }
                names.append(name)
                values[name] = value
                lineOfSetting[name] = index
                endOfSection[section] = index + 1
            case .nothing:
                break
            case .invalid:
                throw Problem(description: "a line is not \"key = value\": \(line)")
            }
        }
    }

    var text: String {
        lines.joined(separator: "\n") + "\n"
    }

    /// Sets a setting, adding it after the last setting in its section, or
    /// adding the section at the end, when the text does not set it yet. A
    /// new line ends the way the text's first line does, so CRLF text stays
    /// CRLF text.
    mutating func set(_ name: String, to value: String) throws {
        let parts = name.split(separator: ".", maxSplits: 1).map(String.init)
        let ending = lines.first?.hasSuffix("\r") == true ? "\r" : ""
        let line = (value.isEmpty ? "\(parts[1]) =" : "\(parts[1]) = \(value)") + ending
        if let index = lineOfSetting[name] {
            lines[index] = line
        } else if let index = endOfSection[parts[0]] {
            lines.insert(line, at: index)
        } else {
            lines += (lines.isEmpty ? [] : [ending]) + ["[\(parts[0])]" + ending, line]
        }
        self = try INIFile(text)
    }
}

struct Configuration {
    let host: String
    let port: UInt16
    let authors: [String]
    let orgs: [String]
    let actions: [String]
    let command: String

    var relay: String { "\(host):\(port)" }

    init(contentsOf url: URL) throws {
        let text: String
        do {
            text = try String(contentsOf: url, encoding: .utf8)
        } catch {
            throw Problem(description: "cannot read \(url.path): \(error.localizedDescription)")
        }
        do {
            try self.init(try INIFile(text))
        } catch {
            throw Problem(description: "\(url.path): \(error)")
        }
    }

    init(_ file: INIFile) throws {
        for name in file.names where !settings.contains(where: { $0.name == name }) {
            throw Problem(description: "\(name) is not a setting github-hookbar knows")
        }
        let values = file.values
        func required(_ key: String) throws -> String {
            guard let value = values[key], !value.isEmpty else {
                throw Problem(description: "\(key) is not set")
            }
            return value
        }
        func list(_ key: String) -> [String] {
            (values[key] ?? "").split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
                .filter { !$0.isEmpty }
        }

        // An IPv6 address is written in brackets, as in [::1]:8081.
        let address = try required("relay.address")
        guard let colon = address.lastIndex(of: ":"),
              let port = UInt16(address[address.index(after: colon)...]) else {
            throw Problem(description: "the relay address \(address) is not host:port")
        }
        var host = address[..<colon]
        if host.hasPrefix("["), host.hasSuffix("]") {
            host = host.dropFirst().dropLast()
        } else if host.contains(":") {
            host = ""
        }
        guard !host.isEmpty else {
            throw Problem(description: "the relay address \(address) is not host:port")
        }
        self.host = String(host)
        self.port = port
        authors = list("github.authors")
        orgs = list("github.orgs")
        let actions = list("github.actions")
        self.actions = actions.isEmpty ? ["opened"] : actions
        command = try required("command.run")
    }
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

/// The parts of a pull_request delivery that decide whether to run the
/// command, and what to run it with.
struct PullRequestEvent: Decodable {
    struct Account: Decodable {
        let login: String
    }

    struct PullRequest: Decodable {
        let htmlUrl: URL
        let number: Int
        let title: String
        let user: Account
    }

    struct Repository: Decodable {
        let fullName: String
        let owner: Account
    }

    let action: String
    let pullRequest: PullRequest
    let repository: Repository

    init(_ body: Data) throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        self = try decoder.decode(PullRequestEvent.self, from: body)
    }

    func matches(_ configuration: Configuration) -> Bool {
        func allows(_ list: [String], _ value: String) -> Bool {
            list.isEmpty || list.contains(value.lowercased())
        }
        return allows(configuration.actions, action)
            && allows(configuration.authors, pullRequest.user.login)
            && allows(configuration.orgs, repository.owner.login)
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

/// Runs the command line in the user's shell, as an interactive login shell
/// so that it sees the environment a terminal sees, with the pull request's
/// URL added as its last argument. Calls back on the main thread with the
/// exit status and the end of the output, standard output and standard
/// error together.
///
/// The output goes to a file whose name is removed as soon as it is made, so
/// a background process that the command leaves running, still holding the
/// file, does not keep the run from finishing.
func runCommand(_ command: String, with url: URL,
                completion: @escaping (Int32, String) -> Void) {
    var template = Array("\(NSTemporaryDirectory())github-hookbar.XXXXXX".utf8CString)
    let descriptor = mkstemp(&template)
    guard descriptor >= 0 else {
        completion(-1, "could not make a file for the output: \(String(cString: strerror(errno)))")
        return
    }
    unlink(template)
    let output = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    let process = Process()
    process.executableURL = URL(fileURLWithPath: loginShellPath())
    process.arguments = ["-ilc", command + " \"$1\"", "github-hookbar", url.absoluteString]
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

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let configurationURL: URL
    private var configuration: Configuration?
    private var connection: RelayConnection?
    private var state = ConnectionState.disconnected(reason: "not started", retryAt: nil)
    private var retryDelay = firstRetryDelay
    private var retry: DispatchWorkItem?
    private var recent: [Run] = []
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
        let connection = RelayConnection(configuration)
        connection.onReady = { [weak self] in
            self?.show(.connected(since: Date()))
        }
        connection.onDelivery = { [weak self] delivery in
            self?.handle(delivery)
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
        guard event.matches(configuration) else { return }
        let item = Run(event)
        log("running the command for \(event.pullRequest.htmlUrl.absoluteString)")
        recent = Array(([item] + recent).prefix(recentLength))
        updateMenu()
        let url = event.pullRequest.htmlUrl
        runCommand(configuration.command, with: url) { [weak self] status, output in
            item.outcome = (status, output)
            log("the command for \(url.absoluteString) exited with status \(status)")
            self?.updateMenu()
        }
    }

    private func show(_ state: ConnectionState) {
        self.state = state
        let symbol: String
        switch state {
        case .connecting:
            symbol = "ellipsis.circle"
        case .connected:
            symbol = "arrow.triangle.pull"
            log("connected to \(configuration?.relay ?? "the relay")")
        case .disconnected(let reason, _):
            symbol = "exclamationmark.triangle"
            log("not connected: \(reason)")
        }
        statusItem.button?.image = NSImage(systemSymbolName: symbol,
                                           accessibilityDescription: "github-hookbar")
        statusItem.button?.toolTip = statusLines.joined(separator: "\n")
        updateMenu()
    }

    private var statusLines: [String] {
        switch state {
        case .connecting:
            return ["Connecting to \(configuration?.relay ?? "the relay")…"]
        case .connected(let since):
            return ["Connected to \(configuration?.relay ?? "the relay") since "
                    + timeFormatter.string(from: since)]
        case .disconnected(let reason, let retryAt):
            var lines = ["Not connected: \(reason)"]
            if let retryAt {
                lines.append("Trying again at " + timeFormatter.string(from: retryAt))
            }
            return lines
        }
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
