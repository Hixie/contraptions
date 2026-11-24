import AppKit
import SwiftUI
import Network

// Tests for reading the configuration, reading deliveries from the relay,
// following jobs and runs from deliveries, and running Codex sessions. Run
// them with `make test`.

var failures = 0

func expect(_ condition: Bool, _ message: String, line: Int = #line) {
    if !condition {
        failures += 1
        // Unbuffered, so that a failure is seen even if a later wait never ends.
        fputs("FAIL line \(line): \(message)\n", stderr)
    }
}

/// What cibar logged, which the tests keep rather than add to cibar's own
/// log.
var logged: [String] = []
logLine = { logged.append($0) }

/// Runs the main run loop until the condition holds.
func waitUntil(_ condition: () -> Bool, line: Int = #line) {
    if ProcessInfo.processInfo.environment["TRACE_WAITS"] != nil { fputs("waiting at line \(line)\n", stderr) }
    while !condition() {
        RunLoop.main.run(mode: .default, before: .distantFuture)
    }
}

/// Folders the tests make, removed once they are done.
var temporaryFolders: [URL] = []

func temporaryFolder() -> URL {
    let folder = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("cibar-tests-\(UUID().uuidString)", isDirectory: true)
    try! FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    temporaryFolders.append(folder)
    return folder
}

extension Watch {
    /// The workflows on branches a delivery turned.
    func turns(event: String, body: Data, at time: Date = Date()) throws -> [Transition] {
        try handle(event: event, body: body, at: time).turns
    }
}

func json(_ object: Any) -> Data {
    try! JSONSerialization.data(withJSONObject: object)
}

// MARK: - The configuration

do {
    let configuration = try Configuration(text: """
        # comment
        [relay]
        address = relay.example.com:8081

        [github]
        owners = octo, other ,

        ; another comment
        [codex]
        folder = projects
        prompt = Fix {branch}.\\n\\n{report} \\\\n
        """)
    expect(configuration.host == "relay.example.com" && configuration.port == 8081,
           "the relay's host and port are read from its address")
    expect(configuration.owners == ["octo", "other"], "owners are split on commas and trimmed")
    expect(configuration.codex?.folder.path == NSHomeDirectory() + "/projects",
           "a relative folder is taken from the home folder")
    expect(configuration.codex?.prompt == "Fix {branch}.\n\n{report} \\n",
           "\\n in a value is a line break, and \\\\ a backslash")
} catch {
    expect(false, "a valid configuration is read: \(error)")
}

do {
    let configuration = try Configuration(text: "[relay]\naddress = [::1]:9\n[codex]\nfolder = ~/x\n")
    expect(configuration.host == "[::1]" && configuration.port == 9,
           "an address splits at its last colon")
    expect(configuration.owners.isEmpty, "no owners means every owner")
    expect(configuration.codex?.folder.path == NSHomeDirectory() + "/x"
           && configuration.codex?.prompt == defaultCodexPrompt,
           "a codex section without a request uses the default request, and ~ is the home folder")
    var codexChanged = configuration
    codexChanged.codex = nil
    expect(configuration.connectsLike(codexChanged), "changing only what Codex is sent connects to the relay alike")
    for change in [{ (c: inout Configuration) in c.host = "other" }, { $0.port = 10 }, { $0.owners = ["octo"] }] {
        var changed = configuration
        change(&changed)
        expect(!configuration.connectsLike(changed),
               "changing the relay or the owners needs a new connection: \(changed)")
    }
    let without = try Configuration(text: "[relay]\naddress = h:1\n")
    expect(without.codex == nil, "without a codex section, no sessions are started")
} catch {
    expect(false, "a minimal configuration is read: \(error)")
}

for (text, problem) in [
    ("[relay]\naddress = h:1\nport = 2\n", "[relay] has no setting port"),
    ("[relay]\naddress = h:1\naddress = h:2\n", "address is set twice"),
    ("[relay]\naddress = h\n", "must set address"),
    ("[relay]\naddress = :1\n", "must set address"),
    ("[github]\nowners = x\n", "must set address"),
    ("[relays]\naddress = h:1\n", "there is no section [relays]"),
    ("address = h:1\n", "address is not in a section"),
    ("[relay]\naddress h:1\n", "expected `key = value`"),
] {
    do {
        _ = try Configuration(text: text)
        expect(false, "\(text.debugDescription) is refused")
    } catch {
        expect("\(error)".contains(problem), "\(text.debugDescription) is refused because \(problem): \(error)")
    }
}

do {
    _ = try Configuration(contentsOf: URL(fileURLWithPath: "/nonexistent/cibar.ini"))
    expect(false, "a missing file is refused")
} catch {
    expect("\(error)".contains("cannot read /nonexistent/cibar.ini"), "a missing file is named: \(error)")
}

// MARK: - Deliveries

do {
    let request = Data(("POST /hook HTTP/1.1\r\nX-GitHub-Event: workflow_job\r\n"
                        + "x-github-delivery: c0e8e4a0-b4d1-11ef-8b5f-ef4b1d0b1a2c\r\n\r\n{\"a\":1}").utf8)
    let delivery = Delivery(request)
    expect(delivery?.event == "workflow_job", "header names are read whatever their case")
    expect(delivery?.body == Data("{\"a\":1}".utf8), "the body is everything after the blank line")
    let created = delivery?.created.map { ISO8601DateFormatter().string(from: $0) }
    expect(created == "2024-12-07T19:30:44Z", "a version 1 UUID says when GitHub made the delivery: \(created ?? "nil")")
    let random = Delivery(Data("POST / HTTP/1.1\r\nX-GitHub-Delivery: \(UUID().uuidString)\r\n\r\n".utf8))
    expect(random != nil && random?.created == nil, "a random UUID says nothing about when")
    expect(Delivery(Data("not a request".utf8)) == nil, "a frame without a blank line is not a request")
}

/// A stand-in relay on the loopback interface, which sends whatever the
/// test gives it to the first connection it accepts.
final class StandInRelay {
    let listener: NWListener
    var connection: NWConnection?
    var port: UInt16 { listener.port?.rawValue ?? 0 }

    init() {
        listener = try! NWListener(using: .tcp, on: .any)
        listener.newConnectionHandler = { [weak self] connection in
            self?.connection = connection
            connection.start(queue: .main)
        }
        listener.start(queue: .main)
        waitUntil { listener.port != nil && listener.port!.rawValue != 0 }
    }

    func send(_ data: Data) {
        connection?.send(content: data, completion: .contentProcessed { _ in })
    }

    func close() {
        connection?.send(content: nil, contentContext: .finalMessage, isComplete: true,
                         completion: .contentProcessed { _ in })
    }
}

func frame(_ event: String, _ body: String) -> Data {
    let request = Data("POST /webhook HTTP/1.1\r\nX-GitHub-Event: \(event)\r\n\r\n\(body)".utf8)
    var length = UInt32(request.count).bigEndian
    return Data(bytes: &length, count: 4) + request
}

do {
    let relay = StandInRelay()
    let connection = RelayConnection(host: "127.0.0.1", port: relay.port)
    var ready = false
    var received: [Delivery] = []
    var ended: String?
    connection.onReady = { ready = true }
    connection.onDelivery = { received.append($0) }
    connection.onEnded = { ended = $0 }
    connection.start()
    waitUntil { ready && relay.connection != nil }
    let frames = frame("workflow_job", "{\"one\":1}") + frame("workflow_run", "{\"two\":2}")
    // The first frame arrives in two pieces, and the second with its end.
    relay.send(frames.prefix(10))
    relay.send(frames.dropFirst(10))
    waitUntil { received.count == 2 }
    expect(received.map(\.event) == ["workflow_job", "workflow_run"], "each frame is one delivery, in order")
    expect(received.map(\.body) == [Data("{\"one\":1}".utf8), Data("{\"two\":2}".utf8)],
           "each delivery's body is its own")
    relay.send(frame("push", "{}").prefix(8))
    relay.close()
    waitUntil { ended != nil }
    expect(ended == "the relay closed the connection", "the end of the connection is reported")
    expect(received.count == 2, "a frame the end of the connection cuts short is not a delivery")
    relay.listener.cancel()
}

do {
    let relay = StandInRelay()
    let connection = RelayConnection(host: "127.0.0.1", port: relay.port)
    var ended: String?
    connection.onEnded = { ended = $0 }
    connection.start()
    waitUntil { relay.connection != nil }
    var huge = UInt32(maximumRequestLength + 1).bigEndian
    relay.send(Data(bytes: &huge, count: 4))
    waitUntil { ended != nil }
    expect(ended?.contains("a frame of") == true, "a frame longer than any delivery ends the connection")
    relay.listener.cancel()
}

// MARK: - Keeping a connection open

final class FakeConnection: Connection {
    var onReady: (() -> Void)?
    var onEnded: ((String) -> Void)?
    var started = false
    var cancelled = false

    func start() {
        started = true
    }

    func cancel() {
        cancelled = true
    }
}

do {
    var clock = Date(timeIntervalSince1970: 1000)
    var fakes: [FakeConnection] = []
    var stateChanges = 0
    let persistent = PersistentConnection(now: { clock })
    persistent.onChange = { stateChanges += 1 }
    let open = { () -> Connection in
        let connection = FakeConnection()
        fakes.append(connection)
        return connection
    }
    /// The state's description, with times as seconds after the clock.
    func describe(_ connection: PersistentConnection) -> String {
        switch connection.state {
        case .connecting:
            return "connecting"
        case .connected(let since):
            return "connected \(since.timeIntervalSince(clock))"
        case .disconnected(let reason, let retryAt):
            return "\(reason), again at \(retryAt.map { "\($0.timeIntervalSince(clock))" } ?? "never")"
        }
    }

    expect(describe(persistent) == "not started, again at never", "a new connection has not started")
    persistent.start(open)
    expect(fakes.count == 1 && fakes[0].started && describe(persistent) == "connecting",
           "starting connects: \(describe(persistent))")
    fakes[0].onReady?()
    expect(describe(persistent) == "connected 0.0", "the connection is ready: \(describe(persistent))")
    persistent.wake()
    expect(fakes.count == 1, "waking while connected does not reconnect")
    var delays: [String] = []
    clock += 0.5
    for _ in 0..<8 {
        fakes[fakes.count - 1].onEnded?("gone")
        delays.append(describe(persistent))
        persistent.wake()
    }
    expect(delays == [1, 2, 4, 8, 16, 32, 64, 64].map { "gone, again at \(Double($0))" },
           "each failure doubles the wait, up to a maximum: \(delays)")
    expect(fakes.count == 9 && describe(persistent) == "connecting",
           "waking connects straight away: \(fakes.count)")
    fakes[8].onReady?()
    clock += 64.5
    fakes[8].onEnded?("gone")
    expect(describe(persistent) == "gone, again at 1.0",
           "a connection that lasted longer than the next wait starts the waits again: "
           + describe(persistent))
    persistent.wake()
    fakes[0].onReady?()
    fakes[1].onEnded?("old")
    expect(describe(persistent) == "connecting", "old connections are not heard: \(describe(persistent))")
    persistent.start(open)
    expect(fakes[9].cancelled && fakes.count == 11, "starting again replaces the connection")
    let changesBeforeStop = stateChanges
    persistent.stop("stopped")
    expect(fakes[10].cancelled && describe(persistent) == "stopped, again at never"
           && stateChanges == changesBeforeStop + 1,
           "stopping ends the connection: \(describe(persistent))")
    persistent.wake()
    expect(fakes.count == 11, "waking does not connect once stopped")

    // A connection that ends as soon as it is ready still makes the next
    // wait longer.
    let brief = PersistentConnection(now: { clock })
    var briefFakes: [FakeConnection] = []
    brief.start {
        let connection = FakeConnection()
        briefFakes.append(connection)
        return connection
    }
    var briefWaits: [TimeInterval] = []
    for _ in 0..<3 {
        briefFakes[briefFakes.count - 1].onReady?()
        briefFakes[briefFakes.count - 1].onEnded?("gone")
        if case .disconnected(_, let retryAt?) = brief.state {
            briefWaits.append(retryAt.timeIntervalSince(clock))
        }
        brief.wake()
    }
    expect(briefWaits == [1, 2, 4], "connections that end at once wait longer each time: \(briefWaits)")

    // The waits are real.
    let quick = PersistentConnection(firstRetryDelay: 0.01)
    var quickOpened = 0
    quick.start {
        quickOpened += 1
        let connection = FakeConnection()
        if quickOpened == 1 {
            DispatchQueue.main.async { connection.onEnded?("gone") }
        }
        return connection
    }
    waitUntil { quickOpened == 2 }
    quick.stop("done")
}

// MARK: - Following jobs and runs

let repository: [String: Any] = [
    "full_name": "octo/repo", "name": "repo", "default_branch": "main", "owner": ["login": "octo"],
]

func job(_ id: Int64, run: Int64, attempt: Int = 1, name: String, status: String,
         conclusion: String? = nil, created: String? = nil, completed: String? = nil) -> Data {
    var job: [String: Any] = [
        "id": id, "run_id": run, "run_attempt": attempt, "name": name, "status": status,
        "workflow_name": "ignored", "head_branch": "ignored", "head_sha": "abc123",
        "html_url": "https://github.com/octo/repo/actions/runs/\(run)/job/\(id)",
    ]
    job["conclusion"] = conclusion
    job["created_at"] = created
    job["completed_at"] = completed
    return json(["action": status, "workflow_job": job, "repository": repository])
}

func run(_ id: Int64, attempt: Int = 1, workflow: String = "CI", path: String? = nil, branch: String = "main",
         event: String = "push", status: String, conclusion: String? = nil,
         head: String = "octo/repo", created: String = "2026-01-01T00:00:00Z", sha: String = "abc123") -> Data {
    var run: [String: Any] = [
        "id": id, "run_attempt": attempt, "name": workflow, "event": event, "status": status,
        "path": path ?? ".github/workflows/\(workflow).yml", "created_at": created,
        "head_branch": branch, "head_sha": sha, "display_title": "Make it work",
        "head_repository": ["full_name": head],
        "html_url": "https://github.com/octo/repo/actions/runs/\(id)",
    ]
    run["conclusion"] = conclusion
    return json(["action": status, "workflow_run": run, "repository": repository])
}

func on(_ workflow: String, _ branch: String = "main") -> WorkflowBranch {
    WorkflowBranch(repository: "octo/repo", workflow: workflow, path: ".github/workflows/\(workflow).yml",
                   branch: branch)
}

do {
    let watch = Watch()
    var turns = try watch.turns(event: "workflow_job", body: job(1, run: 10, name: "a", status: "queued"))
    expect(watch.active.isEmpty, "a job of a run cibar has not heard of waits for news of the run")
    _ = try watch.turns(event: "workflow_run", body: run(10, status: "in_progress"))
    expect(watch.active[1]?.running == false && watch.active[1]?.workflowBranch == on("CI"),
           "news of the run lets the job count, as queued, on the run's workflow and branch")
    expect(watch.active[1]?.runURL == "https://github.com/octo/repo/actions/runs/10",
           "an active job knows its run's page")
    expect(watch.defaultBranches == ["octo/repo": "main"], "deliveries say each repository's default branch")
    _ = try watch.turns(event: "workflow_job", body: job(1, run: 10, name: "a", status: "in_progress"))
    expect(watch.active[1]?.running == true, "a job in progress is running")
    _ = try watch.turns(event: "workflow_job", body: job(1, run: 10, name: "a", status: "queued"))
    expect(watch.active[1]?.running == true, "a queued delivery that arrives late does not stop a job running")
    turns = try watch.turns(event: "workflow_job",
                             body: job(1, run: 10, name: "a", status: "completed", conclusion: "success"))
    expect(watch.active.isEmpty, "a job that completed is no longer active")
    expect(turns.isEmpty, "a first job passing turns nothing, since nothing was red")
    expect(watch.verdicts[on("CI")]?["a"]?.passed == true, "a job that passed is recorded as passing")
    _ = try watch.turns(event: "workflow_job", body: job(1, run: 10, name: "a", status: "in_progress"))
    expect(watch.active.isEmpty, "a delivery that a job is running, after it completed, is out of date")

    _ = try watch.turns(event: "workflow_run", body: run(11, status: "in_progress"))
    turns = try watch.turns(event: "workflow_job", body: job(
        2, run: 11, name: "b", status: "completed", conclusion: "failure"))
    expect(turns.count == 1 && turns[0].red && turns[0].event == "push" && !turns[0].fromFork,
           "a job failing turns its workflow branch red at once, saying what started its run")
    expect(turns.first?.onDefaultBranch == true && turns.first?.failures.map(\.name) == ["b"]
           && turns.first?.sha == "abc123",
           "the turn says it is on the default branch, names the failing job, and gives the commit")
    expect(watch.isRed(on("CI")) && watch.defaultBranchesRed && watch.failingJobCount == 1,
           "the workflow branch is red, and so is the default branch")

    // A failure delivered before its run.
    turns = try watch.turns(event: "workflow_job", body: job(
        30, run: 15, name: "lint", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty, "a failure of a run cibar has not heard of turns nothing yet")
    turns = try watch.turns(event: "workflow_run", body: run(15, workflow: "Lint", status: "in_progress"))
    expect(turns.count == 1 && turns[0].red && turns[0].workflowBranch == on("Lint") && turns[0].event == "push",
           "news of the run turns its workflow red, with what started it")

    // A later run passes the job.
    _ = try watch.turns(event: "workflow_run", body: run(12, status: "in_progress"))
    turns = try watch.turns(event: "workflow_job", body: job(
        3, run: 12, name: "b", status: "completed", conclusion: "success"))
    expect(turns.count == 1 && !turns[0].red && turns[0].event == "push",
           "the failing job passing in a later run turns the workflow branch green")
    turns = try watch.turns(event: "workflow_job", body: job(
        2, run: 11, name: "b", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && !watch.isRed(on("CI")), "a failure from an earlier run that arrives late changes nothing")
    turns = try watch.turns(event: "workflow_job", body: job(
        4, run: 12, name: "b", status: "completed", conclusion: "cancelled"))
    expect(turns.isEmpty && watch.verdicts[on("CI")]?["b"]?.passed == true,
           "a cancelled job neither passes nor fails")

    // A run that passes outranks every failure from it and before it.
    _ = try watch.turns(event: "workflow_run", body: run(100, workflow: "Deploy", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(
        101, run: 100, name: "a", status: "completed", conclusion: "failure"))
    turns = try watch.turns(event: "workflow_run", body: run(
        102, workflow: "Deploy", status: "completed", conclusion: "success"))
    expect(turns.count == 1 && !turns[0].red, "a run passing turns its workflow branch green, whatever failed before")
    turns = try watch.turns(event: "workflow_job", body: job(
        103, run: 100, name: "z", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && !watch.isRed(on("Deploy")),
           "a job of an earlier run that fails late does not turn the workflow red again")
    turns = try watch.turns(event: "workflow_run", body: run(
        100, workflow: "Deploy", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && !watch.isRed(on("Deploy")),
           "an earlier run that fails late does not turn the workflow red again")
    _ = try watch.turns(event: "workflow_run", body: run(110, workflow: "Deploy", status: "in_progress"))
    _ = try watch.turns(event: "workflow_run", body: run(
        110, workflow: "Deploy", status: "completed", conclusion: "success"))
    turns = try watch.turns(event: "workflow_job", body: job(
        111, run: 110, name: "allowed", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && !watch.isRed(on("Deploy")),
           "a job allowed to fail, delivered after its run passed, does not turn the workflow red")

    // A pull request from a fork, from a branch the fork calls main.
    _ = try watch.turns(event: "workflow_run", body: run(
        120, workflow: "Lint", event: "pull_request", status: "in_progress", head: "other/repo"))
    turns = try watch.turns(event: "workflow_job", body: job(
        121, run: 120, name: "lint", status: "completed", conclusion: "success"))
    expect(watch.isRed(on("Lint")) && watch.verdicts[on("Lint", "other:main")] != nil,
           "a fork's branch is its own, named after the fork's owner, and cannot turn main green")
    turns = try watch.turns(event: "workflow_job", body: job(
        122, run: 120, name: "other", status: "completed", conclusion: "failure"))
    expect(turns.count == 1 && turns[0].fromFork && !turns[0].onDefaultBranch
           && turns[0].workflowBranch == on("Lint", "other:main"),
           "a fork's failure says it came from a fork, and is not on the default branch")
    _ = try watch.turns(event: "pull_request", body: json([
        "action": "closed", "repository": repository,
        "pull_request": ["head": ["label": "someone:main", "repo": ["full_name": "someone/repo"]]],
    ]))
    expect(watch.verdicts[on("Lint", "other:main")] != nil,
           "a closed pull request from another fork forgets nothing of this one")
    let revision = watch.revision
    _ = try watch.turns(event: "pull_request", body: json([
        "action": "closed", "repository": repository,
        "pull_request": ["head": ["label": "other:main", "repo": NSNull()]],
    ]))
    expect(watch.verdicts[on("Lint", "other:main")] == nil && watch.isRed(on("Lint")) && watch.revision > revision,
           "a closed pull request from a fork forgets the fork's branch, and only that, even once the fork is gone")
    let unchanged = watch.revision
    _ = try watch.turns(event: "pull_request", body: json([
        "action": "closed", "repository": repository,
        "pull_request": ["head": ["label": "octo:nothing", "repo": ["full_name": "octo/repo"]]],
    ]))
    expect(watch.revision == unchanged, "closing a pull request from the repository itself forgets nothing")

    // Branches deleted.
    _ = try watch.turns(event: "workflow_run", body: run(
        130, branch: "feature", event: "pull_request", status: "completed", conclusion: "success"))
    expect(watch.verdicts[on("CI", "feature")] == nil, "a run that passes with no jobs records no job")
    _ = try watch.turns(event: "workflow_run", body: run(
        131, branch: "feature", event: "pull_request", status: "completed", conclusion: "startup_failure"))
    expect(watch.isRed(on("CI", "feature")), "a run that fails without a failed job turns its workflow red")
    _ = try watch.turns(event: "delete", body: json([
        "ref": "feature", "ref_type": "branch", "repository": repository,
    ]))
    expect(watch.verdicts[on("CI", "feature")] == nil, "a deleted branch is forgotten")
    _ = try watch.turns(event: "delete", body: json([
        "ref": "main", "ref_type": "branch", "repository": repository,
    ]))
    expect(watch.verdicts[on("CI")] != nil, "the default branch is never forgotten")
} catch {
    expect(false, "deliveries are read: \(error)")
}

do {
    let watch = Watch()
    // A run whose failure is delivered before its job's.
    var turns = try watch.turns(event: "workflow_run", body: run(
        40, workflow: "Build", status: "completed", conclusion: "failure"))
    expect(turns.count == 1 && turns[0].failures.map(\.name) == [wholeWorkflow],
           "a run that fails without a failed job turns its workflow red, as a whole")
    expect(turns.first?.failures.first.map { describe(job: $0.name) }?.contains("the run failed without a job failing")
           == true,
           "the report says the run failed without a job failing")
    turns = try watch.turns(event: "workflow_job", body: job(
        41, run: 40, name: "y", status: "completed", conclusion: "failure"))
    expect(watch.failures(on: on("Build")).map(\.name) == ["y"] && watch.failingJobCount == 1 && turns.isEmpty,
           "a job's failure delivered after its run's replaces the run's")

    // A run with one job run again.
    _ = try watch.turns(event: "workflow_run", body: run(50, workflow: "Retry", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(
        51, run: 50, name: "b", status: "completed", conclusion: "failure"))
    _ = try watch.turns(event: "workflow_run", body: run(
        50, workflow: "Retry", status: "completed", conclusion: "failure"))
    _ = try watch.turns(event: "workflow_job", body: job(52, run: 50, attempt: 2, name: "q", status: "in_progress"))
    expect(watch.active[52] == nil, "a job of a new attempt waits for news of the attempt")
    _ = try watch.turns(event: "workflow_run", body: run(50, attempt: 2, workflow: "Retry", status: "in_progress"))
    expect(watch.active[52] != nil, "news of the new attempt lets its job count")
    _ = try watch.turns(event: "workflow_job", body: job(51, run: 50, attempt: 1, name: "b", status: "in_progress"))
    expect(watch.active[51] == nil, "a delivery about an earlier attempt is out of date")
    _ = try watch.turns(event: "workflow_job", body: job(
        53, run: 50, attempt: 2, name: "b", status: "completed", conclusion: "failure"))
    _ = try watch.turns(event: "workflow_run", body: run(
        50, attempt: 2, workflow: "Retry", status: "completed", conclusion: "failure"))
    expect(watch.failures(on: on("Retry")).map(\.name) == ["b"] && watch.active[52] == nil,
           "a second attempt failing because a job did adds no failure for the run, and ends its jobs")
    _ = try watch.turns(event: "workflow_run", body: run(50, attempt: 3, workflow: "Retry", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(54, run: 50, attempt: 3, name: "q", status: "queued"))
    expect(watch.active[54] != nil, "a job of the third attempt is active")
    _ = try watch.turns(event: "workflow_run", body: run(50, attempt: 4, workflow: "Retry", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(55, run: 50, attempt: 4, name: "r", status: "queued"))
    expect(watch.active[54] == nil && watch.active[55] != nil,
           "a later attempt starting ends the jobs of the one before")
    turns = try watch.turns(event: "workflow_job", body: job(
        56, run: 50, attempt: 4, name: "b", status: "completed", conclusion: "success"))
    expect(turns.count == 1 && !turns[0].red, "the job passing on a later attempt turns the workflow green")

    // Restoring what was saved, including which runs passed.
    _ = try watch.turns(event: "workflow_run", body: run(
        60, workflow: "Retry", status: "completed", conclusion: "success"))
    let restored = Watch(restoring: try JSONDecoder().decode(
        WatchState.self, from: try JSONEncoder().encode(watch.state)))
    expect(restored.state == watch.state && restored.verdicts == watch.verdicts
           && restored.defaultBranches == watch.defaultBranches,
           "what is saved is restored exactly")
    _ = try restored.turns(event: "workflow_run", body: run(59, workflow: "Retry", status: "in_progress"))
    turns = try restored.turns(event: "workflow_job", body: job(
        57, run: 59, name: "b", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && !restored.isRed(on("Retry")),
           "a restored watch still ignores failures from before a run that passed")

    _ = try watch.turns(event: "workflow_job", body: job(70, run: 60, name: "y", status: "queued"))
    watch.forgetActiveJobs()
    expect(watch.active.isEmpty, "active jobs can be forgotten")
} catch {
    expect(false, "deliveries are read: \(error)")
}

do {
    let watch = Watch()
    _ = try watch.turns(event: "workflow_run", body: run(1, status: "in_progress"))
    for id in 2...Int64(Watch.rememberedRuns + Watch.rememberedRuns / 5 + 1) {
        _ = try watch.turns(event: "workflow_run", body: run(id, status: "completed", conclusion: "success"))
    }
    let last = Int64(Watch.rememberedRuns + Watch.rememberedRuns / 5 + 1)
    _ = try watch.turns(event: "workflow_job", body: job(1, run: 1, name: "a", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(2, run: last + 1, name: "a", status: "in_progress"))
    _ = try watch.turns(event: "workflow_run", body: run(last + 1, status: "in_progress"))
    expect(watch.active[1] == nil && watch.active[2] != nil,
           "once there are too many runs, the oldest are forgotten")
} catch {
    expect(false, "deliveries are read: \(error)")
}

do {
    let watch = Watch(owners: ["someone-else"])
    var turns = try watch.turns(event: "workflow_run", body: run(
        1, status: "completed", conclusion: "startup_failure"))
    expect(turns.isEmpty && watch.verdicts.isEmpty,
           "deliveries about other owners' repositories change nothing")
    watch.owners = []
    turns = try watch.turns(event: "workflow_run", body: run(
        1, status: "completed", conclusion: "startup_failure"))
    expect(watch.isRed(on("CI")), "with no owners, every owner's deliveries count")
    watch.owners = ["someone-else"]
    expect(watch.verdicts.isEmpty && !watch.defaultBranchesRed,
           "leaving an owner out forgets its workflows, which nothing would clear")
    expect(try watch.turns(event: "issue_comment", body: Data("not json".utf8)).isEmpty,
           "deliveries of other events are not read")
    watch.owners = ["octo"]
    do {
        _ = try watch.turns(event: "workflow_job", body: json(["repository": repository]))
        expect(false, "a workflow_job delivery without a job is refused")
    } catch {
        expect("\(error)".contains("workflowJob"), "the refusal says what is missing: \(error)")
    }
} catch {
    expect(false, "deliveries are read: \(error)")
}

// MARK: - Failures

do {
    // GitHub makes a job that needs others once they have finished, and the
    // jobs of a matrix together, however long they then wait for a runner.
    let watch = Watch()
    _ = try watch.handle(event: "workflow_run", body: run(10, status: "in_progress"))
    var changes = try watch.handle(event: "workflow_job", body: job(
        1, run: 10, name: "shard 1/3", status: "completed", conclusion: "failure",
        created: "2026-01-01T00:00:00Z", completed: "2026-01-01T00:20:00Z"))
    expect(changes.failures.map(\.job) == ["shard 1/3"] && changes.failures.first?.failedBefore == [],
           "the first job of a run to fail follows no other failure")
    changes = try watch.handle(event: "workflow_job", body: job(
        3, run: 10, name: "shard 3/3", status: "completed", conclusion: "failure",
        created: "2026-01-01T00:00:00Z", completed: "2026-01-01T00:40:00Z"))
    expect(changes.failures.first?.failedBefore == [],
           "a shard made with the rest fails for itself, however late it started: \(changes.failures)")
    changes = try watch.handle(event: "workflow_job", body: job(
        3, run: 10, name: "shard 3/3", status: "completed", conclusion: "failure",
        created: "2026-01-01T00:00:00Z", completed: "2026-01-01T00:40:00Z"))
    expect(changes.failures.isEmpty, "a failure delivered again is not one again")
    changes = try watch.handle(event: "workflow_job", body: job(
        4, run: 10, name: "status", status: "completed", conclusion: "failure",
        created: "2026-01-01T00:41:00Z", completed: "2026-01-01T00:42:00Z"))
    expect(changes.failures.map(\.job) == ["status"]
           && changes.failures.first?.failedBefore == ["shard 1/3", "shard 3/3"],
           "a job made once others had failed may need them, which its failure says: \(changes.failures)")
    _ = try watch.handle(event: "workflow_run", body: run(11, status: "in_progress"))
    changes = try watch.handle(event: "workflow_job", body: job(
        5, run: 11, name: "status", status: "completed", conclusion: "failure",
        created: "2026-01-01T01:31:00Z", completed: "2026-01-01T01:32:00Z"))
    expect(changes.failures.first?.failedBefore == [], "another run's failures are not this one's")
} catch {
    expect(false, "deliveries are read: \(error)")
}

do {
    // A workflow that passed a commit may fail on it for something other
    // than the code, which the failure says.
    let watch = Watch()
    _ = try watch.handle(event: "workflow_run", body: run(
        10, workflow: "Notify", event: "workflow_run", status: "completed", conclusion: "success", sha: "aaa"))
    _ = try watch.handle(event: "workflow_run", body: run(11, workflow: "Notify", event: "workflow_run",
                                                         status: "in_progress", sha: "aaa"))
    var changes = try watch.handle(event: "workflow_job", body: job(
        1, run: 11, name: "notify", status: "completed", conclusion: "failure"))
    expect(changes.failures.first?.passedURL == "https://github.com/octo/repo/actions/runs/10",
           "a failure on a commit the workflow passed says which run passed it")
    _ = try watch.handle(event: "workflow_run", body: run(12, workflow: "Notify", event: "workflow_run",
                                                         status: "in_progress", sha: "bbb"))
    changes = try watch.handle(event: "workflow_job", body: job(
        2, run: 12, name: "notify", status: "completed", conclusion: "failure"))
    expect(changes.failures.map(\.job) == ["notify"] && changes.failures.first?.passedURL == nil,
           "a failure on a commit the workflow has not passed says nothing of passing")
    let restored = Watch(restoring: watch.state)
    _ = try restored.handle(event: "workflow_run", body: run(14, workflow: "Notify", event: "workflow_run",
                                                            status: "in_progress", sha: "aaa"))
    changes = try restored.handle(event: "workflow_job", body: job(
        4, run: 14, name: "notify", status: "completed", conclusion: "failure"))
    expect(changes.failures.first?.passedURL == "https://github.com/octo/repo/actions/runs/10",
           "the commit a workflow passed is saved with what cibar knows")
} catch {
    expect(false, "deliveries are read: \(error)")
}

do {
    let watch = Watch()
    _ = try watch.handle(event: "workflow_run", body: run(10, status: "in_progress"))
    var changes = try watch.handle(event: "workflow_job", body: job(
        1, run: 10, name: "shard 1/3", status: "completed", conclusion: "failure"))
    expect(changes.failures.map(\.job) == ["shard 1/3"] && changes.turns.count == 1,
           "a shard failing is a failure, as it turns its workflow red")
    expect(changes.failures.first.map { $0.url.hasSuffix("/job/1") && $0.context.red
                                        && $0.context.failures.map(\.name) == ["shard 1/3"] } == true,
           "a failure gives the job's page, and the workflow branch as it is now")
    changes = try watch.handle(event: "workflow_job", body: job(
        2, run: 10, name: "shard 2/3", status: "completed", conclusion: "timed_out"))
    expect(changes.failures.map(\.job) == ["shard 2/3"] && changes.turns.isEmpty
           && changes.failures.first?.context.failures.count == 2,
           "another shard failing in a workflow already red is a failure of its own")
    changes = try watch.handle(event: "workflow_job", body: job(
        2, run: 10, name: "shard 2/3", status: "completed", conclusion: "timed_out"))
    expect(changes.failures.isEmpty, "the same failure delivered again is not a failure again")
    changes = try watch.handle(event: "workflow_job", body: job(
        3, run: 10, name: "shard 3/3", status: "completed", conclusion: "success"))
    expect(changes.failures.isEmpty, "a shard passing is no failure")
    _ = try watch.handle(event: "workflow_run", body: run(11, status: "in_progress"))
    _ = try watch.handle(event: "workflow_job", body: job(
        4, run: 11, name: "shard 1/3", status: "completed", conclusion: "success"))
    changes = try watch.handle(event: "workflow_job", body: job(
        1, run: 10, name: "shard 1/3", status: "completed", conclusion: "failure"))
    expect(changes.failures.isEmpty, "a failure from a run that a later one has outdated is no failure")
    changes = try watch.handle(event: "workflow_job", body: job(
        5, run: 12, name: "shard 3/3", status: "completed", conclusion: "failure"))
    expect(changes.failures.isEmpty, "a failure of a run cibar has not heard of waits for news of the run")
    changes = try watch.handle(event: "workflow_run", body: run(12, status: "in_progress"))
    expect(changes.failures.map(\.job) == ["shard 3/3"], "news of the run lets its failure count")
    changes = try watch.handle(event: "workflow_run", body: run(
        13, workflow: "Lint", status: "completed", conclusion: "startup_failure"))
    expect(changes.failures.map(\.job) == [wholeWorkflow] && changes.failures.first?.url.hasSuffix("/runs/13") == true,
           "a run that fails without a job failing is a failure of the run, with the run's page")
    changes = try watch.handle(event: "workflow_run", body: run(
        14, workflow: "Fork", status: "completed", conclusion: "startup_failure", head: "other/repo"))
    expect(changes.failures.first?.context.fromFork == true, "a failure says when it came from a fork")

    // A run's failure delivered while its jobs are unfinished.
    _ = try watch.handle(event: "workflow_run", body: run(20, workflow: "Late", status: "in_progress"))
    _ = try watch.handle(event: "workflow_job", body: job(21, run: 20, name: "test", status: "in_progress"))
    _ = try watch.handle(event: "workflow_job", body: job(22, run: 20, name: "lint", status: "in_progress"))
    changes = try watch.handle(event: "workflow_run", body: run(
        20, workflow: "Late", status: "completed", conclusion: "failure"))
    expect(changes.failures.isEmpty && !watch.isRed(on("Late")),
           "a run that fails with jobs unfinished waits for them before failing as a whole")
    changes = try watch.handle(event: "workflow_job", body: job(
        21, run: 20, name: "test", status: "completed", conclusion: "failure"))
    expect(changes.failures.map(\.job) == ["test"], "the job whose failure is delivered late is the failure")
    changes = try watch.handle(event: "workflow_job", body: job(
        22, run: 20, name: "lint", status: "completed", conclusion: "success"))
    expect(changes.failures.isEmpty && watch.failures(on: on("Late")).map(\.name) == ["test"],
           "once a job has said why, the run does not fail as a whole too")
    _ = try watch.handle(event: "workflow_run", body: run(30, workflow: "Lost", status: "in_progress"))
    _ = try watch.handle(event: "workflow_job", body: job(31, run: 30, name: "a", status: "in_progress"))
    _ = try watch.handle(event: "workflow_run", body: run(
        30, workflow: "Lost", status: "completed", conclusion: "failure"))
    changes = try watch.handle(event: "workflow_job", body: job(
        31, run: 30, name: "a", status: "completed", conclusion: "success"))
    expect(changes.failures.map(\.job) == [wholeWorkflow],
           "a run whose unfinished jobs all pass, but which failed, fails as a whole once they have")

    // A job allowed to fail, set aside until news of its run, which passed.
    _ = try watch.handle(event: "workflow_job", body: job(
        41, run: 40, name: "optional", status: "completed", conclusion: "failure"))
    changes = try watch.handle(event: "workflow_run", body: run(
        40, workflow: "Optional", status: "completed", conclusion: "success"))
    expect(changes.failures.map(\.job) == ["optional"] && changes.turns.isEmpty,
           "a job allowed to fail is a failure, whatever order its run's deliveries come in")
} catch {
    expect(false, "deliveries are read: \(error)")
}

// MARK: - Codex sessions

let failure = Failure(job: "test (1/2)", url: "https://example.com/job/1", context: Transition(
    workflowBranch: WorkflowBranch(repository: "octo/repo", workflow: "CI", path: "", branch: "fix/{report}"),
    red: true, run: 7, event: "push", fromFork: false, sha: "abc123",
    runURL: "https://github.com/octo/repo/actions/runs/7", onDefaultBranch: false,
    failures: [(name: "lint", url: "https://example.com/job/0"), (name: "test (1/2)", url: "https://example.com/job/1")]))

expect(failure.context.workflowBranch.title == "repo CI on fix/{report}", "a workflow branch is named for the logs")
expect(expand("{repository} {workflow} {branch} {job} {job_url} {event} {sha} {run_url} {checks_url} {nope} {",
              for: failure)
       == "octo/repo CI fix/{report} test (1/2) https://example.com/job/1 push abc123 "
       + "https://github.com/octo/repo/actions/runs/7 https://github.com/octo/repo/commit/abc123/checks {nope} {",
       "each placeholder is filled in once, and other braces are left alone")
expect(expand("{report}", for: failure) == """
       Failed: test (1/2): https://example.com/job/1
       Failing on this branch:
       - lint: https://example.com/job/0
       - test (1/2): https://example.com/job/1
       Commit: abc123
       Started by: push
       """, "the report names the job that failed, every job failing on the branch, the commit, and what started the run")
var told = failure
told.failedBefore = ["lint"]
told.passedURL = "https://github.com/octo/repo/actions/runs/5"
let earlier = CodexRecord(job: JobKey(workflowBranch: failure.context.workflowBranch, job: "lint"), at: Date(), outcome: .sent(thread: "t-1"))
let elsewhere = CodexRecord(job: JobKey(workflowBranch: on("CI"), job: "test (1/2)"), at: Date(), outcome: .sent(thread: "t-2"))
expect(expand("{report}", for: told, records: [earlier, elsewhere]) == """
       Failed: test (1/2): https://example.com/job/1
       Failing on this branch:
       - lint: https://example.com/job/0 (already sent to a Codex session: codex://threads/t-1)
       - test (1/2): https://example.com/job/1
       Failed earlier in the same run, before this job was made, so this job may need them: lint
       The same workflow passed the same commit on this branch: https://github.com/octo/repo/actions/runs/5
       Commit: abc123
       Started by: push
       """, "the report says which failures were sent to Codex already, which failed before the job was made, "
       + "and which run passed the same commit, and nothing of sessions elsewhere")

/// A stand-in for the Codex command-line tool, which records how it is run,
/// with its folder, and what it is sent, and answers as `codex app-server`
/// and `codex queue` would, or as the test's options say: with
/// `failsToStart`, the server exits when asked to initialize, and with
/// `queueFails`, queueing fails.
func standInCodex(failsToStart: Bool = false, queueFails: Bool = false) -> (tool: URL, log: URL) {
    let folder = temporaryFolder()
    let tool = folder.appendingPathComponent("codex")
    let log = folder.appendingPathComponent("log")
    try! """
        #!/usr/bin/python3 -I
        import json, os, sys
        log = open('\(log.path)', 'a')
        def note(value):
            log.write(json.dumps(value) + "\\n")
            log.flush()
        def send(message):
            sys.stdout.write(json.dumps(message) + "\\n")
            sys.stdout.flush()
        note({"argv": sys.argv[1:], "cwd": os.getcwd()})
        if sys.argv[1] == "queue":
            if \(queueFails ? "True" : "False"):
                sys.stderr.write("no rollout found\\n")
                sys.exit(1)
            sys.exit(0)
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            message = json.loads(line)
            note(message)
            method = message.get("method")
            if method == "initialize":
                if \(failsToStart ? "True" : "False"):
                    sys.stderr.write("not signed in\\n")
                    sys.exit(3)
                send({"id": message["id"], "result": {"userAgent": "stand-in"}})
            elif method == "thread/start":
                send({"method": "thread/started", "params": {"thread": {"id": "thread-1"}}})
                send({"id": message["id"], "result": {"thread": {"id": "thread-1"}}})
            elif method == "thread/name/set":
                send({"id": message["id"], "result": {}})
        """.write(to: tool, atomically: true, encoding: .utf8)
    chmod(tool.path, 0o755)
    return (tool, log)
}

/// What a stand-in recorded, one value to a line.
func recorded(_ log: URL) -> [[String: Any]] {
    ((try? String(contentsOf: log, encoding: .utf8)) ?? "").split(separator: "\n").compactMap {
        try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any]
    }
}

/// Starts a session with a stand-in, recording what it is woken with, and
/// what the stand-in had recorded by then.
func startStandIn(_ tool: URL, log: URL? = nil) -> (thread: Result<String, Error>, woken: [URL], seen: Int) {
    var woken: [URL] = []
    var seen = 0
    let request = SessionRequest(target: "repo CI on main: test", prompt: "Fix it.\nPlease.",
                                 folder: URL(fileURLWithPath: NSTemporaryDirectory()))
    let thread = Result {
        try startCodexSession(tool: tool, request) { url in
            woken.append(url)
            seen = log.map { recorded($0).count } ?? 0
        }
    }
    return (thread, woken, seen)
}

do {
    let codex = standInCodex()
    let (thread, woken, seen) = startStandIn(codex.tool, log: codex.log)
    let log = recorded(codex.log)
    expect((try? thread.get()) == "thread-1", "starting a session gives its thread: \(thread)")
    expect(log.first?["argv"] as? [String] == ["app-server"]
           && log.first?["cwd"] as? String == String(cString: realpath(NSTemporaryDirectory(), nil)),
           "the session is made by Codex's program interface, in the session's folder: \(log.first ?? [:])")
    let methods = log.compactMap { $0["method"] as? String }
    expect(methods == ["initialize", "initialized", "thread/start", "thread/name/set"],
           "cibar introduces itself, makes a session, and names it, which saves it, and starts no turn: \(methods)")
    let parameters = log.compactMap { $0["params"] as? [String: Any] }
    expect(parameters.first.flatMap { ($0["clientInfo"] as? [String: Any])?["name"] as? String } == "cibar"
           && parameters.contains { $0["cwd"] as? String == URL(fileURLWithPath: NSTemporaryDirectory()).path }
           && parameters.contains { $0["threadId"] as? String == "thread-1" && $0["name"] as? String == "repo CI on main: test" },
           "the session starts in its folder, named for what it is for: \(parameters)")
    expect(log.last?["argv"] as? [String] == ["queue", "--thread", "thread-1", "--message=Fix it.\nPlease."],
           "the request is queued whole in the session, once the server that made it is gone: \(log.last ?? [:])")
    expect(woken == [URL(string: "codex://threads/wake/thread-1")!] && seen == log.count,
           "once the request is queued, and only then, the app is woken to take it up: \(woken)")
}

do {
    let codex = standInCodex(queueFails: true)
    let (thread, woken, _) = startStandIn(codex.tool)
    if case .failure(let error) = thread {
        expect("\(error)" == "codex queue failed: no rollout found" && woken.isEmpty,
               "a request that cannot be queued is reported, and the app is not woken: \(error)")
    } else {
        expect(false, "a request that cannot be queued is reported")
    }
}

do {
    let codex = standInCodex(failsToStart: true)
    let (thread, woken, _) = startStandIn(codex.tool)
    let queued = recorded(codex.log).contains { ($0["argv"] as? [String])?.first == "queue" }
    if case .failure(let error) = thread {
        expect("\(error)" == "codex app-server ended: not signed in" && woken.isEmpty && !queued,
               "a server that ends early is reported with what it said, and nothing is queued: \(error)")
    } else {
        expect(false, "a server that ends early is reported")
    }
    if case .failure(let error) = startStandIn(URL(fileURLWithPath: "/nonexistent/codex")).thread {
        expect("\(error)".hasPrefix("could not start /nonexistent/codex"), "a missing tool is reported: \(error)")
    } else {
        expect(false, "a missing tool is reported")
    }
}

// MARK: - Saving

do {
    let folder = temporaryFolder()
    let url = folder.appendingPathComponent("state.json")
    expect(readState(from: url) == WatchState(), "with nothing saved, nothing is known")
    let watch = Watch()
    _ = try? watch.turns(event: "workflow_run", body: run(1, status: "completed", conclusion: "startup_failure"))
    write(watch.state, to: url)
    expect(readState(from: url) == watch.state && !watch.state.verdicts.isEmpty, "what is saved is read back exactly")
    try! Data("{not json".utf8).write(to: url)
    logged = []
    expect(readState(from: url) == WatchState(), "a file that cannot be read gives nothing")
    expect(logged.count == 1 && logged[0].hasPrefix("cannot read \(url.path), so it is now state.json.unreadable: "),
           "a file that cannot be read is logged, with where it went: \(logged)")
    expect((try? String(contentsOf: url.appendingPathExtension("unreadable"), encoding: .utf8)) == "{not json"
           && !FileManager.default.fileExists(atPath: url.path),
           "a file that cannot be read is moved aside, so that saving does not replace it")
    let sessions = folder.appendingPathComponent("sessions.json")
    let sent = [CodexRecord(job: JobKey(workflowBranch: on("CI"), job: "test"), at: Date(timeIntervalSince1970: 9),
                            outcome: .sent(thread: "t")),
                CodexRecord(job: JobKey(workflowBranch: on("CI"), job: "lint"), at: Date(timeIntervalSince1970: 10),
                            outcome: .notSent(reason: "Codex paused"))]
    expect(readState(from: sessions, or: [CodexRecord]()).isEmpty, "with nothing saved, nothing is known")
    write(sent, to: sessions)
    expect(readState(from: sessions, or: [Readable<CodexRecord>]()).compactMap(\.element) == sent,
           "what cibar did about each job, sent or not, is read back exactly")
    let entries = try! JSONSerialization.jsonObject(with: Data(contentsOf: sessions)) as! [Any]
    try! JSONSerialization.data(withJSONObject: [["from": "a later cibar"]] + entries).write(to: sessions)
    expect(readState(from: sessions, or: [Readable<CodexRecord>]()).compactMap(\.element) == sent,
           "a record that cannot be read is left out, and the rest kept")
}

// MARK: - Deliveries that overlap or go astray

do {
    let watch = Watch()
    // Run 10's lint fails, a quicker run 11 passes it, and run 10 then
    // completes as having failed.
    _ = try watch.turns(event: "workflow_run", body: run(10, status: "in_progress"))
    _ = try watch.turns(event: "workflow_run", body: run(11, status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(
        1, run: 10, name: "lint", status: "completed", conclusion: "failure"))
    _ = try watch.turns(event: "workflow_job", body: job(
        2, run: 11, name: "lint", status: "completed", conclusion: "success"))
    expect(!watch.isRed(on("CI")), "a later run passing the failed job turns the workflow green")
    var turns = try watch.turns(event: "workflow_run", body: run(10, status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && !watch.isRed(on("CI")),
           "a run that failed because of a job a later run passed does not turn the workflow red again")
    // The same, when the job's failure was never recorded.
    _ = try watch.turns(event: "workflow_run", body: run(20, workflow: "B", status: "in_progress"))
    _ = try watch.turns(event: "workflow_run", body: run(21, workflow: "B", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(
        3, run: 21, name: "b", status: "completed", conclusion: "success"))
    _ = try watch.turns(event: "workflow_job", body: job(
        4, run: 20, name: "b", status: "completed", conclusion: "failure"))
    turns = try watch.turns(event: "workflow_run", body: run(20, workflow: "B", status: "completed",
                                                                conclusion: "failure"))
    expect(turns.isEmpty && !watch.isRed(on("B")),
           "a run whose failed job was outdated before it was recorded does not count as failing without one")
    // A run's failure, then a job's failure outdated by a later run.
    _ = try watch.turns(event: "workflow_run", body: run(30, workflow: "C", status: "completed",
                                                           conclusion: "failure"))
    expect(watch.failures(on: on("C")).map(\.name) == [wholeWorkflow], "a run fails as a whole until a job says why")
    _ = try watch.turns(event: "workflow_run", body: run(31, workflow: "C", status: "in_progress"))
    turns = try watch.turns(event: "workflow_job", body: job(
        5, run: 31, name: "c", status: "completed", conclusion: "success"))
    expect(turns.count == 1 && !turns[0].red && !watch.isRed(on("C")),
           "a job of a later run finishing shows the workflow runs again, so the earlier run no longer counts")
    turns = try watch.turns(event: "workflow_job", body: job(
        6, run: 30, name: "c", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && !watch.isRed(on("C")),
           "a job's failure in that earlier run, delivered late, changes nothing")
    _ = try watch.turns(event: "workflow_run", body: run(32, workflow: "C", status: "completed",
                                                           conclusion: "startup_failure"))
    _ = try watch.turns(event: "workflow_run", body: run(33, workflow: "C", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(
        7, run: 33, name: "c", status: "completed", conclusion: "failure"))
    expect(watch.failures(on: on("C")).map(\.name) == ["c"],
           "a run that could not start no longer counts once a later run runs its jobs, even failing")

    // A job allowed to fail whose failure arrives before news of its run,
    // which then arrives having passed.
    _ = try watch.turns(event: "workflow_job", body: job(
        7, run: 40, name: "optional", status: "completed", conclusion: "failure"))
    turns = try watch.turns(event: "workflow_run", body: run(40, workflow: "D", status: "completed",
                                                                conclusion: "success"))
    expect(turns.isEmpty && !watch.isRed(on("D")),
           "a delivery that turns a workflow red and back again reports no turn")
    _ = try watch.turns(event: "workflow_job", body: job(
        8, run: 41, name: "x", status: "completed", conclusion: "failure"))
    turns = try watch.turns(event: "workflow_run", body: run(41, workflow: "D", status: "in_progress"))
    expect(turns.count == 1 && turns[0].red, "a delivery that turns a workflow red reports it once")
    turns = try watch.turns(event: "workflow_job", body: job(
        9, run: 41, name: "y", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty, "a workflow already red does not turn red again")

    // A run whose fork has gone.
    turns = try watch.turns(event: "workflow_run", body: json([
        "action": "completed", "repository": repository,
        "workflow_run": [
            "id": 50, "run_attempt": 1, "name": "E", "path": "e.yml", "event": "pull_request", "status": "completed",
            "conclusion": "failure", "head_branch": "main", "head_sha": "abc123", "head_repository": NSNull(),
            "display_title": "Anything", "html_url": "https://github.com/octo/repo/actions/runs/50",
        ] as [String: Any],
    ]))
    expect(turns.isEmpty && !watch.isRed(on("E")) && !watch.verdicts.keys.contains { $0.workflow == "E" },
           "a run that does not say whose branch it ran on does not count at all")

    // A run of a whole workflow that fails, delivered after a job of a
    // later run finished.
    _ = try watch.turns(event: "workflow_run", body: run(71, workflow: "F", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(
        72, run: 71, name: "f", status: "completed", conclusion: "success"))
    turns = try watch.turns(event: "workflow_run", body: run(70, workflow: "F", status: "completed",
                                                                conclusion: "failure"))
    expect(turns.isEmpty && !watch.isRed(on("F")),
           "a run that failed as a whole, delivered after a later run's job finished, does not count")

    // A closed pull request from the repository itself.
    _ = try watch.turns(event: "workflow_run", body: run(
        60, branch: "feature", event: "pull_request", status: "completed", conclusion: "startup_failure"))
    _ = try watch.turns(event: "pull_request", body: json([
        "action": "closed", "repository": repository,
        "pull_request": ["head": ["label": "octo:feature", "repo": ["full_name": "octo/repo"]]],
    ]))
    expect(watch.isRed(on("CI", "feature")),
           "closing a pull request from the repository itself leaves its branch, which may still be used")

    // A run passing that leaves no jobs keeps the workflow listed.
    let restored = Watch(restoring: watch.state)
    expect(Set(restored.verdicts.keys) == Set(watch.verdicts.keys.filter { !(watch.verdicts[$0]?.isEmpty ?? true) })
           && watch.verdicts.values.allSatisfy { !$0.isEmpty },
           "no workflow is kept with no jobs, so restoring lists the same workflows")

    let unchanged = watch.revision
    watch.owners = []
    watch.owners = []
    expect(watch.revision == unchanged, "setting the same owners again changes nothing")

    do {
        _ = try watch.turns(event: "workflow_job", body: json(["workflow_job": ["id": 1]]))
        expect(false, "a delivery without a repository is refused")
    } catch {
        expect("\(error)".contains("repository"), "the refusal says the repository is missing: \(error)")
    }
} catch {
    expect(false, "deliveries are read: \(error)")
}

// MARK: - What failures and turns call for

do {
    let codex = CodexSettings(folder: URL(fileURLWithPath: "/work"), prompt: "Fix {job} on {branch}.")
    func onMain(_ failure: Failure) -> Failure {
        var failure = failure
        failure.context.workflowBranch.branch = "main"
        failure.context.onDefaultBranch = true
        return failure
    }
    let pullRequest = failure
    let main = onMain(failure)
    var fork = main
    fork.context.fromFork = true
    var outsider = main
    outsider.context.event = "pull_request_target"
    var scheduled = main
    scheduled.context.event = "schedule"
    scheduled.job = "build"
    var green = main.context
    green.red = false
    var scheduledTurn = main.context
    scheduledTurn.event = "schedule"
    let changes = Changes(turns: [pullRequest.context, main.context, green, scheduledTurn],
                          failures: [pullRequest, main, fork, outsider, scheduled])
    var rollUp = main
    rollUp.job = "status"
    rollUp.failedBefore = ["test (1/2)"]
    let all = Changes(turns: changes.turns, failures: changes.failures + [rollUp])
    let (sessions, planes) = responses(to: all, codex: codex)
    let fix = SessionRequest(target: "repo CI on main: test (1/2)", prompt: "Fix test (1/2) on main.",
                             folder: URL(fileURLWithPath: "/work"))
    let build = SessionRequest(target: "repo CI on main: build", prompt: "Fix build on main.",
                               folder: URL(fileURLWithPath: "/work"))
    let status = SessionRequest(target: "repo CI on main: status", prompt: "Fix status on main.",
                                folder: URL(fileURLWithPath: "/work"))
    let onMainJob = { JobKey(workflowBranch: main.context.workflowBranch, job: $0) }
    expect(sessions.map(\.job) == ["test (1/2)", "test (1/2)", "test (1/2)", "build", "status"].map(onMainJob)
           && sessions.map(\.response) == [.start(fix), .skip("from a fork"), .skip("started by pull_request_target"),
                                            .start(build), .start(status)],
           "each failure on a default branch calls for a session, filled in from the request, even one made "
           + "after another job of its run failed, which cibar can only guess was the cause; but not one from a "
           + "fork, or in a run of an event strangers start, which says why; and one off a default branch calls "
           + "for nothing: \(sessions)")
    expect(planes.map(\.text) == ["repo CI is red", "repo CI is green"],
           "every turn on a default branch by a run a push started calls for a plane: \(planes.map(\.text))")
    expect(planes.map(\.red) == [true, false], "the plane is red or green as the workflow turned")
    let reasons = { (codex: CodexSettings?, paused: Bool) in
        responses(to: Changes(failures: [main]), codex: codex, paused: paused).sessions.map(\.response)
    }
    var blank = codex
    blank.prompt = " \n"
    expect(reasons(nil, false) == [.skip("Codex not set up")] && reasons(codex, true) == [.skip("Codex paused")]
           && reasons(blank, false) == [.skip("request is empty")],
           "without Codex settings, while sessions are paused, or with an empty request, no session is started, "
           + "and the failure says why")
}

// MARK: - The settings window

do {
    let settings = SettingsWindow(url: temporaryFolder().appendingPathComponent("config.ini"),
                                  startsSessions: { true }, setStartsSessions: { _ in })
    let window = settings.makeWindow()
    window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
    settings.prompt.string = "first line\nsecond line"
    window.contentView?.layoutSubtreeIfNeeded()
    for text in [settings.prompt] {
        let label = text.enclosingScrollView.flatMap { settings.grid?.cell(for: $0)?.row?.cell(at: 0).contentView }
        let firstLine = text.layoutManager.map { text.textContainerOrigin.y + $0.defaultBaselineOffset(for: text.font!) }
        let textBaseline = firstLine.map { text.convert(NSPoint(x: 0, y: $0), to: nil).y }
        let labelBaseline = label.map { $0.convert($0.bounds, to: nil).maxY - $0.firstBaselineOffsetFromTop }
        expect(textBaseline != nil && labelBaseline != nil
               && abs(textBaseline! - labelBaseline!) < 0.5,
               "a text field's label stands on the baseline of its first line: \(String(describing: labelBaseline)) "
               + "\(String(describing: textBaseline))")
        let top = text.enclosingScrollView.map { $0.convert($0.bounds, to: nil).maxY }
        expect(top != nil && labelBaseline != nil && top! - labelBaseline! > 8,
               "a label stands lower than the top of its text field")
    }
}

// MARK: - The settings file

do {
    var settings = Settings()
    settings.address = "relay.example.com:8081"
    settings.owners = "octo, other"
    settings.codex = Settings.Codex(folder: "~/work", prompt: "Fix {job}.\n\nC:\\path\\n is not a break")
    let read = try Settings(text: settings.text)
    expect(read == settings, "settings written to the file are read back exactly: \(read)")
    let configuration = try Configuration(text: settings.text)
    expect(configuration.codex?.prompt == "Fix {job}.\n\nC:\\path\\n is not a break"
           && configuration.owners == ["octo", "other"],
           "a request with line breaks and backslashes survives the file")
    settings.codex?.prompt = ""
    expect(try Settings(text: settings.text).codex?.prompt == "" && (try Configuration(settings)).codex?.prompt == "",
           "an empty request stays empty, in the file and as cibar uses it")
    settings.codex = nil
    expect(try Settings(text: settings.text).codex == nil && (try Configuration(text: settings.text)).codex == nil,
           "settings without Codex sessions say so in the file")
    expect(Settings().codex == Settings.Codex() && Settings().codex?.prompt == defaultCodexPrompt,
           "new settings start Codex sessions with the default request")
    do {
        _ = try Configuration(Settings(), name: "the settings")
        expect(false, "settings without a relay are refused")
    } catch {
        expect("\(error)".contains("must set address"), "settings without a relay say why: \(error)")
    }
} catch {
    expect(false, "settings are read: \(error)")
}

do {
    var written: [WatchState] = []
    var current = WatchState()
    let writer = StateWriter(snapshot: { current }, write: { written.append($0) })
    for count in 1...5 {
        current.defaultBranches = Dictionary(uniqueKeysWithValues: (0..<count).map { ("o/\($0)", "main") })
        writer.save()
    }
    waitUntil { written.count == 2 }
    RunLoop.main.run(until: Date())
    expect(written.count == 2 && written.last == current,
           "saves asked for during a write become one more write, of the latest state")
    current.defaultBranches = [:]
    writer.flush()
    expect(written.last == current, "flushing writes the latest state at once")
}

do {
    let folder = temporaryFolder()
    let url = folder.appendingPathComponent("state.json")
    try! FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    expect(readState(from: url) == WatchState()
           && FileManager.default.fileExists(atPath: url.appendingPathExtension("unreadable").path)
           && !FileManager.default.fileExists(atPath: url.path),
           "a state file that exists but cannot be read for any reason is moved aside")
}

// MARK: - The plane

do {
    /// A generator that gives the same numbers every time, so that a
    /// failure can be repeated.
    struct SplitMix: RandomNumberGenerator {
        var state: UInt64
        mutating func next() -> UInt64 {
            state &+= 0x9e37_79b9_7f4a_7c15
            var value = state
            value = (value ^ (value >> 30)) &* 0xbf58_476d_1ce4_e5b9
            value = (value ^ (value >> 27)) &* 0x94d0_49bb_1331_11eb
            return value ^ (value >> 31)
        }
    }
    var random = SplitMix(state: 1)
    let bounds = CGRect(x: 0, y: 0, width: 1512, height: 982)
    let anchor = CGPoint(x: 1200, y: 945)
    for _ in 0..<50 {
        let path = FlightPath.flyby(from: anchor, within: bounds, using: &random)
        let first = path.poses[0].point
        let last = path.poses[path.poses.count - 1].point
        expect(first.y > anchor.y && last.y > anchor.y, "the plane starts and ends behind the menu bar")
        expect(path.poses.allSatisfy { bounds.insetBy(dx: -1, dy: -1).contains($0.point) || $0.point.y > anchor.y },
               "the plane stays on the screen")
        expect(path.cruisingLength >= path.length * cruisingShare || path.length < flybyDistance.lowerBound,
               "a third of the flight is spent cruising")
        expect(path.length <= flybyDistance.upperBound * 1.1 + 400, "the flight is about as long as chosen: \(path.length)")
    }
    expect(flybyInterval(towing: "a much longer banner than this") > flybyInterval(towing: "short"),
           "a plane towing a longer banner needs longer before the next sets off")
}

// MARK: - Branches deleted, and restarts

do {
    let watch = Watch()
    _ = try watch.turns(event: "workflow_run", body: run(10, branch: "feature", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(1, run: 10, name: "a", status: "in_progress"))
    _ = try watch.turns(event: "delete", body: json([
        "ref": "feature", "ref_type": "branch", "repository": repository,
    ]))
    expect(watch.active.isEmpty, "a deleted branch's jobs are no longer counted as running")
    var turns = try watch.turns(event: "workflow_job", body: job(
        1, run: 10, name: "a", status: "completed", conclusion: "failure"))
    turns += try watch.turns(event: "workflow_run", body: run(
        10, branch: "feature", status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && watch.verdicts.isEmpty,
           "a run on a branch deleted while it ran does not bring the branch back")
    // A run cibar had not heard of when its branch was deleted.
    _ = try watch.turns(event: "workflow_job", body: job(
        3, run: 12, name: "a", status: "completed", conclusion: "failure"))
    _ = try watch.turns(event: "delete", body: json([
        "ref": "other", "ref_type": "branch", "repository": repository,
    ]))
    let restoredAfterDelete = Watch(restoring: watch.state)
    for watcher in [watch, restoredAfterDelete] {
        turns = try watcher.turns(event: "workflow_run", body: run(
            12, branch: "other", status: "completed", conclusion: "failure"))
        expect(turns.isEmpty && watcher.verdicts.keys.allSatisfy { $0.branch != "other" },
               "a run that started before its branch was deleted does not bring it back, even after a restart")
    }
    turns = try watch.turns(event: "workflow_run", body: run(
        13, branch: "feature", status: "completed", conclusion: "startup_failure", created: "2100-01-01T00:00:00Z"))
    expect(turns.count == 1 && watch.isRed(on("CI", "feature")),
           "a run made after its branch was deleted, on a branch of the same name, counts")

    // A branch deleted after a gap in which cibar heard of none of its runs.
    let gap = Watch(restoring: watch.state)
    _ = try gap.turns(event: "delete", body: json([
        "ref": "late", "ref_type": "branch", "repository": repository,
    ]), at: ISO8601DateFormatter().date(from: "2026-06-01T00:00:00Z")!)
    turns = try gap.turns(event: "workflow_run", body: run(
        99999, branch: "late", status: "completed", conclusion: "startup_failure", created: "2026-05-31T23:00:00Z"))
    expect(turns.isEmpty, "a run made before its branch was deleted does not count, whatever its ID")
    turns = try gap.turns(event: "workflow_run", body: run(
        3, branch: "late", status: "completed", conclusion: "startup_failure", created: "2026-06-01T01:00:00Z"))
    expect(turns.count == 1, "a run made after, whatever its ID, counts")

    // A run that fails while cibar restarts.
    _ = try watch.turns(event: "workflow_run", body: run(20, status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(
        2, run: 20, name: "test", status: "completed", conclusion: "failure"))
    let restarted = Watch(restoring: watch.state)
    turns = try restarted.turns(event: "workflow_run", body: run(20, status: "completed", conclusion: "failure"))
    expect(turns.isEmpty && restarted.failures(on: on("CI")).map(\.name) == ["test"],
           "after a restart, a run that failed because of a job cibar recorded does not fail as a whole too")
} catch {
    expect(false, "deliveries are read: \(error)")
}

do {
    let empty = try JSONDecoder().decode(WatchState.self, from: Data("{}".utf8))
    expect(empty == WatchState(), "saved state that leaves everything out reads as nothing known")
} catch {
    expect(false, "saved state from other versions is read: \(error)")
}

// MARK: - Counting running jobs and runs

do {
    let watch = Watch()
    _ = try watch.turns(event: "workflow_run", body: run(10, status: "in_progress"))
    _ = try watch.turns(event: "workflow_run", body: run(11, workflow: "Lint", status: "queued"))
    for (id, run, status) in [(1, 10, "in_progress"), (2, 10, "in_progress"), (3, 10, "queued"), (4, 11, "queued")] {
        _ = try watch.turns(event: "workflow_job",
                            body: job(Int64(id), run: Int64(run), name: "j\(id)", status: status))
    }
    func counts(includingPullRequests: Bool) -> [Int] {
        let state = DashboardState(watch: watch, at: Date(), connection: .connected, sessions: nil,
                                   includesPullRequests: includingPullRequests)
        return [state.runningJobs, state.runningWorkflows, state.queuedJobs,
                watch.runningCount(defaultBranchesOnly: !includingPullRequests)]
    }
    expect(counts(includingPullRequests: false) == [2, 1, 2, 2],
           "queued jobs, and workflows with only queued jobs, are not counted as running: "
           + "\(counts(includingPullRequests: false))")
    _ = try watch.turns(event: "workflow_job", body: job(4, run: 11, name: "j4", status: "in_progress"))
    expect(counts(includingPullRequests: false) == [3, 2, 1, 3], "a workflow is running once one of its jobs is")
    _ = try watch.turns(event: "workflow_run", body: run(12, status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(5, run: 12, name: "j5", status: "in_progress"))
    _ = try watch.turns(event: "workflow_run", body: run(13, branch: "feature", status: "in_progress"))
    _ = try watch.turns(event: "workflow_job", body: job(6, run: 13, name: "j6", status: "in_progress"))
    expect(counts(includingPullRequests: false) == [4, 2, 1, 4],
           "two runs of a workflow on one branch are one workflow running, and another branch's are left out: "
           + "\(counts(includingPullRequests: false))")
    expect(counts(includingPullRequests: true) == [5, 3, 1, 5],
           "including pull requests counts what runs on other branches too: \(counts(includingPullRequests: true))")
} catch {
    expect(false, "running jobs are counted: \(error)")
}

do {
    let watch = Watch()
    for (id, branch, workflow) in [(20, "main", "CI"), (21, "feature", "CI"), (22, "feature", "Lint")] {
        _ = try watch.turns(event: "workflow_run", body: run(Int64(id), workflow: workflow, branch: branch,
                                                              status: "in_progress"))
        _ = try watch.turns(event: "workflow_job", body: job(Int64(id), run: Int64(id), name: "test",
                                                              status: "completed", conclusion: "failure"))
    }
    expect(watch.isRed(on("CI", "feature")) && watch.isRed(on("Lint", "feature"))
           && watch.failingWorkflowBranches == [on("CI")] && watch.failingJobCount == 1,
           "only workflows red on a default branch count as failing")
} catch {
    expect(false, "failing jobs are counted: \(error)")
}

// MARK: - Which default branches are listed

do {
    let watch = Watch()
    let start = Date(timeIntervalSince1970: 1_000_000)
    func listed(after hours: Double, in watch: Watch = watch) -> [String] {
        watch.listedDefaultBranches(at: start + hours * 3600).map { "\($0.repository) \($0.branch) \($0.red)" }
    }
    expect(listed(after: 0).isEmpty, "no default branch is listed before any workflow runs")
    _ = try watch.turns(event: "workflow_run", body: run(1, status: "completed", conclusion: "success"), at: start)
    _ = try watch.turns(event: "workflow_run", body: run(2, branch: "feature", status: "completed", conclusion: "success"),
                        at: start + 20 * 3600)
    _ = try watch.turns(event: "workflow_run", body: run(5, status: "completed", conclusion: "success", head: "fork/repo"),
                        at: start + 21 * 3600)
    expect(listed(after: 23) == ["octo/repo main false"] && listed(after: 25).isEmpty,
           "a green default branch is listed for a day after a workflow last ran on it, and runs on other "
           + "branches, or on a fork's branch of the same name, do not count: "
           + "\(listed(after: 23)) \(listed(after: 25))")
    _ = try watch.turns(event: "workflow_run", body: run(3, status: "completed", conclusion: "startup_failure"),
                        at: start + 30 * 3600)
    expect(listed(after: 100) == ["octo/repo main true"], "a red default branch is listed however long it is quiet")
    _ = try watch.turns(event: "workflow_run", body: run(4, status: "completed", conclusion: "success"),
                        at: start + 40 * 3600)
    let restored = Watch(restoring: try JSONDecoder().decode(WatchState.self,
                                                             from: JSONEncoder().encode(watch.state)))
    expect(listed(after: 63, in: restored) == ["octo/repo main false"] && listed(after: 65, in: restored).isEmpty,
           "when a workflow last finished on a default branch is saved")
    _ = try watch.turns(event: "workflow_run", body: run(6, status: "in_progress"), at: start + 50 * 3600)
    expect(listed(after: 70).isEmpty, "a workflow under way on a default branch does not keep it listed")
    // Several repositories, the one a workflow last finished on first.
    let several = Watch()
    for (index, name) in ["octo/a", "octo/b", "octo/c"].enumerated() {
        var body = try JSONSerialization.jsonObject(with: run(Int64(10 + index), status: "completed",
                                                             conclusion: "success")) as! [String: Any]
        body["repository"] = ["full_name": name, "name": "x", "default_branch": "main", "owner": ["login": "octo"]]
        var workflowRun = body["workflow_run"] as! [String: Any]
        workflowRun["head_repository"] = ["full_name": name]
        body["workflow_run"] = workflowRun
        _ = try several.turns(event: "workflow_run", body: JSONSerialization.data(withJSONObject: body),
                              at: start + [2, 3, 1][index] * 3600)
    }
    expect(several.listedDefaultBranches(at: start + 4 * 3600).map(\.repository) == ["octo/b", "octo/a", "octo/c"],
           "default branches as busy as each other are listed the latest first: "
           + "\(several.listedDefaultBranches(at: start + 4 * 3600).map(\.repository))")
    // octo/a has been much busier, though octo/c has finished a workflow
    // since; so it stays first.
    func finish(_ name: String, _ id: Int64, at hours: Double) throws {
        var body = try JSONSerialization.jsonObject(with: run(id, status: "completed", conclusion: "success"))
            as! [String: Any]
        body["repository"] = ["full_name": name, "name": "x", "default_branch": "main", "owner": ["login": "octo"]]
        var workflowRun = body["workflow_run"] as! [String: Any]
        workflowRun["head_repository"] = ["full_name": name]
        body["workflow_run"] = workflowRun
        _ = try several.turns(event: "workflow_run", body: JSONSerialization.data(withJSONObject: body),
                              at: start + hours * 3600)
    }
    for index in 0..<10 {
        try finish("octo/a", Int64(100 + index), at: 4 + Double(index) / 10)
    }
    try finish("octo/c", 200, at: 6)
    try finish("octo/c", 201, at: 6.5)
    let busiest = several.listedDefaultBranches(at: start + 7 * 3600).map(\.repository)
    expect(busiest == ["octo/a", "octo/c", "octo/b"],
           "the busiest default branch is listed first, ahead of one a workflow finished on since: \(busiest)")
    let saved = Watch(restoring: try JSONDecoder().decode(WatchState.self, from: JSONEncoder().encode(several.state)))
    let everything = PrimedState(coverage: ["octo/a": .init(), "octo/b": .init(), "octo/c": .init()])
    let refreshed = Watch.refreshed(from: everything, previous: saved, deliveries: []).watch
    expect(saved.listedDefaultBranches(at: start + 7 * 3600).map { $0.repository } == busiest
           && refreshed.listedDefaultBranches(at: start + 7 * 3600).map { $0.repository } == busiest,
           "how busy each default branch has been is saved, and kept when cibar reads from GitHub afresh")
    var activity = Activity()
    activity.count(at: start)
    activity.count(at: start - Activity.halfLife)
    expect(abs(activity.value(at: start) - 1.5) < 1e-9
           && abs(activity.value(at: start + Activity.halfLife) - 0.75) < 1e-9,
           "each finish counts half as much for each half-life since it happened, counted in any order: "
           + "\(activity.value(at: start))")
    let mixed = try JSONDecoder().decode(WatchState.self, from: Data("""
        {"defaultBranchRuns": {"o/r": 5, "o/s": "yesterday"}}
        """.utf8))
    expect(mixed.defaultBranchRuns == ["o/r": Date(timeIntervalSinceReferenceDate: 5)],
           "a saved time that cannot be read is left out")
} catch {
    expect(false, "default branches are listed: \(error)")
}

// MARK: - The dashboard

do {
    let watch = Watch()
    let now = Date()
    _ = try watch.turns(event: "workflow_run", body: run(1, status: "in_progress"), at: now)
    _ = try watch.turns(event: "workflow_job", body: job(10, run: 1, name: "test", status: "completed",
                                                         conclusion: "failure"), at: now)
    _ = try watch.turns(event: "workflow_run", body: run(2, branch: "feature", status: "in_progress"), at: now)
    _ = try watch.turns(event: "workflow_job", body: job(20, run: 2, name: "lint", status: "completed",
                                                         conclusion: "failure"), at: now)
    for (id, name, status) in [(21, "b", "queued"), (22, "a", "queued"), (23, "c", "in_progress")] {
        _ = try watch.turns(event: "workflow_job", body: job(Int64(id), run: 2, name: name, status: status), at: now)
    }
    _ = try watch.turns(event: "workflow_run", body: run(4, workflow: "Deploy", status: "in_progress"), at: now)
    _ = try watch.turns(event: "workflow_job", body: job(40, run: 4, name: "deploy", status: "in_progress"), at: now)
    let state = DashboardState(watch: watch, at: now, connection: .connected, sessions: true,
                               includesPullRequests: false)
    expect(state.failing.map { $0.workflowBranch } == [on("CI")]
           && state.failing.first?.jobs.map { $0.name } == ["test"]
           && state.failing.first?.jobs.first?.url
               == URL(string: "https://github.com/octo/repo/actions/runs/1/job/10")
           && state.failingJobs == 1,
           "the dashboard shows what fails on default branches, and nothing that fails elsewhere: \(state.failing)")
    expect(state.failing.first?.jobs.first?.codex == nil, "a job cibar did nothing about has nothing recorded")
    expect(state.failing.first?.jobs.first?.rerun == nil, "a failed job not run again says so")
    let again = Watch()
    _ = try again.turns(event: "workflow_run", body: run(1, status: "in_progress"), at: now)
    _ = try again.turns(event: "workflow_job", body: job(10, run: 1, name: "test", status: "completed",
                                                         conclusion: "failure"), at: now)
    _ = try again.turns(event: "workflow_job", body: job(11, run: 1, name: "lint", status: "completed",
                                                         conclusion: "failure"), at: now)
    _ = try again.turns(event: "workflow_run", body: run(2, status: "in_progress"), at: now)
    _ = try again.turns(event: "workflow_job", body: job(20, run: 2, name: "test", status: "queued"), at: now)
    let rerun = DashboardState(watch: again, at: now, connection: .connected, sessions: true,
                               includesPullRequests: false).failing.first?.jobs ?? []
    expect(rerun.map(\.name) == ["lint", "test"]
           && rerun.map(\.rerun) == [nil, URL(string: "https://github.com/octo/repo/actions/runs/2/job/20")],
           "a failed job queued or running in a later run is running again there, and another is not: \(rerun)")
    let sentWatch = Watch()
    _ = try sentWatch.turns(event: "workflow_run", body: run(1, status: "in_progress"), at: now)
    for (id, name) in [(10, "test"), (11, "build")] {
        _ = try sentWatch.turns(event: "workflow_job", body: job(Int64(id), run: 1, name: name, status: "completed",
                                                                 conclusion: "failure"), at: now)
    }
    _ = try sentWatch.turns(event: "workflow_run", body: run(2, branch: "feature", status: "in_progress"), at: now)
    _ = try sentWatch.turns(event: "workflow_job", body: job(20, run: 2, name: "test", status: "completed",
                                                             conclusion: "failure"), at: now)
    let sessions = [
        CodexRecord(job: JobKey(workflowBranch: on("CI"), job: "test"), at: now - 60, outcome: .sent(thread: "a/b c")),
        CodexRecord(job: JobKey(workflowBranch: on("CI"), job: "build"), at: now - 600, outcome: .sent(thread: "b")),
        CodexRecord(job: JobKey(workflowBranch: on("CI"), job: "lint"), at: now, outcome: .sent(thread: "not failing")),
        CodexRecord(job: JobKey(workflowBranch: on("CI", "feature"), job: "test"), at: now, outcome: .sent(thread: "elsewhere")),
    ]
    let jobs = DashboardState(watch: sentWatch, at: now, connection: .connected, sessions: true, records: sessions,
                              includesPullRequests: false).failing.first?.jobs ?? []
    expect(jobs.map(\.name) == ["build", "test"] && jobs.map(\.codex) == [sessions[1], sessions[0]],
           "each failing job shows what cibar did about it, and nothing about another: \(jobs)")
    let sent = jobs.last?.codex
    expect(sent?.url?.absoluteString == "codex://threads/a/b%20c",
           "a session opens in the Codex app at its thread: \(String(describing: sent?.url))")
    expect(state.branches.map { "\($0.repository) \($0.branch) \($0.red)" } == ["octo/repo main true"]
           && state.branches.first?.url?.absoluteString == "https://github.com/octo/repo/actions?query=branch:main",
           "the dashboard lists each default branch worth listing, opening its runs: \(state.branches)")
    expect(state.runs.map { $0.id } == [4] && state.runningJobs == 1 && state.queuedJobs == 0
           && state.runningWorkflows == 1 && state.isConnected,
           "without pull requests, only what runs on default branches is shown and counted: \(state.runs)")
    let all = DashboardState(watch: watch, at: now, connection: .connected, sessions: true,
                             includesPullRequests: true)
    expect(watch.isRed(on("CI", "feature")) && all.failing.map { $0.workflowBranch } == [on("CI")]
           && all.failingJobs == 1,
           "with pull requests, what fails on other branches is still neither shown nor counted: \(all.failing)")
    expect(all.runs.map { $0.id } == [4, 2] && all.runs.map { $0.onDefaultBranch } == [true, false]
           && all.runs.allSatisfy { $0.pullRequest == nil },
           "runs are shown newest first: \(all.runs.map { $0.id })")
    expect(all.runs.map { $0.failed.map(\.name) } == [[], ["lint"]]
           && all.runs.last?.failed.first?.url == URL(string: "https://github.com/octo/repo/actions/runs/2/job/20"),
           "a run still going shows the jobs of it that have failed, and a run none of whose jobs failed shows none")
    func pullRequest(_ action: String, number: Int = 7) throws {
        _ = try watch.turns(event: "pull_request", body: json([
            "action": action, "repository": repository,
            "pull_request": ["number": number, "user": ["login": "ada"],
                             "head": ["ref": "feature", "label": "octo:feature", "repo": ["full_name": "octo/repo"]]],
        ]))
    }
    func shownPullRequest() -> PullRequestInfo? {
        DashboardState(watch: watch, at: now, connection: .connected, sessions: true,
                       includesPullRequests: true).runs.last?.pullRequest
    }
    try pullRequest("opened")
    expect(shownPullRequest() == PullRequestInfo(number: 7, author: "ada"),
           "a run on a pull request's branch shows the pull request's number and who opened it: "
           + "\(String(describing: shownPullRequest()))")
    try pullRequest("closed")
    expect(shownPullRequest() == nil, "once the pull request closes, its run shows only its branch")
    expect(all.runs.last?.jobs.map { $0.name } == ["b", "a", "c"]
           && all.runs.last?.running == 1 && all.runs.last?.queued == 2
           && all.runs.last?.jobs.last?.url == URL(string: "https://github.com/octo/repo/actions/runs/2/job/23"),
           "each run in progress lists its jobs, oldest first, each opening its own page: \(all.runs)")
    expect(all.runningJobs == 2 && all.queuedJobs == 2 && all.runningWorkflows == 2,
           "with pull requests, what runs on other branches is counted too")
    let disconnected = DashboardState(watch: watch, at: now, connection: .connecting, sessions: nil,
                                      includesPullRequests: false)
    expect(!disconnected.isConnected && disconnected.sessions == nil && disconnected.problems.isEmpty,
           "the dashboard says when cibar is not connected")

    _ = try watch.turns(event: "workflow_run", body: run(3, workflow: "Lint", status: "completed",
                                                         conclusion: "startup_failure"), at: now)
    let whole = DashboardState(watch: watch, at: now, connection: .connecting, sessions: nil,
                               includesPullRequests: false)
    expect(whole.failing.map { $0.jobs.map { $0.name } } == [["test"], ["the run"]],
           "a run that failed without a job failing is shown as the run: \(whole.failing)")

    // How cibar stands with the relay.
    typealias Connection = DashboardState.Connection
    let since = ConnectionState.connected(since: now)
    var lateness = Lateness()
    expect(lateness.text == nil && !lateness.isLate, "before any delivery, no lateness is told")
    lateness.record(7, at: now)
    expect(lateness.isLate && lateness.text == "7.0s", "a delivery more than five seconds late is late")
    lateness.record(-2, at: now + 1)
    expect(!lateness.isLate && lateness.text == "0.0s",
           "a delivery that seems to arrive before it was made arrived on time, and is not late")
    lateness.record(12.6, at: now + 2)
    expect(lateness.text == "12s", "a long lateness is told in whole seconds")
    lateness.record(1, at: now + Lateness.remembered + 1.5)
    expect(lateness.deliveries.map(\.seconds) == [12.6, 1],
           "only the deliveries of the last few seconds are remembered: \(lateness.deliveries)")

    // The chart has a bar for each delivery, until it has scrolled away.
    _ = NSApplication.shared
    let chart = LatencyChartView()
    var recent = Lateness()
    let arrived = Date()
    recent.record(0.5, at: arrived - Lateness.remembered - 2)
    recent.record(3, at: arrived - 3)
    chart.show(recent)
    expect(recent.deliveries.count == 2 && chart.barCount == 1,
           "a delivery that has scrolled away has no bar: \(chart.barCount)")
    recent.record(9, at: arrived)
    chart.show(recent)
    chart.show(recent)
    expect(chart.barCount == 2, "each delivery has one bar, however often the chart is shown: \(chart.barCount)")
    let gone = chart.latencyGoneAt.map { $0 - CACurrentMediaTime() }
    expect(gone.map { abs($0 - Lateness.remembered) < 1 } == true,
           "the latest latency fades away as its delivery's bar scrolls off the chart: \(String(describing: gone))")
    expect(Connection(since, settingsProblem: nil) == .connected
           && Connection(since, settingsProblem: "no address") == .unusableSettings("no address")
           && Connection(.disconnected(reason: "refused", retryAt: nil), settingsProblem: nil)
               == .disconnected(reason: "refused", retryAt: nil)
           && Connection(.connecting, settingsProblem: nil) == .connecting,
           "settings that cannot be used come before the connection, which is otherwise as it is")
    var troubled = disconnected
    troubled.connection = .disconnected(reason: "refused", retryAt: nil)
    troubled.deliveryProblem = "A workflow_job delivery could not be read"
    expect(troubled.problems == ["Not connected to the relay: refused", "A workflow_job delivery could not be read"],
           "what is wrong is said in words: \(troubled.problems)")
    troubled.connection = .unusableSettings("no address")
    troubled.deliveryProblem = nil
    expect(troubled.problems == ["The settings cannot be used: no address"], "settings that cannot be used say why")
    troubled.refreshProblem = "Could not read from GitHub: offline"
    expect(troubled.problems.last == "Could not read from GitHub: offline", "a failure to read from GitHub says why")

    // The view lays itself out for every state, and is never taller than a
    // small screen, however much it has to show.
    _ = NSApplication.shared
    var crowded = state
    crowded.failing = (0..<100).map { _ in state.failing[0] }.enumerated().map { index, failing in
        var failing = failing
        failing.workflowBranch.path = "w\(index).yml"
        return failing
    }
    crowded.branches = (0..<100).map { .init(repository: "o/r\($0)", branch: "main", red: false) }
    crowded.runs = (0..<100).map {
        .init(id: Int64($0), workflowBranch: on("CI"), onDefaultBranch: true, jobs: all.runs[1].jobs)
    }
    let size = dashboardSize(screenHeight: 900)
    expect(size == NSSize(width: 780, height: 640) && dashboardSize(screenHeight: 600).height == 540,
           "the dashboard is always as big, unless the screen is too small for it")
    let visible = CGRect(x: 0, y: 0, width: 1800, height: 1100)
    let middle = dashboardFrame(below: CGRect(x: 900, y: 1110, width: 40, height: 24), size: size, within: visible)
    expect(middle == CGRect(x: 530, y: 1104 - 640, width: 780, height: 640),
           "the dashboard opens centred just below the indicator: \(middle)")
    let right = dashboardFrame(below: CGRect(x: 1740, y: 1110, width: 40, height: 24), size: size, within: visible)
    let left = dashboardFrame(below: CGRect(x: 10, y: 1110, width: 40, height: 24), size: size, within: visible)
    expect(right.maxX == 1792 && left.minX == 8 && right.width == 780 && left.width == 780,
           "an indicator near the screen's edge moves the dashboard in from it: \(left) \(right)")
    let panel = DashboardPanel(showing: NSView())
    expect(panel.canBecomeKey && panel.styleMask.contains(.nonactivatingPanel) && !panel.hidesOnDeactivate,
           "the dashboard takes the keyboard without making cibar the active app")
    var escaped = false
    panel.onEscape = { escaped = true }
    panel.cancelOperation(nil)
    expect(escaped, "Escape closes the dashboard")
    var tornOff = 0
    panel.onTearOff = { tornOff += 1 }
    var titleBars: [TitleBar] = []
    panel.onTitleBar = { titleBars.append($0) }
    panel.setFrame(CGRect(x: 100, y: 100, width: 780, height: 640), display: false)
    panel.setFrame(CGRect(x: 200, y: 100, width: 780, height: 600), display: false)
    expect(!panel.isTornOff && tornOff == 0, "moving the dashboard other than by dragging it does not tear it off")
    panel.tearOff()
    panel.tearOff()
    expect(panel.isTornOff && panel.level == .normal && !panel.styleMask.contains(.resizable) && tornOff == 1
           && panel.frame == CGRect(x: 200, y: 100, width: 780, height: 600),
           "a torn-off dashboard is a window like any other, where it was, the size it was")
    panel.contentView?.layoutSubtreeIfNeeded()
    let shown = [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton].filter {
        panel.standardWindowButton($0).map { !$0.isHidden } ?? false
    }
    let titleBar = panel.frame.height - panel.contentLayoutRect.height
    let grip = panel.contentView?.subviews.first { $0 is TearOffHandle }
    expect(shown == [.closeButton] && panel.styleMask.contains(.utilityWindow) && panel.titlebarAppearsTransparent
           && titleBar > 0 && titleBars.map(\.height) == [titleBar] && grip?.isHidden == true
           && titleBars.first.map { $0.middle > 0 && $0.middle < titleBar } == true,
           "a torn-off dashboard has a palette's slim title bar with just a small button to close it, whose height "
           + "and button's middle its content is told, and no grip")
    escaped = false
    panel.standardWindowButton(.closeButton)?.performClick(nil)
    expect(!panel.isTornOff && panel.level == .popUpMenu && !panel.styleMask.contains(.resizable)
           && !panel.styleMask.contains(.titled) && panel.standardWindowButton(.closeButton) == nil && !escaped,
           "its close button closes a torn-off dashboard, which then opens as a popup again")
    expect(titleBars.last == TitleBar.none && titleBars.count == 2 && grip?.isHidden == false,
           "a popup's content is told it has no title bar, and its grip shows again")
    panel.tearOff()
    panel.cancelOperation(nil)
    expect(!panel.isTornOff && !panel.styleMask.contains(.titled), "and so does Escape")
    for state in [state, disconnected, crowded] {
        let model = DashboardModel(state: state, actions: DashboardActions())
        let fitted = NSHostingController(rootView: DashboardView(model: model)).sizeThatFits(in: size)
        expect(fitted == size, "the dashboard fills its size exactly, whatever it shows: \(fitted)")
    }
} catch {
    expect(false, "the dashboard is worked out: \(error)")
}

// MARK: - Reading from GitHub's API

/// Answers requests as GitHub's API would, from `pages`, by path and query,
/// recording each request; anything else is not found. Requests arrive on
/// URLSession's threads, so what it keeps is kept under a lock.
final class StandInGitHub: URLProtocol {
    struct Page {
        var body: Any
        var link: String? = nil
        var status = 200
        var headers: [String: String] = [:]
    }

    private static let lock = NSLock()
    nonisolated(unsafe) private static var storedPages: [String: Page] = [:]
    nonisolated(unsafe) private static var storedAsked: [String] = []

    static var pages: [String: Page] {
        get { lock.withLock { storedPages } }
        set { lock.withLock { storedPages = newValue } }
    }

    static var asked: [String] {
        get { lock.withLock { storedAsked } }
        set { lock.withLock { storedAsked = newValue } }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let url = request.url!
        let key = url.path + (url.query.map { "?" + $0 } ?? "")
        let page = Self.lock.withLock {
            Self.storedAsked.append(key)
            return Self.storedPages[key]
        }
        var headers = page?.headers ?? [:]
        headers["Content-Type"] = "application/json"
        headers["Link"] = page?.link
        let response = HTTPURLResponse(url: url, statusCode: page?.status ?? 404, httpVersion: nil,
                                       headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: try! JSONSerialization.data(withJSONObject: page?.body ?? [:]))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

let standInSession: URLSession = {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [StandInGitHub.self]
    return URLSession(configuration: configuration)
}()

/// What a read reported of its progress, as it went.
final class ProgressRecord: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: [String] = []
    var reports: [String] { lock.withLock { stored } }
    func add(_ done: Int, _ total: Int) { lock.withLock { stored.append("\(done)/\(total)") } }
}

/// Reads from the stand-in, as cibar reads from GitHub.
func readStandIn(owners: [String], alsoReading: Set<String> = [], now: Date,
                 progress: ProgressRecord = ProgressRecord()) -> Result<PrimedState, Error> {
    let api = GitHubAPI(token: "t", session: standInSession)
    var result: Result<PrimedState, Error>?
    Task {
        result = await Result {
            try await api.prime(owners: owners, alsoReading: alsoReading, now: now, progress: { progress.add($0, $1) })
        }
    }
    waitUntil { result != nil }
    return result!
}

do {
    let now = Date()
    let iso = ISO8601DateFormatter()
    func stamp(_ hoursAgo: Double) -> String { iso.string(from: now - hoursAgo * 3600) }
    func repository(_ name: String, pushed: Double, archived: Bool = false) -> [String: Any] {
        ["full_name": "octo/\(name)", "default_branch": "main", "owner": ["login": "octo"],
         "pushed_at": stamp(pushed), "archived": archived]
    }
    func apiRun(_ id: Int, _ workflow: String, branch: String = "main", head: String? = "octo/repo",
                status: String, conclusion: String? = nil, hoursAgo: Double) -> [String: Any] {
        var run: [String: Any] = [
            "id": id, "run_attempt": 1, "name": workflow, "path": ".github/workflows/\(workflow).yml",
            "created_at": stamp(hoursAgo), "run_started_at": stamp(hoursAgo), "updated_at": stamp(hoursAgo),
            "event": branch == "main" ? "push" : "pull_request", "status": status, "head_branch": branch,
            "head_sha": "abc123", "html_url": "https://github.com/octo/repo/actions/runs/\(id)",
        ]
        run["head_repository"] = head.map { ["full_name": $0] } ?? NSNull()
        run["conclusion"] = conclusion
        return run
    }
    func apiJob(_ id: Int, run: Int, _ name: String, status: String, conclusion: String? = nil) -> [String: Any] {
        var job: [String: Any] = [
            "id": id, "run_id": run, "run_attempt": 1, "name": name, "status": status,
            "html_url": "https://github.com/octo/repo/actions/runs/\(run)/job/\(id)",
        ]
        job["conclusion"] = conclusion
        return job
    }
    func workflow(_ name: String, _ state: String = "active") -> [String: Any] {
        ["path": ".github/workflows/\(name).yml", "state": state]
    }
    typealias Page = StandInGitHub.Page
    let pages: [String: Page] = [
        "/orgs/octo/repos?per_page=100&type=all": Page(
            body: [repository("repo", pushed: 1), repository("gone", pushed: 1, archived: true)],
            link: "<https://api.github.com/orgs/octo/repos?per_page=100&type=all&page=2>; rel=\"next\""),
        "/orgs/octo/repos?per_page=100&type=all&page=2": Page(body: [repository("old", pushed: 24 * 30)]),
        "/user": Page(body: ["login": "me"]),
        "/users/someone/repos?per_page=100&type=owner": Page(body: [repository("theirs", pushed: 400)]),
        "/user/repos?per_page=100&affiliation=owner": Page(body: [repository("mine", pushed: 400)]),
        "/repos/octo/repo/actions/workflows?per_page=100": Page(body: ["workflows": [
            workflow("CI"), workflow("Lint"), workflow("Gone", "deleted"),
        ]]),
        "/repos/octo/repo/pulls?state=open&per_page=100": Page(body: [
            ["number": 12, "user": ["login": "ada"],
             "head": ["ref": "feature", "label": "octo:feature", "repo": ["full_name": "octo/repo"]]],
            ["number": 13, "user": ["login": "grace"],
             "head": ["ref": "patch", "label": "fork:patch", "repo": ["full_name": "fork/repo"]]],
        ]),
        "/repos/octo/repo/actions/runs?per_page=100": Page(body: ["workflow_runs": [
            apiRun(9, "CI", branch: "main", head: nil, status: "completed", conclusion: "success", hoursAgo: 0.5),
            apiRun(8, "CI", branch: "patch", head: "fork/repo", status: "completed", conclusion: "failure",
                   hoursAgo: 1),
            apiRun(7, "Gone", status: "completed", conclusion: "failure", hoursAgo: 1),
            apiRun(6, "CI", branch: "closed", status: "completed", conclusion: "failure", hoursAgo: 1),
            apiRun(5, "Lint", status: "completed", conclusion: "cancelled", hoursAgo: 1),
            apiRun(4, "CI", branch: "feature", status: "in_progress", hoursAgo: 2),
            apiRun(3, "Lint", status: "completed", conclusion: "failure", hoursAgo: 3),
            apiRun(2, "CI", status: "completed", conclusion: "failure", hoursAgo: 4),
        ]]),
        "/repos/octo/repo/actions/runs?per_page=100&branch=main": Page(body: ["workflow_runs": [
            apiRun(3, "Lint", status: "completed", conclusion: "failure", hoursAgo: 3),
            apiRun(2, "CI", status: "completed", conclusion: "failure", hoursAgo: 4),
            apiRun(1, "CI", status: "completed", conclusion: "success", hoursAgo: 30),
        ]]),
        "/repos/octo/repo/actions/runs/2/jobs?per_page=100": Page(body: ["jobs": [
            apiJob(20, run: 2, "test", status: "completed", conclusion: "failure"),
        ]]),
        "/repos/octo/repo/actions/runs/3/jobs?per_page=100": Page(body: ["jobs": [
            apiJob(30, run: 3, "lint", status: "completed", conclusion: "failure"),
        ]]),
        "/repos/octo/repo/actions/runs/8/jobs?per_page=100": Page(body: ["jobs": [
            apiJob(80, run: 8, "test", status: "completed", conclusion: "failure"),
        ]]),
        "/repos/octo/repo/actions/runs/4/jobs?per_page=100": Page(
            body: ["jobs": [
                apiJob(41, run: 4, "test (2/2)", status: "queued"),
                apiJob(40, run: 4, "test (1/2)", status: "in_progress"),
            ]],
            link: "<https://api.github.com/repos/octo/repo/actions/runs/4/jobs?per_page=100&page=2>; rel=\"next\""),
        "/repos/octo/repo/actions/runs/4/jobs?per_page=100&page=2": Page(body: ["jobs": [
            apiJob(42, run: 4, "lint", status: "in_progress"),
        ]]),
    ]
    StandInGitHub.pages = pages
    StandInGitHub.asked = []
    let read = (try? readStandIn(owners: ["octo", "someone", "me"], now: now).get()) ?? PrimedState()
    let watch = Watch.refreshed(from: read, previous: Watch(), deliveries: []).watch
    expect(read.repositories == ["octo/repo"] && read.problems.isEmpty,
           "only repositories pushed to lately and not archived are read: \(read.repositories) \(read.problems)")
    expect(StandInGitHub.asked.contains("/users/someone/repos?per_page=100&type=owner")
           && StandInGitHub.asked.contains("/user/repos?per_page=100&affiliation=owner"),
           "an owner that is not an organization is a user, all of whose repositories are listed when it is the "
           + "token's own, and only the public ones otherwise")
    let jobsAsked = StandInGitHub.asked.filter { $0.contains("/jobs") }.sorted()
    expect(jobsAsked == ["/repos/octo/repo/actions/runs/2/jobs?per_page=100",
                         "/repos/octo/repo/actions/runs/3/jobs?per_page=100",
                         "/repos/octo/repo/actions/runs/4/jobs?per_page=100",
                         "/repos/octo/repo/actions/runs/4/jobs?per_page=100&page=2",
                         "/repos/octo/repo/actions/runs/8/jobs?per_page=100"],
           "jobs are read only for runs under way, and for the latest run of each workflow on each branch to "
           + "pass or fail when it failed, every page of them: \(jobsAsked)")
    expect(watch.redWorkflowBranches.map { "\($0.workflow) \($0.branch)" } == ["CI fork:patch", "CI main", "Lint main"]
           && watch.failures(on: watch.failingWorkflowBranches[0]).map(\.name) == ["test"],
           "a workflow whose latest run to finish on a branch failed is red there, with its failing jobs, while "
           + "the runs of a workflow whose file is gone, of a branch with no open pull request, and of a fork "
           + "since deleted are left out, and a fork's pull request is told by its label: "
           + "\(watch.redWorkflowBranches)")
    expect(watch.active.values.map(\.name).sorted() == ["lint", "test (1/2)", "test (2/2)"]
           && watch.runningCount(defaultBranchesOnly: false) == 2 && watch.defaultBranches == ["octo/repo": "main"],
           "the jobs of runs under way are running or queued: \(watch.active.values.map(\.name))")
    expect(watch.listedDefaultBranches(at: now).map(\.repository) == ["octo/repo"],
           "a default branch a workflow ran on lately is listed")
    expect(watch.pullRequest(for: WorkflowBranch(repository: "octo/repo", workflow: "CI", path: "", branch: "feature"))
           == PullRequestInfo(number: 12, author: "ada")
           && watch.pullRequest(for: WorkflowBranch(repository: "octo/repo", workflow: "CI", path: "",
                                                    branch: "fork:patch")) == PullRequestInfo(number: 13, author: "grace")
           && watch.pullRequest(for: on("CI")) == nil,
           "the open pull requests read say whose each branch is, a fork's included")

    // A quiet repository is read when asked to be.
    StandInGitHub.asked = []
    _ = readStandIn(owners: ["octo"], alsoReading: ["octo/old"], now: now)
    expect(StandInGitHub.asked.contains("/repos/octo/old/actions/workflows?per_page=100"),
           "a quiet repository is read when it is red")

    // What cannot be read is said to be, and the rest is read.
    var broken = pages
    broken["/orgs/octo/repos?per_page=100&type=all"] =
        Page(body: [repository("repo", pushed: 1), repository("broken", pushed: 1)])
    broken["/repos/octo/broken/actions/workflows?per_page=100"] = Page(body: ["message": "Server Error"], status: 502)
    broken["/repos/octo/repo/actions/runs/4/jobs?per_page=100"] = nil
    StandInGitHub.pages = broken
    let progress = ProgressRecord()
    let partial = (try? readStandIn(owners: ["octo", "nobody"], now: now, progress: progress).get()) ?? PrimedState()
    expect(progress.reports == ["0/2", "1/2", "2/2"],
           "a read says how many of how many repositories it has read, as each is, failed or not: "
           + "\(progress.reports)")
    expect(partial.repositories == ["octo/repo"]
           && partial.problems == [
               "nobody: GitHub has no /users/nobody/repos, or the token cannot see it",
               "octo/broken: GitHub answered 502 for /repos/octo/broken/actions/workflows: Server Error",
               "octo/repo: GitHub has no /repos/octo/repo/actions/runs/4/jobs, or the token cannot see it",
           ],
           "an owner, a repository, or a run's jobs that cannot be read is a problem of its own: \(partial.problems)")

    // GitHub refusing to answer more ends the read.
    var limited = pages
    limited["/repos/octo/repo/pulls?state=open&per_page=100"] = Page(
        body: ["message": "API rate limit exceeded"], status: 403,
        headers: ["x-ratelimit-remaining": "0", "x-ratelimit-reset": "\(Int(now.timeIntervalSince1970) + 600)"])
    StandInGitHub.pages = limited
    StandInGitHub.asked = []
    if case .failure(let error) = readStandIn(owners: ["octo"], now: now) {
        expect("\(error)".hasPrefix("GitHub answered 403 for /repos/octo/repo/pulls: API rate limit exceeded (no "
                                    + "requests left until ")
               && !StandInGitHub.asked.contains { $0.contains("/actions/runs") },
               "running out of requests ends the read, saying until when: \(error)")
    } else {
        expect(false, "running out of requests ends the read")
    }
}

expect(nextPageURL(link: #"<https://a.example/x?page=2>; rel="next", <https://a.example/x?page=9>; rel="last""#)
       == URL(string: "https://a.example/x?page=2")
       && nextPageURL(link: #"<https://a.example/x?page=9>; rel="last""#) == nil && nextPageURL(link: nil) == nil,
       "the next page is found in a Link header, and only there")

// MARK: - Starting afresh from what GitHub's API says

do {
    // cibar knows of a repository it will not read, and of runs in one it
    // will: one whose job failed while it was reading, one of a workflow
    // that seldom runs, and one of a workflow whose file is gone.
    let previous = Watch()
    let other: [String: Any] = ["full_name": "octo/other", "name": "other", "default_branch": "main",
                                "owner": ["login": "octo"]]
    func inOther(_ body: Data) -> Data {
        var object = try! JSONSerialization.jsonObject(with: body) as! [String: Any]
        object["repository"] = other
        return try! JSONSerialization.data(withJSONObject: object)
    }
    _ = try previous.turns(event: "workflow_run", body: inOther(run(1, status: "completed", conclusion: "failure")))
    _ = try previous.turns(event: "workflow_run", body: run(3, workflow: "Nightly", status: "completed",
                                                            conclusion: "failure"))
    _ = try previous.turns(event: "workflow_run", body: run(4, workflow: "Gone", status: "completed",
                                                            conclusion: "failure"))
    _ = try previous.turns(event: "delete", body: json([
        "ref": "deleted", "ref_type": "branch", "repository": repository,
    ]), at: Date(timeIntervalSince1970: 2_000_000_000))
    _ = try previous.turns(event: "workflow_run", body: run(2, status: "in_progress"))
    let failed = job(20, run: 2, name: "test", status: "completed", conclusion: "failure")
    let delivery = Delivery(Data("POST / HTTP/1.1\r\nX-GitHub-Event: workflow_job\r\n\r\n".utf8) + failed)!
    let sessions = try previous.handle(event: "workflow_job", body: failed).failures.count
    var coverage = PrimedState.Coverage()
    coverage.workflows = [".github/workflows/CI.yml", ".github/workflows/Nightly.yml"]
    coverage.branches = ["main"]
    coverage.seen = [".github/workflows/CI.yml main"]
    let read = PrimedState(coverage: ["octo/repo": coverage])
    let (fresh, failures) = Watch.refreshed(from: read, previous: previous, deliveries: [delivery])
    expect(fresh.redWorkflowBranches.map { "\($0.repository) \($0.workflow)" }
           == ["octo/other CI", "octo/repo CI", "octo/repo Nightly"],
           "what was known of a repository not read is kept, as is what the read did not see a run of, and what "
           + "arrived while reading is taken in, while a workflow whose file is gone is forgotten: "
           + "\(fresh.redWorkflowBranches)")
    let later = try fresh.handle(event: "workflow_run", body: run(2, status: "completed", conclusion: "failure"))
    expect(sessions == 1 && failures.isEmpty && later.failures.isEmpty,
           "a failure taken in again calls for no second session, then or when its run ends: \(later.failures)")
    let resurrected = try fresh.handle(event: "workflow_run", body: run(
        5, branch: "deleted", status: "completed", conclusion: "failure", created: "2026-01-01T00:00:00Z"))
    expect(resurrected.turns.isEmpty && resurrected.failures.isEmpty
           && fresh.redWorkflowBranches.allSatisfy { $0.branch != "deleted" },
           "a branch forgotten before stays forgotten")

    // A failure delivered while reading, of a run cibar had not heard of,
    // calls for a session once the run is known.
    let unheard = Watch()
    let pending = job(60, run: 6, name: "build", status: "completed", conclusion: "failure")
    _ = try unheard.handle(event: "workflow_job", body: pending)
    let runDelivery = Delivery(Data("POST / HTTP/1.1\r\nX-GitHub-Event: workflow_run\r\n\r\n".utf8)
                               + run(6, status: "in_progress"))!
    let pendingDelivery = Delivery(Data("POST / HTTP/1.1\r\nX-GitHub-Event: workflow_job\r\n\r\n".utf8) + pending)!
    let caught = Watch.refreshed(from: PrimedState(), previous: unheard, deliveries: [runDelivery, pendingDelivery])
    expect(caught.failures.map(\.job) == ["build"],
           "a failure delivered while reading, that cibar had not acted on, is returned: \(caught.failures)")
} catch {
    expect(false, "cibar starts afresh from what GitHub's API says: \(error)")
}

expect(counted(1, "repository") == "1 repository" && counted(3, "repository") == "3 repositories"
       && counted(2, "workflow") == "2 workflows", "counts name what they count, one or many")

// MARK: - Jobs in columns

do {
    let short = (1...40).map { "Tests (\($0)/40)" }
    let columns = JobColumns(names: short, width: 352)
    expect(columns.count == 3 && CGFloat(columns.count) * columns.width + 2 * JobColumns.gap <= 352,
           "short names are set out in as many columns as fit: \(columns)")
    expect(columns.named(of: 40) == 24 && columns.named(of: 25) == 25 && columns.named(of: 26) == 24,
           "as many jobs are named as fill the rows, or all when only one more would be left over")
    let long = JobColumns(names: ["Python tests (cf-dist runtime 2/4) on a long-named runner pool"], width: 352)
    expect(long.count == 1 && long.named(of: 20) == 8, "long names take a column each, eight to a run: \(long)")
    expect(JobColumns(names: short, width: 0).count == 1, "with no room yet, there is one column")
    let longest = JobColumns(names: [String(repeating: "Python tests (people scoped audit) ", count: 4)], width: 300)
    expect(longest.count == 1 && longest.width == 300, "a name too long for the room is cut short to fit: \(longest)")
}

// MARK: - Commit hashes in names

do {
    let hash = "10ffe7a81ce7f6128fd69dffc18926648537f0aa"
    expect("Deploy Rapids \(hash)".abbreviatingHashes == "Deploy Rapids 10ffe7a"
           && "snapshot \(hash) and \(hash + hash.prefix(24))".abbreviatingHashes == "snapshot 10ffe7a and 10ffe7a",
           "a full commit hash, of either kind, is shortened to its first seven digits")
    expect("Tests (11/40)".abbreviatingHashes == "Tests (11/40)"
           && "deadbeefcafe".abbreviatingHashes == "deadbeefcafe"
           && "x\(hash)".abbreviatingHashes == "x\(hash)" && "\(hash)1".abbreviatingHashes == "\(hash)1",
           "anything else, a shorter run of digits or a hash run into other characters, is left as it is")
}

// MARK: - The odometer

do {
    func wheels(_ value: Double, _ places: Int) -> [Double] {
        (0..<places).map { Odometer(value: value).wheel($0) }
    }
    expect(wheels(123, 4) == [123, 12, 1, 0],
           "each wheel rests at the number the digits from its place on make, and those before the first at "
           + "nothing")
    expect(wheels(-1, 1) == [0], "a number below nothing shows as nothing")
    expect(String(Int(pow(10, Double(Odometer.places))) - 1).count == Odometer.places,
           "an odometer has a wheel for each place of the largest number it shows")
}

// MARK: - Workflows of the same name, and what is kept

do {
    let watch = Watch()
    _ = try watch.turns(event: "workflow_run", body: run(1, workflow: "CI", path: ".github/workflows/a.yml",
                                                          status: "completed", conclusion: "startup_failure"))
    _ = try watch.turns(event: "workflow_run", body: run(2, workflow: "CI", path: ".github/workflows/b.yml",
                                                          status: "completed", conclusion: "success"))
    expect(watch.failingWorkflowBranches.map(\.path) == [".github/workflows/a.yml"],
           "two workflows of the same name are told apart by their files")
    for index in 0...Watch.rememberedForgettings {
        _ = try watch.turns(event: "delete", body: json([
            "ref": "branch-\(index)", "ref_type": "branch", "repository": repository,
        ]), at: Date(timeIntervalSince1970: TimeInterval(index)))
    }
    let kept = watch.state.forgotten
    expect(kept.count == Watch.rememberedForgettings && !kept.contains { $0.workflowBranch.branch == "branch-0" },
           "only the latest forgotten branches are kept")
    watch.owners = ["someone-else"]
    expect(watch.defaultBranches.isEmpty, "leaving an owner out forgets its repositories' default branches")
    let mixed = try JSONDecoder().decode(WatchState.self, from: Data("""
        {"verdicts": [
            {"bad": 1},
            {"workflowBranch": {"repository": "o/r", "workflow": "W", "branch": "b"},
             "job": "nopath", "verdict": {"order": {"run": 1, "attempt": 1}, "passed": false, "url": "u"}},
            {"workflowBranch": {"repository": "o/r", "workflow": "W", "path": "w.yml", "branch": "b"},
             "job": "j", "verdict": {"order": {"run": 1, "attempt": 1}, "passed": false, "url": "u"}}],
          "defaultBranches": {"o/r": "main", "o/s": 7}}
        """.utf8))
    expect(mixed.verdicts.map(\.job) == ["j"] && mixed.defaultBranches == ["o/r": "main"],
           "an entry that cannot be read, or does not say which workflow it is about, is left out")
    let reshaped = try JSONDecoder().decode(WatchState.self, from: Data("""
        {"verdicts": {"new": "shape"}, "defaultBranches": ["o/r"],
         "passes": [{"workflowBranch": {"repository": "o/r", "workflow": "W", "path": "w.yml", "branch": "b"},
                     "order": {"run": 1, "attempt": 1}}]}
        """.utf8))
    expect(reshaped.verdicts.isEmpty && reshaped.defaultBranches.isEmpty && reshaped.passes.count == 1,
           "a part saved in another shape is taken as empty, and the rest are kept")

    // A workflow renamed.
    let renamed = Watch()
    _ = try renamed.turns(event: "workflow_run", body: run(1, workflow: "CI", path: ".github/workflows/ci.yml",
                                                            status: "completed", conclusion: "startup_failure"))
    var turns = try renamed.turns(event: "workflow_run", body: run(
        2, workflow: "Build", path: ".github/workflows/ci.yml", status: "completed", conclusion: "success"))
    expect(turns.count == 1 && !turns[0].red && renamed.verdicts.isEmpty,
           "a workflow renamed is the same workflow, so its passing run turns it green")
    _ = try renamed.turns(event: "workflow_run", body: run(
        3, workflow: "Build", path: ".github/workflows/ci.yml", status: "completed", conclusion: "startup_failure"))
    expect(renamed.failingWorkflowBranches.map(\.workflow) == ["Build"], "a renamed workflow shows its new name")
    _ = try renamed.turns(event: "workflow_run", body: run(
        4, workflow: "Build", path: ".github/workflows/ci.yml", status: "in_progress"))
    _ = try renamed.turns(event: "workflow_job", body: job(40, run: 4, name: "j", status: "in_progress"))
    _ = try renamed.turns(event: "workflow_run", body: run(
        1, attempt: 2, workflow: "CI", path: ".github/workflows/ci.yml", status: "in_progress"))
    expect(renamed.failingWorkflowBranches.map(\.workflow) == ["Build"],
           "an earlier run, run again under the old name, does not rename the workflow back")
    _ = try renamed.turns(event: "workflow_run", body: run(
        5, workflow: "Make", path: ".github/workflows/ci.yml", status: "in_progress"))
    expect(renamed.active[40]?.workflowBranch.workflow == "Make"
           && Watch(restoring: renamed.state).failingWorkflowBranches.map(\.workflow) == ["Make"],
           "a rename reaches running jobs, and is saved")
    do {
        var body = try JSONSerialization.jsonObject(with: run(6, status: "in_progress")) as! [String: Any]
        var workflowRun = body["workflow_run"] as! [String: Any]
        workflowRun["path"] = nil
        body["workflow_run"] = workflowRun
        _ = try renamed.turns(event: "workflow_run", body: json(body))
        expect(false, "a run that does not say which file defines its workflow is refused")
    } catch {
        expect("\(error)".contains("path"), "the refusal names the missing path: \(error)")
    }

    // A run run again after its branch was forgotten.
    let rerun = Watch()
    _ = try rerun.turns(event: "delete", body: json([
        "ref": "f", "ref_type": "branch", "repository": repository,
    ]), at: ISO8601DateFormatter().date(from: "2026-06-01T00:00:00Z")!)
    func again(_ attempt: Int, started: String) -> Data {
        var body = try! JSONSerialization.jsonObject(with: run(
            50, attempt: attempt, branch: "f", status: "completed", conclusion: "startup_failure",
            created: "2026-05-01T00:00:00Z")) as! [String: Any]
        var run = body["workflow_run"] as! [String: Any]
        run["run_started_at"] = started
        body["workflow_run"] = run
        return json(body)
    }
    turns = try rerun.turns(event: "workflow_run", body: again(1, started: "2026-05-01T00:00:00Z"))
    expect(turns.isEmpty, "a run made before its branch was forgotten does not count")
    _ = try rerun.turns(event: "workflow_job", body: job(
        51, run: 50, attempt: 2, name: "test", status: "completed", conclusion: "failure"))
    turns = try rerun.turns(event: "workflow_run", body: again(2, started: "2026-06-02T00:00:00Z"))
    expect(turns.count == 1 && turns.first?.failures.map(\.name) == ["test"],
           "the same run run again after its branch was forgotten counts, with its jobs delivered first")
} catch {
    expect(false, "deliveries are read: \(error)")
}

// MARK: - The runners jobs run on

do {
    // Deliveries say what runner a job asks for, which one takes it, and how
    // it ended.
    let watch = Watch()
    func runnerJob(_ id: Int64, status: String, conclusion: String? = nil, runner: String? = nil,
                   group: String? = nil) -> Data {
        var body = try! JSONSerialization.jsonObject(with: job(id, run: 10, name: "test \(id)", status: status,
                                                               conclusion: conclusion,
                                                               created: "2026-01-01T00:00:00Z")) as! [String: Any]
        var job = body["workflow_job"] as! [String: Any]
        job["labels"] = ["self-hosted", "macOS", "cf-mac"]
        job["runner_name"] = runner ?? NSNull()
        job["runner_group_name"] = group ?? NSNull()
        job["started_at"] = "2026-01-01T00:05:00Z"
        body["workflow_job"] = job
        return json(body)
    }
    let iso = ISO8601DateFormatter()
    _ = try watch.handle(event: "workflow_run", body: run(10, status: "in_progress"))
    _ = try watch.handle(event: "workflow_job", body: runnerJob(1, status: "queued"))
    expect(watch.active[1]?.labels == ["self-hosted", "macOS", "cf-mac"] && watch.active[1]?.runner == nil
           && watch.active[1]?.since == iso.date(from: "2026-01-01T00:00:00Z") && watch.active[1]?.sha == "abc123",
           "a queued job has the labels it asks for, no runner, its run's commit, and has waited since it was made")
    _ = try watch.handle(event: "workflow_job", body: runnerJob(1, status: "in_progress", runner: "ci-mac-01-2",
                                                                group: "cf-mac"))
    expect(watch.active[1]?.runner == "ci-mac-01-2" && watch.active[1]?.runnerGroup == "cf-mac"
           && watch.active[1]?.since == iso.date(from: "2026-01-01T00:05:00Z"),
           "a running job has its runner, and has run since it started")
    for (id, conclusion, outcome) in [(Int64(2), "success", Outcome.passed), (3, "failure", .failed),
                                      (4, "cancelled", .neither)] {
        _ = try watch.handle(event: "workflow_job", body: runnerJob(id, status: "in_progress", runner: "r"))
        _ = try watch.handle(event: "workflow_job", body: runnerJob(id, status: "completed", conclusion: conclusion))
        expect(watch.active[id] == nil && watch.outcomes[id] == outcome,
               "a job that \(conclusion == "success" ? "passed" : conclusion) leaves, saying how it ended")
    }
    expect(watch.outcomes[1] == nil, "a job still running has no outcome")
    // A run that ends with jobs still queued or running ends them too, as
    // when it is cancelled before they start: all passed if it passed, and
    // otherwise neither passed nor failed, as far as is known.
    let ending = Watch()
    _ = try ending.handle(event: "workflow_run", body: run(10, status: "in_progress"))
    _ = try ending.handle(event: "workflow_job", body: runnerJob(7, status: "queued"))
    _ = try ending.handle(event: "workflow_job", body: runnerJob(8, status: "in_progress", runner: "r"))
    _ = try ending.handle(event: "workflow_job", body: runnerJob(9, status: "in_progress", runner: "r"))
    _ = try ending.handle(event: "workflow_job", body: runnerJob(9, status: "completed", conclusion: "failure"))
    _ = try ending.handle(event: "workflow_run", body: run(10, status: "completed", conclusion: "cancelled"))
    _ = try ending.handle(event: "workflow_job", body: runnerJob(7, status: "queued"))
    expect(ending.active.isEmpty && ending.outcomes[7] == .neither && ending.outcomes[8] == .neither
           && ending.outcomes[9] == .failed,
           "a run cancelled with jobs queued and running ends them as neither passing nor failing, and keeps "
           + "how those that had ended ended")
    let passingRun = Watch()
    _ = try passingRun.handle(event: "workflow_run", body: run(10, status: "in_progress"))
    _ = try passingRun.handle(event: "workflow_job", body: runnerJob(7, status: "in_progress", runner: "r"))
    _ = try passingRun.handle(event: "workflow_run", body: run(10, status: "completed", conclusion: "success"))
    expect(passingRun.active.isEmpty && passingRun.outcomes[7] == .passed,
           "a run that passed with a job still running ends it as passed")
    _ = try watch.handle(event: "workflow_job", body: runnerJob(5, status: "in_progress", runner: "", group: ""))
    expect(watch.active[5]?.runner == nil && watch.active[5]?.runnerGroup == nil,
           "a job just started, whose runner GitHub names as nothing yet, runs on no runner known")

    // How runners and jobs are pooled.
    func active(_ name: String, workflow: String = "CI", labels: [String], runner: String? = nil,
                group: String? = nil, since: Double = 0) -> ActiveJob {
        // A run of its own for each workflow.
        ActiveJob(workflowBranch: on(workflow), name: name,
                  order: RunOrder(run: Int64(stableHash(workflow) % 1_000_000), attempt: 1),
                  running: runner != nil || group != nil, runURL: "https://example.com/run",
                  url: "https://example.com/\(name)", labels: labels, runner: runner, runnerGroup: group,
                  since: Date(timeIntervalSince1970: since))
    }
    let mac = ["self-hosted", "macOS", "ARM64", "cf-mac"]
    let spot = ["self-hosted", "Linux", "X64", "cf-spot"]
    let runners = [
        Runner(owner: "octo", name: "ci-mac-01-2", labels: mac + ["ci-mac-01"], online: true),
        Runner(owner: "octo", name: "ci-mac-01-1", labels: mac + ["ci-mac-01"], online: true),
        Runner(owner: "octo", name: "ci-mac-01-builder", labels: ["self-hosted", "macOS", "ci-mac-01", "cf-mac-builder"], online: true),
        Runner(owner: "octo", name: "ci-mac-02-1", labels: mac + ["ci-mac-02"], online: false),
        Runner(owner: "octo", name: "spot-a", labels: spot, online: true),
        Runner(owner: "octo", name: "spot-b", labels: spot, online: false),
    ]
    let jobs: [Int64: ActiveJob] = [
        1: active("mac running", labels: mac, runner: "ci-mac-01-2", group: "cf-mac"),
        2: active("mac queued late", labels: mac, since: 20),
        3: active("mac queued early", labels: ["self-hosted", "cf-mac", "ci-mac-02"], since: 10),
        4: active("hosted running", workflow: "Lint", labels: ["ubuntu-latest"], runner: "GitHub Actions 7",
                  group: gitHubHostedGroup),
        5: active("hosted queued", workflow: "Lint", labels: ["ubuntu-latest"]),
        6: active("nowhere", labels: ["self-hosted", "macOS", "apple-container"]),
        7: active("spot running", labels: spot.map { $0.lowercased() }, runner: "spot-z", group: "cf-spot"),
        8: active("spot queued", labels: ["self-hosted", "cf-spot"]),
        9: active("any", labels: []),
    ]
    let board = RunnerBoard(jobs: jobs, runners: runners, outcomes: [3: .failed])
    expect(board.pools.map(\.title) == ["ci-mac", "apple-container", "cf-spot", "GitHub-hosted"],
           "machines come first, then other self-hosted runners, then GitHub's: \(board.pools.map(\.title))")
    let macs = board.pools[0]
    expect(macs.boxes.map(\.id) == ["ci-mac-01", "ci-mac-02"]
           && macs.boxes[0].slots?.map(\.label) == ["1", "2", "builder"]
           && macs.boxes[0].slots?[1].job?.id == 1 && macs.boxes[0].slots?[0].job == nil
           && macs.boxes[1].slots?.map(\.online) == [false],
           "a runner whose name starts with one of its labels has a slot on that machine, in order, with the job it "
           + "runs: \(macs.boxes)")
    expect(macs.detail == "self-hosted macOS ARM64 · cf-mac, cf-mac-builder" && macs.queue.map(\.id) == [3, 2],
           "a pool of machines says what runs on them, and its queue has every job a runner of it can take, the "
           + "oldest first: \(macs.detail) \(macs.queue.map(\.id))")
    expect(board.pools[1].queue.map(\.id) == [6] && board.pools[1].running == 0
           && board.pools[1].detail == "self-hosted macOS",
           "a job no runner can take waits in a pool of its own")
    let spots = board.pools[2]
    expect(spots.boxes.count == 1 && spots.boxes[0].slots == nil && spots.boxes[0].jobs.map(\.id) == [7]
           && spots.boxes[0].idle == 1 && spots.queue.map(\.id) == [8],
           "runners that are not on a machine are pooled by their labels, with the jobs on them, however their "
           + "labels are spelt, those online and idle, and those queued for them: \(spots)")
    let hosted = board.pools[3]
    expect(hosted.boxes.map(\.title) == ["any runner", "ubuntu-latest"] && hosted.boxes[1].jobs.map(\.id) == [4]
           && Set(hosted.queue.map(\.id)) == [5, 9],
           "GitHub's runners have a box for each set of labels asked for: \(hosted.boxes.map(\.title))")
    expect(board.running == 3 && board.queued == 6 && board.slots == 4 && board.idleSlots == 2
           && board.offlineSlots == 1 && board.tokens.count == jobs.count,
           "the board counts every job and runner once: \(board.running) \(board.queued) \(board.idleSlots)")
    expect(board.runs.map(\.jobs) == [7, 2],
           "the board knows each run on it, those with the most jobs first, which choose colours first")
    var pr1 = active("test", labels: ["ubuntu-latest"], group: gitHubHostedGroup)
    pr1.workflowBranch = on("CI", "one")
    var pr2 = pr1
    pr2.workflowBranch = on("CI", "two")
    pr2.order = RunOrder(run: 99, attempt: 1)
    let prs = RunnerBoard(jobs: [1: pr1, 2: pr2, 3: pr2], runners: [], outcomes: [:], pullRequests: {
        $0.branch == "one" ? PullRequestInfo(number: 12, author: "ada") : nil
    })
    var firstRun = active("test", labels: ["ubuntu-latest"], group: gitHubHostedGroup)
    firstRun.sha = "1a2b3c4d5e"
    var secondRun = firstRun
    secondRun.order = RunOrder(run: 98, attempt: 1)
    secondRun.sha = "9f8e7d6c5b"
    let twoRuns = RunnerBoard(jobs: [1: firstRun, 2: secondRun, 3: secondRun], runners: [], outcomes: [:])
    expect(Set(twoRuns.tokens.map(\.runID)).count == 2
           && Set(twoRuns.tokens.map { RunnerBoardView.describe($0, at: Date()).split(separator: "\n")[0] })
               == ["repo · CI on main @ 9f8e7d6", "repo · CI on main @ 1a2b3c4"],
           "two runs of a workflow on a branch at once are two colours, named apart by their commits")
    expect(Set(prs.tokens.map(\.workflowTitle)) == ["repo · CI on two", "repo PR #12 · CI"]
           && Set(prs.tokens.map(\.runID)).count == 2,
           "the same workflow on two branches is two runs, named by pull request or branch")
    expect(RunnerBoard.title(["ci-mac-01", "ci-mac-02"]) == "ci-mac" && RunnerBoard.title(["solo"]) == "solo"
           && RunnerBoard.title(["alpha", "beta"]) == "alpha, beta" && RunnerBoard.title(["m1", "m2"]) == "m",
           "machines are named by what their names start with, without what tells them apart")
    let unlisted = RunnerBoard(jobs: [1: jobs[1]!, 2: jobs[2]!], runners: [], outcomes: [:])
    expect(unlisted.pools.map(\.title) == ["cf-mac"] && unlisted.pools[0].boxes[0].jobs.map(\.id) == [1]
           && unlisted.pools[0].queue.map(\.id) == [2],
           "without a list of runners, those jobs run on are learnt from the jobs: \(unlisted.pools)")
    let autoscaled = RunnerBoard(jobs: [:], runners: [
        Runner(owner: "octo", name: "arc-linux-x7k2p-runner-abcde", labels: ["arc-linux", "big"], online: true),
        Runner(owner: "octo", name: "cf-spot-x7k2", labels: ["self-hosted", "cf-spot"], online: true),
        Runner(owner: "octo", name: "alpha-1", labels: ["self-hosted", "alpha", "ios"], online: true),
        Runner(owner: "octo", name: "beta-1", labels: ["self-hosted", "beta", "android"], online: true),
    ], outcomes: [:])
    expect(autoscaled.pools.map(\.title) == ["alpha", "beta", "arc-linux, big", "cf-spot"]
           && autoscaled.pools.map { $0.boxes[0].slots == nil } == [false, false, true, true]
           && autoscaled.pools[3].boxes[0].idle == 1,
           "a runner named with more than a word after its label, or with no other label to say what it is for, "
           + "is not on a machine, and machines that share no label are pools of their own: "
           + "\(autoscaled.pools.map(\.title))")
    let doubled = RunnerBoard(jobs: [
        1: active("finished, not yet said", labels: mac, runner: "ci-mac-01-1", group: "cf-mac"),
        2: active("next", labels: mac, runner: "ci-mac-01-1", group: "cf-mac"),
    ], runners: runners, outcomes: [:])
    expect(doubled.tokens.map(\.id).sorted() == [1, 2] && doubled.running == 2
           && doubled.pools[0].boxes[0].jobs.map(\.id) == [2]
           && BoardLayout(board: doubled, size: CGSize(width: 780, height: 588)).tokens.count == 2,
           "two jobs that say they run on one runner, as deliveries out of order can, are both shown")
    let spelt = RunnerBoard(jobs: [
        1: active("upper", labels: ["self-hosted", "CF-Spot"], runner: "spot-z", group: "cf-spot"),
        2: active("bare", labels: []),
    ], runners: runners + [Runner(owner: "octo", name: "vm-abc-def", labels: ["self-hosted", "Linux"], online: true)],
       outcomes: [:])
    expect(spelt.pools.filter { $0.title.lowercased() == "cf-spot" }.count == 1
           && spelt.pools.first { $0.kind == .gitHubHosted }?.queue.map(\.id) == [2],
           "labels are the same however they are spelt, and a job that asks for none waits for GitHub's runners: "
           + "\(spelt.pools.map(\.title))")
    let twoOwners = RunnerBoard(jobs: [1: active("mac running", labels: mac, runner: "ci-mac-01-2", group: "cf-mac")],
                                runners: runners + [Runner(owner: "other", name: "ci-mac-01-2",
                                                           labels: mac + ["ci-mac-01"], online: true)],
                                outcomes: [:])
    let twoSlots = twoOwners.pools[0].boxes[0].slots?.filter { $0.name == "ci-mac-01-2" }
    expect(twoSlots?.map(\.id) == ["octo/ci-mac-01-2", "other/ci-mac-01-2"]
           && twoSlots?.map { $0.job?.id } == [1, nil],
           "runners of the same name of two owners are two runners, and a job runs on its own owner's")
    let linux = ["self-hosted", "Linux"]
    let choices = [Runner(owner: "octo", name: "m-1", labels: linux + ["m"], online: false),
                   Runner(owner: "octo", name: "x-1", labels: linux + ["x"], online: true)]
    let waiting: [Int64: ActiveJob] = [1: active("any linux", labels: linux)]
    expect(RunnerBoard(jobs: waiting, runners: choices, outcomes: [:]).pools.first { !$0.queue.isEmpty }?.title == "x",
           "a queued job waits for a runner that is online rather than one that is not")
    var allOffline = choices
    allOffline[1].online = false
    expect(RunnerBoard(jobs: waiting, runners: allOffline, outcomes: [:]).pools.first { !$0.queue.isEmpty }?.title
           == "m", "with every runner that could take it offline, a queued job waits for the first of them")
    let unplaced = RunnerBoard(jobs: [
        1: active("just started", labels: ["ubuntu-24.04"], runner: nil, group: nil, since: 1),
        2: active("named", labels: ["ubuntu-24.04"], runner: "GitHub Actions 1001438681", group: nil, since: 2),
        3: active("grouped", labels: ["ubuntu-24.04"], runner: "GitHub Actions 7", group: gitHubHostedGroup, since: 3),
    ].mapValues { job in
        var job = job
        job.running = true
        return job
    }, runners: [], outcomes: [:])
    expect(unplaced.pools.map(\.title) == ["GitHub-hosted"] && unplaced.pools[0].boxes.map(\.title) == ["ubuntu-24.04"]
           && unplaced.pools[0].boxes[0].jobs.count == 3,
           "jobs on GitHub's runners are together, whether GitHub has said yet where they run, names their runner "
           + "after its own, or gives its group: \(unplaced.pools.map(\.title))")
    let larger = RunnerBoard(jobs: [
        1: active("bench", labels: ["ubuntu-24.04-32-core"], runner: "ubuntu-24.04-32-core_7f3a", group: "labs"),
        2: active("bench next", labels: ["ubuntu-24.04-32-core"]),
        3: active("lint", labels: ["ubuntu-latest"]),
    ], runners: [], outcomes: [:])
    let largeRunners = [LargerRunner(owner: "octo", name: "ubuntu-24.04-32-core", maximum: 3, cores: 32, memory: 128),
                        LargerRunner(owner: "octo", name: "ubuntu-24.04-32-core-internal", maximum: 2, cores: 32,
                                     memory: 128)]
    var large: [Int64: ActiveJob] = [
        1: active("bench 1", labels: [], runner: "ubuntu-24.04-32-core-1001427100", group: "labs", since: 1),
        2: active("bench 2", labels: [], runner: "ubuntu-24.04-32-core-1001427101", group: "labs", since: 2),
        3: active("internal", labels: [], runner: "Ubuntu-24.04-32-core-internal-5", group: "internal", since: 3),
        4: active("asks", labels: ["ubuntu-24.04-32-core"], since: 4),
    ]
    let largeBoard = RunnerBoard(jobs: large, runners: [], larger: largeRunners, outcomes: [:])
    expect(largeBoard.pools.map(\.title) == ["ubuntu-24.04-32-core", "ubuntu-24.04-32-core-internal"]
           && largeBoard.pools[0].boxes[0].capacity == 3 && largeBoard.pools[0].boxes[0].jobs.map(\.id) == [1, 2]
           && largeBoard.pools[0].queue.map(\.id) == [4] && largeBoard.pools[1].boxes[0].jobs.map(\.id) == [3]
           && largeBoard.pools[0].detail == "GitHub-hosted · 32 cores, 128 GB",
           "a job on, or asking for, a larger runner is in its pool, the runner with the longest name it matches: "
           + "\(largeBoard.pools.map(\.title))")
    var steadiness = RunnerBoard.Steadiness()
    let seatedBoard = largeBoard.steadied(&steadiness)
    expect(seatedBoard.pools[0].boxes[0].slots?.map { $0.job?.id } == [1, 2, nil]
           && BoardLayout(board: seatedBoard, size: CGSize(width: 780, height: 588)).texts
               .contains { $0.string == "1 queued · 2 of 3 running" },
           "a larger runner has a place for each job it can run at once, and says how many it runs")
    expect(!BoardLayout(board: seatedBoard, size: CGSize(width: 780, height: 588)).texts
               .contains { $0.string.contains("runners idle") },
           "places for jobs on runners known only by their number are not counted as runners")
    let huge = RunnerBoard(jobs: [1: large[1]!], runners: [],
                           larger: [LargerRunner(owner: "octo", name: "ubuntu-24.04-32-core", maximum: 500, cores: nil,
                                                 memory: nil),
                                    LargerRunner(owner: "octo", name: "unbounded", maximum: nil, cores: nil,
                                                 memory: nil)],
                           outcomes: [:]).steadied(&steadiness)
    expect(huge.pools.first { $0.title == "ubuntu-24.04-32-core" }?.boxes[0].slots?.map { $0.job?.id } == [1]
           && BoardLayout(board: huge, size: CGSize(width: 780, height: 588)).texts
               .contains { $0.string == "0 queued · 1 of 500 running" },
           "a larger runner that can run more jobs at once than there is room for grows with its jobs")
    let gpu = RunnerBoard(jobs: [1: active("render", labels: ["self-hosted", "gpu"], runner: "gpu-1", group: "Default")],
                          runners: [Runner(owner: "octo", name: "gpu-1", labels: ["self-hosted", "gpu", "cuda"],
                                           online: true)],
                          larger: [LargerRunner(owner: "octo", name: "gpu", maximum: 4, cores: nil, memory: nil)],
                          outcomes: [:])
    expect(gpu.pools.map(\.title) == ["gpu"] && gpu.pools[0].kind == .machines
           && gpu.pools[0].boxes[0].slots?.first?.job?.id == 1,
           "a job on a listed self-hosted runner is on it, whatever larger runner its runner's name starts with")
    steadiness = RunnerBoard.Steadiness()
    _ = largeBoard.steadied(&steadiness)
    large[1] = nil
    large[5] = active("bench 3", labels: [], runner: "ubuntu-24.04-32-core-1001427102", group: "labs", since: 5)
    large[6] = active("bench 4", labels: [], runner: "ubuntu-24.04-32-core-1001427103", group: "labs", since: 6)
    large[7] = active("bench 5", labels: [], runner: "ubuntu-24.04-32-core-1001427104", group: "labs", since: 7)
    let reseated = RunnerBoard(jobs: large, runners: [], larger: largeRunners, outcomes: [:]).steadied(&steadiness)
    expect(reseated.pools[0].boxes[0].slots?.map { $0.job?.id } == [5, 2, 6]
           && reseated.pools[0].boxes[0].jobs.map(\.id) == [7],
           "a job keeps its place while others come and go, a new one takes the first free place, and one that "
           + "does not fit is beside them: \(String(describing: reseated.pools[0].boxes[0].slots?.map { $0.job?.id }))")
    // A job's place in its queue says nothing of its place in its box.
    var memory = RunnerBoard.Steadiness()
    var moving: [Int64: ActiveJob] = [
        1: active("a", labels: ["ubuntu-latest"], runner: "GitHub Actions 1", group: gitHubHostedGroup, since: 1),
        2: active("b", labels: ["ubuntu-latest"], runner: "GitHub Actions 2", group: gitHubHostedGroup, since: 2),
        3: active("c", labels: ["ubuntu-latest"], runner: "GitHub Actions 3", group: gitHubHostedGroup, since: 3),
        4: active("d", labels: ["ubuntu-latest"], since: 4),
        5: active("e", labels: ["ubuntu-latest"], since: 5),
        6: active("f", labels: ["ubuntu-latest"], since: 6),
    ]
    _ = RunnerBoard(jobs: moving, runners: [], outcomes: [:]).steadied(&memory)
    moving[2] = nil
    moving[6] = active("f", labels: ["ubuntu-latest"], runner: "GitHub Actions 6", group: gitHubHostedGroup, since: 7)
    let moved = RunnerBoard(jobs: moving, runners: [], outcomes: [:]).steadied(&memory)
    expect(moved.pools[0].boxes[0].slots?.map { $0.job?.id } == [1, 6, 3]
           && moved.pools[0].queue.map(\.id) == [4, 5],
           "a job that starts takes the first empty place in its box, whatever its place in the queue")
    let grouped = RunnerBoard(jobs: [
        1: active("bench", labels: [], runner: "ubuntu-24.04-32-core-1001427100", group: "labs"),
    ], runners: [], outcomes: [:])
    expect(grouped.pools.map(\.title) == ["runner group labs"] && grouped.pools[0].boxes[0].jobs.map(\.id) == [1],
           "a job that asked for a runner group rather than labels is pooled by the group")
    expect(larger.pools.map(\.title) == ["ubuntu-24.04-32-core", "GitHub-hosted"]
           && larger.pools[0].boxes[0].jobs.map(\.id) == [1] && larger.pools[0].queue.map(\.id) == [2]
           && larger.pools[1].queue.map(\.id) == [3],
           "a job on a runner outside GitHub's own group is pooled by its labels, with the jobs queued for them, "
           + "and only jobs with nowhere else to go wait for GitHub's runners: \(larger.pools.map(\.title))")

    // The circles are as big as lets everything fit, and nothing overlaps.
    let size = CGSize(width: 780, height: 588)
    let roomy = BoardLayout(board: board, size: size)
    expect(roomy.size == BoardLayout.sizes[0] && roomy.tokens.count == board.running
           && roomy.heaps.reduce(0, { $0 + $1.jobs.count }) == board.queued
           && roomy.slots.count == 4 && roomy.pools.count == 4,
           "a board with room to spare has the biggest circles, a place for each running job, a heap for each "
           + "queue, and a slot for each runner")
    var crowdedJobs: [Int64: ActiveJob] = [:]
    for id in 1...900 {
        crowdedJobs[Int64(id)] = id <= 700 ? active("r\(id)", labels: spot, runner: "spot-\(id)", group: "cf-spot")
            : active("q\(id)", labels: id % 2 == 0 ? spot : ["ubuntu-latest"])
    }
    let crowded = BoardLayout(board: RunnerBoard(jobs: crowdedJobs, runners: runners, outcomes: [:]), size: size)
    let frames = Array(crowded.tokens.values)
    let bounds = CGRect(origin: .zero, size: size)
    expect(crowded.size < roomy.size && (1...700).allSatisfy { crowded.tokens[Int64($0)] != nil }
           && frames.allSatisfy { bounds.contains($0) },
           "a crowded board has smaller circles, every running job's on it: \(crowded.size)")
    expect(!frames.enumerated().contains { index, frame in
               frames[(index + 1)...].contains { $0.intersects(frame) }
           },
           "no two circles overlap")
    var manyPools: [Int64: ActiveJob] = [:]
    for id in 1...30 {
        manyPools[Int64(id)] = active("j\(id)", labels: ["self-hosted", "pool-\(id)"], since: Double(id))
    }
    let overflowing = BoardLayout(board: RunnerBoard(jobs: manyPools, runners: [], outcomes: [:]),
                                  size: CGSize(width: 780, height: 400))
    let note = overflowing.texts.first { $0.string.hasSuffix("not shown") }
    expect(note.map { $0.string == "\(30 - overflowing.pools.count) more pools not shown" } == true
           && overflowing.pools.count < 30
           && overflowing.tokens.values.allSatisfy { $0.maxY <= note!.frame.minY },
           "pools that do not fit even with the smallest circles are counted instead: \(String(describing: note))")
    var longQueue: [Int64: ActiveJob] = [1: active("lint", labels: ["ubuntu-latest"], group: gitHubHostedGroup)]
    for id in 2...1500 {
        longQueue[Int64(id)] = active("matrix \(id)", labels: mac, since: Double(id))
    }
    let long = BoardLayout(board: RunnerBoard(jobs: longQueue, runners: runners, outcomes: [:]), size: size)
    let longHeap = long.heaps.first { !$0.jobs.isEmpty }
    expect(long.pools.count == 3 && long.tokens[1] != nil && longHeap?.jobs.count == 1499
           && long.texts.contains { $0.string == "1499 queued · 0 running" }
           && longHeap.map { heap in long.boxes.contains { $0.maxY >= heap.frame.maxY } } == true,
           "one long queue is a heap no taller than its runners, crowding out nothing")
    let queuedHere = BoardLayout(board: board, size: size)
    let heading = queuedHere.texts.first { $0.string == "2 queued · 1 running" }
    let title = queuedHere.texts.first { $0.string == "ci-mac" }
    let firstHeap = queuedHere.heaps.first?.frame
    expect(heading != nil && title != nil && firstHeap != nil && title!.frame.minX <= firstHeap!.minX
           && firstHeap!.minY == queuedHere.pools.first?.minY && firstHeap!.minY < title!.frame.minY
           && heading!.frame.maxY > firstHeap!.minY,
           "a pool's name is at its top left, over its queue, which reaches up to the pool's top, and how many "
           + "wait and run at its top right")
    // Under a title bar, the board's heading goes in it, past its close
    // button, and the pools start just below it.
    let underTitleBar = BoardLayout(board: board, size: size, titleBar: TitleBar(height: 24, middle: 13))
    let summaryHere = queuedHere.texts.first { $0.style == .heading }
    let summaryThere = underTitleBar.texts.first { $0.style == .heading }
    expect(summaryHere.map { $0.frame.minX == BoardLayout.inset && $0.frame.minY > 10 } == true
           && summaryThere.map { abs($0.frame.midY - 13) < 0.01 && $0.frame.minX >= BoardLayout.besideClose } == true
           && (underTitleBar.pools.first?.minY ?? 0) >= 24
           && (underTitleBar.pools.first?.minY ?? 0) < (queuedHere.pools.first?.minY ?? 0),
           "under a title bar, the board's heading goes in it, past and level with its close button, and its pools "
           + "move up to it")
    expect(BoardLayout(board: RunnerBoard(), size: size).texts.map(\.string).contains(
               "Nothing is queued or running, and no runners are known."),
           "an empty board says why it is empty")
    expect(roomy.token(at: CGPoint(x: roomy.tokens[1]!.midX, y: roomy.tokens[1]!.midY)) == 1
           && roomy.slot(at: CGPoint(x: roomy.slots[0].frame.midX, y: roomy.slots[0].frame.midY))?.id
               == "octo/ci-mac-01-1"
           && roomy.token(at: .zero) == nil,
           "what is pointed at is found")

    // Thousands of balls heap as a few do: in their space, bearing their own
    // weight without being squeezed into each other, and coming to rest.
    let crowdSize = CGSize(width: 173, height: 68)
    let crowdD = Heap.diameter(for: 3000, in: crowdSize, largest: 16)
    let crowdAcross = Int(crowdSize.width / (crowdD * 1.1))
    let crowd = (0..<3000).map { (i: Int) -> Heap.Ball in
        let x: CGFloat = crowdD / 2 + CGFloat(i % crowdAcross) * crowdD * 1.1
        let y: CGFloat = -crowdD - CGFloat(i / crowdAcross) * crowdD * 1.2
        return Heap.Ball(id: Int64(i), position: CGPoint(x: x, y: y))
    }
    let crowdPaths = Heap.paths(of: crowd, in: crowdSize, diameter: crowdD)
    let crowdRests = crowdPaths.values.map { $0[$0.count - 1] }
    let crowdInside = crowdRests.allSatisfy {
        $0.x >= crowdD / 2 - 0.01 && $0.x <= crowdSize.width - crowdD / 2 + 0.01 && $0.y >= crowdD / 2 - 0.5
            && $0.y <= crowdSize.height - crowdD / 2 + 0.01
    }
    var crowdGrid: [Int: [CGPoint]] = [:]
    for rest in crowdRests {
        crowdGrid[Int(rest.x / crowdD) * 1000 + Int(rest.y / crowdD), default: []].append(rest)
    }
    let crowdSquashed = crowdRests.contains { rest in
        (-1...1).contains { dx in
            (-1...1).contains { dy in
                (crowdGrid[(Int(rest.x / crowdD) + dx) * 1000 + Int(rest.y / crowdD) + dy] ?? []).contains {
                    $0 != rest && hypot($0.x - rest.x, $0.y - rest.y) < crowdD * 0.85
                }
            }
        }
    }
    let crowdLongest = crowdPaths.values.map(\.count).max() ?? 0
    expect(crowdRests.count == 3000 && crowdD < 2 && crowdInside && !crowdSquashed
           && crowdLongest < Int(Heap.longest * Heap.rate),
           "three thousand balls, smaller than two points, heap in their space, none squeezed into another, and "
           + "come to rest: \(crowdD), \(crowdLongest) samples")

    // Balls that grow, as when most jobs leave a queue at once, part from
    // each other without being flung out of their space.
    let growSize = CGSize(width: 173, height: 68)
    let smallBalls = Heap.diameter(for: 200, in: growSize, largest: 16)
    let pouredBalls = (0..<200).map { (i: Int) -> Heap.Ball in
        let x: CGFloat = smallBalls / 2 + CGFloat((i * 37) % 160)
        let y: CGFloat = -smallBalls * 1.2 * CGFloat(i / 6 + 1)
        return Heap.Ball(id: Int64(i), position: CGPoint(x: x, y: y))
    }
    let restedBalls = Heap.paths(of: pouredBalls, in: growSize, diameter: smallBalls)
        .map { Heap.Ball(id: $0.key, position: $0.value[$0.value.count - 1]) }.sorted { $0.id < $1.id }
    let grownSize = Heap.diameter(for: 60, in: growSize, largest: 16)
    // Whichever sixty stay: those at the bottom, those at the top, or some of
    // each.
    let highest = [Array(restedBalls.prefix(60)), Array(restedBalls.suffix(60)),
                   restedBalls.enumerated().filter { $0.offset % 3 == 0 }.prefix(60).map(\.element)].map { stay in
        Heap.paths(of: stay, in: growSize, diameter: grownSize, grownFrom: smallBalls).values
            .flatMap { $0 }.map { (point: CGPoint) -> CGFloat in point.y }.min() ?? 0
    }
    expect(highest.allSatisfy { $0 > 0 } && grownSize > smallBalls,
           "balls that grow into each other part without flying up out of their heap: \(highest)")

    expect(runTitle(repository: "loom", workflow: "Tests", pullRequest: 1234) == "loom PR #1234 · Tests"
           && runTitle(repository: "labs", workflow: "CI", pullRequest: nil) == "labs · CI",
           "a run for a pull request is named by its repository, then the pull request, then its workflow")

    // Balls moving on their own are moved by forces alone: none changes speed
    // all at once, so each starts off moving as it was.
    func smooth(_ path: [CGPoint], from state: Flight.State) -> Bool {
        path.count > 1 && abs(path[1].x - path[0].x - state.velocity.dx / Flight.rate) < 2
            && abs(path[1].y - path[0].y - state.velocity.dy / Flight.rate) < 2
    }
    let falling = Flight.State(position: CGPoint(x: 100, y: 100), velocity: CGVector(dx: 0, dy: 400))
    let steered = Flight.steer(from: falling, to: CGPoint(x: 400, y: 40), supported: false)
    let resting = Flight.steer(from: Flight.State(position: CGPoint(x: 100, y: 100)), to: CGPoint(x: 400, y: 40),
                               supported: true)
    expect(smooth(steered, from: falling) && (steered.map(\.y).max() ?? 0) > 110 && steered.last == CGPoint(x: 400, y: 40)
           && steered.allSatisfy { $0.x <= 400.5 } && steered.count < Int(1.5 * Flight.rate)
           && (resting.map(\.y).max() ?? 0) <= 100.5 && resting.last == CGPoint(x: 400, y: 40),
           "a falling ball drawn to a runner goes on falling a moment, then curves round to it and stops there, "
           + "and one at rest goes straight up to it")

    // A job that fails pauses, slowing to a stop, then is thrown against the
    // right side, bounces off it onto the line along the bottom, bounces
    // there, slides left at a steady speed, and falls past the line's end.
    let floorBoard = Flight.Board(width: 780, floor: 500, line: 18...762)
    let pause = 0.45, paused = Int(pause * Flight.rate)
    let thrown = Flight.State(position: CGPoint(x: 100, y: 300), velocity: CGVector(dx: 120, dy: -60))
    let tumbling = Flight.leaving(from: thrown, diameter: 12, outcome: .failed, pause: pause, board: floorBoard)
    let held = tumbling[paused]
    let atWall = tumbling.indices.max { tumbling[$0].x < tumbling[$1].x } ?? tumbling.count
    // Where a path comes down on the line and goes back up again.
    func bounces(_ path: [CGPoint]) -> [Int] {
        path.indices.dropFirst().dropLast().filter { k in
            path[k].y > 490 && path[k].x >= 18 && path[k].y >= path[k - 1].y && path[k].y > path[k + 1].y
        }
    }
    let onLine = bounces(tumbling)
    let landed = onLine.first ?? 0
    let overLine = tumbling.indices.filter { tumbling[$0].x >= 18 }
    let across = (landed..<(overLine.last ?? landed)).map { tumbling[$0 + 1].x - tumbling[$0].x }
    let stopped = hypot(held.x - tumbling[paused - 1].x, held.y - tumbling[paused - 1].y) < 0.1
        && hypot(held.x - 100, held.y - 300) < 20
    let bounced = tumbling[atWall].x > 765 && atWall < landed && onLine.count >= 2
        && tumbling.allSatisfy { (point: CGPoint) -> Bool in point.x <= 774 && (point.y <= 494 || point.x < 18) }
    let steady = Set(across.map { (step: CGFloat) -> CGFloat in (step * 100).rounded() }).count == 1
        && (across.first ?? 0) < 0 && (tumbling.last?.x ?? 0) < 18
    expect(smooth(tumbling, from: thrown) && stopped && bounced && steady,
           "a failed job's ball slows to a stop, is thrown up and right, bounces off the right side before it "
           + "lands, and bounces along the line, going left at a steady speed, off its end: \(onLine.count) bounces")
    let later = Flight.leaving(from: Flight.State(position: CGPoint(x: 600, y: 300), velocity: CGVector(dx: 120, dy: -60)),
                               diameter: 12, outcome: .failed, pause: pause, board: floorBoard)
    expect(later.contains { $0.x < 18 && $0.y > 494 },
           "past the line's left end, nothing holds the ball up, and it falls")
    let nearWall = Flight.leaving(from: Flight.State(position: CGPoint(x: 740, y: 480)), diameter: 12, outcome: .failed,
                                  pause: pause, board: floorBoard)
    let nearest = nearWall.indices.max { nearWall[$0].x < nearWall[$1].x } ?? nearWall.count
    expect(nearWall[nearest].x > 765 && nearest < (bounces(nearWall).first ?? 0) && (nearWall.last?.x ?? 0) < 18,
           "even from near the right side and the bottom, it reaches the side before it lands")
    let wide = Flight.leaving(from: Flight.State(position: CGPoint(x: 1560, y: 480)), diameter: 12, outcome: .failed,
                              pause: pause, board: Flight.Board(width: 1600, floor: 500, line: 18...1582))
    expect((wide.last?.x ?? 1600) < 18, "however wide the board, it goes all the way off before its path ends")
    let passing = Flight.leaving(from: thrown, diameter: 12, outcome: .passed, pause: pause, board: floorBoard)
    let leftAt = passing.last ?? .zero
    expect(smooth(passing, from: thrown) && leftAt.x > 792 && abs(leftAt.y - held.y - 60) < 25
           && (passing.map { (point: CGPoint) -> CGFloat in point.y }.min() ?? 0) < held.y - 10,
           "a passed job's ball is thrown up, over and off the right side, a little lower than it was: \(leftAt)")
    let smoke = Flight.leaving(from: thrown, diameter: 12, outcome: .neither, pause: pause, board: floorBoard)
    let rising = Array(smoke[paused...])
    let climbs = zip(rising, rising.dropFirst()).map { $0.y - $1.y }
    expect(smooth(smoke, from: thrown) && (smoke.last?.y ?? 0) < -24
           && rising.allSatisfy { abs($0.x - (rising.first?.x ?? 0)) < 3 }
           && (climbs.dropFirst().first ?? 0) < (climbs.dropFirst(20).first ?? 0),
           "a cancelled job's ball rises straight up, ever faster, off the top")

    // However many balls, and from whatever size they shrink, they are never
    // smaller than a point; and two balls in one spot part, however small.
    expect(Heap.diameter(for: 1_000_000, in: CGSize(width: 50, height: 20), largest: 13.7) == 1
           && Heap.diameter(for: 3, in: CGSize(width: 50, height: 20), largest: 13.7) == 13.7
           && Heap.diameter(for: 3, in: CGSize(width: 50, height: 20), largest: 0) == 1,
           "balls shrink to fit, to a point across and no smaller")
    let together = Heap.paths(of: [Heap.Ball(id: 1, position: CGPoint(x: 10, y: 19.5)),
                                   Heap.Ball(id: 2, position: CGPoint(x: 10, y: 19.5))],
                              in: CGSize(width: 50, height: 20), diameter: 1)
    let apart = together[1].flatMap { a in together[2].map { b in hypot(a[a.count - 1].x - b[b.count - 1].x,
                                                                         a[a.count - 1].y - b[b.count - 1].y) } } ?? 0
    expect(apart > 0.85, "two balls a point across in one spot part: \(apart)")

    // Balls dropped far off to the left land on the slope that leads up and
    // away from the box's top left corner, and roll down it into the box,
    // whose right side goes on above it; one dropped over the box never
    // goes left of it.
    let box = CGSize(width: 173, height: 68)
    let offLeft = (0..<6).map { (i: Int) -> Heap.Ball in
        Heap.Ball(id: Int64(i), position: CGPoint(x: -346 + CGFloat(i) * 12, y: -600))
    }
    let rolled = Heap.paths(of: offLeft, in: box, diameter: 10)
    let onSlope = rolled.values.allSatisfy { path in
        path.contains { $0.x < -20 && abs(($0.x - $0.y) / 2.squareRoot() - 5) < 0.5 }
    }
    let landedIn = rolled.values.allSatisfy { path in
        let rest = path[path.count - 1]
        return rest.x >= 5 - 0.01 && rest.x <= 168.01 && abs(rest.y - 63) < 12
    }
    let overBox = Heap.paths(of: [Heap.Ball(id: 9, position: CGPoint(x: 160, y: -200), velocity: CGVector(dx: 400, dy: 0))],
                             in: box, diameter: 10)[9] ?? []
    let wallward = Heap.paths(of: [Heap.Ball(id: 3, position: CGPoint(x: 16, y: 21), velocity: CGVector(dx: -540, dy: 0))],
                              in: box, diameter: 4)[3] ?? []
    expect(wallward.allSatisfy { $0.y < 0 || $0.x >= 2 - 0.01 } && (wallward.last?.x ?? 0) >= 2 - 0.01,
           "a ball thrown hard at the box's left side stays in the box")
    expect(onSlope && landedIn && overBox.allSatisfy { $0.x >= 0 && $0.x <= 168.01 },
           "balls dropped far to the left roll down the slope into the box, and one over the box stays over it")

    // A heap that, poured, lies less flat than it might, with balls left
    // sticking out of the top, is made of smaller balls, that all fit.
    let shallow = CGSize(width: 140, height: 18)
    let lumpy = (0..<10).map { (i: Int) -> Heap.Ball in
        let x: CGFloat = 7 + CGFloat(i % 3) * 15.4, dx: CGFloat = 90 + 14 * CGFloat(i)
        return Heap.Ball(id: Int64(i), position: CGPoint(x: x, y: -14 - CGFloat(i / 3) * 18),
                         velocity: CGVector(dx: dx, dy: 150))
    }
    func rests(_ paths: [Int64: [CGPoint]]) -> [CGFloat] { paths.values.map { $0[$0.count - 1].y } }
    let tooBig = rests(Heap.paths(of: lumpy, in: shallow, diameter: 14)).min() ?? 0
    let fitted = Heap.fit(lumpy, in: shallow, diameter: 14)
    let fittedTop = (rests(fitted.paths).min() ?? 0) - fitted.diameter / 2
    let flat = Heap.fit(Array(lumpy.prefix(3)), in: shallow, diameter: 14)
    expect(tooBig < 6.5 && fitted.diameter < 14 && fittedTop >= -0.5 && flat.diameter == 14,
           "a poured heap with balls sticking out of the top is made of smaller balls, which fit, and one that "
           + "fits keeps its size: \(fitted.diameter)")

    // Runs on the board at once have colours of their own, which they keep.
    var palette = RunPalette()
    let many = (0..<runColours.count).map { RunnerBoard.Run(id: "\($0 + 1000)", jobs: 1) }
    palette.update(for: many)
    expect(Set(palette.colours.values).count == runColours.count,
           "as many runs as there are colours each have one of their own, whatever their hashes")
    let kept = palette.colours[many[3].id]
    palette.update(for: Array(many[3...]) + [RunnerBoard.Run(id: "new", jobs: 9)])
    expect(palette.colours[many[3].id] == kept && palette.colours[many[0].id] == nil
           && Set(palette.colours.values).count == palette.colours.count,
           "a run keeps its colour while it is on the board, and one that comes on takes a free one")

    // The view puts a layer for each job, moves a job that starts running
    // along an arc, and takes off those that leave.
    _ = NSApplication.shared
    let view = RunnerBoardView()
    view.frame = NSRect(origin: .zero, size: size)
    view.show(board, animated: false)
    expect(view.tokenCount == jobs.count && view.tokenLayer(2)?.animationKeys() ?? [] == [],
           "the board has a layer for each job, still when not animated")
    var started = jobs
    started[2] = active("mac queued late", labels: mac, runner: "ci-mac-01-1", group: "cf-mac", since: 30)
    started[1] = nil
    view.show(RunnerBoard(jobs: started, runners: runners, outcomes: [1: .passed]), animated: true)
    let reduced = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    expect(view.tokenCount == jobs.count - 1, "a job that finishes leaves the board")
    expect(reduced || view.tokenLayer(2)?.animation(forKey: "position") is CAKeyframeAnimation,
           "a job that starts running moves from its queue to its runner")
    expect(view.tokenLayer(3)?.backgroundColor != nil && view.tokenLayer(3)?.borderWidth == 0,
           "a queued job is a circle, filled as a running one is")
    view.show(RunnerBoard(jobs: started, runners: runners, outcomes: [1: .passed]), animated: true)
    expect(reduced || view.tokenLayer(2)?.animation(forKey: "position") is CAKeyframeAnimation,
           "a job on its way to its runner keeps going when the board is shown again")
    var shifted = RunnerBoard(jobs: started, runners: runners, outcomes: [1: .passed])
    shifted.problems = ["Not connected to the relay: refused"]
    view.show(shifted, animated: true)
    let slide = (view.tokenLayer(2)?.animation(forKey: "position") as? CAKeyframeAnimation)?.values?
        .compactMap { $0 as? CGPoint } ?? []
    expect(reduced || (slide.count > 2 && slide.last == view.tokenLayer(2)?.position && slide.first != slide.last),
           "a job whose place moves goes there from where it is")
    view.show(RunnerBoard(jobs: started, runners: runners, outcomes: [1: .passed]), animated: false)

    // A job that ends frees its place at once, and flies off the board in
    // its run's colour, with a mark of how it ended.
    let spotJobs: [Int64: ActiveJob] = [
        41: active("a", labels: spot, runner: "spot-x", group: "cf-spot", since: 1),
        42: active("b", labels: spot, runner: "spot-y", group: "cf-spot", since: 2),
        43: active("c", labels: spot, runner: "spot-z", group: "cf-spot", since: 3),
        44: active("lone", labels: ["ubuntu-latest"], group: gitHubHostedGroup),
    ]
    let leaving = RunnerBoardView()
    leaving.frame = NSRect(origin: .zero, size: size)
    leaving.show(RunnerBoard(jobs: spotJobs, runners: [], outcomes: [:]), animated: false)
    let before = leaving.placement.tokens
    let firstLayer = leaving.tokenLayer(41)
    let failedLayer = leaving.tokenLayer(44)
    expect(firstLayer.map { $0.cornerRadius == $0.bounds.width / 2 } == true, "jobs are circles")
    var ended = spotJobs
    ended[41] = nil
    ended[44] = nil
    leaving.show(RunnerBoard(jobs: ended, runners: [], outcomes: [41: .passed, 44: .failed]), animated: true)
    if !reduced {
        expect(leaving.placement.tokens[41] == nil && leaving.placement.tokens[44] == nil
               && leaving.placement.tokens[42] == before[42] && leaving.placement.tokens[43] == before[43]
               && leaving.placement.pools.count == 2,
               "a job that ends frees its place at once, nothing else moves, and its box stays")
        expect(leaving.placement.texts.contains { $0.string == "0 queued · 2 running" }
               && firstLayer?.superlayer != nil && firstLayer?.animation(forKey: "leave") != nil
               && (firstLayer?.sublayers?.first as? CATextLayer).map { "\($0.string ?? "")".contains("✓") } == true,
               "a job that ends is not counted, and leaves the board with a mark of how it ended")
        func flight(_ layer: CALayer?) -> CAKeyframeAnimation? {
            (layer?.animation(forKey: "leave") as? CAAnimationGroup)?.animations?
                .first { ($0 as? CAKeyframeAnimation)?.keyPath == "position" } as? CAKeyframeAnimation
        }
        let tumbled = flight(failedLayer)?.values?.compactMap { $0 as? CGPoint } ?? []
        let onLine = size.height - (failedLayer?.bounds.width ?? 0) / 2
        let landedOnLine = tumbled.contains { $0.y > onLine - 3 && $0.x > BoardLayout.inset }
            && tumbled.allSatisfy { $0.x < BoardLayout.inset || $0.y <= onLine + 0.01 }
        let thrownOff = flight(firstLayer)?.values?.compactMap { $0 as? CGPoint } ?? []
        // Paths worked out ahead of time play at their own pace, eased at
        // neither end.
        func linear(_ animation: CAAnimation?) -> Bool {
            guard let timing = animation?.timingFunction else { return false }
            var first = [Float](repeating: 0, count: 2), second = [Float](repeating: 0, count: 2)
            timing.getControlPoint(at: 1, values: &first)
            timing.getControlPoint(at: 2, values: &second)
            return first == [0, 0] && second == [1, 1]
        }
        expect(linear(failedLayer?.animation(forKey: "leave")) && linear(firstLayer?.animation(forKey: "leave")),
               "a job leaves along its path at the path's own pace, slowing at neither end")
        expect(tumbled.count > 30 && landedOnLine && (tumbled.last?.x ?? 0) < BoardLayout.inset
               && (thrownOff.last?.x ?? 0) > size.width,
               "a job that fails lands on the line along the bottom and goes off past its left end, and one that "
               + "passes is thrown off")
        let smoky = RunnerBoardView()
        smoky.frame = NSRect(origin: .zero, size: size)
        smoky.show(RunnerBoard(jobs: spotJobs, runners: [], outcomes: [:]), animated: false)
        let smokeLayer = smoky.tokenLayer(42)
        var cancelled = spotJobs
        cancelled[42] = nil
        smoky.show(RunnerBoard(jobs: cancelled, runners: [], outcomes: [42: .neither]), animated: true)
        let puff = flight(smokeLayer)?.values?.compactMap { $0 as? CGPoint } ?? []
        let fades = ((smokeLayer?.animation(forKey: "leave") as? CAAnimationGroup)?.animations ?? [])
            .compactMap { $0 as? CAKeyframeAnimation }.first { $0.keyPath == "opacity" }
        expect(puff.count > 2 && abs((puff.first?.x ?? 0) - (puff.last?.x ?? 1)) < 1 && (puff.last?.y ?? 0) < 0
               && (fades?.values?.last as? Int) == 0
               && (smokeLayer?.animation(forKey: "leave") as? CAAnimationGroup)?.animations?.count == 3
               && ((leaving.tokenLayer(41) ?? firstLayer)?.animation(forKey: "leave") as? CAAnimationGroup)?
                   .animations?.count == 2,
               "a job that was cancelled floats straight up off the board, fading, and one that passed does not fade")
        var swapped = ended
        swapped[47] = active("next", labels: spot, runner: "spot-u", group: "cf-spot", since: 9)
        let swapping = RunnerBoardView()
        swapping.frame = NSRect(origin: .zero, size: size)
        swapping.show(RunnerBoard(jobs: spotJobs, runners: [], outcomes: [:]), animated: false)
        swapping.show(RunnerBoard(jobs: swapped, runners: [], outcomes: [41: .passed, 44: .failed]), animated: true)
        var crowd = spotJobs
        crowd[41] = nil
        for id in 0..<(runColours.count - 1) {
            var job = active("new \(id)", labels: spot, runner: "spot-n\(id)", group: "cf-spot", since: 20)
            job.order = RunOrder(run: Int64(500 + id), attempt: 1)
            crowd[Int64(500 + id)] = job
        }
        let colours = RunnerBoardView()
        colours.frame = NSRect(origin: .zero, size: size)
        var lone = spotJobs[41]!
        lone.order = RunOrder(run: 777, attempt: 1)
        var startWith = spotJobs
        startWith[41] = lone
        colours.show(RunnerBoard(jobs: startWith, runners: [], outcomes: [:]), animated: false)
        let leavingColour = colours.palette.colours["777"]
        colours.show(RunnerBoard(jobs: crowd, runners: [], outcomes: [41: .passed]), animated: true)
        expect(leavingColour != nil && !(500..<(500 + runColours.count - 1)).contains { colours.palette.colours["\($0)"] == leavingColour },
               "no run takes the colour of a run whose last job is on its way off the board")
        expect(swapping.placement.tokens[47] == before[41],
               "a job that starts as another ends takes its place at once, while the other is still on its way off")
        // A queue is a heap of balls, as small as lets them all fit, which
        // pours in from above and settles as jobs leave it.
        var longWait: [Int64: ActiveJob] = [:]
        for id in 100..<200 {
            longWait[Int64(id)] = active("wait \(id)", labels: ["ubuntu-latest"], since: Double(id))
        }
        let heaped = RunnerBoardView()
        heaped.frame = NSRect(origin: .zero, size: size)
        heaped.show(RunnerBoard(jobs: longWait, runners: [], outcomes: [:]), animated: false)
        func settledInside(_ view: RunnerBoardView, _ ids: [Int64]) -> Bool {
            guard let heap = view.placement.heaps.first(where: { !$0.jobs.isEmpty }) else { return false }
            let frames = ids.compactMap { view.placement.tokens[$0] }
            let d = frames.first?.width ?? 0
            let overlapping = frames.enumerated().contains { i, a in
                frames[(i + 1)...].contains { b in hypot(a.midX - b.midX, a.midY - b.midY) < d * 0.85 }
            }
            return frames.count == ids.count && d < view.placement.size && !overlapping
                && frames.allSatisfy { heap.frame.insetBy(dx: -1, dy: -1).contains($0) }
        }
        expect(settledInside(heaped, Array(longWait.keys)) && heaped.tokenLayer(100)?.animation(forKey: "position") == nil,
               "every queued job is a ball in its queue's heap, smaller to fit them all, barely overlapping, still "
               + "when the board is shown afresh")
        // A queue's balls are clipped to its pool's rounded box, where they
        // are on the board.
        let queuedBall = heaped.tokenLayer(100)
        let clip = queuedBall?.superlayer
        let queuePool = heaped.placement.pools.first { box in
            heaped.placement.heaps.first { !$0.jobs.isEmpty }.map { box.contains(CGPoint(x: $0.frame.midX, y: $0.frame.midY)) } == true
        }
        expect(clip?.masksToBounds == true && clip?.cornerRadius == 10 && queuePool.map { clip?.frame == $0 } == true
               && queuedBall.map { ball in clip?.convert(ball.position, to: clip?.superlayer) == ball.position } == true,
               "a queue's balls are clipped to its pool's rounded box, and are where they are on the board")
        var poured = longWait
        poured[300] = active("new", labels: ["ubuntu-latest"], since: 300)
        heaped.show(RunnerBoard(jobs: poured, runners: [], outcomes: [:]), animated: true)
        let pour = heaped.tokenLayer(300)?.animation(forKey: "position") as? CAKeyframeAnimation
        let heapTop = heaped.placement.heaps.first { !$0.jobs.isEmpty }!.frame.minY
        let pourPoints = pour?.values?.compactMap { $0 as? CGPoint } ?? []
        let entry = Int(((heaped.tokenLayer(300)?.animation(forKey: "wait") as? CABasicAnimation)?.duration ?? 0)
                        * Heap.rate)
        expect(reduced || (entry > 0 && pourPoints.count > entry + 1 && (pourPoints.first?.y ?? 0) < heapTop - 30
                           && pourPoints[entry].y - pourPoints[entry - 1].y > 4 && pourPoints[entry].x >= pourPoints[entry - 1].x),
               "a job just queued falls, out of sight, from well above its heap, and comes in moving fast, down and "
               + "a little to the right, as if down a chute")
        expect(reduced || ((pour?.values?.first as? CGPoint).map { $0.y < heapTop } == true
                           && settledInside(heaped, Array(poured.keys))),
               "a job just queued pours into the heap from above, and comes to rest in it")
        var started = poured
        for id in 100..<130 {
            started[Int64(id)] = active("wait \(id)", labels: ["ubuntu-latest"], group: gitHubHostedGroup, since: 400)
        }
        heaped.show(RunnerBoard(jobs: started, runners: [], outcomes: [:]), animated: true)
        let stillQueued = started.filter { !$0.value.running }.map(\.key)
        expect(reduced || (settledInside(heaped, stillQueued)
                           && heaped.tokenLayer(150)?.animation(forKey: "position") is CAKeyframeAnimation),
               "when jobs leave the heap, the rest fall into the holes they leave")
        let restingBall = heaped.tokenLayer(150)
        heaped.show(RunnerBoard(jobs: started, runners: [], outcomes: [:]), animated: true)
        expect(heaped.tokenLayer(150) === restingBall && settledInside(heaped, stillQueued),
               "a heap that has not changed is not worked out again, and its balls go on as they were")
        // Many jobs queued at once stream in, one after another, each out of
        // sight until its turn; those queued while others wait follow
        // them; and one that starts while waiting comes into sight as it goes.
        let streaming = RunnerBoardView()
        streaming.frame = NSRect(origin: .zero, size: size)
        // As if no time passed between one show and the next, however long
        // working out the heaps takes.
        streaming.clock = { 1000 }
        streaming.show(RunnerBoard(jobs: [:], runners: [], outcomes: [:]), animated: false)
        var flood: [Int64: ActiveJob] = [:]
        for id in 600..<640 {
            flood[Int64(id)] = active("flood \(id)", labels: ["ubuntu-latest"], since: Double(id))
        }
        streaming.show(RunnerBoard(jobs: flood, runners: [], outcomes: [:]), animated: true)
        func waits(_ id: Int64) -> Double {
            (streaming.tokenLayer(id)?.animation(forKey: "wait") as? CABasicAnimation)?.duration ?? 0
        }
        let floodWaits = (600..<640).map { waits(Int64($0)) }
        let longestWait = floodWaits.max() ?? 0
        let oldestFirst = (floodWaits.first ?? 0) < (floodWaits.last ?? 0)
        expect(reduced || (floodWaits.allSatisfy { $0 > 0 } && oldestFirst && longestWait - (floodWaits.min() ?? 0) > 0.05
                           && (streaming.tokenLayer(639)?.animation(forKey: "wait") as? CABasicAnimation)?.toValue as? Int == 0),
               "many jobs queued at once come in one after another, each out of sight until its turn: \(floodWaits)")
        var more = flood
        more[700] = active("late", labels: ["ubuntu-latest"], since: 700)
        streaming.show(RunnerBoard(jobs: more, runners: [], outcomes: [:]), animated: true)
        func starts(_ id: Int64) -> CGPoint? {
            (streaming.tokenLayer(id)?.animation(forKey: "position") as? CAKeyframeAnimation)?.values?.first as? CGPoint
        }
        let highestBefore = (600..<640).compactMap { starts(Int64($0))?.y }.min() ?? 0
        let lateHeap = streaming.placement.heaps.first { !$0.jobs.isEmpty }?.frame ?? .zero
        let lateRest = streaming.placement.tokens[700] ?? .zero
        expect(reduced || (waits(700) > 0 && (starts(700)?.y ?? 0) < highestBefore
                           && lateHeap.insetBy(dx: -1, dy: -1).contains(lateRest)),
               "a job queued while others are on their way in starts out of sight above them, and comes in too")
        let waiter = (600..<640).map { Int64($0) }.max { waits($0) < waits($1) } ?? 639
        let streamTop = streaming.placement.heaps.first { !$0.jobs.isEmpty }?.frame.minY ?? 0
        more[waiter] = active("flood \(waiter)", labels: ["ubuntu-latest"], group: gitHubHostedGroup, since: 800)
        streaming.show(RunnerBoard(jobs: more, runners: [], outcomes: [:]), animated: true)
        let emerged = ((streaming.tokenLayer(waiter)?.animation(forKey: "position") as? CAKeyframeAnimation)?.values?
            .first as? CGPoint)?.y ?? 0
        expect(reduced || (streaming.tokenLayer(waiter) != nil && waits(waiter) == 0 && emerged >= streamTop - 0.01),
               "a job that starts while waiting its turn comes into sight out of its heap's top as it goes to its "
               + "runner: \(emerged) \(streamTop)")

        // A job whose ball is falling into its queue when it starts goes on
        // falling a moment, then curves round to its runner.
        var now: CFTimeInterval = 2000
        let dropping = RunnerBoardView()
        dropping.frame = NSRect(origin: .zero, size: size)
        dropping.clock = { now }
        dropping.show(RunnerBoard(jobs: [:], runners: [], outcomes: [:]), animated: false)
        var one = [Int64(900): active("drop", labels: ["ubuntu-latest"], since: 900)]
        dropping.show(RunnerBoard(jobs: one, runners: [], outcomes: [:]), animated: true)
        now += 0.25
        one[900] = active("drop", labels: ["ubuntu-latest"], group: gitHubHostedGroup, since: 901)
        dropping.show(RunnerBoard(jobs: one, runners: [], outcomes: [:]), animated: true)
        let steering = dropping.tokenLayer(900)?.animation(forKey: "position") as? CAKeyframeAnimation
        let curve = steering?.values?.compactMap { $0 as? CGPoint } ?? []
        let eased = steering?.timingFunction.map { timing -> Bool in
            var point = [Float](repeating: 0, count: 2)
            timing.getControlPoint(at: 1, values: &point)
            return point != [0, 0]
        } ?? true
        expect(reduced || (curve.count > 3 && curve[1].y - curve[0].y > 3 && curve.last == dropping.tokenLayer(900)?.position
                           && !eased && dropping.tokenLayer(900)?.superlayer?.masksToBounds == false),
               "a job that starts while its ball is falling goes on falling, then goes to its runner")

        let afresh = RunnerBoardView()
        afresh.frame = NSRect(origin: .zero, size: size)
        afresh.show(RunnerBoard(jobs: longWait, runners: [], outcomes: [:]), animated: false)
        let restingAt = afresh.placement.tokens[120]
        var oneMore = longWait
        oneMore[101] = active("wait 101", labels: ["ubuntu-latest"], group: gitHubHostedGroup, since: 500)
        afresh.show(RunnerBoard(jobs: oneMore, runners: [], outcomes: [:]), animated: true)
        let firstStep = (afresh.tokenLayer(120)?.animation(forKey: "position") as? CAKeyframeAnimation)?.values?.first as? CGPoint
        expect(restingAt != nil && firstStep.map { hypot($0.x - restingAt!.midX, $0.y - restingAt!.midY) < 0.5 } ?? true,
               "a ball resting since the board was shown afresh moves on from where it rests")
        // A problem line above moves every heap down; a heap that moves keeps
        // its balls in it, rather than flinging them off.
        var moved = RunnerBoard(jobs: oneMore, runners: [], outcomes: [:])
        moved.problems = ["Not connected to the relay: refused", "And another thing"]
        afresh.show(moved, animated: true)
        let ids = oneMore.filter { !$0.value.running }.map(\.key)
        expect(settledInside(afresh, ids), "a heap that moves keeps every ball in it")
        afresh.show(RunnerBoard(jobs: oneMore, runners: [], outcomes: [:]), animated: true)
        expect(settledInside(afresh, ids), "and so does one that moves back up")

        // Boxes sharing a pool are as wide as a few places, so that one
        // filling up does not move another.
        let shared: [Int64: ActiveJob] = [
            71: active("a", labels: ["alpha"], runner: "GitHub Actions 1", group: gitHubHostedGroup),
            72: active("b", labels: ["beta"], runner: "GitHub Actions 2", group: gitHubHostedGroup),
        ]
        let sharing = RunnerBoardView()
        sharing.frame = NSRect(origin: .zero, size: size)
        sharing.show(RunnerBoard(jobs: shared, runners: [], outcomes: [:]), animated: false)
        let beta = sharing.placement.tokens[72]
        var fuller = shared
        for id in 73..<79 {
            fuller[Int64(id)] = active("a\(id)", labels: ["alpha"], runner: "GitHub Actions \(id)", group: gitHubHostedGroup)
        }
        sharing.show(RunnerBoard(jobs: fuller, runners: [], outcomes: [:]), animated: true)
        expect(beta != nil && sharing.placement.tokens[72] == beta,
               "a box filling up does not move the jobs of the box beside it")
        var three = fuller
        three[79] = active("g", labels: ["gamma"], runner: "GitHub Actions 79", group: gitHubHostedGroup)
        sharing.show(RunnerBoard(jobs: three, runners: [], outcomes: [:]), animated: true)
        let boxFrames = Array(sharing.placement.boxes.suffix(3))
        expect(boxFrames[0].minY == boxFrames[1].minY && boxFrames[1].minX > boxFrames[0].maxX
               && boxFrames[2].minX == boxFrames[0].minX && boxFrames[2].minY == boxFrames[0].maxY + BoardLayout.spacing
               && Set(boxFrames.map(\.width)).count == 1 && sharing.placement.tokens[72] == beta,
               "boxes of places that share a pool sit in columns of one width, each taking the next column, and one "
               + "that comes after the last column sits right under the first: \(boxFrames)")
        expect(BoardLayout.sizes.allSatisfy { size in
               size + 2 * BoardLayout.rim(size) + BoardLayout.gap(size) == size * 1.5
           },
           "a place is half as big again as its circle at every size, so smaller circles sit no further apart")
    let shrunk = BoardLayout(board: RunnerBoard(jobs: three, runners: [], outcomes: [:]), size: size, largest: 6)
        let shrunkBoxes = Array(shrunk.boxes.suffix(3))
        expect(shrunk.size == 6 && shrunkBoxes.map(\.width) == boxFrames.map(\.width),
               "smaller circles fit more to a line in a box, rather than more columns of narrower boxes")
        let narrow = RunnerBoardView()
        narrow.frame = NSRect(x: 0, y: 0, width: 420, height: 588)
        narrow.show(RunnerBoard(jobs: three, runners: [], outcomes: [:]), animated: false)
        let stacked = Array(narrow.placement.boxes.suffix(3))
        expect(Set(stacked.map(\.minX)).count == 1 && stacked[1].minY == stacked[0].maxY + BoardLayout.spacing,
               "on a narrow board, boxes that share a pool are one column, one under another: \(stacked)")
        var titled = three
        titled[80] = active("long", labels: ["ubuntu-24.04-arm", "macos-15-xlarge", "windows-2025-large"],
                            runner: "GitHub Actions 80", group: gitHubHostedGroup)
        let wide = BoardLayout(board: RunnerBoard(jobs: titled, runners: [], outcomes: [:]), size: size)
        let longTitle = wide.texts.first { $0.string.hasPrefix("ubuntu-24.04-arm") }
        expect(longTitle.map { $0.frame.width >= BoardLayout.width($0.string, .detail) } == true,
               "a box's name is not cut short to fit a column")

        // Pools kept empty give way when the board runs out of room.
        var roomy: [Int64: ActiveJob] = [81: active("lone", labels: ["self-hosted", "rare"])]
        for id in 82..<86 {
            roomy[Int64(id)] = active("s\(id)", labels: spot, runner: "spot-\(id)", group: "cf-spot")
        }
        let giving = RunnerBoardView()
        giving.frame = NSRect(origin: .zero, size: CGSize(width: 780, height: 300))
        giving.show(RunnerBoard(jobs: roomy, runners: [], outcomes: [:]), animated: false)
        roomy[81] = nil
        giving.show(RunnerBoard(jobs: roomy, runners: [], outcomes: [:]), animated: true)
        expect(giving.placement.pools.count == 2, "a pool whose jobs have gone stays, empty, while there is room")
        for id in 86..<600 {
            roomy[Int64(id)] = active("s\(id)", labels: spot, runner: "spot-\(id)", group: "cf-spot")
        }
        giving.show(RunnerBoard(jobs: roomy, runners: [], outcomes: [:]), animated: true)
        expect(giving.placement.pools.count == 1, "an empty pool gives way once there is no room for it")

        var refilled = ended
        refilled[45] = active("d", labels: spot, runner: "spot-w", group: "cf-spot", since: 4)
        refilled[46] = active("e", labels: spot, runner: "spot-v", group: "cf-spot", since: 5)
        leaving.show(RunnerBoard(jobs: refilled, runners: [], outcomes: [:]), animated: true)
        expect(leaving.placement.tokens[45] == before[41] && leaving.placement.tokens[42] == before[42]
               && leaving.placement.tokens[43] == before[43] && leaving.placement.tokens[46].map { $0.minX > before[43]!.minX } == true
               && leaving.words(at: CGPoint(x: before[44]!.midX, y: before[44]!.midY), now: Date())?.hasSuffix(": free") == true,
               "a new job takes the first empty place, and another, with none left, one after the rest")
    }
    let learnt = active("on a machine learnt from its job", labels: ["self-hosted", "mybox", "build"], runner: "mybox-1",
                        group: "Default")
    leaving.show(RunnerBoard(jobs: [50: learnt], runners: [], outcomes: [:]), animated: false)
    let learntSlot = leaving.placement.slots.first?.frame
    leaving.show(RunnerBoard(jobs: [:], runners: [], outcomes: [50: .passed]), animated: true)
    expect(reduced || (leaving.placement.slots.first?.frame == learntSlot && learntSlot != nil
                       && leaving.placement.tokens[50] == nil),
           "a machine learnt from a job stays where it was, empty, once the job ends")
    leaving.show(RunnerBoard(jobs: spotJobs, runners: [], outcomes: [:]), animated: false)
    let again = leaving.tokenLayer(41)
    leaving.show(RunnerBoard(jobs: ended, runners: [], outcomes: [41: .passed, 44: .failed]), animated: true)
    expect(reduced || again?.superlayer != nil, "a job on its way off is on the board")
    leaving.show(RunnerBoard(jobs: ended, runners: [], outcomes: [41: .passed, 44: .failed]), animated: false)
    expect(again?.superlayer == nil,
           "shown as things are, as when the dashboard opens, the board has no jobs leaving")
    let quietly = leaving.tokenLayer(42)
    leaving.show(RunnerBoard(jobs: [:], runners: [], outcomes: [:]), animated: true)
    expect(leaving.tokenCount == 0 && quietly?.superlayer == nil,
           "a job that goes without its end being delivered simply goes")

    // The legend says what each run's colour is, one line for each.
    let legendView = RunnerBoardView()
    legendView.frame = NSRect(origin: .zero, size: size)
    legendView.show(board, animated: false)
    expect(Set(legendView.legendEntries.map(\.id)) == Set(board.runs.map(\.id))
           && legendView.legendEntries.allSatisfy { entry in entry.colour == legendView.palette.colour(entry.id) }
           && legendView.legendEntries.map(\.title) == ["repo · CI on main", "repo · Lint on main"]
           && legendView.legendEntries.map(\.jobs) == [7, 2],
           "the legend has a line for each run, in its colour, in alphabetical order, saying what it is and how "
           + "many of its jobs show")
    let screen = CGRect(x: 0, y: 0, width: 1600, height: 1000)
    let legendSize = CGSize(width: 340, height: 200)
    expect(legendFrame(beside: CGRect(x: 400, y: 300, width: 780, height: 640), size: legendSize, within: screen)
               == CGRect(x: 1188, y: 740, width: 340, height: 200)
           && legendFrame(beside: CGRect(x: 800, y: 300, width: 780, height: 640), size: legendSize, within: screen)
               == CGRect(x: 452, y: 740, width: 340, height: 200)
           && legendFrame(beside: CGRect(x: 200, y: 300, width: 1300, height: 640), size: legendSize, within: screen)
               .maxX == 1600,
           "the legend opens beside the dashboard, to its right or else its left, its tops level, and only "
           + "over it when there is no room on either side")
    legendView.toggleLegend()
    expect(legendView.legend == nil, "a board in no window has no legend to show")

    // What a job says when pointed at.
    let now = Date(timeIntervalSince1970: 1000)
    expect(RunnerBoardView.describe(board.pools[0].queue[0], at: now)
           == "repo · CI on main\nmac queued early\nQueued for 16m\nfor a runner labelled self-hosted, cf-mac, ci-mac-02",
           "a queued job says how long it has waited, and for what: \(RunnerBoardView.describe(board.pools[0].queue[0], at: now))")
    expect(RunnerBoardView.describe(board.pools[0].boxes[0].slots![1].job!, at: now)
           == "repo · CI on main\nmac running\nRunning on ci-mac-01-2 for 16m",
           "a running job says where, and for how long")
    func middle(_ frame: CGRect) -> CGPoint { CGPoint(x: frame.midX, y: frame.midY) }
    let slots = view.placement.slots.filter { !$0.slot.place }
    expect(slots.map(\.slot.name) == ["ci-mac-01-1", "ci-mac-01-2", "ci-mac-01-builder", "ci-mac-02-1"]
           && view.words(at: middle(slots[1].frame), now: now) == "ci-mac-01-2: idle"
           && view.words(at: middle(slots[3].frame), now: now) == "ci-mac-02-1: offline"
           && view.words(at: middle(slots[0].frame), now: now)?.contains("\nmac queued late\n") == true
           && view.words(at: .zero, now: now) == nil,
           "pointing at a slot names its runner and what it is doing, or the job on it, and pointing at nothing "
           + "says nothing")
    let unfinished = view.tokenLayer(2)
    view.show(RunnerBoard(jobs: [:], runners: runners, outcomes: [1: .passed]), animated: true)
    expect(view.tokenCount == 0 && unfinished?.superlayer == nil,
           "a job that goes without its end being delivered, as when cibar loses its connection, simply goes")
    expect([0, 59, 60, 3599, 3600, 90000].map { RunnerBoardView.duration($0) } == ["0s", "59s", "1m", "59m", "1h", "1d"],
           "a length of time is told in its largest unit")

    // The dashboard shows the board in place of everything above its bottom
    // row when asked to, and the board shows nothing running unless
    // connected.
    let shown = DashboardState(watch: watch, at: Date(), connection: .connected, sessions: nil,
                               includesPullRequests: false, showsRunners: true,
                               runners: runners, runnersProblem: "Could not read the runners: offline")
    expect(shown.board.tokens.count == watch.active.count && shown.board.slots == 4
           && shown.problems.last == "Could not read the runners: offline" && shown.board.problems == shown.problems,
           "the board has every job, whatever its branch, every runner, and what is wrong")
    let hidden = DashboardState(watch: watch, at: Date(), connection: .connected, sessions: nil,
                                includesPullRequests: false, showsRunners: false, runners: runners)
    expect(hidden.board == RunnerBoard(), "while the board does not show, it is not worked out")
    let offline = DashboardState(watch: watch, at: Date(), connection: .connecting, sessions: nil,
                                 includesPullRequests: false, showsRunners: true, runners: runners)
    expect(offline.board.tokens.isEmpty && offline.board.slots == 4,
           "while not connected, no job is known to be queued or running")
    let model = DashboardModel(state: offline, actions: DashboardActions())
    let dashboard = makeDashboard(model, width: 780)
    let boardView = dashboard.subviews.first { $0 is RunnerBoardView }
    expect(boardView?.isHidden == false && dashboard.subviews.filter(\.isHidden).count == 2,
           "the board shows in place of the numbers and the lists")
    var lists = offline
    lists.showsRunners = false
    model.state = lists
    expect(boardView?.isHidden == true && dashboard.subviews.filter(\.isHidden).count == 1,
           "the lists show in place of the board")
} catch {
    expect(false, "the runners board is worked out: \(error)")
}

// Reading the runners from GitHub's API.
do {
    typealias Page = StandInGitHub.Page
    StandInGitHub.pages = [
        "/orgs/octo/actions/runners?per_page=100": Page(
            body: ["runners": [["name": "m-1", "status": "online", "labels": [["name": "self-hosted"], ["name": "m"]]]]],
            link: "<https://api.github.com/orgs/octo/actions/runners?per_page=100&page=2>; rel=\"next\""),
        "/orgs/octo/actions/runners?per_page=100&page=2": Page(
            body: ["runners": [["name": "m-2", "status": "offline", "labels": [["name": "m"]]]]]),
        "/orgs/locked/actions/runners?per_page=100": Page(
            body: ["message": "Resource not accessible by personal access token"], status: 403),
        "/orgs/broken/actions/runners?per_page=100": Page(body: ["message": "Server Error"], status: 502),
        "/orgs/locked/actions/hosted-runners?per_page=100": Page(
            body: ["message": "Resource not accessible by personal access token"], status: 403),
        "/orgs/octo/actions/hosted-runners?per_page=100": Page(body: ["runners": [
            ["name": "big", "maximum_runners": 20, "machine_size_details": ["cpu_cores": 32, "memory_gb": 128]],
            ["name": "plain", "maximum_runners": 5],
            ["name": "unbounded"],
        ]]),
    ]
    var result: Result<(runners: [String: [Runner]], larger: [String: [LargerRunner]], problems: [String]), Error>?
    Task {
        result = await Result {
            try await GitHubAPI(token: "t", session: standInSession).runners(of: ["octo", "user", "locked", "broken"])
        }
    }
    waitUntil { result != nil }
    let read = try result!.get()
    expect(read.runners["octo"] == [Runner(owner: "octo", name: "m-1", labels: ["self-hosted", "m"], online: true),
                                    Runner(owner: "octo", name: "m-2", labels: ["m"], online: false)],
           "an organization's runners are read, every page of them: \(read.runners)")
    expect(read.larger["octo"] == [LargerRunner(owner: "octo", name: "big", maximum: 20, cores: 32, memory: 128),
                                   LargerRunner(owner: "octo", name: "plain", maximum: 5, cores: nil, memory: nil),
                                   LargerRunner(owner: "octo", name: "unbounded", maximum: nil, cores: nil, memory: nil)]
           && read.larger["user"] == [],
           "an organization's larger runners are read, with how many jobs each can run at once: \(read.larger)")
    expect(read.runners["user"] == [] && read.runners["locked"] == nil && read.runners["broken"] == nil
           && read.problems == [
               "broken's runners: GitHub answered 502 for /orgs/broken/actions/runners: Server Error",
               "locked's runners: GitHub answered 403 for /orgs/locked/actions/runners: Resource not accessible by "
                   + "personal access token",
               "locked's larger runners: GitHub answered 403 for /orgs/locked/actions/hosted-runners: Resource not "
                   + "accessible by personal access token",
           ],
           "an owner that is not an organization has no runners, and runners that cannot be read are a problem: "
           + "\(read.problems)")
} catch {
    expect(false, "the runners are read: \(error)")
}

for folder in temporaryFolders {
    try? FileManager.default.removeItem(at: folder)
}

if failures > 0 {
    fputs("\(failures) failed\n", stderr)
    exit(1)
}
print("all passed")
