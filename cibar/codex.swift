import AppKit

// Each failure on a default branch starts a Codex session to fix it, which
// the Codex app runs as it runs its own sessions, with the tools it gives
// them, without coming to the front. See `startCodexSession`.

let codexBundleIdentifier = "com.openai.codex"
let codexToolPath = "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"

/// The Codex command-line tool inside the installed Codex app, if there is
/// one.
func installedCodexTool() -> URL? {
    NSWorkspace.shared.urlsForApplications(withBundleIdentifier: codexBundleIdentifier).lazy
        .map { $0.appendingPathComponent(codexToolPath) }
        .first { FileManager.default.isExecutableFile(atPath: $0.path) }
}

/// The events that start runs of what the repository's own people wrote, on
/// their own branches. Other events, such as `pull_request_target` or
/// `issue_comment`, run for strangers, who could word what goes into a
/// request.
let trustedEvents: Set<String> = [
    "push", "pull_request", "merge_group", "schedule", "workflow_dispatch", "workflow_run",
    "repository_dispatch",
]

/// What cibar does about a job that failed on a default branch: start a
/// session with a request, or start none, for a reason short enough to show
/// beside the job. cibar starts none only for what it knows for certain:
/// that strangers could word the request, that it was told not to, or that
/// it cannot. What it can only guess at, such as whether the job failed
/// because another did, goes into the request, for the session to look into.
enum Response: Equatable {
    case start(SessionRequest)
    case skip(String)
}

/// What a delivery calls for, on repositories' default branches alone,
/// since a pull request's runs are its author's to look after: for each
/// failure, a session, given `codex` and unless `paused`, whose report names
/// the sessions already started for the branch's other failures, or a
/// reason for none; and a plane for each workflow that turned in a run that
/// a push started, with its banner's text.
func responses(to changes: Changes, codex: CodexSettings?, paused: Bool = false, records: [CodexRecord] = [])
    -> (sessions: [(job: JobKey, response: Response)], planes: [(text: String, red: Bool)]) {
    let sessions = changes.failures.filter(\.context.onDefaultBranch).map { failure in
        let context = failure.context
        let response: Response
        if context.fromFork {
            response = .skip("from a fork")
        } else if !trustedEvents.contains(context.event) {
            // Strangers start runs of these, and could word the request.
            response = .skip("started by \(context.event)")
        } else if let codex, !paused {
            let prompt = expand(codex.prompt, for: failure, records: records)
            response = prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? .skip("request is empty")
                : .start(SessionRequest(target: "\(context.workflowBranch.title): \(describe(job: failure.job))",
                                        prompt: prompt, folder: codex.folder))
        } else {
            response = .skip(codex == nil ? "Codex not set up" : "Codex paused")
        }
        return (JobKey(workflowBranch: context.workflowBranch, job: failure.job), response)
    }
    let planes = changes.turns.filter { $0.onDefaultBranch && $0.event == "push" }.map { turn in
        ("\(turn.workflowBranch.repositoryName) \(turn.workflowBranch.workflow)"
            + (turn.red ? " is red" : " is green"), turn.red)
    }
    return (sessions, planes)
}

/// The name a request gives a job; a run that failed as a whole is not one.
func describe(job: String) -> String {
    job == wholeWorkflow ? "(the run failed without a job failing)" : job
}

/// What went wrong, for the request: the job that failed and its page;
/// every job failing on the branch, its page, and the session already
/// started for it, of the `records`, if any; the jobs of the run that failed before this one
/// was made, which it may need; the run of the same workflow that passed
/// the same commit, if one did; the commit; and what started the run.
func failureReport(_ failure: Failure, records: [CodexRecord] = []) -> String {
    let context = failure.context
    var lines = ["Failed: \(describe(job: failure.job)): \(failure.url)", "Failing on this branch:"]
    for failing in context.failures {
        let record = records.last { $0.job == JobKey(workflowBranch: context.workflowBranch, job: failing.name) }
        lines.append("- \(describe(job: failing.name)): \(failing.url)"
                     + (record?.url.map { " (already sent to a Codex session: \($0))" } ?? ""))
    }
    if !failure.failedBefore.isEmpty {
        lines.append("Failed earlier in the same run, before this job was made, so this job may need them: "
                     + failure.failedBefore.map(describe(job:)).joined(separator: ", "))
    }
    if let passed = failure.passedURL {
        lines.append("The same workflow passed the same commit on this branch: \(passed)")
    }
    lines.append("Commit: \(context.sha)")
    lines.append("Started by: \(context.event)")
    return lines.joined(separator: "\n")
}

/// Replaces each placeholder in `template` once, leaving other text alone and
/// not looking for placeholders in what it puts in.
func expand(_ template: String, for failure: Failure, records: [CodexRecord] = []) -> String {
    let context = failure.context
    let workflowBranch = context.workflowBranch
    let values = [
        "{repository}": workflowBranch.repository,
        "{branch}": workflowBranch.branch,
        "{workflow}": workflowBranch.workflow,
        "{job}": describe(job: failure.job),
        "{job_url}": failure.url,
        "{event}": context.event,
        "{sha}": context.sha,
        "{run_url}": context.runURL,
        "{checks_url}": "https://github.com/\(workflowBranch.repository)/commit/\(context.sha)/checks",
        "{report}": failureReport(failure, records: records),
    ]
    var remaining = template[...]
    var result = ""
    while let start = remaining.firstIndex(of: "{") {
        result += remaining[..<start]
        if let (token, value) = values.first(where: { remaining[start...].hasPrefix($0.key) }) {
            result += value
            remaining = remaining[remaining.index(start, offsetBy: token.count)...]
        } else {
            result += "{"
            remaining = remaining[remaining.index(after: start)...]
        }
    }
    return result + remaining
}

// MARK: - Sessions

/// A job of a workflow on a branch.
struct JobKey: Hashable, Codable {
    var workflowBranch: WorkflowBranch
    var job: String
}

/// What cibar did about a job that failed on a default branch, and when:
/// started a Codex session, which is one of Codex's threads, or started
/// none, for a reason.
struct CodexRecord: Codable, Equatable {
    enum Outcome: Codable, Equatable {
        case sent(thread: String)
        case notSent(reason: String)
    }

    var job: JobKey
    var at: Date
    var outcome: Outcome

    /// What opens the session in the Codex app, if one was started.
    var url: URL? {
        guard case .sent(let thread) = outcome else { return nil }
        return thread.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed)
            .flatMap { URL(string: "codex://threads/\($0)") }
    }
}

/// What a session is asked to do, what it is for, which names it, and the
/// folder it starts in.
struct SessionRequest: Equatable {
    var target: String
    var prompt: String
    var folder: URL
}

/// Starts `executable` with `arguments`, in `folder` when one is given, with
/// `input`, `output` and `errors` as its standard input, output and error,
/// or the null device in place of any that are not given. Nothing else this
/// process has open is passed on.
func spawn(_ executable: URL, _ arguments: [String], in folder: URL? = nil,
           input: Int32? = nil, output: Int32? = nil, errors: Int32? = nil) throws -> pid_t {
    var attributes: posix_spawnattr_t?
    posix_spawnattr_init(&attributes)
    defer { posix_spawnattr_destroy(&attributes) }
    posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_CLOEXEC_DEFAULT))
    var actions: posix_spawn_file_actions_t?
    posix_spawn_file_actions_init(&actions)
    defer { posix_spawn_file_actions_destroy(&actions) }
    for (descriptor, given) in [(Int32(0), input), (1, output), (2, errors)] {
        if let given {
            posix_spawn_file_actions_adddup2(&actions, given, descriptor)
        } else {
            posix_spawn_file_actions_addopen(&actions, descriptor, "/dev/null",
                                             descriptor == 0 ? O_RDONLY : O_WRONLY, 0)
        }
    }
    if let folder {
        posix_spawn_file_actions_addchdir(&actions, folder.path)
    }
    let argv = ([executable.path] + arguments).map { strdup($0) }
    defer { argv.forEach { free($0) } }
    var process: pid_t = 0
    let failure = posix_spawn(&process, executable.path, &actions, &attributes, argv + [nil], environ)
    guard failure == 0 else {
        throw Problem(description: "could not start \(executable.path): " + String(cString: strerror(failure)))
    }
    return process
}

/// Runs `tool` with `arguments` to its end, failing with what it said if it
/// fails.
func run(_ tool: URL, _ arguments: [String]) throws {
    let errors = try TemporaryFile()
    let status = try waitForExit(try spawn(tool, arguments, errors: errors.handle.fileDescriptor))
    guard status == 0 else {
        let said = (try? errors.contents())?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        throw Problem(description: "codex \(arguments.first ?? "") failed" + (said.isEmpty ? "" : ": \(said)"))
    }
}

/// Waits for `process` to exit, and returns its status.
func waitForExit(_ process: pid_t) throws -> Int32 {
    var status: Int32 = 0
    // A signal that arrives while waiting interrupts the wait, not the
    // process.
    while waitpid(process, &status, 0) < 0 {
        guard errno == EINTR else {
            throw Problem(description: "could not wait for process \(process): " + String(cString: strerror(errno)))
        }
    }
    return status
}

// MARK: - Starting a session

/// Starts a session in the Codex app, which runs it as it runs its own,
/// with the tools it gives its own, such as for naming a session, and
/// without coming to the front. Returns the session's thread, once the app
/// has been handed it.
///
/// A session that `codex app-server` runs is not one the app runs, and has
/// none of its tools, even when the app lists it. So the session is made
/// through `codex app-server`, named after `request.target`, which saves
/// it, as nothing that does not happen in a session does; the request is
/// queued in it by `codex queue`, since the server the session was made in
/// would run it at once; and opening `codex://threads/wake/ID`, which
/// `wake` does, has the app take up what is queued in the session.
func startCodexSession(tool: URL, _ request: SessionRequest, wake: (URL) -> Void) throws -> String {
    let thread = try makeThread(tool: tool, named: request.target, in: request.folder)
    try run(tool, ["queue", "--thread", thread, "--message=\(request.prompt)"])
    guard let url = URL(string: "codex://threads/wake/\(thread)") else {
        throw Problem(description: "Codex named the session \(thread)")
    }
    wake(url)
    return thread
}

/// Opens `url` in the Codex app, without bringing the app to the front.
func wakeInCodex(_ url: URL) {
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = false
    NSWorkspace.shared.open(url, configuration: configuration) { _, error in
        if let error {
            log("could not hand \(url) to the Codex app: \(error)")
        }
    }
}

/// Makes a session, in `folder`, named `name`, and returns its thread.
private func makeThread(tool: URL, named name: String, in folder: URL) throws -> String {
    let server = try CodexServer(tool: tool, folder: folder)
    defer { server.close() }
    _ = try server.call("initialize", Initialize(), as: Empty.self)
    try server.notify("initialized")
    let thread = try server.call("thread/start", ThreadStart(cwd: folder.path), as: Started.self).thread.id
    _ = try server.call("thread/name/set", NameSet(threadId: thread, name: name), as: Empty.self)
    return thread
}

private struct Initialize: Encodable {
    struct Client: Encodable {
        var name = applicationName
        var title = applicationName
        var version = "1"
    }
    var clientInfo = Client()
}

private struct ThreadStart: Encodable {
    var cwd: String
}

private struct NameSet: Encodable {
    var threadId: String
    var name: String
}

private struct Empty: Codable {}

private struct Started: Decodable {
    struct Thread: Decodable { var id: String }
    var thread: Thread
}

/// An error a message reports.
private struct ReportedError: Decodable {
    var message: String
}

/// What every message carries: an ID when it is a request or the answer to
/// one, a method when it is a request or a notification, and an error when
/// it is an answer that failed.
private struct Envelope: Decodable {
    var id: Int?
    var method: String?
    var error: ReportedError?
}

private struct Answer<Result: Decodable>: Decodable {
    var result: Result
}

private struct Outgoing<Parameters: Encodable>: Encodable {
    var id: Int?
    var method: String
    var params: Parameters?
}

/// A running `codex app-server`, which speaks JSON-RPC over its standard
/// input and output, one message to a line.
final class CodexServer {
    private let process: pid_t
    private let input: FileHandle
    private let output: Int32
    private let errors: TemporaryFile
    private var pending = Data()
    private var nextID = 1

    init(tool: URL, folder: URL) throws {
        var toServer: [Int32] = [0, 0]
        var fromServer: [Int32] = [0, 0]
        guard pipe(&toServer) == 0, pipe(&fromServer) == 0 else {
            throw Problem(description: "could not make a pipe: " + String(cString: strerror(errno)))
        }
        errors = try TemporaryFile()
        defer {
            Darwin.close(toServer[0])
            Darwin.close(fromServer[1])
        }
        do {
            process = try spawn(tool, ["app-server"], in: folder, input: toServer[0], output: fromServer[1],
                                errors: errors.handle.fileDescriptor)
        } catch {
            Darwin.close(toServer[1])
            Darwin.close(fromServer[0])
            throw error
        }
        input = FileHandle(fileDescriptor: toServer[1], closeOnDealloc: true)
        output = fromServer[0]
    }

    /// Ends the server, by closing its input, and waits for it to exit.
    func close() {
        try? input.close()
        Darwin.close(output)
        _ = try? waitForExit(process)
    }

    func notify(_ method: String) throws {
        try send(Outgoing<Empty>(id: nil, method: method, params: nil))
    }

    /// Sends a request and returns the answer's result, passing over the
    /// notifications that come before it.
    func call<Parameters: Encodable, Result: Decodable>(
        _ method: String, _ parameters: Parameters, as: Result.Type
    ) throws -> Result {
        let id = nextID
        nextID += 1
        try send(Outgoing(id: id, method: method, params: parameters))
        while true {
            let line = try readLine()
            let envelope = try JSONDecoder().decode(Envelope.self, from: line)
            guard envelope.method == nil, envelope.id == id else { continue }
            if let error = envelope.error {
                throw Problem(description: "\(method) failed: \(error.message)")
            }
            return try JSONDecoder().decode(Answer<Result>.self, from: line).result
        }
    }

    private func send(_ message: some Encodable) throws {
        try input.write(contentsOf: JSONEncoder().encode(message) + Data("\n".utf8))
    }

    /// The next line the server writes, without its line break.
    private func readLine() throws -> Data {
        var buffer = [UInt8](repeating: 0, count: 65536)
        while true {
            if let newline = pending.firstIndex(of: UInt8(ascii: "\n")) {
                let line = pending[pending.startIndex..<newline]
                pending.removeSubrange(...newline)
                return Data(line)
            }
            let count = read(output, &buffer, buffer.count)
            guard count > 0 else {
                let reported = (try? errors.contents())?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                throw Problem(description: "codex app-server ended" + (reported.isEmpty ? "" : ": \(reported)"))
            }
            pending.append(contentsOf: buffer[..<count])
        }
    }
}

/// A file only this user can read, in the temporary folder, whose name is
/// removed as soon as it is made, so it lasts only as long as its handle.
struct TemporaryFile {
    let handle: FileHandle

    init() throws {
        var template = Array("\(NSTemporaryDirectory())\(applicationName).XXXXXX".utf8CString)
        let descriptor = mkstemp(&template)
        guard descriptor >= 0 else {
            throw Problem(description: "could not make a temporary file: "
                          + String(cString: strerror(errno)))
        }
        unlink(template)
        handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    }

    func contents() throws -> String {
        try handle.seek(toOffset: 0)
        return String(decoding: try handle.readToEnd() ?? Data(), as: UTF8.self)
    }
}
