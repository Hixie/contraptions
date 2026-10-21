import Foundation
import Network

// Tests for how ghbar keeps track of a branch's checks, from GitHub's
// answers and from a relay of webhook deliveries, and for when it asks
// GitHub. Run them with `make test`.

var failures = 0

func expect(_ condition: Bool, _ message: String, line: Int = #line) {
    if !condition {
        failures += 1
        // Unbuffered, so that a failure is seen even if a later wait never ends.
        fputs("FAIL line \(line): \(message)\n", stderr)
    }
}

/// Runs the main run loop until the condition holds.
func waitUntil(_ condition: () -> Bool, line: Int = #line) {
    if ProcessInfo.processInfo.environment["TRACE_WAITS"] != nil { fputs("waiting at line \(line)\n", stderr) }
    while !condition() {
        RunLoop.main.run(mode: .default, before: .distantFuture)
    }
}

// MARK: - Building inputs

let repository = RepositoryReference(owner: "octo", name: "repo")

/// A moment, in seconds after a fixed start.
func at(_ seconds: Int) -> Date {
    Date(timeIntervalSince1970: 1_800_000_000 + Double(seconds))
}

func stamp(_ seconds: Int) -> String {
    ISO8601DateFormatter().string(from: at(seconds))
}

/// GitHub's answer about a commit: one suite per entry, finished in the
/// given state, or running if nil.
func snapshot(sha: String = "a1", _ suites: [Int: CheckState?] = [1: nil],
              named: RepositoryReference = repository) -> BranchSnapshot {
    var checks = CommitChecks()
    for (suite, state) in suites {
        if let state {
            checks.suiteFinished(suite: suite, state: state, detail: state.rawValue, at: at(0),
                                 workflow: "Workflow \(suite)")
        } else {
            checks.runStarted(suite: suite, run: "Workflow \(suite)", url: nil, at: at(0),
                              workflow: "Workflow \(suite)")
        }
    }
    return BranchSnapshot(repository: named, sha: sha, headline: "Head", checks: checks, rateLimit: nil)
}

func request(_ event: String, _ body: String, eventHeader: String = "X-GitHub-Event") -> Data {
    Data("POST /webhook HTTP/1.1\r\nContent-Type: application/json\r\n\(eventHeader): \(event)\r\n\r\n\(body)".utf8)
}

func quoted(_ text: String?) -> String {
    text.map { "\"\($0)\"" } ?? "null"
}

func checkRunBody(sha: String = "a1", suite: Int = 1, name: String = "test", status: String = "completed",
                  conclusion: String? = "failure", started: Int = 10, completed: Int = 20,
                  branch: String? = "main", before: String? = nil, app: String = "github-actions",
                  repository: String = "octo/repo") -> String {
    """
    {"action": "completed", "check_run": {"id": 7, "head_sha": "\(sha)", "name": "\(name)",
     "status": "\(status)", "conclusion": \(quoted(conclusion)),
     "started_at": "\(stamp(started))", "completed_at": "\(stamp(completed))",
     "details_url": "https://example.com/run", "html_url": "https://github.com/run",
     "app": {"slug": "\(app)"},
     "check_suite": {"id": \(suite), "head_branch": \(quoted(branch)), "before": \(quoted(before))}},
     "repository": {"full_name": "\(repository)"}}
    """
}

func checkSuiteBody(sha: String = "a1", suite: Int = 1, conclusion: String = "success", updated: Int = 30,
                    status: String = "completed", branch: String? = "main", before: String? = nil,
                    app: String = "github-actions") -> String {
    """
    {"action": "completed", "check_suite": {"id": \(suite), "head_sha": "\(sha)", "head_branch": \(quoted(branch)),
     "before": \(quoted(before)), "status": "\(status)", "conclusion": "\(conclusion)",
     "updated_at": "\(stamp(updated))", "app": {"slug": "\(app)"}},
     "repository": {"full_name": "octo/repo"}}
    """
}

func statusBody(sha: String = "a1", id: Int = 1, state: String = "failure") -> String {
    #"{"id": \#(id), "sha": "\#(sha)", "state": "\#(state)", "context": "lint", "description": "Lint", "target_url": null, "branches": [{"name": "main"}], "repository": {"full_name": "octo/repo"}}"#
}

/// A push, at `pushed` seconds after the start if given, as the seconds
/// since 1970 that GitHub gives.
func pushBody(ref: String = "refs/heads/main", before: String = "a1", after: String,
              message: String = "Second\n\nBody", deleted: Bool = false, pushed: Int? = nil) -> String {
    let time = pushed.map { #", "pushed_at": \#(Int(at($0).timeIntervalSince1970))"# } ?? ""
    return #"{"ref": "\#(ref)", "before": "\#(before)", "after": "\#(after)", "deleted": \#(deleted), "head_commit": {"message": "\#(message.replacingOccurrences(of: "\n", with: "\\n"))"}, "repository": {"full_name": "octo/repo"\#(time)}}"#
}

func read(_ name: String, _ body: String) -> WebhookEvent? {
    try? WebhookEvent(Delivery(request(name, body))!)
}

func event(_ name: String, _ body: String) -> WebhookEvent {
    read(name, body)!
}

func frame(_ request: Data) -> Data {
    let length = UInt32(request.count)
    return Data([UInt8(length >> 24), UInt8(length >> 16 & 0xff), UInt8(length >> 8 & 0xff),
                 UInt8(length & 0xff)]) + request
}

func changed(_ reaction: LedgerReaction) -> BranchStatus? {
    if case .changed(let status) = reaction { return status }
    return nil
}

func isFetch(_ reaction: LedgerReaction) -> Bool {
    if case .fetch = reaction { return true }
    return false
}

func isIgnore(_ reaction: LedgerReaction) -> Bool {
    if case .ignore = reaction { return true }
    return false
}

// MARK: - Relay addresses

expect(parseRelayAddress("relay.example.com:8081") == RelayAddress(host: "relay.example.com", port: 8081),
       "a host and port")
expect(parseRelayAddress(" [::1]:80 ") == RelayAddress(host: "::1", port: 80),
       "an IPv6 address in brackets")
expect(RelayAddress(host: "::1", port: 80).description == "[::1]:80",
       "an IPv6 address is written back in brackets")
for text in ["relay.example.com", "relay:0", "relay:port", ":8081", "relay:70000", "a b:1", "[::1]]:1",
             "::1:80", "http://relay:80", "user@relay:80"] {
    expect(parseRelayAddress(text) == nil, "\"\(text)\" is not an address")
}

// MARK: - Frames

do {
    let first = Data("one".utf8)
    let second = Data("second".utf8)
    let third = frame(Data("third".utf8))
    var buffer = frame(first) + frame(second) + third.prefix(5)
    let frames = try takeFrames(from: &buffer)
    expect(frames == [first, second], "complete frames come out in order")
    expect(buffer == third.prefix(5), "the start of an incomplete frame stays behind")
    buffer.append(third.dropFirst(5))
    expect(try takeFrames(from: &buffer) == [Data("third".utf8)], "the rest of the frame completes it")
    expect(buffer.isEmpty, "nothing is left once every frame is taken")
    var partialLength = Data([0, 0])
    expect(try takeFrames(from: &partialLength).isEmpty, "half a length is not a frame")
    expect(partialLength.count == 2, "half a length waits for the rest")
} catch {
    expect(false, "frames that fit are read without complaint: \(error)")
}

do {
    var huge = Data([0xff, 0xff, 0xff, 0xff])
    _ = try takeFrames(from: &huge)
    expect(false, "a frame longer than any delivery is refused")
} catch {
    expect(error.length == 0xffff_ffff, "the refusal names the length")
}

// MARK: - Deliveries

do {
    let delivery = Delivery(request("push", pushBody(after: "b2"), eventHeader: "x-github-EVENT"))
    expect(delivery?.headers["x-github-event"] == "push", "header names are read without regard to case")
    expect(Delivery(Data("POST / HTTP/1.1\r\nX-GitHub-Event: push\r\n".utf8)) == nil,
           "a request with no end to its headers is not a delivery")

    let failed = read("check_run", checkRunBody(sha: "c3"))
    if case .runFailed(let suite, let run, _, let time)? = failed?.kind {
        expect(suite == 1 && run == "test" && time == at(20), "a failed check run says when it failed")
    } else {
        expect(false, "a failed check run is read as a failure")
    }
    expect(failed?.repository == "octo/repo" && failed?.sha == "c3" && failed?.branch == "main",
           "a check run names its repository, its commit, and the branch its suite was started for")
    expect(read("check_run", checkRunBody(before: "b2"))?.before == "b2",
           "a check run names the commit the branch was on before the push that started its suite")
    expect(read("check_run", checkRunBody(app: "cubic")) == nil && read("check_suite", checkSuiteBody(app: "cubic")) == nil,
           "check runs and suites of apps other than GitHub Actions are passed over")
    if case .runStarted(_, _, _, let time)? = read("check_run", checkRunBody(status: "in_progress",
                                                                             conclusion: nil))?.kind {
        expect(time == at(10), "a check run in progress says when it started")
    } else {
        expect(false, "a check run in progress is read as started")
    }
    expect(read("check_run", checkRunBody(conclusion: "success")) == nil,
           "a check run that passed says nothing until its suite finishes")
    let fork = read("check_run", checkRunBody(branch: nil))
    expect(fork != nil && fork?.branch == nil, "a check run started in a fork names no branch")

    if case .suiteFinished(let suite, let state, _, let time)? = read("check_suite", checkSuiteBody())?.kind {
        expect(suite == 1 && state == .passing && time == at(30), "a finished suite says how and when")
    } else {
        expect(false, "a finished check suite is read")
    }
    expect(read("check_suite", checkSuiteBody(status: "in_progress")) == nil,
           "a suite that has not finished is not read")

    let status = read("status", statusBody(sha: "c3"))
    if case .status(let context, let record)? = status?.kind {
        expect(context == "lint" && record.state == .failing && record.id == 1, "a status says its context and state")
    } else {
        expect(false, "a status is read")
    }

    let push = read("push", pushBody(after: "b2", pushed: 5))
    if case .push(let headline, let deleted, let time)? = push?.kind {
        expect(headline == "Second" && !deleted, "a push gives the first line of the new commit's message")
        expect(time == at(5), "and when it happened, in seconds since 1970")
    } else {
        expect(false, "a push is read")
    }
    let written = read("push", pushBody(after: "b2").replacingOccurrences(
        of: #""full_name": "octo/repo""#, with: #""full_name": "octo/repo", "pushed_at": "\#(stamp(6))""#))
    if case .push(_, _, let time)? = written?.kind {
        expect(time == at(6), "or as a written time")
    } else {
        expect(false, "a push with a written time is read")
    }
    let odd = read("push", pushBody(after: "b2").replacingOccurrences(
        of: #""full_name": "octo/repo""#, with: #""full_name": "octo/repo", "pushed_at": true"#))
    if case .push(_, _, let time)? = odd?.kind {
        expect(time == nil, "a push time in no form GitHub uses is left out")
    } else {
        expect(false, "a push with an odd time is still read")
    }
    expect(push?.sha == "b2" && push?.branch == "main" && push?.before == "a1",
           "a push names the branch, where it was, and where it now is")
    expect(read("push", pushBody(ref: "refs/tags/v1", after: "b2")) == nil, "a push of a tag is not read")

    do {
        expect(try WebhookEvent(Delivery(request("pull_request", "not json"))!) == nil,
               "other events are passed over without being read")
    } catch {
        expect(false, "other events are not complained about")
    }
    do {
        _ = try WebhookEvent(Delivery(request("push", "payload=%7B%7D"))!)
        expect(false, "a delivery that does not parse is complained about")
    } catch {
        expect(error.description.hasPrefix("A push delivery could not be read"), "the complaint names the event")
    }
}

// MARK: - A commit's checks

do {
    var record = CommitChecks()
    record.runStarted(suite: 1, run: "build", url: nil, at: at(10))
    expect(record.summaries.first?.state == .running, "a suite with a run started is running")
    record.suiteFinished(suite: 1, state: .passing, detail: "success", at: at(30), workflow: "CI")
    expect(record.summaries.first?.state == .passing, "a suite that finished after its runs started passed")
    expect(record.summaries.first?.name == "CI", "a suite is named by its workflow")
    record.runStarted(suite: 1, run: "build", url: nil, at: at(20))
    expect(record.summaries.first?.state == .passing, "a start reported late, from before the finish, changes nothing")
    record.runStarted(suite: 1, run: "build", url: nil, at: at(40))
    expect(record.summaries.first?.state == .running, "a run started after the finish runs the suite again")
    record.runFailed(suite: 1, run: "build", url: nil, at: at(50))
    record.runStarted(suite: 1, run: "lint", url: nil, at: at(55))
    expect(record.summaries.first?.state == .failing, "a failure stands while other runs in the suite go on")
    expect(record.summaries.first?.detail == "build failed", "the failure names the run")
    record.suiteFinished(suite: 1, state: .passing, detail: "success", at: at(60))
    expect(record.summaries.first?.state == .passing, "a suite that passed after the failure, on a rerun, passed")
    record.suiteFinished(suite: 1, state: .failing, detail: "failure", at: at(45))
    expect(record.summaries.first?.state == .passing, "a finish reported late changes nothing")

    record.statusSet(context: "lint", StatusRecord(id: 5, state: .failing, detail: "", url: nil))
    record.statusSet(context: "lint", StatusRecord(id: 4, state: .passing, detail: "", url: nil))
    expect(record.statuses["lint"]?.state == .failing, "the newest status in a context stands")
    expect(rollUp(record.summaries) == .failing, "a failing status makes the commit fail")
}

do {
    var early = CommitChecks()
    early.runStarted(suite: 1, run: "build", url: nil, at: at(10))
    early.runFailed(suite: 2, run: "lint", url: nil, at: at(15))
    var late = CommitChecks()
    late.suiteFinished(suite: 1, state: .passing, detail: "success", at: at(30))
    late.suiteFinished(suite: 2, state: .passing, detail: "success", at: at(12))
    var one = early
    one.merge(late)
    var other = late
    other.merge(early)
    expect(one.summaries.map(\.state) == other.summaries.map(\.state),
           "what is known does not depend on the order it was learned in")
    expect(one.summaries.map(\.state) == [.passing, .failing],
           "each suite is in the state its latest report gives")
}

do {
    let date = ISO8601DateFormatter()
    let runs = try gitHubDecoder.decode(WorkflowRuns.self, from: Data("""
    {"workflow_runs": [
      {"id": 1, "name": "CI", "status": "completed", "conclusion": "success", "check_suite_id": 11,
       "html_url": "https://github.com/1", "run_started_at": "\(date.string(from: at(0)))",
       "updated_at": "\(date.string(from: at(5)))"},
      {"id": 2, "name": "Tests", "status": "in_progress", "conclusion": null, "check_suite_id": 12,
       "html_url": "https://github.com/2", "run_started_at": "\(date.string(from: at(1)))",
       "updated_at": "\(date.string(from: at(6)))"},
      {"id": 3, "name": "Skipped", "status": "completed", "conclusion": "skipped", "check_suite_id": 13,
       "html_url": null, "run_started_at": null, "updated_at": "\(date.string(from: at(2)))"}]}
    """.utf8)).workflowRuns
    let jobs = try gitHubDecoder.decode(Jobs.self, from: Data("""
    {"jobs": [{"name": "shard 1", "status": "completed", "conclusion": "failure",
               "completed_at": "\(date.string(from: at(4)))", "html_url": "https://github.com/2/1",
               "steps": [{"name": "Run", "conclusion": "failure"}]},
              {"name": "shard 2", "status": "in_progress", "conclusion": null, "completed_at": null,
               "html_url": null, "steps": null}]}
    """.utf8)).jobs
    let checks = commitChecks(runs: runs, jobs: [2: jobs], statuses: [])
    let byName = Dictionary(uniqueKeysWithValues: checks.summaries.map { ($0.name, $0) })
    expect(byName["CI"]?.state == .passing, "a finished workflow run gives its conclusion")
    expect(byName["Tests"]?.state == .failing, "a job that failed fails its workflow run before the run finishes")
    expect(byName["Tests"]?.detail == "shard 1 failed", "and is named")
    expect(byName["Skipped"]?.state == .other, "a skipped workflow run neither passes nor fails")
} catch {
    expect(false, "GitHub's lists of workflow runs and jobs are read: \(error)")
}

// MARK: - The ledger

func ledger(_ first: BranchSnapshot = snapshot()) -> BranchLedger {
    let ledger = BranchLedger(repository: repository, branch: "main")
    _ = ledger.adopt(first, complete: true, replacing: false)
    return ledger
}

do {
    let fresh = BranchLedger(repository: repository, branch: "main")
    let first = fresh.adopt(snapshot(), complete: true, replacing: false)
    expect(first.settled && first.shown.health == .running && first.shown.sha == "a1",
           "an answer during a connection is shown, and leaves the rest to deliveries")
    expect(!fresh.adopt(snapshot(), complete: false, replacing: true).settled,
           "an answer that may have missed changes is not settled")

    let ledger = ledger()
    expect(changed(ledger.take(event("check_suite", checkSuiteBody())))?.health == .passing,
           "a suite finishing on the head turns the branch green, without asking GitHub")
    expect(changed(ledger.take(event("check_run", checkRunBody(started: 40, completed: 50))))?.health == .failing,
           "a run failing on the head turns the branch red, without asking GitHub")
    expect(changed(ledger.take(event("status", statusBody(state: "success"))))?.health == .failing,
           "a passing status does not hide a failed suite")
    expect(changed(ledger.take(event("check_suite", checkSuiteBody(updated: 60))))?.health == .passing,
           "a suite that passes on a rerun turns the branch green again")

    let merged = ledger.adopt(snapshot([1: .failing]), complete: true, replacing: false)
    expect(merged.shown.health == .passing, "an answer older than the deliveries does not undo them")
    let replaced = ledger.adopt(snapshot([2: .passing]), complete: true, replacing: true)
    expect(replaced.shown.checks.map(\.suite) == [2],
           "an answer that is believed replaces what was known, so a deleted run leaves")
    expect(!replaced.settled, "and is checked once more, in case it lags the deliveries")
}

do {
    let ledger = ledger()
    // Deliveries about the next commit can arrive before the push.
    _ = ledger.take(event("check_run", checkRunBody(sha: "b2", suite: 2, status: "queued", conclusion: nil)))
    let moved = changed(ledger.take(event("push", pushBody(after: "b2"))))
    expect(moved?.sha == "b2" && moved?.headline == "Second", "a push moves the branch to the new commit")
    expect(moved?.health == .running, "what was delivered about the new commit before the push counts")
    expect(isIgnore(ledger.take(event("check_suite", checkSuiteBody(sha: "a1", conclusion: "failure")))),
           "a suite finishing on the commit pushed over is ignored")
    expect(isIgnore(ledger.take(event("push", pushBody(after: "b2")))), "the same push delivered again is ignored")
    // An answer from before the push arrives late.
    let late = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    expect(!late.settled && late.shown.sha == "b2",
           "an answer about the commit pushed over keeps the new commit, and asks again later")
}

do {
    // The branch goes a1, b2, c3, and the pushes arrive out of order.
    let ledger = ledger()
    expect(isFetch(ledger.take(event("push", pushBody(before: "b2", after: "c3", pushed: 20)))),
           "a first push that does not start from the head asks GitHub, in case one went missing")
    expect(isIgnore(ledger.take(event("push", pushBody(before: "a1", after: "b2", pushed: 10)))),
           "a push older than the last one is ignored")
    expect(ledger.adopt(snapshot(sha: "c3"), complete: true, replacing: false).shown.sha == "c3",
           "and GitHub's answer says where the branch is")
    expect(changed(ledger.take(event("push", pushBody(before: "x9", after: "d4", pushed: 30))))?.sha == "d4",
           "a newer push moves the branch, wherever it starts from")
    expect(changed(ledger.take(event("push", pushBody(before: "d4", after: "c3", pushed: 40))))?.sha == "c3",
           "a newer push back to a commit the branch has left moves it there")
    expect(isIgnore(ledger.take(event("push", pushBody(before: "c3", after: "d4", pushed: 30)))),
           "and a late delivery of the push before it does not move it on again")
}

do {
    // A revert is delivered before the push it reverts.
    let ledger = ledger()
    expect(isIgnore(ledger.take(event("push", pushBody(before: "b2", after: "a1", pushed: 20)))),
           "a push to the commit the branch is on changes nothing")
    expect(isIgnore(ledger.take(event("push", pushBody(before: "a1", after: "b2", pushed: 10)))),
           "and the earlier push it reverted, delivered after it, leaves the branch on a1")
    // Without times, a push moves the branch only from the head, and not
    // onto a commit it has left.
    let untimed = BranchLedger(repository: repository, branch: "main")
    _ = untimed.adopt(snapshot(), complete: true, replacing: false)
    expect(isIgnore(untimed.take(event("push", pushBody(before: "b2", after: "a1")))),
           "a push to the commit the branch is on changes nothing")
    expect(isFetch(untimed.take(event("push", pushBody(before: "a1", after: "b2")))),
           "and the push it reverted, delivered after it, asks GitHub rather than moving the branch")
    expect(changed(untimed.take(event("push", pushBody(before: "a1", after: "c3"))))?.sha == "c3",
           "a push without a time moves the branch from the head")
    expect(isFetch(untimed.take(event("push", pushBody(before: "zz", after: "d4")))),
           "and one from elsewhere asks GitHub")
    // Times outlast a reconnect, so an old push delivered again after one
    // is still ignored.
    ledger.forgetPushes()
    expect(isIgnore(ledger.take(event("push", pushBody(before: "a1", after: "b2", pushed: 15)))),
           "an older push is ignored after a reconnect too")
}

do {
    // GitHub's answer can lag a push, but not forever.
    let ledger = ledger()
    _ = ledger.take(event("push", pushBody(before: "a1", after: "b2")))
    let lagging = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    expect(lagging.shown.sha == "b2" && !lagging.settled,
           "an answer about a commit the branch was pushed away from is checked again")
    let again = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    expect(again.shown.sha == "a1", "and believed if it says the same again")
}

do {
    // An answer still lags after the branch has moved on again.
    let ledger = ledger()
    _ = ledger.take(event("push", pushBody(before: "a1", after: "b2", pushed: 10)))
    _ = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    _ = ledger.take(event("push", pushBody(before: "b2", after: "c3", pushed: 20)))
    let stale = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    expect(stale.shown.sha == "c3" && !stale.settled,
           "a doubt about an answer starts again when the branch moves")

    // A push that asked GitHub, answered by an answer that still lags it.
    let pushed = BranchLedger(repository: repository, branch: "main")
    _ = pushed.adopt(snapshot(), complete: true, replacing: false)
    expect(isFetch(pushed.take(event("push", pushBody(before: "zz", after: "d4")))), "a push from elsewhere asks")
    let lagging = pushed.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    expect(!lagging.settled, "an answer that does not name the pushed commit is checked again")
    expect(pushed.adopt(snapshot(sha: "d4"), complete: true, replacing: false).shown.sha == "d4",
           "and the next answer can move the branch there")
}

do {
    let ledger = ledger()
    // GitHub never delivered the push to b2, but the suite it started says
    // where it came from.
    expect(isFetch(ledger.take(event("check_run", checkRunBody(sha: "b2", suite: 2, status: "queued",
                                                               conclusion: nil, before: "a1")))),
           "a suite on the branch at a commit it has not been on asks GitHub, and does not move the branch")
    expect(ledger.status?.sha == "a1", "the branch stays where it was until GitHub says")
    let first = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    expect(!first.settled, "an answer that says the branch is not on the commit asked about is checked again")
    expect(isFetch(ledger.take(event("check_run", checkRunBody(sha: "b2", suite: 2, before: "a1")))),
           "in case it lagged, so deliveries about the commit go on asking")
    let second = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    expect(isIgnore(ledger.take(event("check_run", checkRunBody(sha: "b2", suite: 2, before: "a1")))),
           "once two answers running have said the branch is not on that commit, its deliveries stop asking")
    let third = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: false)
    expect(!second.settled && third.settled, "and the answers settle")
    expect(isIgnore(ledger.take(event("check_suite", checkSuiteBody(sha: "f6", branch: "other", before: "a1")))),
           "a suite on another branch does not ask")
    expect(isIgnore(ledger.take(event("check_suite", checkSuiteBody(sha: "f6", branch: nil, before: "a1")))),
           "nor does one started in a fork")
    expect(changed(ledger.take(event("push", pushBody(before: "a1", after: "b2"))))?.sha == "b2",
           "a push still moves the branch to such a commit")
    // b2 is force-pushed back to a1, and the push is lost.
    expect(isFetch(ledger.take(event("check_run", checkRunBody(sha: "a1", suite: 3, status: "queued",
                                                               conclusion: nil, before: "b2")))),
           "a suite pushed from the head onto a commit the branch left asks GitHub")
    expect(isIgnore(ledger.take(event("check_run", checkRunBody(sha: "a1", suite: 1, before: "a0")))),
           "a late check run on a commit the branch left does not")

    ledger.forgetPushes()
    let back = ledger.adopt(snapshot(sha: "a1"), complete: true, replacing: true)
    expect(!back.settled && back.shown.sha == "a1",
           "a believed answer says where the branch is, and is checked again")
    let refreshed = ledger.adopt(snapshot(sha: "b2"), complete: true, replacing: true)
    expect(refreshed.shown.sha == "b2", "even onto a commit the branch has left")
}

do {
    let ledger = ledger()
    expect(isIgnore(ledger.take(event("push", pushBody(before: "a0", after: "a1")))),
           "a push that leaves the branch where it was is ignored")
    expect(isIgnore(ledger.take(event("push", pushBody(ref: "refs/heads/other", after: "b2")))),
           "a push to another branch is ignored")
    expect(isFetch(ledger.take(event("push", pushBody(after: "0000000", deleted: true)))),
           "a branch being deleted asks GitHub")
    expect(isIgnore(ledger.take(event("check_suite", checkSuiteBody(sha: "c3", branch: "other")))),
           "a suite on another branch's commit is ignored")
    expect(isIgnore(ledger.take(event("check_suite", checkSuiteBody(sha: "a1")
        .replacingOccurrences(of: "octo/repo", with: "octo/other")))),
           "a delivery about another repository is ignored")
    expect(changed(ledger.take(event("check_suite", checkSuiteBody(sha: "a1")
        .replacingOccurrences(of: "octo/repo", with: "Octo/Repo")))) != nil,
           "repository names are compared without regard to case")

    _ = ledger.adopt(snapshot(named: RepositoryReference(owner: "octo", name: "renamed")), complete: true,
                     replacing: false)
    expect(changed(ledger.take(event("check_suite", checkSuiteBody(updated: 70)
        .replacingOccurrences(of: "octo/repo", with: "octo/renamed")))) != nil,
           "a delivery under the name GitHub last answered with counts")
    expect(changed(ledger.take(event("check_suite", checkSuiteBody(updated: 80)))) != nil,
           "and so does one under the name in the settings")

    ledger.missedDelivery()
    expect(!ledger.adopt(snapshot(), complete: true, replacing: false).settled,
           "an answer after a delivery that could not be read is not settled")
    expect(ledger.adopt(snapshot(), complete: true, replacing: false).settled, "the next one is")
}

do {
    // Deliveries about many other commits push the oldest out.
    let ledger = ledger()
    _ = ledger.take(event("check_run", checkRunBody(sha: "z0", suite: 9, before: "zz")))
    for index in 1...rememberedCommits {
        _ = ledger.take(event("check_run", checkRunBody(sha: "z\(index)", suite: 9, before: "zz")))
    }
    _ = ledger.take(event("push", pushBody(before: "a1", after: "z0")))
    expect(ledger.status?.sha == "z0" && ledger.status?.checks.isEmpty == true,
           "what is remembered about other commits is bounded")
    _ = ledger.take(event("push", pushBody(before: "z0", after: "z\(rememberedCommits)")))
    expect(ledger.status?.health == .failing, "and the newest is kept")
}

do {
    var record = CommitChecks()
    record.runFailed(suite: 1, run: "lint", url: URL(string: "https://github.com/job"), at: at(10))
    record.runStarted(suite: 1, run: "test", url: nil, at: at(20))
    expect(record.summaries.first?.name == "lint", "a suite keeps the name it was first given")
    record.runStarted(suite: 1, run: "Workflow", url: URL(string: "https://github.com/run"), at: at(5),
                      workflow: "CI")
    expect(record.summaries.first?.name == "CI" && record.summaries.first?.url == URL(string: "https://github.com/run"),
           "until its workflow is known, whose name and page then stay")
}

// MARK: - Reading a branch from GitHub

/// Stands in for GitHub's HTTP API: answers each request from `routes`, by
/// path and query, and remembers what was asked.
final class StubGitHub: URLProtocol {
    struct Answer {
        var status = 200
        var headers: [String: String] = [:]
        var body = ""
        /// Fails as the network would, without an answer.
        var unreachable = false
    }

    nonisolated(unsafe) static var routes: [String: Answer] = [:]
    nonisolated(unsafe) static var asked: [(path: String, etag: String?)] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let url = request.url!
        let key = url.path + (url.query.map { "?" + $0 } ?? "")
        let etag = request.value(forHTTPHeaderField: "If-None-Match")
        StubGitHub.asked.append((key, etag))
        var answer = StubGitHub.routes[key] ?? Answer(status: 404, body: "{}")
        if answer.unreachable {
            client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
            return
        }
        if let etag, etag == answer.headers["ETag"] {
            answer.status = 304
            answer.body = ""
        }
        let response = HTTPURLResponse(url: url, statusCode: answer.status, httpVersion: "HTTP/1.1",
                                       headerFields: answer.headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(answer.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

do {
    StubGitHub.routes = [
        "/repos/octo/repo/commits/main/status?per_page=100": .init(headers: ["ETag": "\"s1\""], body: """
            {"sha": "a1", "repository": {"full_name": "octo/renamed"},
             "statuses": [{"id": 3, "context": "lint", "state": "success", "description": "Clean", "target_url": null}]}
            """),
        "/repos/octo/renamed/actions/runs?head_sha=a1&per_page=100": .init(headers: [
            "ETag": "\"r1\"",
            "Link": "<https://api.github.com/repos/octo/renamed/actions/runs?head_sha=a1&per_page=100&page=2>; rel=\"next\"",
        ], body: """
            {"workflow_runs": [{"id": 1, "name": "CI", "status": "completed", "conclusion": "success",
              "check_suite_id": 11, "html_url": null, "run_started_at": null, "updated_at": "\(stamp(5))"}]}
            """),
        "/repos/octo/renamed/actions/runs?head_sha=a1&per_page=100&page=2": .init(body: """
            {"workflow_runs": [{"id": 2, "name": "Tests", "status": "in_progress", "conclusion": null,
              "check_suite_id": 12, "html_url": null, "run_started_at": "\(stamp(1))", "updated_at": "\(stamp(6))"}]}
            """),
        "/repos/octo/renamed/actions/runs/2/jobs?filter=latest&per_page=100": .init(body: """
            {"jobs": [{"name": "shard 3", "status": "completed", "conclusion": "failure",
              "completed_at": "\(stamp(4))", "html_url": "https://github.com/job",
              "steps": [{"name": "Set up", "conclusion": "success"}, {"name": "Run", "conclusion": "failure"}]}]}
            """),
        "/repos/octo/renamed/commits/a1": .init(body: #"{"commit": {"message": "First\n\nBody"}}"#),
        "/repos/octo/renamed/actions/runs?check_suite_id=12": .init(body: """
            {"workflow_runs": [{"id": 2, "name": "Tests", "status": "in_progress", "conclusion": null,
              "check_suite_id": 12, "html_url": null, "run_started_at": null, "updated_at": "\(stamp(6))"}]}
            """),
        "/repos/octo/renamed/compare/g0...a1": .init(body: """
            {"commits": [{"sha": "b0000000", "commit": {"message": "Older"}},
                         {"sha": "a1000000", "commit": {"message": "Newer\\nmore"}}]}
            """),
    ]
    let reader = BranchReader(client: GitHubClient(protocolClasses: [StubGitHub.self]))
    var answer: BranchSnapshot?
    reader.fetch(repository: repository, branch: "main", token: "t") { result in
        if case .success(let found) = result { answer = found } else { expect(false, "the branch is read: \(result)") }
    }
    waitUntil { answer != nil || StubGitHub.asked.count > 9 }
    expect(answer?.repository == RepositoryReference(owner: "octo", name: "renamed"),
           "the repository is answered for under its current name")
    expect(answer?.sha == "a1" && answer?.headline == "First", "the branch's commit and headline are read")
    let status = answer.map { BranchStatus(repository: $0.repository, branch: "main", sha: $0.sha,
                                           headline: $0.headline, checks: $0.checks, fetched: Date(),
                                           rateLimit: nil) }
    expect(status?.health == .failing, "a failed job in a running workflow fails the branch")
    expect(status?.checks.map(\.name) == ["CI", "lint", "Tests"], "every workflow run on every page, and each status")

    // Asked again, the unchanged answers come back as they were.
    StubGitHub.asked.removeAll()
    answer = nil
    reader.fetch(repository: repository, branch: "main", token: "t") { result in
        if case .success(let found) = result { answer = found }
    }
    waitUntil { answer != nil || StubGitHub.asked.count > 9 }
    expect(StubGitHub.asked.first?.etag == "\"s1\"", "a request asks again conditionally")
    expect(answer?.checks.statuses["lint"]?.state == .passing, "an answer of no change gives the earlier answer")
    expect(!StubGitHub.asked.contains { $0.path == "/repos/octo/renamed/commits/a1" },
           "a commit's headline is asked for once")

    // A commit message GitHub refuses is not asked for again, but one the
    // network could not fetch is.
    func headlineAsks(_ commit: StubGitHub.Answer) -> Int {
        let other = BranchReader(client: GitHubClient(protocolClasses: [StubGitHub.self]))
        StubGitHub.routes["/repos/octo/renamed/commits/a1"] = commit
        StubGitHub.asked.removeAll()
        for _ in 0..<2 {
            var done = false
            other.fetch(repository: repository, branch: "main", token: "t") { _ in done = true }
            waitUntil { done }
        }
        return StubGitHub.asked.filter { $0.path == "/repos/octo/renamed/commits/a1" }.count
    }
    expect(headlineAsks(.init(status: 403, body: "{}")) == 1, "a refused commit message is asked for once")
    expect(headlineAsks(.init(status: 403, headers: ["x-ratelimit-remaining": "0", "x-ratelimit-limit": "60",
                                                     "x-ratelimit-reset": "1800000000"], body: "{}")) == 2,
           "one refused for want of rate limit is asked for again")
    expect(headlineAsks(.init(status: 403, headers: ["retry-after": "60"], body: "{}")) == 2,
           "and so is one refused to slow requests down")
    expect(headlineAsks(.init(unreachable: true)) == 2, "one the network could not fetch is asked for again")

    var quiet: String?
    reader.failureReport(BranchStatus(repository: repository, branch: "main", sha: "a1", headline: nil,
                                      checks: CommitChecks(), fetched: Date(), rateLimit: nil),
                         lastGreen: nil, token: nil) { quiet = $0 }
    waitUntil { quiet != nil }
    expect(quiet == "Nothing on a1 has failed.", "a report on a branch where nothing failed says so")

    var report: String?
    if let status {
        reader.failureReport(status, lastGreen: "g0", token: "t") { report = $0 }
    }
    waitUntil { report != nil }
    expect(report?.contains("- Tests / shard 3, at the step \"Run\": failure https://github.com/job") == true,
           "the report names each failed job, the step it failed at, and where it is")
    expect(report?.contains("- a100000 Newer\n- b000000 Older") == true,
           "the report lists the commits since the last green one, newest first")
}

// MARK: - The monitor

/// Stands in for GitHub: holds each request until the test answers it.
final class FakeGitHub {
    private var pending: [(Result<BranchSnapshot, FetchFailure>) -> Void] = []
    private(set) var asked = 0

    func fetch() -> BranchFetch {
        { _, _, completion in
            self.asked += 1
            self.pending.append(completion)
            // A request made from a timer is not an input source, after
            // which the run loop would return to the test on its own.
            CFRunLoopStop(CFRunLoopGetMain())
        }
    }

    var waiting: Bool { !pending.isEmpty }

    func answer(_ result: Result<BranchSnapshot, FetchFailure>) {
        pending.removeFirst()(result)
    }

    func answer(_ answer: BranchSnapshot) {
        self.answer(.success(answer))
    }
}

/// Stands in for the relay: listens on the loopback interface and sends
/// frames to the last program that connected.
final class FakeRelay {
    private let listener: NWListener
    private(set) var clients: [NWConnection] = []
    private var stopped = false

    init() {
        listener = try! NWListener(using: .tcp, on: .any)
        listener.newConnectionHandler = { [unowned self] connection in
            connection.start(queue: .main)
            clients.append(connection)
        }
        var ready = false
        listener.stateUpdateHandler = { [unowned self] state in
            switch state {
            case .ready: ready = true
            case .cancelled: stopped = true
            default: break
            }
        }
        listener.start(queue: .main)
        waitUntil { ready }
    }

    var address: RelayAddress { RelayAddress(host: "127.0.0.1", port: listener.port!.rawValue) }

    func send(_ requests: Data...) {
        let frames = requests.reduce(Data()) { $0 + frame($1) }
        clients.last!.send(content: frames, completion: .contentProcessed { _ in })
    }

    func dropClient() {
        clients.last!.cancel()
    }

    /// Stops listening, and returns once the port is closed.
    func stop() {
        listener.cancel()
        for client in clients {
            client.cancel()
        }
        waitUntil { stopped }
    }
}

func shownStatus(_ monitor: BranchMonitor) -> BranchStatus? {
    switch monitor.state {
    case .ready(let status): return status
    case .failed(_, let status): return status
    case .unconfigured, .waiting: return nil
    }
}

func isUnavailable(_ state: RelayState) -> Bool {
    if case .unavailable = state { return true }
    return false
}

/// Waits for the monitor to schedule a request, checks that it did not ask
/// GitHub sooner, and asks now in place of the timer.
func pollFor(_ monitor: BranchMonitor, _ github: FakeGitHub, line: Int = #line) {
    waitUntil({ monitor.nextPoll != nil || github.waiting }, line: line)
    expect(!github.waiting, "a delivery soon after a request waits for the polling interval", line: line)
    if !github.waiting {
        monitor.askNow()
    }
}

/// Answers the monitor's polls, asking in place of the timer, until one
/// leaves nothing more to ask.
func settle(_ monitor: BranchMonitor, _ github: FakeGitHub, with answer: BranchSnapshot, line: Int = #line) {
    var polls = 0
    while github.waiting || monitor.nextPoll != nil {
        if !github.waiting {
            monitor.askNow()
        }
        github.answer(answer)
        polls += 1
        expect(polls <= 3, "the polls come to an end", line: line)
        if polls > 3 { return }
    }
}

do {
    let relay = FakeRelay()
    let github = FakeGitHub()
    let monitor = BranchMonitor(makeFetch: github.fetch)

    monitor.configure(repository: repository, branch: "main", refreshSeconds: 3600, relay: relay.address)
    expect(github.asked == 1, "configuring the branch asks GitHub straight away")
    waitUntil { monitor.relayState == .connected && relay.clients.count == 1 }
    github.answer(snapshot())
    expect(!github.waiting && monitor.nextPoll != nil,
           "an answer asked for before the relay connected is followed by a poll")
    monitor.askNow()
    github.answer(snapshot())
    expect(github.asked == 2 && monitor.nextPoll == nil,
           "while the relay is connected, GitHub is not asked again")

    relay.send(request("check_run", checkRunBody(started: 40, completed: 50)))
    waitUntil { shownStatus(monitor)?.health == .failing }
    relay.send(request("check_suite", checkSuiteBody(updated: 60)))
    waitUntil { shownStatus(monitor)?.health == .passing }
    expect(github.asked == 2 && !github.waiting && monitor.nextPoll == nil,
           "the branch turns red and green from deliveries alone")

    // These arrive while GitHub is being asked, and wait for its answer,
    // which is about the commit the push moved the branch away from.
    monitor.askNow()
    relay.send(request("push", pushBody(after: "b2")),
               request("check_run", checkRunBody(sha: "b2", suite: 2, status: "queued", conclusion: nil)),
               Data("not a request".utf8))
    waitUntil { monitor.unreadableDelivery != nil }
    expect(monitor.unreadableDelivery == "A frame from the relay was not an HTTP request",
           "a frame that is not a request is reported")
    github.answer(snapshot())
    expect(shownStatus(monitor)?.sha == "b2" && shownStatus(monitor)?.health == .running,
           "a push held during the request moves the branch after the answer is taken in")
    expect(monitor.nextPoll != nil, "a frame that could not be read is followed by a poll")
    settle(monitor, github, with: snapshot(sha: "b2", [2: nil]))

    // GitHub never delivered the push to c3, but the suite it started says
    // a push went missing.
    relay.send(request("check_suite", checkSuiteBody(sha: "c3", suite: 3, before: "b2")))
    pollFor(monitor, github)
    github.answer(snapshot(sha: "c3", [3: .passing]))
    expect(shownStatus(monitor)?.sha == "c3" && shownStatus(monitor)?.health == .passing,
           "a suite on the branch at a commit it has not been on asks GitHub, which says where the branch is")
    settle(monitor, github, with: snapshot(sha: "c3", [3: .passing]))

    relay.send(request("status", "payload=%7B%7D"))
    waitUntil { monitor.unreadableDelivery?.hasPrefix("A status delivery") == true }
    expect(!github.waiting && monitor.nextPoll != nil,
           "a delivery that cannot be read is checked at the next poll, since it may have mattered")
    settle(monitor, github, with: snapshot(sha: "c3", [3: .passing]))

    // Deliveries go on being followed while GitHub cannot be asked.
    monitor.refreshNow()
    github.answer(.failure(FetchFailure(message: "offline", authenticationFailed: false)))
    expect(monitor.nextPoll != nil, "GitHub is asked again later when it could not be asked")
    relay.send(request("check_run", checkRunBody(sha: "c3", suite: 3, started: 70, completed: 80)))
    waitUntil { shownStatus(monitor)?.health == .failing }
    expect(!github.waiting, "a delivery is followed without asking GitHub")
    settle(monitor, github, with: snapshot(sha: "c3", [3: .passing]))
    expect(shownStatus(monitor)?.health == .failing, "an answer older than a delivery does not undo it")
    monitor.refreshNow()
    github.answer(snapshot(sha: "c3", [3: .passing]))
    expect(shownStatus(monitor)?.health == .passing && monitor.nextPoll != nil,
           "Refresh Now believes GitHub's answer, and checks it once more")
    settle(monitor, github, with: snapshot(sha: "c3", [3: .passing]))

    // Changing the branch keeps the connection to the relay.
    monitor.configure(repository: repository, branch: "release", refreshSeconds: 3600, relay: relay.address)
    expect(github.waiting && monitor.relayState == .connected, "a new branch asks GitHub over the same connection")
    github.answer(snapshot(sha: "r1"))
    expect(relay.clients.count == 1 && monitor.nextPoll == nil,
           "an answer for a new branch, asked over the connection, needs no polling")
    monitor.configure(repository: repository, branch: "main", refreshSeconds: 3600, relay: relay.address)
    github.answer(snapshot(sha: "c3"))

    relay.dropClient()
    waitUntil { isUnavailable(monitor.relayState) }
    expect(!github.waiting && monitor.nextPoll != nil,
           "losing the relay asks GitHub, as soon as the polling interval allows")
    monitor.askNow()
    github.answer(snapshot(sha: "c3"))
    expect(monitor.nextPoll != nil, "without the relay, GitHub is polled")
    expect(relay.clients.count == 1, "the relay is not tried again until the next poll")

    // A relay that accepts a connection and then drops it costs no more
    // requests than polling.
    let asked = github.asked
    monitor.refreshNow()
    waitUntil { monitor.relayState == .connected && relay.clients.count == 2 }
    relay.dropClient()
    waitUntil { isUnavailable(monitor.relayState) }
    github.answer(snapshot(sha: "c3"))
    expect(github.asked == asked + 1 && !github.waiting && monitor.nextPoll != nil,
           "a connection that comes and goes between polls adds no request")

    monitor.refreshNow()
    waitUntil { monitor.relayState == .connected && relay.clients.count == 3 }
    settle(monitor, github, with: snapshot(sha: "c3"))
    expect(monitor.unreadableDelivery == nil, "a new connection starts with no complaint")

    monitor.woke()
    expect(monitor.relayState == .connecting && github.waiting, "waking connects again and asks GitHub")
    waitUntil { monitor.relayState == .connected && relay.clients.count == 4 }
    settle(monitor, github, with: snapshot(sha: "c3"))

    // Refresh Now during a request asks again, even if that request fails.
    monitor.refreshNow()
    monitor.refreshNow()
    github.answer(.failure(FetchFailure(message: "offline", authenticationFailed: false)))
    expect(github.waiting, "a refresh asked for during a request is not dropped")
    settle(monitor, github, with: snapshot(sha: "c3"))

    // Refresh Now believes the commit GitHub names over one a push named.
    relay.send(request("push", pushBody(before: "c3", after: "d4")))
    waitUntil { shownStatus(monitor)?.sha == "d4" }
    monitor.refreshNow()
    github.answer(snapshot(sha: "c3"))
    expect(shownStatus(monitor)?.sha == "c3", "Refresh Now takes the branch to the commit GitHub names")
    settle(monitor, github, with: snapshot(sha: "c3"))

    monitor.configure(repository: nil, branch: "main", refreshSeconds: 3600, relay: relay.address)
    expect(monitor.relayState == .off, "with no repository, the relay is not listened to")
    relay.stop()
}

do {
    // With an interval of no time at all, a delivery that asks GitHub asks
    // straight away.
    let relay = FakeRelay()
    let github = FakeGitHub()
    let monitor = BranchMonitor(makeFetch: github.fetch)
    monitor.configure(repository: repository, branch: "main", refreshSeconds: 0, relay: relay.address)
    waitUntil { monitor.relayState == .connected && relay.clients.count == 1 }
    github.answer(snapshot())
    waitUntil { github.waiting }
    github.answer(snapshot())
    expect(monitor.nextPoll == nil, "the answer is settled")
    let asked = github.asked
    relay.send(request("push", pushBody(before: "zz", after: "c3")))
    waitUntil { github.waiting }
    expect(github.asked == asked + 1, "a delivery once the interval has passed asks GitHub straight away")
    github.answer(snapshot(sha: "c3"))
    expect(shownStatus(monitor)?.sha == "c3", "and GitHub's answer says where the branch is")
    relay.dropClient()
    waitUntil { relay.clients.count == 2 }
    expect(github.waiting, "the first request after the relay drops also tries the relay again")
    relay.stop()
}

do {
    // Nothing listens on the port a stopped relay listened on.
    let gone = FakeRelay()
    let address = gone.address
    gone.stop()
    let github = FakeGitHub()
    let monitor = BranchMonitor(makeFetch: github.fetch)
    monitor.configure(repository: repository, branch: "main", refreshSeconds: 3600, relay: address)
    waitUntil { isUnavailable(monitor.relayState) }
    github.answer(snapshot())
    expect(github.asked == 1, "a relay that never connected missed nothing, so GitHub is asked once")
    expect(monitor.nextPoll != nil, "without the relay, GitHub is polled")
}

do {
    let github = FakeGitHub()
    let monitor = BranchMonitor(makeFetch: github.fetch)
    monitor.configure(repository: repository, branch: "main", refreshSeconds: 3600, relay: nil)
    github.answer(snapshot())
    expect(monitor.relayState == .off && monitor.nextPoll != nil, "with no relay chosen, GitHub is polled")
    monitor.askNow()
    github.answer(snapshot([1: .failing]))
    monitor.askNow()
    github.answer(snapshot([2: .passing]))
    expect(shownStatus(monitor)?.health == .passing,
           "each poll replaces what is known, so a workflow run that was deleted stops counting")
    monitor.refreshNow()
    monitor.refreshNow()
    expect(github.asked == 4, "asking again while GitHub is being asked waits for the answer")
    github.answer(snapshot())
    expect(github.asked == 5, "and then asks once more")
    github.answer(snapshot())

    monitor.configure(repository: repository, branch: "main", refreshSeconds: 0.01, relay: nil)
    github.answer(snapshot())
    waitUntil { github.waiting }
    expect(github.asked == 7, "the poll timer asks GitHub again")
    github.answer(snapshot())
}

print(failures == 0 ? "All tests passed." : "\(failures) failed.")
exit(failures == 0 ? 0 : 1)
