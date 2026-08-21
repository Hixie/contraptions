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

let applicationName = "ghbar"

/// Prefix for the preference domain, the lock file names, and the launchd
/// agent labels. The instance number is appended to it.
let identifierPrefix = "local.ghbar"

/// Highest instance number that automatic allocation considers.
let maximumInstances = 64

let defaultBranchName = "main"
let defaultRefreshSeconds = 60.0
let minimumRefreshSeconds = 15.0

let apiRoot = "https://api.github.com"
let apiVersion = "2022-11-28"
let checkRunsPageSize = 100

/// How many of a commit's checks the rollup query asks for by name. The
/// verdict it comes back with covers every check, however many there are.
let rollupContextLimit = 100

/// Marks the fields the login shell prints, so the token can be picked out of
/// whatever else the user's profile writes to standard output.
let shellFieldSeparator = "\u{1e}"

/// Longest inline list of failing or running checks in the menu. Anything past
/// this is reported as a count and stays reachable through the All Checks
/// submenu.
let inlineCheckLimit = 12

// MARK: - Repository references

struct RepositoryReference: Equatable {
    let owner: String
    let name: String

    var slug: String { "\(owner)/\(name)" }
}

func isRepositoryNameCharacter(_ character: Character) -> Bool {
    guard character.isASCII else { return false }
    return character.isLetter || character.isNumber || character == "." || character == "-"
        || character == "_"
}

func isRepositoryNameComponent(_ text: String) -> Bool {
    !text.isEmpty && text.allSatisfy(isRepositoryNameCharacter)
}

/// Reads "owner/name", "github.com/owner/name", and full repository or branch
/// URLs. A URL that names a branch, such as one ending in "/tree/main", also
/// yields that branch.
func parseRepository(_ text: String) -> (repository: RepositoryReference, branch: String?)? {
    var remainder = text.trimmingCharacters(in: .whitespacesAndNewlines)
    if let host = remainder.range(of: "github.com") {
        remainder = String(remainder[host.upperBound...])
    }
    while remainder.hasPrefix("/") || remainder.hasPrefix(":") {
        remainder.removeFirst()
    }
    if remainder.hasSuffix(".git") {
        remainder.removeLast(4)
    }
    let parts = remainder.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
    guard parts.count >= 2,
          isRepositoryNameComponent(parts[0]),
          isRepositoryNameComponent(parts[1]) else {
        return nil
    }
    let repository = RepositoryReference(owner: parts[0], name: parts[1])
    if parts.count >= 4, parts[2] == "tree" || parts[2] == "commits" {
        return (repository, parts[3...].joined(separator: "/"))
    }
    return (repository, nil)
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

// MARK: - Login item

/// The launchd user agent that owns one instance. An ordinary process writes
/// and loads the agent, then exits after its launchd copy starts waiting for
/// the instance lock.
enum LoginItem {
    private struct LaunchctlResult {
        let status: Int32
        let error: String
    }

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
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = ["print", "gui/\(getuid())/\(label(instance: instance))"]
        process.standardInput = FileHandle.nullDevice
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return false
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0,
              let text = String(data: data, encoding: .utf8) else {
            return false
        }
        return text.split(separator: "\n").contains {
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
        if runLaunchctl(["print", service]).status == 0 {
            let result = runLaunchctl(["bootout", service])
            guard result.status == 0 else {
                return result.error.isEmpty
                    ? "launchctl bootout exited with status \(result.status)"
                    : result.error
            }
        }
        let result = runLaunchctl([
            "bootstrap", domain, plistURL(instance: instance).path,
        ])
        guard result.status == 0 else {
            return result.error.isEmpty
                ? "launchctl exited with status \(result.status)"
                : result.error
        }
        return nil
    }

    private static func runLaunchctl(_ arguments: [String]) -> LaunchctlResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        let errors = Pipe()
        process.standardError = errors
        do {
            try process.run()
        } catch {
            return LaunchctlResult(status: -1, error: error.localizedDescription)
        }
        let data = errors.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let message = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return LaunchctlResult(status: process.terminationStatus, error: message)
    }

    /// Removes the agent and stops the job. When launchd is the parent of this
    /// process, stopping the job also ends this process.
    static func unregister(instance: Int) {
        try? FileManager.default.removeItem(at: plistURL(instance: instance))
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = ["bootout", "gui/\(getuid())/\(label(instance: instance))"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            // launchd has nothing loaded for this label, so the file was the
            // whole of the login item.
        }
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
    let process = Process()
    process.executableURL = URL(fileURLWithPath: loginShellPath())
    process.arguments = ["-ilc", tokenProbeScript]
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = FileHandle.nullDevice
    process.standardInput = FileHandle.nullDevice
    do {
        try process.run()
    } catch {
        return Credential(token: nil, source: .absent)
    }
    let output = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    guard let text = String(data: output, encoding: .utf8) else {
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

// MARK: - Check results

enum CheckState: String {
    case passing
    case failing
    case running
    /// Reported, but neither a pass nor a failure: skipped, neutral, or stale.
    case other
}

struct CheckSummary {
    let name: String
    let state: CheckState
    let detail: String
    let url: URL?
}

enum Health {
    case passing
    case failing
    case running
    /// Nothing reported a pass, a failure, or work in progress.
    case inconclusive
}

func rollUp(_ checks: [CheckSummary]) -> Health {
    if checks.contains(where: { $0.state == .failing }) { return .failing }
    if checks.contains(where: { $0.state == .running }) { return .running }
    if checks.contains(where: { $0.state == .passing }) { return .passing }
    return .inconclusive
}

func checkState(runStatus: String, conclusion: String?) -> CheckState {
    guard runStatus == "completed" else { return .running }
    switch conclusion {
    case "success":
        return .passing
    case "failure", "timed_out", "cancelled", "action_required", "startup_failure":
        return .failing
    default:
        return .other
    }
}

/// GitHub's own verdict over every check on a commit, whether or not the
/// token may see them one by one.
func health(rollupState state: String) -> Health {
    switch state {
    case "SUCCESS": return .passing
    case "FAILURE", "ERROR": return .failing
    case "PENDING", "EXPECTED": return .running
    default: return .inconclusive
    }
}

func checkState(commitStatus state: String) -> CheckState {
    switch state {
    case "success":
        return .passing
    case "failure", "error":
        return .failing
    case "pending":
        return .running
    default:
        return .other
    }
}

struct RateLimit {
    let remaining: Int
    let limit: Int
    let reset: Date
}

struct BranchStatus {
    let repository: RepositoryReference
    let branch: String
    let sha: String
    let headline: String?
    let checks: [CheckSummary]
    /// How many checks GitHub says are on the commit, which can be more than
    /// it is willing to name.
    let reportedCheckCount: Int
    /// How many of those it refused to name, for want of a permission.
    let unreadableCheckCount: Int
    let health: Health
    let fetched: Date
    let rateLimit: RateLimit?

    /// Checks that GitHub counted but did not name, and did not refuse
    /// either: they are past the end of the page that was asked for.
    var unlistedCheckCount: Int {
        max(0, reportedCheckCount - checks.count - unreadableCheckCount)
    }

    var shortSHA: String { String(sha.prefix(7)) }

    var checksURL: URL? {
        URL(string: "https://github.com/\(repository.slug)/commit/\(sha)/checks")
    }

    func count(_ state: CheckState) -> Int {
        checks.reduce(into: 0) { total, check in
            if check.state == state { total += 1 }
        }
    }
}

struct FetchFailure: Error {
    /// Short enough for a menu item.
    let message: String
    /// Everything known about the refusal, for the tooltip.
    var detail: String? = nil
    let authenticationFailed: Bool
}

// MARK: - GitHub requests

private struct CombinedStatusResponse: Decodable {
    struct Entry: Decodable {
        let context: String
        let state: String
        let description: String?
        let targetUrl: String?
    }
    let sha: String
    let statuses: [Entry]
}

private struct CheckRunsResponse: Decodable {
    struct Run: Decodable {
        let name: String
        let status: String
        let conclusion: String?
        let htmlUrl: String?
    }
    let checkRuns: [Run]
}

private struct CommitResponse: Decodable {
    struct Details: Decodable {
        let message: String
    }
    let commit: Details
}

/// Asks for one branch's head commit and GitHub's verdict over the checks on
/// it. The verdict is computed by GitHub across every check, so it is right
/// even when the token may not see the checks one by one, which is the case
/// for a fine-grained personal access token: those cannot be granted the
/// Checks permission at all.
let rollupQuery = """
query($owner: String!, $name: String!, $branch: String!) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: $branch) {
      target {
        ... on Commit {
          oid
          messageHeadline
          statusCheckRollup {
            state
            contexts(first: \(rollupContextLimit)) {
              totalCount
              nodes {
                kind: __typename
                ... on CheckRun { name status conclusion detailsUrl }
                ... on StatusContext { context state targetUrl description }
              }
            }
          }
        }
      }
    }
  }
}
"""

private struct RollupResponse: Decodable {
    struct Complaint: Decodable {
        let message: String
    }

    struct Node: Decodable {
        let kind: String
        let name: String?
        let status: String?
        let conclusion: String?
        let detailsUrl: String?
        let context: String?
        let state: String?
        let targetUrl: String?
        let description: String?
    }

    struct Contexts: Decodable {
        let totalCount: Int
        let nodes: [Node?]
    }

    struct Rollup: Decodable {
        let state: String
        let contexts: Contexts
    }

    struct Target: Decodable {
        let oid: String?
        let messageHeadline: String?
        let statusCheckRollup: Rollup?
    }

    struct Ref: Decodable {
        let target: Target?
    }

    struct Repository: Decodable {
        let ref: Ref?
    }

    struct Payload: Decodable {
        let repository: Repository?
    }

    let data: Payload?
    let errors: [Complaint]?
}

/// Reads one entry of the rollup. A null entry is one GitHub counted but
/// would not name.
private func checkSummary(_ node: RollupResponse.Node) -> CheckSummary? {
    switch node.kind {
    case "CheckRun":
        guard let name = node.name, let status = node.status else { return nil }
        let conclusion = node.conclusion?.lowercased()
        return CheckSummary(name: name,
                            state: checkState(runStatus: status.lowercased(),
                                              conclusion: conclusion),
                            detail: conclusion ?? status.lowercased(),
                            url: node.detailsUrl.flatMap(URL.init(string:)))
    case "StatusContext":
        guard let context = node.context, let state = node.state else { return nil }
        return CheckSummary(name: context,
                            state: checkState(commitStatus: state.lowercased()),
                            detail: node.description ?? state.lowercased(),
                            url: node.targetUrl.flatMap(URL.init(string:)))
    default:
        return nil
    }
}

enum GetOutcome {
    case ok(data: Data, etag: String?, rate: RateLimit?, nextPage: URL?)
    case notModified(rate: RateLimit?)
    case failure(FetchFailure)
}

func escapePathComponent(_ text: String) -> String {
    // Slashes are left alone: GitHub takes a branch such as "release/1.2" as
    // the whole of the ref in the path.
    text.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? text
}

func parseRateLimit(_ response: HTTPURLResponse) -> RateLimit? {
    guard let remaining = Int(response.value(forHTTPHeaderField: "x-ratelimit-remaining") ?? ""),
          let limit = Int(response.value(forHTTPHeaderField: "x-ratelimit-limit") ?? ""),
          let reset = Double(response.value(forHTTPHeaderField: "x-ratelimit-reset") ?? "") else {
        return nil
    }
    return RateLimit(remaining: remaining, limit: limit,
                     reset: Date(timeIntervalSince1970: reset))
}

/// Reads the address of the next page out of a Link header, which looks like
/// `<https://api.github.com/...&page=2>; rel="next", <...>; rel="last"`.
func nextPageURL(from header: String?) -> URL? {
    guard let header else { return nil }
    for link in header.components(separatedBy: ",") {
        let pieces = link.components(separatedBy: ";")
        guard pieces.count >= 2 else { continue }
        let relation = pieces[1].trimmingCharacters(in: .whitespaces)
        guard relation == "rel=\"next\"" else { continue }
        var target = pieces[0].trimmingCharacters(in: .whitespaces)
        guard target.hasPrefix("<"), target.hasSuffix(">") else { continue }
        target.removeFirst()
        target.removeLast()
        return URL(string: target)
    }
    return nil
}

let timeOfDayFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.timeStyle = .medium
    formatter.dateStyle = .none
    return formatter
}()

/// One GET at a time against the GitHub REST API, with the conditional request
/// handling left to the caller. Completions run on the main thread.
final class GitHubClient {
    private let session: URLSession

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        // The ETags this app sends are its own, so the session is kept out of
        // the way of them.
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.httpAdditionalHeaders = [
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": apiVersion,
            "User-Agent": applicationName,
        ]
        session = URLSession(configuration: configuration)
    }

    func get(path: String, token: String?, etag: String?,
             completion: @escaping (GetOutcome) -> Void) {
        guard let url = URL(string: "\(apiRoot)/\(path)") else {
            completion(.failure(FetchFailure(message: "\(path) is not a usable address.",
                                             authenticationFailed: false)))
            return
        }
        get(url: url, token: token, etag: etag, completion: completion)
    }

    func get(url: URL, token: String?, etag: String?,
             completion: @escaping (GetOutcome) -> Void) {
        var request = URLRequest(url: url)
        if let token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let etag {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }
        send(request, completion: completion)
    }

    /// The GraphQL endpoint takes one POST and refuses anonymous callers, so
    /// it carries no ETag and is only reachable with a token.
    func post(query: String, variables: [String: String], token: String,
              completion: @escaping (GetOutcome) -> Void) {
        guard let url = URL(string: "\(apiRoot)/graphql"),
              let body = try? JSONSerialization.data(
                withJSONObject: ["query": query, "variables": variables]) else {
            completion(.failure(FetchFailure(message: "The query could not be assembled.",
                                             authenticationFailed: false)))
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        send(request, completion: completion)
    }

    private func send(_ request: URLRequest, completion: @escaping (GetOutcome) -> Void) {
        let task = session.dataTask(with: request) { data, response, error in
            let outcome = GitHubClient.interpret(data: data, response: response, error: error)
            DispatchQueue.main.async {
                completion(outcome)
            }
        }
        task.resume()
    }

    /// GitHub explains a refusal in the body of its answer, and names the
    /// permission the request wanted in a header. A token that can read a
    /// repository's commit statuses but not its checks is refused on the
    /// header's terms alone, so both are worth repeating.
    private static func explanation(_ data: Data?,
                                    _ response: HTTPURLResponse) -> (said: String?, needs: String?) {
        var said: String?
        if let data,
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let message = payload["message"] as? String,
           !message.isEmpty, message != "Not Found" {
            said = message
        }
        var needs: String?
        if let header = response.value(forHTTPHeaderField: "x-accepted-github-permissions"),
           !header.isEmpty {
            needs = header
        }
        return (said, needs)
    }

    private static func interpret(data: Data?, response: URLResponse?,
                                  error: Error?) -> GetOutcome {
        if let error {
            return .failure(FetchFailure(message: error.localizedDescription,
                                         authenticationFailed: false))
        }
        guard let response = response as? HTTPURLResponse else {
            return .failure(FetchFailure(message: "GitHub sent a reply this app cannot read.",
                                         authenticationFailed: false))
        }
        let rate = parseRateLimit(response)
        switch response.statusCode {
        case 200:
            return .ok(data: data ?? Data(),
                       etag: response.value(forHTTPHeaderField: "ETag"),
                       rate: rate,
                       nextPage: nextPageURL(from: response.value(forHTTPHeaderField: "Link")))
        case 304:
            return .notModified(rate: rate)
        case 401:
            let (said, _) = explanation(data, response)
            let short = "GitHub rejected the token."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: true))
        case 403, 429:
            if let rate, rate.remaining == 0 {
                let when = timeOfDayFormatter.string(from: rate.reset)
                return .failure(FetchFailure(
                    message: "GitHub's rate limit is used up until \(when).",
                    authenticationFailed: false))
            }
            let (said, needs) = explanation(data, response)
            let short = needs.map { "GitHub refused: the token needs \($0)." }
                ?? "GitHub refused the request."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: false))
        case 404:
            let (said, needs) = explanation(data, response)
            let short = "GitHub has no such repository or branch, or the token cannot see it."
            var long = short
            if let said { long += " GitHub said: \(said)" }
            if let needs { long += " The request wanted \(needs)." }
            return .failure(FetchFailure(message: short,
                                         detail: long == short ? nil : long,
                                         authenticationFailed: false))
        case 422:
            let (said, _) = explanation(data, response)
            let short = "GitHub could not use that branch name."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: false))
        default:
            let (said, _) = explanation(data, response)
            let short = "GitHub answered with status \(response.statusCode)."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: false))
        }
    }
}

// MARK: - Fetching one branch

/// Collects the state of a branch from three GitHub endpoints and remembers
/// enough between calls to ask conditionally. A conditional request that comes
/// back unchanged costs nothing against the rate limit, which matters most
/// when there is no token and the hourly allowance is sixty requests.
final class BranchFetcher {
    private struct StatusCache {
        let key: String
        let etag: String
        let sha: String
        let checks: [CheckSummary]
    }

    private struct CheckRunsCache {
        let sha: String
        let etag: String
        let singlePage: Bool
        let checks: [CheckSummary]
    }

    private let client = GitHubClient()
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private var statusCache: StatusCache?
    private var checkRunsCache: CheckRunsCache?
    private var headlines: [String: String] = [:]

    func forget() {
        statusCache = nil
        checkRunsCache = nil
        headlines.removeAll()
    }

    /// With a token, one GraphQL query answers the whole question, and it
    /// answers it even for a token that may not see the checks one by one.
    /// Without one, GraphQL is closed and the REST endpoints, which do serve
    /// anonymous callers, are asked instead.
    func fetch(repository: RepositoryReference, branch: String, token: String?,
               completion: @escaping (Result<BranchStatus, FetchFailure>) -> Void) {
        if let token {
            fetchRollup(repository: repository, branch: branch, token: token,
                        completion: completion)
        } else {
            fetchViaREST(repository: repository, branch: branch, completion: completion)
        }
    }

    // MARK: The rollup, in one query

    private func fetchRollup(repository: RepositoryReference, branch: String, token: String,
                             completion: @escaping (Result<BranchStatus, FetchFailure>) -> Void) {
        let variables = ["owner": repository.owner, "name": repository.name, "branch": branch]
        client.post(query: rollupQuery, variables: variables, token: token) { outcome in
            switch outcome {
            case .failure(let failure):
                completion(.failure(failure))
            case .notModified:
                completion(.failure(FetchFailure(
                    message: "GitHub reported no change to a question asked without one.",
                    authenticationFailed: false)))
            case .ok(let data, _, let rate, _):
                completion(self.readRollup(data, repository: repository, branch: branch,
                                           rate: rate))
            }
        }
    }

    private func readRollup(_ data: Data, repository: RepositoryReference, branch: String,
                            rate: RateLimit?) -> Result<BranchStatus, FetchFailure> {
        let payload: RollupResponse
        do {
            payload = try decoder.decode(RollupResponse.self, from: data)
        } catch {
            let short = "GitHub's answer did not parse."
            return .failure(FetchFailure(message: short,
                                         detail: "\(short) \(error.localizedDescription)",
                                         authenticationFailed: false))
        }
        guard let found = payload.data?.repository else {
            let said = payload.errors?.first?.message
            let short = "GitHub has no such repository, or the token cannot see it."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: false))
        }
        guard let target = found.ref?.target, let sha = target.oid else {
            return .failure(FetchFailure(message: "GitHub has no branch named \(branch).",
                                         authenticationFailed: false))
        }
        var checks: [CheckSummary] = []
        var unreadable = 0
        var reported = 0
        var verdict = Health.inconclusive
        if let rollup = target.statusCheckRollup {
            // The verdict is GitHub's, taken over every check on the commit.
            // The entries beside it are only as many as this token may name;
            // the rest arrive as nulls.
            verdict = health(rollupState: rollup.state)
            reported = rollup.contexts.totalCount
            for node in rollup.contexts.nodes {
                if let node, let summary = checkSummary(node) {
                    checks.append(summary)
                } else {
                    unreadable += 1
                }
            }
        }
        checks.sort { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        return .success(BranchStatus(repository: repository,
                                     branch: branch,
                                     sha: sha,
                                     headline: target.messageHeadline,
                                     checks: checks,
                                     reportedCheckCount: max(reported, checks.count),
                                     unreadableCheckCount: unreadable,
                                     health: verdict,
                                     fetched: Date(),
                                     rateLimit: rate))
    }

    // MARK: The same question, for a caller with no token

    private func fetchViaREST(repository: RepositoryReference, branch: String,
                              completion: @escaping (Result<BranchStatus, FetchFailure>) -> Void) {
        let token: String? = nil
        fetchCombinedStatus(repository: repository, branch: branch, token: token) { result in
            switch result {
            case .failure(let failure):
                completion(.failure(failure))
            case .success(let status):
                self.fetchCheckRuns(repository: repository, sha: status.sha,
                                    token: token) { runsResult in
                    switch runsResult {
                    case .failure(let failure):
                        completion(.failure(failure))
                    case .success(let runs):
                        self.fetchHeadline(repository: repository, sha: status.sha,
                                           token: token) { headline in
                            let checks = (status.checks + runs.checks)
                                .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
                            completion(.success(BranchStatus(
                                repository: repository,
                                branch: branch,
                                sha: status.sha,
                                headline: headline,
                                checks: checks,
                                reportedCheckCount: checks.count,
                                unreadableCheckCount: 0,
                                health: rollUp(checks),
                                fetched: Date(),
                                rateLimit: runs.rate ?? status.rate)))
                        }
                    }
                }
            }
        }
    }

    // MARK: Commit statuses

    private struct StatusStep {
        let sha: String
        let checks: [CheckSummary]
        let rate: RateLimit?
    }

    private func fetchCombinedStatus(repository: RepositoryReference, branch: String,
                                     token: String?,
                                     completion: @escaping (Result<StatusStep, FetchFailure>) -> Void) {
        let key = "\(repository.slug)@\(branch)"
        let cache = statusCache?.key == key ? statusCache : nil
        let path = "repos/\(repository.slug)/commits/\(escapePathComponent(branch))/status"
        client.get(path: path, token: token, etag: cache?.etag) { outcome in
            switch outcome {
            case .failure(let failure):
                completion(.failure(failure))
            case .notModified(let rate):
                guard let cache else {
                    completion(.failure(FetchFailure(
                        message: "GitHub reported no change to something this app never read.",
                        authenticationFailed: false)))
                    return
                }
                completion(.success(StatusStep(sha: cache.sha, checks: cache.checks, rate: rate)))
            case .ok(let data, let etag, let rate, _):
                do {
                    let payload = try self.decoder.decode(CombinedStatusResponse.self, from: data)
                    let checks = payload.statuses.map { entry in
                        CheckSummary(name: entry.context,
                                     state: checkState(commitStatus: entry.state),
                                     detail: entry.description ?? entry.state,
                                     url: entry.targetUrl.flatMap(URL.init(string:)))
                    }
                    if let etag {
                        self.statusCache = StatusCache(key: key, etag: etag,
                                                       sha: payload.sha, checks: checks)
                    } else {
                        self.statusCache = nil
                    }
                    completion(.success(StatusStep(sha: payload.sha, checks: checks, rate: rate)))
                } catch {
                    completion(.failure(FetchFailure(
                        message: "GitHub's answer about \(key) did not parse: \(error.localizedDescription)",
                        authenticationFailed: false)))
                }
            }
        }
    }

    // MARK: Check runs

    private struct CheckRunsStep {
        let checks: [CheckSummary]
        let rate: RateLimit?
    }

    private func fetchCheckRuns(repository: RepositoryReference, sha: String, token: String?,
                                completion: @escaping (Result<CheckRunsStep, FetchFailure>) -> Void) {
        let path = "repos/\(repository.slug)/commits/\(sha)/check-runs"
            + "?per_page=\(checkRunsPageSize)&filter=latest"
        // A conditional request covers the whole answer only when the answer
        // fitted on one page last time; the ETag of the first page says
        // nothing about the pages behind it.
        let cache = checkRunsCache
        let etag = (cache?.sha == sha && cache?.singlePage == true) ? cache?.etag : nil
        client.get(path: path, token: token, etag: etag) { outcome in
            switch outcome {
            case .failure(let failure):
                completion(.failure(failure))
            case .notModified(let rate):
                guard let cache, cache.sha == sha else {
                    completion(.failure(FetchFailure(
                        message: "GitHub reported no change to something this app never read.",
                        authenticationFailed: false)))
                    return
                }
                completion(.success(CheckRunsStep(checks: cache.checks, rate: rate)))
            case .ok(let data, let firstETag, let rate, let nextPage):
                self.collectCheckRuns(page: data, nextPage: nextPage, token: token,
                                      collected: []) { result in
                    switch result {
                    case .failure(let failure):
                        completion(.failure(failure))
                    case .success(let gathered):
                        if let firstETag {
                            self.checkRunsCache = CheckRunsCache(sha: sha, etag: firstETag,
                                                                 singlePage: nextPage == nil,
                                                                 checks: gathered.checks)
                        } else {
                            self.checkRunsCache = nil
                        }
                        completion(.success(CheckRunsStep(checks: gathered.checks,
                                                          rate: gathered.rate ?? rate)))
                    }
                }
            }
        }
    }

    private func collectCheckRuns(page: Data, nextPage: URL?, token: String?,
                                  collected: [CheckSummary],
                                  completion: @escaping (Result<CheckRunsStep, FetchFailure>) -> Void) {
        var gathered = collected
        do {
            let payload = try decoder.decode(CheckRunsResponse.self, from: page)
            gathered += payload.checkRuns.map { run in
                CheckSummary(name: run.name,
                             state: checkState(runStatus: run.status, conclusion: run.conclusion),
                             detail: run.conclusion ?? run.status,
                             url: run.htmlUrl.flatMap(URL.init(string:)))
            }
        } catch {
            completion(.failure(FetchFailure(
                message: "GitHub's list of checks did not parse: \(error.localizedDescription)",
                authenticationFailed: false)))
            return
        }
        guard let nextPage else {
            completion(.success(CheckRunsStep(checks: gathered, rate: nil)))
            return
        }
        client.get(url: nextPage, token: token, etag: nil) { outcome in
            switch outcome {
            case .failure(let failure):
                completion(.failure(failure))
            case .notModified:
                completion(.success(CheckRunsStep(checks: gathered, rate: nil)))
            case .ok(let data, _, let rate, let following):
                self.collectCheckRuns(page: data, nextPage: following, token: token,
                                      collected: gathered) { result in
                    // The rate limit from the newest page is the useful one.
                    switch result {
                    case .failure(let failure):
                        completion(.failure(failure))
                    case .success(let step):
                        completion(.success(CheckRunsStep(checks: step.checks,
                                                          rate: step.rate ?? rate)))
                    }
                }
            }
        }
    }

    // MARK: Commit headline

    /// The first line of the commit message, asked for once per commit. A
    /// branch head changes rarely compared to how often the checks on it do,
    /// so this costs almost nothing.
    private func fetchHeadline(repository: RepositoryReference, sha: String, token: String?,
                               completion: @escaping (String?) -> Void) {
        if let known = headlines[sha] {
            completion(known)
            return
        }
        client.get(path: "repos/\(repository.slug)/commits/\(sha)", token: token,
                   etag: nil) { outcome in
            guard case .ok(let data, _, _, _) = outcome,
                  let payload = try? self.decoder.decode(CommitResponse.self, from: data) else {
                completion(nil)
                return
            }
            let headline = payload.commit.message
                .components(separatedBy: .newlines)
                .first?
                .trimmingCharacters(in: .whitespaces) ?? ""
            self.headlines[sha] = headline
            completion(headline.isEmpty ? nil : headline)
        }
    }
}

// MARK: - Polling one branch

enum MonitorState {
    case unconfigured
    case waiting
    case ready(BranchStatus)
    case failed(FetchFailure, BranchStatus?)
}

/// Asks GitHub about the configured branch, waits, and asks again. All methods
/// run on the main thread.
final class BranchMonitor {
    var onChange: ((MonitorState) -> Void)?
    private(set) var state: MonitorState = .unconfigured

    private let credentials: CredentialResolver
    private var fetcher = BranchFetcher()
    private var repository: RepositoryReference?
    private var branch = defaultBranchName
    private var refreshSeconds = defaultRefreshSeconds
    private var timer: Timer?
    private var busy = false
    private var lastGood: BranchStatus?

    init(credentials: CredentialResolver) {
        self.credentials = credentials
    }

    func configure(repository: RepositoryReference?, branch: String, refreshSeconds: Double) {
        let changed = repository != self.repository || branch != self.branch
        self.repository = repository
        self.branch = branch
        self.refreshSeconds = refreshSeconds
        if changed {
            fetcher.forget()
            lastGood = nil
            publish(repository == nil ? .unconfigured : .waiting)
        }
        refreshNow()
    }

    func refreshNow() {
        timer?.invalidate()
        timer = nil
        poll()
    }

    private func poll() {
        guard let repository else {
            publish(.unconfigured)
            return
        }
        guard !busy else { return }
        busy = true
        let branch = self.branch
        credentials.resolve { [weak self] credential in
            guard let self else { return }
            self.fetcher.fetch(repository: repository, branch: branch,
                               token: credential.token) { result in
                self.busy = false
                switch result {
                case .success(let status):
                    self.lastGood = status
                    self.publish(.ready(status))
                case .failure(let failure):
                    if failure.authenticationFailed {
                        self.credentials.invalidate()
                    }
                    self.publish(.failed(failure, self.lastGood))
                }
                self.scheduleNextPoll()
            }
        }
    }

    private func scheduleNextPoll() {
        timer?.invalidate()
        let timer = Timer(timeInterval: refreshSeconds, repeats: false) { [weak self] _ in
            self?.poll()
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func publish(_ state: MonitorState) {
        self.state = state
        onChange?(state)
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
    if status.unreadableCheckCount > 0 {
        parts.append("\(status.unreadableCheckCount) not listed by this token")
    }
    if status.unlistedCheckCount > 0 {
        parts.append("\(status.unlistedCheckCount) beyond the first \(rollupContextLimit)")
    }
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
        intervalField.alignment = .right

        tokenLabel.lineBreakMode = .byTruncatingTail
        tokenLabel.textColor = .secondaryLabelColor
        messageLabel.textColor = .systemRed
        messageLabel.lineBreakMode = .byWordWrapping
        messageLabel.maximumNumberOfLines = 2

        for field in [repositoryField, branchField, nameField] {
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
        settings.repository = parsed.repository
        settings.branch = branch
        settings.menuBarName = nameField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        settings.showsMenuBarName = showNameButton.state == .on
        settings.refreshSeconds = seconds
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
    private lazy var monitor = BranchMonitor(credentials: credentials)

    private var statusItem: NSStatusItem!
    private var menu: NSMenu!
    private var headerItem: NSMenuItem!
    private var summaryItem: NSMenuItem!
    private var commitItem: NSMenuItem!
    private var dynamicStart: NSMenuItem!
    private var dynamicEnd: NSMenuItem!
    private var openItem: NSMenuItem!
    private var bannerItem: NSMenuItem!
    private var footerItem: NSMenuItem!
    private var settingsWindow: SettingsWindowController?
    private var latest: MonitorState = .unconfigured
    private var dynamicSignature: String?
    private let banner = BannerWindowController()
    private var lastHealth: Health?
    private var lastFailure: String?
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
        // The banner is behind Option, since waiting for a branch to break is
        // no way to show anybody what it looks like. It is an alternate of the
        // item above rather than an item that is hidden and unhidden: AppKit
        // swaps alternates as the modifier is pressed and released, whereas
        // anything this app hides for itself can only be decided once, when
        // the menu is asked for its contents. An alternate has to follow its
        // twin immediately and carry the same key equivalent, differing only
        // in the modifiers, which also gives it Command-Option-N.
        bannerItem = menu.addItem(withTitle: "Drop the Banner",
                                  action: #selector(dropBanner(_:)), keyEquivalent: "n")
        bannerItem.target = self
        bannerItem.keyEquivalentModifierMask = [.command, .option]
        bannerItem.isAlternate = true
        bannerItem.toolTip = "Hang the banner now: the failure the branch is carrying if it has one, and an invented one if it does not."

        menu.addItem(.separator())
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
                          refreshSeconds: settings.refreshSeconds)
        updateStatusItem()
    }

    private func apply(_ state: MonitorState) {
        latest = state
        updateStatusItem()
        refreshMenuContents()
        if case .ready(let status) = state {
            considerBanner(status)
        }
    }

    /// Drops the banner when the branch turns red, and again if it is still
    /// red but has broken in a new way. Nothing is dropped for the first
    /// answer GitHub gives: a branch that was already red when the indicator
    /// started has not just turned, and a login should not fill the screen
    /// with banners for every indicator that comes back with it.
    private func considerBanner(_ status: BranchStatus) {
        let failing = status.checks.filter { $0.state == .failing }.map(\.name).sorted()
        let signature = ([status.sha] + failing).joined(separator: "\u{1}")
        let previous = lastHealth
        let previousFailure = lastFailure
        lastHealth = status.health
        lastFailure = signature
        guard status.health == .failing, let previous else { return }
        guard previous != .failing || signature != previousFailure else { return }
        guard !demonstratesBanner else { return }
        // A bullet rather than a middot: the label is letterspaced far enough
        // that a middot disappears into the gaps around it.
        banner.show(bannerMessage(subject: "\(status.repository.slug) • \(status.branch)",
                                  failing: failing,
                                  unlisted: status.unreadableCheckCount),
                    from: statusItem?.button)
    }

    /// What the banner would say right now. A branch that is failing supplies
    /// its own bad news; one that is not has some invented for it, which is
    /// the only way to show the banner to anybody on demand.
    private func currentBannerMessage() -> BannerMessage {
        let subject = settings.repository.map { "\($0.slug) • \(settings.branch)" }
            ?? "owner/repository · main"
        if let status = shownStatus, status.health == .failing {
            let failing = status.checks.filter { $0.state == .failing }.map(\.name).sorted()
            return bannerMessage(subject: subject, failing: failing,
                                 unlisted: status.unreadableCheckCount)
        }
        return bannerMessage(
            subject: subject,
            failing: ["Coverage Check", "Runner Tests (3/8)", "Deploy to Staging"],
            unlisted: 0)
    }

    @objc private func dropBanner(_ sender: Any?) {
        banner.show(currentBannerMessage(), from: statusItem?.button)
    }

    /// Hangs a banner immediately so the effect can be looked at, then quits.
    private func demonstrateBanner() {
        let message = currentBannerMessage()
        // A status item made a moment ago has not been placed in the bar yet,
        // so the banner waits for the turn of the run loop in which it is.
        DispatchQueue.main.async { [weak self] in
            self?.banner.show(message, from: self?.statusItem?.button) {
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
        footerItem.title = footerText()
        rebuildDynamicItems(status)
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
        let item = NSMenuItem(title: check.name, action: #selector(openCheck(_:)), keyEquivalent: "")
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
        monitor.refreshNow()
    }

    @objc private func refresh(_ sender: Any?) {
        monitor.refreshNow()
    }

    @objc private func openCheck(_ sender: NSMenuItem) {
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
    var waitsForInstance = false
}

let usage = """
usage: \(applicationName) [--instance N] [--repo OWNER/NAME] [--branch BRANCH]
              [--once] [--banner] [--no-startup]

  --instance N   run as indicator N rather than the lowest free number
  --repo R       track this repository, and remember it for this indicator
  --branch B     track this branch, and remember it for this indicator
  --once         report the branch on standard output and exit, without
                 touching the menu bar; exits 0 when the branch is passing,
                 1 when it is failing, and 2 otherwise
  --banner       hang the red banner straight away and quit when it has
                 rolled back up, to see what it looks like
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

/// Reports the branch once on standard output. Checks the settings, the token
/// discovery, and the reading of GitHub's answers without the menu bar.
func runOnce(options: Options) -> Never {
    let settings = Settings(instance: options.instance ?? 1)
    guard let repository = options.repository ?? settings.repository else {
        fail("no repository is configured for indicator \(settings.instance); pass --repo owner/name")
    }
    let branch = options.branch ?? settings.branch
    let credentials = CredentialResolver()
    let fetcher = BranchFetcher()
    credentials.resolve { credential in
        print("token: \(credential.source.summary)")
        fetcher.fetch(repository: repository, branch: branch,
                      token: credential.token) { result in
            switch result {
            case .failure(let failure):
                print("\(repository.slug)@\(branch): \(failure.detail ?? failure.message)")
                exit(2)
            case .success(let status):
                print(describe(status))
                switch status.health {
                case .passing: exit(0)
                case .failing: exit(1)
                case .running, .inconclusive: exit(2)
                }
            }
        }
    }
    dispatchMain()
}

// MARK: - Entry point

let options = parseOptions()

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
