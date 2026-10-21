import AppKit

// ghbar: a menu bar indicator for the state of the checks GitHub reports on
// the head commit of one branch of one repository. The indicator is green when
// every check has passed, red when any check has failed, orange while checks
// are still running, and gray when the state is not known.
//
// Several indicators can run side by side. Each one owns an instance number,
// and that number picks its settings, its lock file, and its launchd agent, so
// the indicators never share state. Every ordinary launch hands the running
// instance to its launchd agent, which also starts it at the next login; the
// Disable and Quit menu item removes the agent and exits.

// MARK: - Constants

/// Prefix for the preference domain, the lock file names, and the launchd
/// agent labels. The instance number is appended to it.
let identifierPrefix = "local.ghbar"

/// Highest instance number that automatic allocation considers.
let maximumInstances = 64

let defaultBranchName = "main"
let defaultRefreshSeconds = 60.0
let minimumRefreshSeconds = 15.0

/// Marks the fields the login shell prints, so the token can be picked out of
/// whatever else the user's profile writes to standard output.
let shellFieldSeparator = "\u{1e}"

/// Longest inline list of failing or running checks in the menu. Anything past
/// this is reported as a count and stays reachable through the All Checks
/// submenu.
let inlineCheckLimit = 12

// MARK: - Repository references

/// Reads a folder as typed, which may start with a tilde. A relative path is
/// taken from the home folder, and empty means the home folder.
func folderURL(_ text: String) -> URL {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty {
        return FileManager.default.homeDirectoryForCurrentUser
    }
    return URL(fileURLWithPath: (trimmed as NSString).expandingTildeInPath, isDirectory: true,
               relativeTo: FileManager.default.homeDirectoryForCurrentUser).absoluteURL
}

func isBranchName(_ text: String) -> Bool {
    guard !text.isEmpty, !text.hasPrefix("/"), !text.hasSuffix("/") else { return false }
    return !text.contains(where: { $0.isWhitespace }) && !text.contains("..")
}

// MARK: - Settings

enum SettingsKey {
    static let repository = "repository"
    static let branch = "branch"
    static let menuBarName = "menuBarName"
    static let showsMenuBarName = "showsMenuBarName"
    static let refreshSeconds = "refreshSeconds"
    static let handOff = "handOff"
    static let handOffFolder = "handOffFolder"
    static let relay = "relay"
}

/// One indicator's settings, kept in a preference domain of its own so that
/// indicators with different instance numbers do not overwrite each other.
final class Settings {
    let instance: Int
    private let store: UserDefaults

    init(instance: Int) {
        self.instance = instance
        store = UserDefaults(suiteName: "\(identifierPrefix).\(instance)") ?? .standard
        store.register(defaults: [
            SettingsKey.branch: defaultBranchName,
            SettingsKey.showsMenuBarName: true,
            SettingsKey.refreshSeconds: defaultRefreshSeconds,
        ])
    }

    var repository: RepositoryReference? {
        get {
            guard let text = store.string(forKey: SettingsKey.repository) else { return nil }
            return parseRepository(text)?.repository
        }
        set { store.set(newValue?.slug, forKey: SettingsKey.repository) }
    }

    var branch: String {
        get { store.string(forKey: SettingsKey.branch) ?? defaultBranchName }
        set { store.set(newValue, forKey: SettingsKey.branch) }
    }

    var menuBarName: String {
        get { store.string(forKey: SettingsKey.menuBarName) ?? "" }
        set { store.set(newValue, forKey: SettingsKey.menuBarName) }
    }

    var showsMenuBarName: Bool {
        get { store.bool(forKey: SettingsKey.showsMenuBarName) }
        set { store.set(newValue, forKey: SettingsKey.showsMenuBarName) }
    }

    var refreshSeconds: Double {
        get { max(minimumRefreshSeconds, store.double(forKey: SettingsKey.refreshSeconds)) }
        set { store.set(max(minimumRefreshSeconds, newValue), forKey: SettingsKey.refreshSeconds) }
    }

    /// The agent a branch that turns red is handed to, if any.
    var handOff: Agent? {
        get { store.string(forKey: SettingsKey.handOff).flatMap(Agent.init(rawValue:)) }
        set { store.set(newValue?.rawValue, forKey: SettingsKey.handOff) }
    }

    /// The folder an agent's session starts in, as typed. Empty means the
    /// home folder.
    var handOffFolder: String {
        get { store.string(forKey: SettingsKey.handOffFolder) ?? "" }
        set { store.set(newValue, forKey: SettingsKey.handOffFolder) }
    }

    var handOffFolderURL: URL { folderURL(handOffFolder) }

    /// The webhook relay to hear about changes from, if any.
    var relay: RelayAddress? {
        get { store.string(forKey: SettingsKey.relay).flatMap(parseRelayAddress) }
        set { store.set(newValue?.description, forKey: SettingsKey.relay) }
    }

    /// The text drawn next to the indicator in the menu bar.
    var displayName: String {
        if !menuBarName.isEmpty { return menuBarName }
        return repository?.name ?? applicationName
    }
}

// MARK: - Instance numbers

/// An exclusive claim on one instance number, held for as long as the process
/// runs. Two indicators with the same number would share settings and a
/// launchd agent, so the second one to start steps aside.
final class InstanceLock {
    enum Outcome {
        case claimed(InstanceLock)
        case busy(Int)
        case failed(String)
    }

    private enum Attempt {
        case took(descriptor: Int32, inode: ino_t)
        case busy
        case failed(String)
    }

    let number: Int
    private var descriptor: Int32
    private var inode: ino_t

    private init(number: Int, descriptor: Int32, inode: ino_t) {
        self.number = number
        self.descriptor = descriptor
        self.inode = inode
    }

    deinit {
        if descriptor >= 0 {
            close(descriptor)
        }
    }

    private func release() {
        close(descriptor)
        descriptor = -1
    }

    static var directory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/\(applicationName)",
                                   isDirectory: true)
    }

    static func path(for number: Int) -> String {
        directory.appendingPathComponent("instance-\(number).lock").path
    }

    private static func attempt(_ number: Int, waiting: Bool = false) -> Attempt {
        do {
            try FileManager.default.createDirectory(at: directory,
                                                    withIntermediateDirectories: true)
        } catch {
            return .failed("could not create \(directory.path): \(error.localizedDescription)")
        }
        let path = path(for: number)
        let descriptor = open(path, O_CREAT | O_RDWR, 0o644)
        guard descriptor >= 0 else {
            return .failed("could not open \(path): \(String(cString: strerror(errno)))")
        }
        // Keeps a spawned indicator from inheriting the lock.
        _ = fcntl(descriptor, F_SETFD, FD_CLOEXEC)
        let operation = waiting ? LOCK_EX : LOCK_EX | LOCK_NB
        guard flock(descriptor, operation) == 0 else {
            let reason = errno
            close(descriptor)
            return reason == EWOULDBLOCK
                ? .busy
                : .failed("could not lock \(path): \(String(cString: strerror(reason)))")
        }
        var details = stat()
        guard fstat(descriptor, &details) == 0 else {
            let reason = errno
            close(descriptor)
            return .failed("could not read \(path): \(String(cString: strerror(reason)))")
        }
        return .took(descriptor: descriptor, inode: details.st_ino)
    }

    static func claim(_ number: Int, waiting: Bool = false) -> Outcome {
        switch attempt(number, waiting: waiting) {
        case .took(let descriptor, let inode):
            return .claimed(InstanceLock(number: number, descriptor: descriptor, inode: inode))
        case .busy:
            return .busy(number)
        case .failed(let message):
            return .failed(message)
        }
    }

    /// Takes the lock again when the file it was taken on is no longer the
    /// file at the path. A lock on a file that has been deleted or replaced
    /// holds nobody off, and it holds nobody off quietly: the next indicator
    /// to start makes a new file at the same path, locks that, and takes a
    /// number this indicator is already using, after which the two of them
    /// share one set of settings and one login item.
    func reassertIfNeeded() {
        var current = stat()
        if stat(InstanceLock.path(for: number), &current) == 0, current.st_ino == inode {
            return
        }
        guard case .took(let replacement, let replacementInode) =
                InstanceLock.attempt(number) else {
            return
        }
        close(descriptor)
        descriptor = replacement
        inode = replacementInode
    }

    /// Takes the lowest numbered instance that no other indicator is running.
    static func claimLowestFree() -> Outcome {
        var lastFailure: String?
        for number in 1...maximumInstances {
            switch claim(number) {
            case .claimed(let lock):
                if LoginItem.isRunning(instance: number) {
                    lock.release()
                    continue
                }
                return .claimed(lock)
            case .busy:
                continue
            case .failed(let message):
                lastFailure = message
            }
        }
        if let lastFailure {
            return .failed(lastFailure)
        }
        return .failed("all \(maximumInstances) instance numbers are in use")
    }
}

// MARK: - Running other programs

struct ToolRun {
    let status: Int32
    let killed: Bool
    let output: String
    let errors: String

    /// How the tool ended, and what it reported on its error stream.
    func failure(_ name: String) -> String {
        let reported = errors.trimmingCharacters(in: .whitespacesAndNewlines)
        let ending = killed ? "was killed by signal \(status)" : "exited with status \(status)"
        return "\(name) \(ending)" + (reported.isEmpty ? "" : ": \(reported)")
    }
}

/// Runs a tool to completion and returns what it wrote to each stream. The
/// streams go to unnamed files, which nothing the tool leaves running can
/// hold open once the tool itself has exited, and which disappear with this
/// process however it ends.
func runTool(_ executable: URL, _ arguments: [String]) throws -> ToolRun {
    let output = try TemporaryLog()
    let errors = try TemporaryLog()
    let process = Process()
    process.executableURL = executable
    process.arguments = arguments
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = output.handle
    process.standardError = errors.handle
    let exited = DispatchSemaphore(value: 0)
    process.terminationHandler = { _ in exited.signal() }
    try process.run()
    exited.wait()
    return ToolRun(status: process.terminationStatus,
                   killed: process.terminationReason == .uncaughtSignal,
                   output: try output.contents(),
                   errors: try errors.contents())
}

/// A file a tool writes to. Its name is removed as soon as it is made, so
/// the file lasts only as long as the handle.
private final class TemporaryLog {
    let handle: FileHandle

    init() throws {
        var template = Array(
            "\(FileManager.default.temporaryDirectory.path)/\(applicationName).XXXXXX".utf8CString)
        let descriptor = mkstemp(&template)
        guard descriptor >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        unlink(template)
        handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    }

    func contents() throws -> String {
        try handle.seek(toOffset: 0)
        return String(decoding: try handle.readToEnd() ?? Data(), as: UTF8.self)
    }
}

// MARK: - Login item

/// The launchd user agent that owns one instance. An ordinary process writes
/// and loads the agent, then exits after its launchd copy starts waiting for
/// the instance lock.
enum LoginItem {
    static var directory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents", isDirectory: true)
    }

    static func label(instance: Int) -> String {
        "\(identifierPrefix).\(instance)"
    }

    static func plistURL(instance: Int) -> URL {
        directory.appendingPathComponent("\(label(instance: instance)).plist")
    }

    static var executablePath: String {
        let url = Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0])
        return url.resolvingSymlinksInPath().standardizedFileURL.path
    }

    static func isRegistered(instance: Int) -> Bool {
        FileManager.default.fileExists(atPath: plistURL(instance: instance).path)
    }

    static func isCurrentProcessManaged(instance: Int) -> Bool {
        ProcessInfo.processInfo.environment["XPC_SERVICE_NAME"] == label(instance: instance)
    }

    static func isRunning(instance: Int) -> Bool {
        let service = "gui/\(getuid())/\(label(instance: instance))"
        guard let result = try? runTool(launchctl, ["print", service]), result.status == 0 else {
            return false
        }
        return result.output.split(separator: "\n").contains {
            $0.trimmingCharacters(in: .whitespaces) == "state = running"
        }
    }

    /// Writes the agent, pointing it at the running executable. Returns a
    /// message when the file could not be written.
    static func register(instance: Int) -> String? {
        let job: [String: Any] = [
            "Label": label(instance: instance),
            "ProgramArguments": [
                executablePath,
                "--instance", String(instance),
                "--launchd",
            ],
            "RunAtLoad": true,
            "KeepAlive": ["SuccessfulExit": false],
        ]
        do {
            try FileManager.default.createDirectory(at: directory,
                                                    withIntermediateDirectories: true)
            let data = try PropertyListSerialization.data(fromPropertyList: job,
                                                          format: .xml,
                                                          options: 0)
            try data.write(to: plistURL(instance: instance), options: .atomic)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    /// Loads a newly written agent. The launchd copy waits for the current
    /// process to release the instance lock, so a successful return means this
    /// process can exit.
    static func start(instance: Int) -> String? {
        let domain = "gui/\(getuid())"
        let service = "\(domain)/\(label(instance: instance))"
        do {
            if try runTool(launchctl, ["print", service]).status == 0 {
                let result = try runTool(launchctl, ["bootout", service])
                guard result.status == 0 else {
                    return result.failure("launchctl bootout")
                }
            }
            let result = try runTool(launchctl,
                                     ["bootstrap", domain, plistURL(instance: instance).path])
            guard result.status == 0 else {
                return result.failure("launchctl bootstrap")
            }
            return nil
        } catch {
            return "could not run launchctl: \(error.localizedDescription)"
        }
    }

    private static let launchctl = URL(fileURLWithPath: "/bin/launchctl")

    /// Removes the agent and stops the job. When launchd is the parent of this
    /// process, stopping the job also ends this process.
    static func unregister(instance: Int) {
        try? FileManager.default.removeItem(at: plistURL(instance: instance))
        // This fails when launchd has nothing loaded for the label, in which
        // case the file was the whole of the login item.
        _ = try? runTool(launchctl, ["bootout", "gui/\(getuid())/\(label(instance: instance))"])
    }
}

// MARK: - GitHub credentials

enum CredentialSource {
    case processEnvironment(String)
    case loginShell(String)
    case githubCLI
    case absent

    var summary: String {
        switch self {
        case .processEnvironment(let name):
            return "$\(name) from the environment"
        case .loginShell(let name):
            return "$\(name) from your login shell"
        case .githubCLI:
            return "the GitHub CLI (gh auth token)"
        case .absent:
            return "none found; public repositories only"
        }
    }
}

struct Credential {
    let token: String?
    let source: CredentialSource
}

/// Asks the login shell for the token. Written for the shell rather than for
/// any one of them, so it works under zsh, bash, and sh alike.
let tokenProbeScript = """
token="${GH_TOKEN-}"; origin=GH_TOKEN
if [ -z "$token" ]; then token="${GITHUB_TOKEN-}"; origin=GITHUB_TOKEN; fi
if [ -z "$token" ] && command -v gh >/dev/null 2>&1; then
  token="$(gh auth token 2>/dev/null)"; origin=gh
fi
if [ -z "$token" ]; then origin=none; fi
printf '\\036%s\\036%s\\036' "$origin" "$token"
"""

func loginShellPath() -> String {
    if let entry = getpwuid(getuid()), let shell = entry.pointee.pw_shell {
        let path = String(cString: shell)
        if !path.isEmpty {
            return path
        }
    }
    return "/bin/sh"
}

/// Runs the user's shell as an interactive login shell and reads the token out
/// of it. launchd starts login items with almost no environment, so this is
/// how an indicator started at login sees the same GH_TOKEN a terminal sees.
func askLoginShellForToken() -> Credential {
    guard let text = try? runTool(URL(fileURLWithPath: loginShellPath()),
                                  ["-ilc", tokenProbeScript]).output else {
        return Credential(token: nil, source: .absent)
    }
    // The profile may print before the script does, so the fields are counted
    // back from the end.
    let fields = text.components(separatedBy: shellFieldSeparator)
    guard fields.count >= 3 else {
        return Credential(token: nil, source: .absent)
    }
    let origin = fields[fields.count - 3]
    let token = fields[fields.count - 2].trimmingCharacters(in: .whitespacesAndNewlines)
    guard !token.isEmpty else {
        return Credential(token: nil, source: .absent)
    }
    if origin == "gh" {
        return Credential(token: token, source: .githubCLI)
    }
    return Credential(token: token, source: .loginShell(origin))
}

func environmentCredential() -> Credential? {
    for name in ["GH_TOKEN", "GITHUB_TOKEN"] {
        if let value = ProcessInfo.processInfo.environment[name], !value.isEmpty {
            return Credential(token: value, source: .processEnvironment(name))
        }
    }
    return nil
}

/// Finds the token once and hands the same answer to everyone who asks, until
/// something invalidates it. All methods run on the main thread; the shell runs
/// off it.
final class CredentialResolver {
    private var resolved: Credential?
    private var waiting: [(Credential) -> Void] = []
    private var running = false

    func resolve(_ completion: @escaping (Credential) -> Void) {
        if let resolved {
            completion(resolved)
            return
        }
        waiting.append(completion)
        guard !running else { return }
        running = true
        if let direct = environmentCredential() {
            finish(direct)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let credential = askLoginShellForToken()
            DispatchQueue.main.async {
                self.finish(credential)
            }
        }
    }

    /// Drops the answer, so the next request asks the shell again. Used when
    /// GitHub rejects the token.
    func invalidate() {
        resolved = nil
    }

    private func finish(_ credential: Credential) {
        resolved = credential
        running = false
        let pending = waiting
        waiting.removeAll()
        for completion in pending {
            completion(credential)
        }
    }
}

// MARK: - Asking GitHub with the user's token

/// Asks GitHub through a reader of its own, with the token the login shell
/// gives, and asks the shell again when GitHub rejects the token.
func tokenFetch(_ credentials: CredentialResolver) -> BranchFetch {
    let reader = BranchReader()
    return { repository, branch, completion in
        credentials.resolve { credential in
            reader.fetch(repository: repository, branch: branch,
                         token: credential.token) { result in
                if case .failure(let failure) = result, failure.authenticationFailed {
                    credentials.invalidate()
                }
                completion(result)
            }
        }
    }
}

// MARK: - Indicator images

enum Indicator {
    case passing
    case failing
    case running
    case inconclusive
    case unknown
    case unconfigured

    var symbolName: String {
        switch self {
        case .passing: return "checkmark.circle.fill"
        case .failing: return "xmark.octagon.fill"
        case .running: return "clock.fill"
        case .inconclusive: return "minus.circle.fill"
        case .unknown: return "questionmark.circle.fill"
        case .unconfigured: return "gearshape"
        }
    }

    var color: NSColor {
        switch self {
        case .passing: return .systemGreen
        case .failing: return .systemRed
        case .running: return .systemOrange
        case .inconclusive, .unknown, .unconfigured: return .systemGray
        }
    }

    var label: String {
        switch self {
        case .passing: return "checks passed"
        case .failing: return "checks failed"
        case .running: return "checks running"
        case .inconclusive: return "no conclusive checks"
        case .unknown: return "state unknown"
        case .unconfigured: return "not configured"
        }
    }
}

func checkIndicator(_ state: CheckState) -> Indicator {
    switch state {
    case .passing: return .passing
    case .failing: return .failing
    case .running: return .running
    case .other: return .inconclusive
    }
}

/// The shapes differ as well as the colors, so the indicator still reads when
/// red and green do not.
func indicatorImage(_ indicator: Indicator, pointSize: CGFloat) -> NSImage? {
    guard let symbol = NSImage(systemSymbolName: indicator.symbolName,
                               accessibilityDescription: indicator.label) else {
        return nil
    }
    let configuration = NSImage.SymbolConfiguration(pointSize: pointSize, weight: .regular)
        .applying(NSImage.SymbolConfiguration(paletteColors: [indicator.color]))
    let image = symbol.withSymbolConfiguration(configuration)
    image?.isTemplate = false
    return image
}

// MARK: - Wording

/// Capitalizes the first letter and leaves the rest, which is what a
/// sentence wants and what `capitalized` does not do.
func sentenceCase(_ text: String) -> String {
    text.prefix(1).uppercased() + text.dropFirst()
}

func healthWord(_ health: Health) -> String {
    switch health {
    case .passing: return "passing"
    case .failing: return "failing"
    case .running: return "running"
    case .inconclusive: return "no result"
    }
}

func countsSentence(_ status: BranchStatus) -> String {
    var parts: [String] = []
    let passed = status.count(.passing)
    let failed = status.count(.failing)
    let running = status.count(.running)
    let other = status.count(.other)
    if failed > 0 { parts.append("\(failed) failed") }
    if running > 0 { parts.append("\(running) running") }
    if passed > 0 { parts.append("\(passed) passed") }
    if other > 0 { parts.append("\(other) with no verdict") }
    if parts.isEmpty { return "no checks reported" }
    return parts.joined(separator: ", ")
}

/// Failures first, then work in progress, then everything else, and passes
/// last, with names in order within each group.
func checksInMenuOrder(_ checks: [CheckSummary]) -> [CheckSummary] {
    checks.sorted {
        checkRank($0.state) != checkRank($1.state)
            ? checkRank($0.state) < checkRank($1.state)
            : $0.name.localizedStandardCompare($1.name) == .orderedAscending
    }
}

/// What the checks on a branch add up to, and how many of them there are.
/// The wording of an inconclusive branch already carries both, so it is not
/// given a prefix that would say the same thing twice.
func summarySentence(_ status: BranchStatus) -> String {
    status.health == .inconclusive
        ? countsSentence(status)
        : "\(healthWord(status.health)): \(countsSentence(status))"
}

func checkRank(_ state: CheckState) -> Int {
    switch state {
    case .failing: return 0
    case .running: return 1
    case .other: return 2
    case .passing: return 3
    }
}

// MARK: - Settings window

final class SettingsWindowController: NSWindowController, NSTextFieldDelegate {
    var onSave: (() -> Void)?

    private let settings: Settings
    private let credentials: CredentialResolver
    private let repositoryField = NSTextField()
    private let branchField = NSTextField()
    private let nameField = NSTextField()
    private let intervalField = NSTextField()
    private let showNameButton = NSButton(checkboxWithTitle: "Show the name in the menu bar",
                                          target: nil, action: nil)
    private let handOffButton = NSPopUpButton()
    private let folderField = NSTextField()
    private let relayField = NSTextField()
    private let tokenLabel = NSTextField(labelWithString: "Looking…")
    private let messageLabel = NSTextField(labelWithString: "")
    private var hasBeenPlaced = false

    init(settings: Settings, credentials: CredentialResolver) {
        self.settings = settings
        self.credentials = credentials
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 260),
                              styleMask: [.titled, .closable],
                              backing: .buffered,
                              defer: false)
        window.title = "\(applicationName) indicator \(settings.instance)"
        window.isReleasedWhenClosed = false
        super.init(window: window)
        buildContent()
    }

    required init?(coder: NSCoder) {
        fatalError("SettingsWindowController is built in code")
    }

    private func caption(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.alignment = .right
        return label
    }

    private func buildContent() {
        repositoryField.placeholderString = "owner/name"
        repositoryField.delegate = self
        branchField.placeholderString = defaultBranchName
        nameField.placeholderString = "the repository's name"
        folderField.placeholderString = "~"
        relayField.placeholderString = "host:port, or empty to only ask GitHub"
        handOffButton.addItems(withTitles: [noAgentTitle] + Agent.allCases.map(\.title))
        intervalField.alignment = .right

        tokenLabel.lineBreakMode = .byTruncatingTail
        tokenLabel.textColor = .secondaryLabelColor
        messageLabel.textColor = .systemRed
        messageLabel.lineBreakMode = .byWordWrapping
        messageLabel.maximumNumberOfLines = 2

        for field in [repositoryField, branchField, nameField, folderField, relayField] {
            field.widthAnchor.constraint(equalToConstant: 320).isActive = true
        }
        intervalField.widthAnchor.constraint(equalToConstant: 64).isActive = true

        let intervalRow = NSStackView(views: [intervalField,
                                              NSTextField(labelWithString: "seconds")])
        intervalRow.orientation = .horizontal
        intervalRow.alignment = .firstBaseline
        intervalRow.spacing = 6

        let grid = NSGridView(views: [
            [caption("Repository:"), repositoryField],
            [caption("Branch:"), branchField],
            [caption("Menu bar name:"), nameField],
            [caption("Check every:"), intervalRow],
            [NSGridCell.emptyContentView, showNameButton],
            [caption("When it turns red:"), handOffButton],
            [caption("Session folder:"), folderField],
            [caption("Webhook relay:"), relayField],
            [caption("GitHub token:"), tokenLabel],
        ])
        grid.column(at: 0).xPlacement = .trailing
        grid.rowAlignment = .firstBaseline
        grid.rowSpacing = 10
        grid.columnSpacing = 10
        grid.translatesAutoresizingMaskIntoConstraints = false

        let cancelButton = NSButton(title: "Cancel", target: self, action: #selector(cancel(_:)))
        cancelButton.keyEquivalent = "\u{1b}"
        let saveButton = NSButton(title: "Save", target: self, action: #selector(save(_:)))
        saveButton.keyEquivalent = "\r"
        let buttons = NSStackView(views: [cancelButton, saveButton])
        buttons.orientation = .horizontal
        buttons.spacing = 10
        buttons.translatesAutoresizingMaskIntoConstraints = false

        messageLabel.translatesAutoresizingMaskIntoConstraints = false

        let root = NSView()
        root.addSubview(grid)
        root.addSubview(messageLabel)
        root.addSubview(buttons)
        NSLayoutConstraint.activate([
            grid.topAnchor.constraint(equalTo: root.topAnchor, constant: 20),
            grid.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 20),
            grid.trailingAnchor.constraint(lessThanOrEqualTo: root.trailingAnchor, constant: -20),
            messageLabel.topAnchor.constraint(equalTo: grid.bottomAnchor, constant: 12),
            messageLabel.leadingAnchor.constraint(equalTo: grid.leadingAnchor),
            messageLabel.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -20),
            buttons.topAnchor.constraint(equalTo: messageLabel.bottomAnchor, constant: 12),
            buttons.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -20),
            buttons.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -20),
        ])
        window?.contentView = root
        if let root = window?.contentView {
            window?.setContentSize(root.fittingSize)
        }
    }

    /// Fills the fields from the stored settings and shows the window.
    func present() {
        repositoryField.stringValue = settings.repository?.slug ?? ""
        branchField.stringValue = settings.branch
        nameField.stringValue = settings.menuBarName
        intervalField.stringValue = String(Int(settings.refreshSeconds.rounded()))
        showNameButton.state = settings.showsMenuBarName ? .on : .off
        handOffButton.selectItem(at: settings.handOff.map { Agent.allCases.firstIndex(of: $0)! + 1 } ?? 0)
        folderField.stringValue = settings.handOffFolder
        relayField.stringValue = settings.relay?.description ?? ""
        messageLabel.stringValue = ""
        tokenLabel.stringValue = "Looking…"
        credentials.resolve { [weak self] credential in
            self?.tokenLabel.stringValue = credential.source.summary
        }
        if !hasBeenPlaced {
            window?.center()
            hasBeenPlaced = true
        }
        NSApp.activate(ignoringOtherApps: true)
        showWindow(nil)
        window?.makeKeyAndOrderFront(nil)
        window?.makeFirstResponder(repositoryField)
    }

    /// Rewrites what was typed in the repository field as owner/name, and
    /// takes the branch from the address when the field held one.
    func controlTextDidEndEditing(_ notification: Notification) {
        guard notification.object as AnyObject === repositoryField,
              let parsed = parseRepository(repositoryField.stringValue) else {
            return
        }
        repositoryField.stringValue = parsed.repository.slug
        if let branch = parsed.branch,
           branchField.stringValue.trimmingCharacters(in: .whitespaces).isEmpty {
            branchField.stringValue = branch
        }
    }

    @objc private func cancel(_ sender: Any?) {
        close()
    }

    @objc private func save(_ sender: Any?) {
        guard let parsed = parseRepository(repositoryField.stringValue) else {
            complain("Write the repository as owner/name, or paste its address on GitHub.",
                     in: repositoryField)
            return
        }
        var branch = branchField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if branch.isEmpty {
            branch = parsed.branch ?? defaultBranchName
        }
        guard isBranchName(branch) else {
            complain("That is not a branch name.", in: branchField)
            return
        }
        let typed = intervalField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let seconds = Double(typed), seconds >= minimumRefreshSeconds else {
            complain("Check at least every \(Int(minimumRefreshSeconds)) seconds.", in: intervalField)
            return
        }
        let choice = handOffButton.indexOfSelectedItem
        let handOff = choice == 0 ? nil : Agent.allCases[choice - 1]
        let folder = folderField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        var isFolder: ObjCBool = false
        guard handOff == nil
              || FileManager.default.fileExists(atPath: folderURL(folder).path,
                                                isDirectory: &isFolder) && isFolder.boolValue else {
            complain("The session folder has to be a folder that exists.", in: folderField)
            return
        }
        let relayText = relayField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let relay = parseRelayAddress(relayText)
        guard relayText.isEmpty || relay != nil else {
            complain("Write the relay as host:port.", in: relayField)
            return
        }
        settings.repository = parsed.repository
        settings.branch = branch
        settings.menuBarName = nameField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        settings.showsMenuBarName = showNameButton.state == .on
        settings.refreshSeconds = seconds
        settings.handOff = handOff
        settings.handOffFolder = folder
        settings.relay = relay
        close()
        onSave?()
    }

    private func complain(_ message: String, in field: NSTextField) {
        messageLabel.stringValue = message
        window?.makeFirstResponder(field)
    }
}

// MARK: - Menu bar app

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let lock: InstanceLock
    private let settings: Settings
    private let registersLoginItem: Bool
    private let credentials = CredentialResolver()
    private lazy var monitor = BranchMonitor { [credentials] in tokenFetch(credentials) }

    private var statusItem: NSStatusItem!
    private var menu: NSMenu!
    private var headerItem: NSMenuItem!
    private var summaryItem: NSMenuItem!
    private var commitItem: NSMenuItem!
    private var dynamicStart: NSMenuItem!
    private var dynamicEnd: NSMenuItem!
    private var openItem: NSMenuItem!
    private var bannerItem: NSMenuItem!
    private var handOffItem: NSMenuItem!
    private var relayItem: NSMenuItem!
    private var footerItem: NSMenuItem!
    private var settingsWindow: SettingsWindowController?
    private var latest: MonitorState = .unconfigured
    private var dynamicSignature: String?
    private let flyby = FlybyController()
    private var lastHealth: Health?
    private var lastFailure: String?
    /// The repository and branch the last answer was about.
    private var lastTarget: String?
    /// The last commit the branch passed on, for the agent a red branch is
    /// handed to.
    private var lastGreen: String?
    private let reader = BranchReader()
    /// Whether the branch has been handed off since it last passed, or was
    /// already red when the indicator first heard about it.
    private var handedOff = false
    private let demonstratesBanner: Bool

    init(lock: InstanceLock, settings: Settings, registersLoginItem: Bool,
         demonstratesBanner: Bool) {
        self.lock = lock
        self.settings = settings
        self.registersLoginItem = registersLoginItem
        self.demonstratesBanner = demonstratesBanner
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMainMenu()
        if registersLoginItem {
            if let problem = LoginItem.register(instance: lock.number) {
                report("could not write the login item: \(problem)")
            } else if !LoginItem.isCurrentProcessManaged(instance: lock.number) {
                if let problem = LoginItem.start(instance: lock.number) {
                    report("could not start the login item: \(problem)")
                } else {
                    NSApp.terminate(nil)
                    return
                }
            }
        }
        buildStatusItem()
        monitor.onChange = { [weak self] state in
            // Every round of work is a chance to notice that the lock file
            // went away.
            self?.lock.reassertIfNeeded()
            self?.apply(state)
        }
        monitor.onRelayChange = { [weak self] in
            self?.refreshMenuContents()
        }
        apply(.unconfigured)
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(wokeUp),
            name: NSWorkspace.didWakeNotification,
            object: nil
        )
        applySettings()
        if demonstratesBanner {
            demonstrateBanner()
        } else if settings.repository == nil {
            openSettings(nil)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// An accessory app shows no menu bar of its own, but the main menu is
    /// still where the key equivalents for editing text come from, so the
    /// settings window needs one to accept Command-V.
    private func buildMainMenu() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        let quit = appMenu.addItem(withTitle: "Quit \(applicationName)",
                                   action: #selector(NSApplication.terminate(_:)),
                                   keyEquivalent: "q")
        quit.target = NSApp
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")),
                                    keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)),
                         keyEquivalent: "a")
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)

        NSApp.mainMenu = mainMenu
    }

    private func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        menu = NSMenu()
        menu.delegate = self

        headerItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")
        summaryItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")
        commitItem = menu.addItem(withTitle: "", action: #selector(openChecksPage(_:)),
                                  keyEquivalent: "")
        commitItem.target = self

        dynamicStart = NSMenuItem.separator()
        menu.addItem(dynamicStart)
        dynamicEnd = NSMenuItem.separator()
        menu.addItem(dynamicEnd)

        openItem = menu.addItem(withTitle: "Open Checks on GitHub",
                                action: #selector(openChecksPage(_:)), keyEquivalent: "")
        openItem.target = self
        let refresh = menu.addItem(withTitle: "Refresh Now", action: #selector(refresh(_:)),
                                   keyEquivalent: "r")
        refresh.target = self

        menu.addItem(.separator())
        let settingsItem = menu.addItem(withTitle: "Settings…", action: #selector(openSettings(_:)),
                                        keyEquivalent: ",")
        settingsItem.target = self
        let newItem = menu.addItem(withTitle: "New Indicator",
                                   action: #selector(newIndicator(_:)), keyEquivalent: "n")
        newItem.target = self
        newItem.keyEquivalentModifierMask = [.command]
        // The flyby is behind Option, since waiting for a branch to break is
        // no way to show anybody what it looks like. It is an alternate of the
        // item above rather than an item that is hidden and unhidden: AppKit
        // swaps alternates as the modifier is pressed and released, whereas
        // anything this app hides for itself can only be decided once, when
        // the menu is asked for its contents. An alternate has to follow its
        // twin immediately and carry the same key equivalent, differing only
        // in the modifiers, which also gives it Command-Option-N.
        bannerItem = menu.addItem(withTitle: "Fly the Banner",
                                  action: #selector(dropBanner(_:)), keyEquivalent: "n")
        bannerItem.target = self
        bannerItem.keyEquivalentModifierMask = [.command, .option]
        bannerItem.isAlternate = true
        bannerItem.toolTip = "Send the plane and its banner round now, whatever state the branch is in."

        menu.addItem(.separator())
        handOffItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")
        handOffItem.target = self
        handOffItem.isHidden = true
        relayItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")
        footerItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")

        menu.addItem(.separator())
        let quit = menu.addItem(withTitle: "Quit \(applicationName)",
                                action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quit.target = NSApp
        let disable = menu.addItem(withTitle: "Disable and Quit",
                                   action: #selector(disableAndQuit(_:)), keyEquivalent: "")
        disable.target = self
        disable.toolTip = "Stop starting this indicator at login, and quit it now."

        statusItem.menu = menu
    }

    private func applySettings() {
        monitor.configure(repository: settings.repository,
                          branch: settings.branch,
                          refreshSeconds: settings.refreshSeconds,
                          relay: settings.relay)
        updateStatusItem()
    }

    private func apply(_ state: MonitorState) {
        latest = state
        updateStatusItem()
        refreshMenuContents()
        if case .ready(let status) = state {
            considerRed(status)
        }
    }

    /// Flies the banner when the branch turns red, and again if it is still
    /// red but has broken in a new way, which includes being red at a new
    /// commit. Hands the branch to an agent when it turns red, and not again
    /// until it has passed.
    ///
    /// Nothing happens for the first answer about a branch: a branch that
    /// was already red when the indicator started, or when the settings
    /// changed to it, has not just turned, and a login should not fill the
    /// screen with banners for every indicator that comes back with it.
    private func considerRed(_ status: BranchStatus) {
        let failing = status.checks.filter { $0.state == .failing }
            .map { $0.suite.map { "suite \($0)" } ?? $0.name }.sorted()
        let signature = ([status.sha] + failing).joined(separator: "\u{1}")
        let target = "\(status.repository.slug)\u{1}\(status.branch)"
        let isFirst = target != lastTarget
        let previous = lastHealth
        let previousFailure = lastFailure
        lastTarget = target
        lastHealth = status.health
        lastFailure = signature
        if isFirst {
            lastGreen = nil
        }
        if status.health == .passing {
            lastGreen = status.sha
        }
        guard !demonstratesBanner else { return }
        if isFirst {
            handedOff = status.health == .failing
            return
        }
        if status.health == .passing {
            handedOff = false
        }
        guard status.health == .failing else { return }
        if !handedOff {
            handedOff = true
            handOffIfWanted(status)
        }
        if previous != .failing || signature != previousFailure {
            flyby.show(bannerText, from: statusItem?.button)
        }
    }

    /// Hands the branch to the chosen agent, with a report of what failed.
    private func handOffIfWanted(_ status: BranchStatus) {
        guard let agent = settings.handOff else { return }
        let lastGreen = self.lastGreen
        let folder = settings.handOffFolderURL
        credentials.resolve { [weak self] credential in
            self?.reader.failureReport(status, lastGreen: lastGreen, token: credential.token) { report in
                handOff(status, report: report, to: agent, in: folder) { outcome in
                    self?.show(outcome)
                }
            }
        }
    }

    /// Puts the outcome of the last hand-off in the menu. Choosing it opens
    /// the session, when there is a session to open.
    private func show(_ outcome: HandOffOutcome) {
        handOffItem.isHidden = false
        switch outcome {
        case .started(let summary, let url):
            handOffItem.title = summary
            handOffItem.toolTip = nil
            handOffItem.representedObject = url
            handOffItem.action = url == nil ? nil : #selector(openItemURL(_:))
        case .failed(let summary, let detail):
            handOffItem.title = summary
            handOffItem.toolTip = detail
            handOffItem.representedObject = nil
            handOffItem.action = nil
            report(outcome.summary)
        }
    }

    /// What the banner says: the name shown in the menu bar, and that it is
    /// red. It is short because it goes by quickly.
    private var bannerText: String { "\(settings.displayName) is red" }

    @objc private func dropBanner(_ sender: Any?) {
        flyby.show(bannerText, from: statusItem?.button)
    }

    /// Flies the banner immediately so the effect can be looked at, then quits.
    private func demonstrateBanner() {
        // A status item made a moment ago has not been placed in the bar yet,
        // so the flight waits for the turn of the run loop in which it is.
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.flyby.show(self.bannerText, from: self.statusItem?.button) {
                NSApp.terminate(nil)
            }
        }
    }

    /// The newest result worth showing, which is the last one that arrived
    /// even when the poll after it did not.
    private var shownStatus: BranchStatus? {
        switch latest {
        case .ready(let status): return status
        case .failed(_, let stale): return stale
        case .unconfigured, .waiting: return nil
        }
    }

    private var indicator: Indicator {
        switch latest {
        case .unconfigured:
            return .unconfigured
        case .waiting, .failed:
            return .unknown
        case .ready(let status):
            switch status.health {
            case .passing: return .passing
            case .failing: return .failing
            case .running: return .running
            case .inconclusive: return .inconclusive
            }
        }
    }

    private func updateStatusItem() {
        guard let button = statusItem?.button else { return }
        button.image = indicatorImage(indicator, pointSize: 14)
        if settings.showsMenuBarName {
            button.title = settings.displayName
            button.imagePosition = .imageLeading
        } else {
            button.title = ""
            button.imagePosition = .imageOnly
        }
        button.toolTip = tooltip()
    }

    private func tooltip() -> String {
        let target = settings.repository.map { "\($0.slug) on \(settings.branch)" }
            ?? "no repository chosen"
        switch latest {
        case .unconfigured:
            return "\(applicationName): no repository chosen"
        case .waiting:
            return "\(target): asking GitHub"
        case .failed(let failure, _):
            return "\(target) — \(failure.detail ?? failure.message)"
        case .ready(let status):
            return "\(target) — \(summarySentence(status))"
        }
    }

    // MARK: Menu contents

    func menuNeedsUpdate(_ menu: NSMenu) {
        refreshMenuContents()
    }

    private func refreshMenuContents() {
        guard menu != nil else { return }
        let status = shownStatus

        if let repository = settings.repository {
            headerItem.title = "\(repository.slug) · \(settings.branch)"
        } else {
            headerItem.title = "No repository chosen"
        }

        summaryItem.toolTip = nil
        switch latest {
        case .unconfigured:
            summaryItem.title = "Choose one in Settings"
        case .waiting:
            summaryItem.title = "Asking GitHub…"
        case .failed(let failure, _):
            summaryItem.title = failure.message
            summaryItem.toolTip = failure.detail
        case .ready(let ready):
            summaryItem.title = sentenceCase(summarySentence(ready))
        }
        summaryItem.image = indicatorImage(indicator, pointSize: 11)

        if let status {
            var title = status.shortSHA
            if let headline = status.headline {
                title += headline.count > 60
                    ? "  \(headline.prefix(59))…"
                    : "  \(headline)"
            }
            commitItem.title = title
            commitItem.isHidden = false
        } else {
            commitItem.isHidden = true
        }
        openItem.isHidden = status == nil
        updateRelayItem()
        footerItem.title = footerText()
        rebuildDynamicItems(status)
    }

    /// Says whether changes are coming from the relay, or from asking
    /// GitHub over and over.
    private func updateRelayItem() {
        relayItem.toolTip = nil
        switch monitor.relayState {
        case .off:
            relayItem.isHidden = true
            return
        case .connecting:
            relayItem.title = "Connecting to the webhook relay…"
        case .connected:
            if let unreadable = monitor.unreadableDelivery {
                relayItem.title = "Some deliveries from the webhook relay could not be read"
                relayItem.toolTip = unreadable
            } else {
                relayItem.title = "Hearing about changes from the webhook relay"
            }
        case .unavailable(let reason):
            relayItem.title = monitor.nextPoll.map {
                "Webhook relay unavailable; asking GitHub at \(timeOfDayFormatter.string(from: $0))"
            } ?? "Webhook relay unavailable"
            relayItem.toolTip = sentenceCase(reason)
        }
        relayItem.isHidden = false
    }

    private func footerText() -> String {
        var parts = ["Indicator \(lock.number)"]
        if let status = shownStatus {
            parts.append("checked \(timeOfDayFormatter.string(from: status.fetched))")
            if let rate = status.rateLimit {
                parts.append("\(rate.remaining) of \(rate.limit) requests left")
            }
        }
        if !LoginItem.isRegistered(instance: lock.number) {
            parts.append("not starting at login")
        }
        return parts.joined(separator: " · ")
    }

    /// Replaces everything between the two separators that mark the part of
    /// the menu that follows the checks.
    private func rebuildDynamicItems(_ status: BranchStatus?) {
        let signature = status.map { current in
            current.sha + current.checks.map { "\u{1}\($0.name)\u{2}\($0.state.rawValue)" }
                .joined()
        } ?? ""
        guard signature != dynamicSignature else { return }
        dynamicSignature = signature
        let start = menu.index(of: dynamicStart)
        guard start >= 0 else { return }
        var index = start + 1
        while menu.index(of: dynamicEnd) > index {
            menu.removeItem(at: index)
        }
        guard let status, !status.checks.isEmpty else {
            // Nothing between the separators, so one of them would draw a
            // second line against the first.
            dynamicEnd.isHidden = true
            return
        }
        dynamicEnd.isHidden = false

        let pressing = checksInMenuOrder(
            status.checks.filter { $0.state == .failing || $0.state == .running })
        for check in pressing.prefix(inlineCheckLimit) {
            menu.insertItem(checkItem(check), at: index)
            index += 1
        }
        if pressing.count > inlineCheckLimit {
            let more = NSMenuItem(title: "\(pressing.count - inlineCheckLimit) more, listed under All Checks",
                                  action: nil, keyEquivalent: "")
            menu.insertItem(more, at: index)
            index += 1
        }

        let all = NSMenuItem(title: "All Checks", action: nil, keyEquivalent: "")
        let submenu = NSMenu()
        for check in checksInMenuOrder(status.checks) {
            submenu.addItem(checkItem(check))
        }
        all.submenu = submenu
        menu.insertItem(all, at: index)
    }

    private func checkItem(_ check: CheckSummary) -> NSMenuItem {
        let item = NSMenuItem(title: check.name, action: #selector(openItemURL(_:)), keyEquivalent: "")
        item.target = self
        item.image = indicatorImage(checkIndicator(check.state), pointSize: 11)
        item.representedObject = check.url
        item.toolTip = check.detail
        if check.url == nil {
            item.action = nil
        }
        return item
    }

    // MARK: Actions

    @objc private func wokeUp() {
        monitor.woke()
    }

    @objc private func refresh(_ sender: Any?) {
        monitor.refreshNow()
    }

    @objc private func openItemURL(_ sender: NSMenuItem) {
        guard let url = sender.representedObject as? URL else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func openChecksPage(_ sender: Any?) {
        guard let url = shownStatus?.checksURL else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func openSettings(_ sender: Any?) {
        if settingsWindow == nil {
            let controller = SettingsWindowController(settings: settings, credentials: credentials)
            controller.onSave = { [weak self] in
                self?.applySettings()
            }
            settingsWindow = controller
        }
        settingsWindow?.present()
    }

    /// Starts another copy of this executable. It takes the lowest instance
    /// number nothing else is using, so it gets its own settings and its own
    /// login item.
    @objc private func newIndicator(_ sender: Any?) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: LoginItem.executablePath)
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            let alert = NSAlert()
            alert.messageText = "Could not start another indicator."
            alert.informativeText = error.localizedDescription
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
        }
    }

    @objc private func disableAndQuit(_ sender: Any?) {
        LoginItem.unregister(instance: lock.number)
        NSApp.terminate(nil)
    }

    private func report(_ message: String) {
        FileHandle.standardError.write(Data("\(applicationName): \(message)\n".utf8))
    }
}

// MARK: - Command line

struct Options {
    var instance: Int?
    var repository: RepositoryReference?
    var branch: String?
    var once = false
    var registersLoginItem = true
    var demonstratesBanner = false
    var handsOff = false
    var waitsForInstance = false
}

let usage = """
usage: \(applicationName) [--instance N] [--repo OWNER/NAME] [--branch BRANCH]
              [--once] [--banner] [--hand-off] [--no-startup]

  --instance N   run as indicator N rather than the lowest free number
  --repo R       track this repository; in the menu bar, also remember it
                 for this indicator
  --branch B     track this branch; in the menu bar, also remember it for
                 this indicator
  --once         report the branch on standard output and exit, without
                 touching the menu bar; exits 0 when the branch is passing,
                 1 when it is failing, and 2 otherwise
  --banner       fly the plane and its banner straight away and quit when
                 it has landed, to see what it looks like
  --hand-off     hand the branch to the agent chosen in the settings
                 straight away, whatever state it is in, and exit, without
                 touching the menu bar
  --no-startup   leave the login item alone for this run
"""

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("\(applicationName): \(message)\n\(usage)\n".utf8))
    exit(2)
}

func parseOptions() -> Options {
    var options = Options()
    var arguments = CommandLine.arguments.dropFirst().makeIterator()
    while let argument = arguments.next() {
        switch argument {
        case "--instance":
            guard let value = arguments.next(), let number = Int(value),
                  number >= 1, number <= maximumInstances else {
                fail("--instance takes a number from 1 to \(maximumInstances)")
            }
            options.instance = number
        case "--repo", "--repository":
            guard let value = arguments.next(), let parsed = parseRepository(value) else {
                fail("--repo takes a repository, written owner/name")
            }
            options.repository = parsed.repository
            if options.branch == nil {
                options.branch = parsed.branch
            }
        case "--branch":
            guard let value = arguments.next(), isBranchName(value) else {
                fail("--branch takes a branch name")
            }
            options.branch = value
        case "--once":
            options.once = true
        case "--banner":
            options.demonstratesBanner = true
            options.registersLoginItem = false
        case "--hand-off":
            options.handsOff = true
        case "--no-startup":
            options.registersLoginItem = false
        case "--launchd":
            options.registersLoginItem = false
            options.waitsForInstance = true
        case "--help", "-h":
            print(usage)
            exit(0)
        default:
            fail("unknown option \(argument)")
        }
    }
    if options.waitsForInstance, options.instance == nil {
        fail("--launchd requires --instance")
    }
    if [options.once, options.handsOff, options.demonstratesBanner].filter({ $0 }).count > 1 {
        fail("--once, --banner, and --hand-off cannot be used together")
    }
    return options
}

// MARK: - One-off mode

func describe(_ status: BranchStatus) -> String {
    var head = "\(status.repository.slug)@\(status.branch) \(status.shortSHA)"
    if let headline = status.headline {
        head += " \(headline)"
    }
    var lines = [head, "  \(summarySentence(status))"]
    for check in status.checks where check.state == .failing || check.state == .running {
        let word = check.state == .failing ? "failed" : "running"
        lines.append("  \(word): \(check.name) (\(check.detail))")
    }
    return lines.joined(separator: "\n")
}

/// Asks GitHub about the branch once, without the menu bar, and gives the
/// answer, the reader and the token to `body`, which is expected to exit.
func fetchOnce(options: Options, settings: Settings,
               _ body: @escaping (BranchStatus, BranchReader, String?) -> Void) -> Never {
    guard let repository = options.repository ?? settings.repository else {
        fail("no repository is configured for indicator \(settings.instance); pass --repo owner/name")
    }
    let branch = options.branch ?? settings.branch
    let credentials = CredentialResolver()
    let reader = BranchReader()
    credentials.resolve { credential in
        print("token: \(credential.source.summary)")
        reader.fetch(repository: repository, branch: branch,
                     token: credential.token) { result in
            switch result {
            case .failure(let failure):
                print("\(repository.slug)@\(branch): \(failure.detail ?? failure.message)")
                exit(2)
            case .success(let answer):
                body(BranchStatus(repository: answer.repository, branch: branch, sha: answer.sha,
                                  headline: answer.headline, checks: answer.checks, fetched: Date(),
                                  rateLimit: answer.rateLimit),
                     reader, credential.token)
            }
        }
    }
    dispatchMain()
}

/// Reports the branch once on standard output. Checks the settings, the token
/// discovery, and the reading of GitHub's answers without the menu bar.
func runOnce(options: Options) -> Never {
    fetchOnce(options: options, settings: Settings(instance: options.instance ?? 1)) { status, _, _ in
        print(describe(status))
        switch status.health {
        case .passing: exit(0)
        case .failing: exit(1)
        case .running, .inconclusive: exit(2)
        }
    }
}

/// Hands the branch off once, so that what the agent is given can be seen
/// without waiting for the branch to break.
func runHandOff(options: Options) -> Never {
    let settings = Settings(instance: options.instance ?? 1)
    guard let agent = settings.handOff else {
        FileHandle.standardError.write(Data(
            "\(applicationName): indicator \(settings.instance) hands red branches to no agent; choose one in its settings\n".utf8))
        exit(2)
    }
    fetchOnce(options: options, settings: settings) { status, reader, token in
        reader.failureReport(status, lastGreen: nil, token: token) { report in
            print(handOffPrompt(status, report: report))
            handOff(status, report: report, to: agent, in: settings.handOffFolderURL) { outcome in
                print(outcome.summary)
                if case .failed = outcome {
                    exit(1)
                }
                exit(0)
            }
        }
    }
}

// MARK: - Entry point

let options = parseOptions()

if options.handsOff {
    runHandOff(options: options)
}
if options.once {
    runOnce(options: options)
}

ProcessInfo.processInfo.automaticTerminationSupportEnabled = true
ProcessInfo.processInfo.disableAutomaticTermination(
    "ghbar keeps a menu bar indicator available."
)

let claim = options.instance.map {
    InstanceLock.claim($0, waiting: options.waitsForInstance)
} ?? InstanceLock.claimLowestFree()
let instanceLock: InstanceLock
switch claim {
case .claimed(let lock):
    instanceLock = lock
case .busy(let number):
    // Another indicator already holds this number, so this one steps aside.
    // Exiting cleanly keeps launchd from starting it again.
    FileHandle.standardError.write(
        Data("\(applicationName): indicator \(number) is already running\n".utf8))
    exit(0)
case .failed(let message):
    FileHandle.standardError.write(Data("\(applicationName): \(message)\n".utf8))
    exit(1)
}

let instanceSettings = Settings(instance: instanceLock.number)
if let repository = options.repository {
    instanceSettings.repository = repository
}
if let branch = options.branch {
    instanceSettings.branch = branch
}

let application = NSApplication.shared
let appDelegate = AppDelegate(lock: instanceLock, settings: instanceSettings,
                              registersLoginItem: options.registersLoginItem,
                              demonstratesBanner: options.demonstratesBanner)
application.delegate = appDelegate
application.setActivationPolicy(.accessory)
application.run()
