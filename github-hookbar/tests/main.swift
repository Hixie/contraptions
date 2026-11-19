import Foundation
import Network

// Tests for the parts of github-hookbar that do not need the menu bar. Run
// them with `make test`.

var failures = 0

func expect(_ condition: Bool, _ message: String, line: Int = #line) {
    if !condition {
        failures += 1
        fputs("FAIL line \(line): \(message)\n", stderr)
    }
}

// MARK: - When GitHub made a delivery

// A delivery ID from GitHub, and the time Python's uuid module reads from it.
let made = creationTime(ofDelivery: "109f6680-cd6c-11f1-9e6d-f689524137cb")
expect(made.map { abs($0.timeIntervalSince1970 - 1_792_599_944.424) < 0.001 } == true,
       "a GitHub delivery ID gives the time it was made: \(String(describing: made))")

// Differing only in the version digit, so the same bytes cannot be read as a
// time.
expect(creationTime(ofDelivery: "109f6680-cd6c-41f1-9e6d-f689524137cb") == nil,
       "a random (version 4) UUID gives no time")
expect(creationTime(ofDelivery: "109f6680-cd6c-11f1-1e6d-f689524137cb") == nil,
       "a version 1 UUID of a variant other than RFC 4122's gives no time")
expect(creationTime(ofDelivery: "not a uuid") == nil, "text that is not a UUID gives no time")

// The smallest version 1 UUID is the start of the Gregorian calendar.
let epoch = Date(timeIntervalSince1970: -12_219_292_800)
let zero = creationTime(ofDelivery: "00000000-0000-1000-8000-000000000000")
expect(zero == epoch, "the zero time is 15 October 1582: \(String(describing: zero))")

// Each field of the time lands in its place: a time_low of 1 is one tick,
// a time_mid of 1 is 2^32 ticks, and a time_hi of 1 is 2^48 ticks.
for (id, ticks) in [("00000001-0000-1000-8000-000000000000", 1.0),
                    ("00000000-0001-1000-8000-000000000000", 4_294_967_296.0),
                    ("00000000-0000-1001-8000-000000000000", 281_474_976_710_656.0)] {
    let time = creationTime(ofDelivery: id)
    expect(time.map { abs($0.timeIntervalSince(epoch) - ticks / 10_000_000) < 1e-6 } == true,
           "\(id) is \(ticks) ticks after the zero time: \(String(describing: time))")
}

// MARK: - Which deliveries are late

expect(!isLate(0.3), "a delivery that took 0.3 seconds is not late")
expect(!isLate(4.9), "a delivery that took 4.9 seconds is not late")
expect(!isLate(5), "a delivery that took exactly 5 seconds is not late")
expect(isLate(5.1), "a delivery that took 5.1 seconds is late")
expect(isLate(955), "a delivery that took 955 seconds is late")

// MARK: - How the indicator dims

/// Delivers to the dimming at each time in turn, and checks what the last
/// delivery returns.
func expectDimming(deliveries: [TimeInterval], _ expected: [(Float, TimeInterval)],
                   _ message: String, line: Int = #line) {
    var dimming = Dimming()
    var steps: [(opacity: Float, time: TimeInterval)] = []
    for time in deliveries {
        steps = dimming.deliver(at: time)
    }
    expect(steps.count == expected.count && zip(steps, expected).allSatisfy {
        abs($0.opacity - $1.0) < 1e-5 && abs($0.time - $1.1) < 1e-6
    }, "\(message): \(steps)", line: line)
}

expectDimming(deliveries: [10], [(1, 0), (0.6, 0.25), (0.6, 0.25), (1, 0.5)],
              "at full opacity, it fades down and straight back up")
expectDimming(deliveries: [10, 11], [(1, 0), (0.6, 0.25), (0.6, 0.25), (1, 0.5)],
              "once it is back to full opacity, it starts afresh")
expectDimming(deliveries: [0, 0.1], [(0.84, 0), (0.6, 0.15), (0.6, 0.25), (1, 0.5)],
              "fading down, it carries on and resets the clock")
expectDimming(deliveries: [0, 0.1, 0.3], [(0.6, 0), (0.6, 0), (0.6, 0.25), (1, 0.5)],
              "dimmed, it stays dimmed and resets the clock")
expectDimming(deliveries: [0, 0.4], [(0.84, 0), (0.6, 0.15), (0.6, 0.15), (1, 0.4)],
              "fading up, it fades down again without resetting the clock")
expectDimming(deliveries: [0, 0.4, 0.5], [(0.68, 0), (0.6, 0.05), (0.6, 0.25), (1, 0.5)],
              "fading down after turning round, it resets the clock")

// MARK: - The pull request list

let first = TrackedPullRequest(repository: "octo/one", branch: "fix-bug", number: 12)
let second = TrackedPullRequest(repository: "octo/two", branch: "fix-bug", number: 12)
expect(first.line == "octo/one\tfix-bug\t12", "a line is tab-separated: \(first.line)")
expect(TrackedPullRequest(line: first.line[...]) == first, "a line reads back as what wrote it")
for line in ["octo/one\tfix-bug", "octo/one\tfix-bug\t12\textra", "octo/one\tfix-bug\ttwelve",
             "octo/one\tfix-bug\t0", "\tfix-bug\t12", "octo/one\t\t12", ""] {
    expect(TrackedPullRequest(line: line[...]) == nil, "\(line.debugDescription) is not a line")
}

let renamed = TrackedPullRequest(repository: "octo/one", branch: "renamed", number: 12)
let listed = ["# a comment", second.line, renamed.line, "octo/one\tother\t13"]
expect(PullRequestListChange.add(first).applied(to: []) == [first.line], "adding to nothing")
expect(PullRequestListChange.add(first).applied(to: listed)
       == ["# a comment", second.line, "octo/one\tother\t13", first.line],
       "adding replaces only the line with the same repository and number")
expect(PullRequestListChange.remove(first).applied(to: listed)
       == ["# a comment", second.line, "octo/one\tother\t13"],
       "removing takes out only the line with the same repository and number, whatever its branch")
expect(PullRequestListChange.remove(first).applied(to: [second.line]) == [second.line],
       "removing a pull request that is not listed changes nothing")

expect(openPullRequestSearch(authors: [], owners: []) == nil,
       "accepting every author and owner leaves nothing to search for")
expect(openPullRequestSearch(authors: ["octocat", "hubot"], owners: [])
       == "is:pr is:open author:octocat author:hubot", "searching by author")
expect(openPullRequestSearch(authors: ["octocat"], owners: ["github", "octo"])
       == "is:pr is:open author:octocat user:github user:octo", "searching by author and owner")

expect((try? trackedPullRequests(fromSearchOutput: "")) == [], "a search can find nothing")
expect((try? trackedPullRequests(fromSearchOutput: "\(first.line)\n\(second.line)\n"))
       == [first, second], "each line of the search's output is a pull request")
expect((try? trackedPullRequests(fromSearchOutput: "\(first.line)\nnot a pull request\n")) == nil,
       "a line of the search's output that is not a pull request fails the whole search")

/// A directory of its own for each test that writes files.
func temporaryDirectory() -> String {
    var template = Array("\(NSTemporaryDirectory())github-hookbar-tests.XXXXXX".utf8CString)
    return String(cString: mkdtemp(&template))
}

func contents(_ path: String) -> String? {
    try? String(contentsOfFile: path, encoding: .utf8)
}

/// Whether another holder could take a shared lock on the file right now, as
/// a reader using `flock -s` does.
func canReadLock(_ path: String, line: Int = #line) -> Bool {
    let descriptor = open(path, O_RDONLY)
    expect(descriptor >= 0, "\(path) opens", line: line)
    defer { close(descriptor) }
    return flock(descriptor, LOCK_SH | LOCK_NB) == 0
}

let directory = temporaryDirectory()
let listPath = directory + "/cache/github-prs.tsv"
var linesSeen: [String]?
var lockedWhileWriting: Bool?
do {
    try rewriteLines(ofFileAt: listPath) { read in
        linesSeen = try read()
        lockedWhileWriting = !canReadLock(listPath)
        return [first.line, second.line]
    }
} catch {
    expect(false, "rewriting a file that does not exist yet: \(error)")
}
expect(linesSeen == [], "a file that does not exist yet has no lines")
expect(lockedWhileWriting == true, "readers cannot take a shared lock while the file changes")
expect(canReadLock(listPath), "readers can take a shared lock once the file has changed")
expect(contents(listPath) == "\(first.line)\n\(second.line)\n",
       "the file and its directory are made: \(String(describing: contents(listPath)))")

try? rewriteLines(ofFileAt: listPath) { read in
    linesSeen = try read()
    return [second.line]
}
expect(linesSeen == [first.line, second.line], "rewriting sees the lines as they were")
expect(contents(listPath) == "\(second.line)\n", "a shorter list leaves nothing of the longer one")
try? rewriteLines(ofFileAt: listPath) { _ in [first.line, second.line, "# a comment"] }
expect(contents(listPath) == "\(first.line)\n\(second.line)\n# a comment\n",
       "a longer list replaces the shorter one")

FileManager.default.createFile(atPath: listPath, contents: Data([0xff, 0x0a]))
expect((try? rewriteLines(ofFileAt: listPath) { read in try read() }) == nil,
       "a file that is not UTF-8 cannot be read")
expect(FileManager.default.contents(atPath: listPath) == Data([0xff, 0x0a]),
       "a file that cannot be read is left as it was")
expect((try? rewriteLines(ofFileAt: listPath) { _ in [first.line] }) != nil
       && contents(listPath) == "\(first.line)\n",
       "a file that is not UTF-8 can be replaced without reading it")

/// Runs the main run loop until `body` calls the function it is given.
func waitForMain(_ body: (@escaping () -> Void) -> Void) {
    var finished = false
    body {
        finished = true
        CFRunLoopStop(CFRunLoopGetMain())
    }
    while !finished {
        CFRunLoopRun()
    }
}

// A refresh replaces the list, but makes again the changes made while it
// looked, and those made shortly before it started, which GitHub's search may
// not show yet.
let third = TrackedPullRequest(repository: "octo/three", branch: "new", number: 3)
let fourth = TrackedPullRequest(repository: "octo/four", branch: "late", number: 4)
let stale = TrackedPullRequest(repository: "octo/stale", branch: "old", number: 9)
let refreshPath = temporaryDirectory() + "/github-prs.tsv"
FileManager.default.createFile(atPath: refreshPath, contents: Data("\(first.line)\n".utf8))
let list = PullRequestList(path: refreshPath)
let start = Date()
list.make(.add(stale), at: start - searchDelay - 1) { _ in }
list.make(.add(fourth), at: start - searchDelay + 1) { _ in }
var refreshProblem: Error?
waitForMain { done in
    list.refresh(at: start, { found in
        expect(list.isRefreshing, "the list says while it is refreshing")
        list.make(.add(third), at: start + 1) { _ in }
        list.make(.remove(first), at: start + 2) { _ in }
        found(.success([first, second]))
        expect(list.isRefreshing, "the list says it is refreshing until it has written the list")
    }) { error in
        refreshProblem = error
        done()
    }
}
expect(refreshProblem == nil, "the refresh succeeds: \(String(describing: refreshProblem))")
expect(!list.isRefreshing, "the list says when it has finished refreshing")
expect(contents(refreshPath) == "\(second.line)\n\(fourth.line)\n\(third.line)\n",
       "the refresh replaces the list, and then makes the recent changes again: "
       + String(describing: contents(refreshPath)))

waitForMain { done in
    list.refresh({ $0(.failure(Problem(description: "no network"))) }) { error in
        refreshProblem = error
        done()
    }
}
expect(refreshProblem.map { "\($0)" } == "no network", "a refresh can fail")
expect(contents(refreshPath) == "\(second.line)\n\(fourth.line)\n\(third.line)\n",
       "a refresh that fails leaves the list as it was")

// A refresh replaces one that is still looking, which neither changes the list
// nor calls back.
var abandonedLookUp: ((Result<[TrackedPullRequest], Error>) -> Void)?
var abandonedCalledBack = false
list.refresh(at: start + 9, { abandonedLookUp = $0 }) { _ in abandonedCalledBack = true }
waitForMain { done in
    list.refresh(at: start + 10, { $0(.success([second])) }) { error in
        refreshProblem = error
        done()
    }
}
abandonedLookUp?(.success([stale]))
// Changes are written in order, so this one comes after anything the earlier
// refresh might have written.
waitForMain { done in
    list.make(.remove(first), at: start + 11) { _ in done() }
}
expect(refreshProblem == nil, "the later refresh succeeds: \(String(describing: refreshProblem))")
expect(!list.isRefreshing, "the later refresh finishing ends the refreshing")
expect(!abandonedCalledBack, "the earlier refresh does not call back")
expect(contents(refreshPath) == "\(second.line)\n\(third.line)\n",
       "the earlier refresh does not change the list: " + String(describing: contents(refreshPath)))

// An earlier refresh that finishes writing while a later one looks leaves the
// list refreshing.
var laterLookUp: ((Result<[TrackedPullRequest], Error>) -> Void)?
waitForMain { done in
    list.refresh(at: start + 12, { $0(.success([second, third])) }) { _ in done() }
    list.refresh(at: start + 13, { laterLookUp = $0 }) { _ in }
}
expect(list.isRefreshing, "the later refresh is still running")
waitForMain { done in
    laterLookUp?(.failure(Problem(description: "no network")))
    DispatchQueue.main.async(execute: done)
}
expect(!list.isRefreshing, "the later refresh failing ends the refreshing")

// MARK: - The search script

/// Runs a script that writes what gh finds to the file named by its first
/// argument, with a fake gh that records its arguments and answers with
/// `response` filtered through the jq program it is given, and returns the
/// script's exit status, what it wrote to its output file, and the arguments
/// gh was given.
func runGitHubScript(_ script: String, _ arguments: [String],
                     response: String) -> (Int32, String?, [String]) {
    let directory = temporaryDirectory()
    let fake = directory + "/gh"
    FileManager.default.createFile(atPath: fake, contents: Data("""
        #!/bin/sh
        printf '%s\\n' "$@" > "$FAKE_GH_ARGUMENTS"
        while [ $# -gt 0 ]; do [ "$1" = --jq ] && filter=$2; shift; done
        if [ -n "$filter" ]; then exec jq -r "$filter" "$FAKE_GH_RESPONSE"; fi
        cat "$FAKE_GH_RESPONSE"
        ! grep -q '"errors"' "$FAKE_GH_RESPONSE"
        """.utf8), attributes: [.posixPermissions: 0o755])
    FileManager.default.createFile(atPath: directory + "/response.json",
                                   contents: Data(response.utf8))
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/sh")
    process.arguments = ["-c", script, "sh", directory + "/output.tsv"] + arguments
    process.environment = ["PATH": "\(directory):/usr/bin:/bin",
                           "FAKE_GH_ARGUMENTS": directory + "/arguments.txt",
                           "FAKE_GH_RESPONSE": directory + "/response.json"]
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
    } catch {
        expect(false, "could not run the script: \(error)")
        return (-1, nil, [])
    }
    process.waitUntilExit()
    return (process.terminationStatus, contents(directory + "/output.tsv"),
            contents(directory + "/arguments.txt")?.components(separatedBy: "\n") ?? [])
}

func searchResponse(count: Int, _ nodes: String) -> String {
    #"{"data": {"search": {"issueCount": \#(count), "nodes": [\#(nodes)]}}}"#
}

let awkwardSearch = #"is:pr author:o'brien $(touch nowhere) "quoted""#
let (searchStatus, searchOutput, searchArguments) = runGitHubScript(searchScript, [awkwardSearch], response:
    searchResponse(count: 2, """
        {"number": 12, "headRefName": "fix-bug", "repository": {"nameWithOwner": "octo/one"}},
        {"number": 3, "headRefName": "new", "repository": {"nameWithOwner": "octo/three"}}
        """))
expect(searchStatus == 0, "the search script succeeds: \(searchStatus)")
expect(searchArguments.contains("q=\(awkwardSearch)"),
       "the search reaches gh as it was given: \(searchArguments)")
expect(searchArguments.prefix(3) == ["api", "graphql", "--paginate"],
       "the script pages through the search's results: \(searchArguments)")
expect(searchOutput.map { try? trackedPullRequests(fromSearchOutput: $0) } == [first, third],
       "the script writes each pull request as a line: \(String(describing: searchOutput))")

let (tooManyStatus, _, _) = runGitHubScript(searchScript, ["is:pr"],
                                            response: searchResponse(count: 1001, ""))
expect(tooManyStatus != 0, "the script fails when the search finds more than GitHub returns")
let (enoughStatus, enoughOutput, _) = runGitHubScript(searchScript, ["is:pr"],
                                                      response: searchResponse(count: 1000, ""))
expect(enoughStatus == 0 && enoughOutput == "",
       "the script succeeds when the search finds as many as GitHub returns")

// MARK: - Configuration

func configuration(_ text: String) -> Result<Configuration, Error> {
    Result { try Configuration(INIFile(text)) }
}

func problem(_ text: String) -> String? {
    if case .failure(let error) = configuration(text) {
        return "\(error)"
    }
    return nil
}

let example = """
    # A comment.
    [relay]
    address = relay.example.com:8081

    [github]
    authors = OctoCat, hubot
    orgs = Octo
    ; Another comment.

    [command]
    run = open -a Safari
    """
if case .success(let parsed) = configuration(example) {
    expect(parsed.host == "relay.example.com" && parsed.port == 8081, "the relay's address")
    expect(parsed.authors == ["octocat", "hubot"], "authors are lowercase: \(parsed.authors)")
    expect(parsed.orgs == ["octo"], "owners are lowercase: \(parsed.orgs)")
    expect(parsed.actions == ["opened"], "the actions default to opened: \(parsed.actions)")
    expect(parsed.command == "open -a Safari", "the command: \(parsed.command)")
} else {
    expect(false, "the example configuration is read: \(String(describing: problem(example)))")
}
let minimal = "[relay]\naddress = [::1]:8081\n[command]\nrun = open\n"
if case .success(let parsed) = configuration(minimal) {
    expect(parsed.host == "::1" && parsed.relay == "::1:8081", "an IPv6 relay: \(parsed.host)")
    expect(parsed.authors.isEmpty && parsed.orgs.isEmpty, "no authors or owners accepts anyone")
    expect(parsed.stream == nil, "no event stream")
} else {
    expect(false, "a minimal configuration is read: \(String(describing: problem(minimal)))")
}
for (text, expected) in [
    ("[command]\nrun = open\n", "relay.address is not set"),
    ("[relay]\naddress = relay.example.com:8081\n", "command.run is not set"),
    ("[relay]\naddress = relay.example.com\n[command]\nrun = open\n",
     "the relay address relay.example.com is not host:port"),
    ("[relay]\naddress = ::1:8081\n[command]\nrun = open\n",
     "the relay address ::1:8081 is not host:port"),
    (minimal + "[github]\nauthor = octocat\n", "github.author is not a setting github-hookbar knows"),
    (minimal + "[command]\nrun = open\n", "command.run is set more than once"),
    (minimal + "authors\n", "a line is not \"key = value\": authors"),
    (minimal + "[stream]\npath = a.b\n", "stream.path is set, but stream.url is not"),
    (minimal + "[stream]\nfile = /tmp/x\n", "stream.file is set, but stream.url is not"),
    (minimal + "[stream]\nurl = ftp://example.com/\npath = a\nfile = /x\n",
     "stream.url is not an http or https URL"),
    (minimal + "[stream]\nurl = https:events\npath = a\nfile = /x\n",
     "stream.url is not an http or https URL"),
    (minimal + "[stream]\nurl = https://example.com/\nfile = /x\n", "stream.path is not set"),
    (minimal + "[stream]\nurl = https://example.com/\npath = a..b\nfile = /x\n",
     "the path a..b has an empty key"),
    (minimal + "[stream]\nurl = https://example.com/\npath = a\n", "stream.file is not set"),
    (minimal + "[stream]\nurl = https://example.com/\npath = a\nfile = x.txt\n",
     "the value file x.txt is not an absolute path"),
] {
    expect(problem(text) == expected, "\(expected): \(String(describing: problem(text)))")
}
let withStream = minimal
    + "[stream]\nurl = HTTPS://example.com/events\npath = message.text\nfile = ~/a/b.txt\n"
expect(StreamSettings(url: URL(string: "https://me:secret@example.com:8/events?token=t#top")!,
                      path: [], file: "/").publicURL == "https://example.com:8/events#top",
       "the URL shown leaves out the user name, password and query")
if case .success(let parsed) = configuration(withStream) {
    expect(parsed.stream == StreamSettings(url: URL(string: "HTTPS://example.com/events")!,
                                           path: ["message", "text"],
                                           file: NSHomeDirectory() + "/a/b.txt"),
           "an event stream: \(String(describing: parsed.stream))")
} else {
    expect(false, "a stream is read: \(String(describing: problem(withStream)))")
}

/// What setting a setting does to INI text.
func setting(_ name: String, to value: String, in text: String) -> String? {
    guard var file = try? INIFile(text) else { return nil }
    try? file.set(name, to: value)
    return file.text
}

expect(setting("relay.address", to: "other:1", in: example)
       == example.replacingOccurrences(of: "relay.example.com:8081", with: "other:1") + "\n",
       "changing a setting changes only its line")
expect(setting("github.actions", to: "opened, closed", in: example)
       == example.replacingOccurrences(of: "orgs = Octo\n", with: "orgs = Octo\nactions = opened, closed\n")
       + "\n", "a new setting goes after the last one in its section")
expect(setting("github.authors", to: "", in: "[relay]\r\naddress = a:1\r\n")
       == "[relay]\r\naddress = a:1\r\n\r\n[github]\r\nauthors =\r\n",
       "a new section goes at the end, with the text's line endings")

// MARK: - Pull request deliveries

/// A pull_request delivery's body, for a pull request octo/one#12 from the
/// branch fix-bug.
func event(_ action: String, author: String = "OctoCat", owner: String = "Octo",
           state: String = "open") -> PullRequestEvent? {
    try? PullRequestEvent(Data(#"""
        {"action": "\#(action)", "repository": {"full_name": "\#(owner)/one", "owner": {"login": "\#(owner)"}},
         "pull_request": {"html_url": "https://github.com/\#(owner)/one/pull/12", "number": 12,
         "title": "Fix the bug", "user": {"login": "\#(author)"}, "head": {"ref": "fix-bug"},
         "state": "\#(state)"}}
        """#.utf8))
}

guard case .success(let chosen) = configuration(
    "[relay]\naddress = a:1\n[github]\nauthors = octocat\norgs = octo\n[command]\nrun = open\n"),
    case .success(let alsoClosed) = configuration(
    "[relay]\naddress = a:1\n[github]\nactions = opened, closed\n[command]\nrun = open\n") else {
    fatalError("the configurations for the delivery tests are not read")
}
let octoOne = TrackedPullRequest(repository: "Octo/one", branch: "fix-bug", number: 12)
let opened = event("opened")
expect(opened?.pullRequest.htmlUrl.absoluteString == "https://github.com/Octo/one/pull/12",
       "a delivery is read")
expect(opened?.matches(chosen) == true, "opening a chosen pull request runs the command")
expect(opened?.listChange(chosen) == .add(octoOne), "opening a chosen pull request lists it")
expect(event("OPENED")?.matches(chosen) == true, "actions match whatever their case")
for (other, message) in [(event("opened", author: "hubot"), "another author"),
                         (event("opened", owner: "elsewhere"), "another owner")] {
    expect(other?.matches(chosen) == false, "\(message)'s pull request does not run the command")
    expect(other.map { $0.listChange(chosen) == nil } == true,
           "\(message)'s pull request is not listed")
}
let pushed = event("synchronize")
expect(pushed?.matches(chosen) == false, "an action the configuration does not choose does not run the command")
expect(pushed?.listChange(chosen) == .add(octoOne),
       "any delivery about an open tracked pull request lists it")
expect(event("reopened")?.listChange(chosen) == .add(octoOne), "reopening lists it again")
let closed = event("closed", state: "closed")
expect(closed?.listChange(chosen) == .remove(octoOne), "closing a tracked pull request unlists it")
expect(event("edited", state: "closed")?.listChange(chosen) == .remove(octoOne),
       "any delivery about a closed tracked pull request unlists it")
expect(closed?.matches(chosen) == false, "closing does not run the command for opened")
expect(closed?.matches(alsoClosed) == true && closed?.listChange(alsoClosed) == .remove(octoOne),
       "running the command for a closed pull request still unlists it")
expect(event("closed", author: "hubot", state: "closed")
       .map { $0.listChange(chosen) == nil } == true,
       "closing another author's pull request does not change the list")
expect((try? PullRequestEvent(Data(#"{"action": "opened"}"#.utf8))) == nil,
       "a delivery without a pull request is not read")

// MARK: - Catching up after reconnecting

expect(openedPullRequestsQuery(authors: [], owners: []) == nil,
       "accepting every author and owner leaves nothing to look for")
let authorsQuery = openedPullRequestsQuery(authors: ["octocat", #"o"brien"#], owners: ["octo"])
expect(authorsQuery?.contains(#"author0: user(login: "octocat") { pullRequests(first: 100, "#
                              + "states: OPEN, orderBy: {field: CREATED_AT, direction: DESC})") == true
       && authorsQuery?.contains(#"author1: user(login: "o\"brien")"#) == true
       && authorsQuery?.contains("search") == false,
       "each author's open pull requests are listed, newest first: \(String(describing: authorsQuery))")
let ownersQuery = openedPullRequestsQuery(authors: [], owners: ["octo", "github"])
expect(ownersQuery?.contains(#"owners: search(query: "is:pr is:open user:octo user:github "#
                             + #"sort:created-desc", type: ISSUE, first: 100)"#) == true
       && ownersQuery?.contains("user(") == false,
       "without authors, the owners' repositories are searched: \(String(describing: ownersQuery))")

/// A pull request as `openedPullRequestsQuery` describes it.
func pullRequestNode(_ number: Int, made: String, author: String? = "OctoCat") -> String {
    #"{"url": "https://github.com/Octo/one/pull/\#(number)", "number": \#(number), "#
        + #""title": "Number \#(number)", "createdAt": "\#(made)", "headRefName": "branch\#(number)", "#
        + #""author": \#(author.map { #"{"login": "\#($0)"}"# } ?? "null"), "#
        + #""repository": {"nameWithOwner": "Octo/one", "owner": {"login": "Octo"}}}"#
}

// 1,800,000,000 seconds after 1970 is 2027-01-15T08:00:00Z.
let catchUpSince = Date(timeIntervalSince1970: 1_800_000_000)
let catchUpFilter = openedPullRequestsFilter(since: catchUpSince)
let (catchUpStatus, catchUpOutput, catchUpArguments) = runGitHubScript(
    catchUpScript, ["query { the query }", catchUpFilter], response: """
        {"data": {"author0": {"pullRequests": {"nodes": [
                    \(pullRequestNode(7, made: "2027-01-15T09:00:00Z")),
                    \(pullRequestNode(6, made: "2027-01-15T08:00:00Z")),
                    \(pullRequestNode(5, made: "2027-01-15T07:59:59Z"))]}},
                  "author1": null,
                  "owners": {"nodes": [\(pullRequestNode(8, made: "2027-01-16T00:00:00Z", author: nil))]}},
         "errors": [{"type": "NOT_FOUND", "path": ["author1"], "message": "no such user"}]}
        """)
expect(catchUpStatus == 0,
       "the catch-up script succeeds, though GitHub does not know an author: \(catchUpStatus)")
expect(catchUpArguments == ["api", "graphql", "-f", "query=query { the query }", ""],
       "the query reaches gh as it was given: \(catchUpArguments)")
let caughtUp = catchUpOutput.flatMap { try? openedEvents(fromCatchUpOutput: $0) } ?? []
expect(caughtUp.map(\.pullRequest.number) == [7, 6, 8],
       "the pull requests made since the time are found, from every list: \(caughtUp.map(\.pullRequest.number))")
if let seven = caughtUp.first {
    expect(seven.action == "opened" && seven.pullRequest.htmlUrl.absoluteString
           == "https://github.com/Octo/one/pull/7" && seven.pullRequest.title == "Number 7"
           && seven.pullRequest.state == "open",
           "each is the delivery GitHub sent when it was opened")
    expect(seven.matches(chosen) && seven.listChange(chosen)
           == .add(TrackedPullRequest(repository: "Octo/one", branch: "branch7", number: 7)),
           "a pull request found after reconnecting runs the command and is listed")
}
expect(caughtUp.last?.pullRequest.user.login == "ghost" && caughtUp.last?.matches(chosen) == false,
       "a pull request whose author's account is gone is the ghost's, which is not chosen")
expect((try? openedEvents(fromCatchUpOutput: "{}\n")) == nil,
       "output that is not a delivery fails the whole catch-up")

let fullList = (0..<catchUpLimit).map { pullRequestNode($0, made: "2027-01-15T08:00:00Z") }
let (fullStatus, _, _) = runGitHubScript(catchUpScript, ["", catchUpFilter], response:
    #"{"data": {"owners": {"nodes": [\#(fullList.joined(separator: ","))]}}}"#)
expect(fullStatus != 0, "the script fails when every pull request in a full list is new")
let oldestOutside = fullList.dropLast() + [pullRequestNode(99, made: "2027-01-15T07:00:00Z")]
let (outsideStatus, outsideOutput, _) = runGitHubScript(catchUpScript, ["", catchUpFilter], response:
    #"{"data": {"owners": {"nodes": [\#(oldestOutside.joined(separator: ","))]}}}"#)
expect(outsideStatus == 0
       && outsideOutput.map { $0.split(separator: "\n").count } == catchUpLimit - 1,
       "a full list reaching back before the time holds all that is new")
for (response, message) in [
    (#"{"data": {"owners": null}, "errors": [{"type": "RATE_LIMITED", "message": "slow down"}]}"#,
     "GitHub answers with an error other than not knowing an author"),
    (#"{"errors": [{"message": "Bad credentials"}]}"#, "GitHub answers without data"),
    ("", "gh gets no answer"),
] {
    let (status, _, _) = runGitHubScript(catchUpScript, ["", catchUpFilter], response: response)
    expect(status != 0, "the catch-up script fails when \(message): \(status)")
}

let firstConnection = Date(timeIntervalSince1970: 1_000_000)
var missed = MissedDeliveries()
expect(missed.changed(to: .connecting) == nil
       && missed.changed(to: .disconnected(reason: "down", retryAt: nil)) == nil
       && missed.changed(to: .connected(since: firstConnection)) == nil,
       "the first connection has nothing to catch up on")
missed.heard(at: firstConnection + 3600)
expect(missed.changed(to: .disconnected(reason: "timed out", retryAt: nil)) == nil
       && missed.changed(to: .connecting) == nil,
       "losing the connection has nothing to catch up on yet")
expect(missed.changed(to: .connected(since: firstConnection + 4000))
       == firstConnection + 3600 - catchUpMargin,
       "connecting again catches up from shortly before the relay was last heard from")
_ = missed.changed(to: .connecting)
missed.notLooked(from: firstConnection + 3000)
expect(missed.changed(to: .connected(since: firstConnection + 5000)) == firstConnection + 3000,
       "a catch-up that failed is tried again from where it started, when that is earlier")
_ = missed.changed(to: .disconnected(reason: "down", retryAt: nil))
missed.notLooked(from: firstConnection + 9000)
expect(missed.changed(to: .connected(since: firstConnection + 6000))
       == firstConnection + 5000 - catchUpMargin,
       "a catch-up that failed does not move a later one's start later")
missed.heard(at: firstConnection + 7000)
expect(missed.changed(to: .connecting) == nil
       && missed.changed(to: .connected(since: firstConnection + 7001)) == nil,
       "opening a new connection while connected, as new settings do, catches up on nothing")
var missedEarly = MissedDeliveries()
_ = missedEarly.changed(to: .connected(since: firstConnection))
_ = missedEarly.changed(to: .disconnected(reason: "down", retryAt: nil))
expect(missedEarly.changed(to: .connected(since: firstConnection + 10)) == firstConnection,
       "a catch-up looks no earlier than the first connection")

// MARK: - Event streams

/// The values at `path` in the events of a stream that arrives in the given
/// pieces.
func values(_ pieces: [Data], at path: [String] = ["a"]) -> [String] {
    var parser = EventStreamParser(path: path)
    return pieces.flatMap { (try? parser.read($0)) ?? ["failed"] }
}

func values(_ pieces: String..., at path: [String] = ["a"]) -> [String] {
    values(pieces.map { Data($0.utf8) }, at: path)
}

let streamText = ": a comment\n\nevent: update\nid: 7\nretry: 10\ndata: {\"a\": \"✓\"}\n\n"
    + "data: {\"a\":\ndata:  \"two\"\ndata\ndata: }\n\n"
expect(values(streamText) == ["✓", "two"],
       "each event's data lines, joined, give the value: \(values(streamText))")
expect(values(Data(streamText.utf8).map { Data([$0]) }) == values(streamText),
       "a stream split between every byte, even within a character, reads the same")
expect(values(#"data: "a"\#r\#n\#r\#ndata: "b"\#r\#rdata: "c"\#n\#n"#, at: []) == ["a", "b", "c"],
       "lines can end with CRLF, CR, or LF")
expect(values("data: [\"a\"\r", "\ndata: ,\"b\"]\r", "\n\r", "\n", at: ["1"]) == ["b"],
       "a CRLF split between pieces is one line ending")
expect(values("\u{feff}data: \"a\"\n\n\u{feff}data: \"b\"\n\n", at: []) == ["a"],
       "a byte order mark is dropped only at the start of the stream")
expect(values([Data([0xef, 0xbb]), Data("data: \"a\"\n\ndata: \"b\"\n\n".utf8)], at: []) == ["b"],
       "a field name that starts like a byte order mark is not data")
expect(values("event: ping\n\n\n: comment\n\n") == [], "events without data give no value")
expect(values("dat: \"no\"\ndatum: \"no\"\ndatas: \"no\"\ndata:\"yes\"\n\n", at: []) == ["yes"],
       "only the field named data counts, with or without a space after its colon")
expect(values("data: {\"a\": 1}\n\ndata: {\"b\": \"x\"}\n\ndata: {\"a\": \"x\"}\n\n") == ["x"],
       "events without a string at the path give no value")
expect(values("data: {\"a\": \"1\"}\n\ndata: {\"a\": \"2\"} and more\n\n") == ["1", "2"],
       "each event is read afresh, and only as far as its value")
expect(values("data: \"done\"\n\ndata: \"cut short\"\n", at: []) == ["done"],
       "an event that the stream does not finish gives no value")
var limited = EventStreamParser(path: [], limit: 8)
expect((try? limited.read(Data("data: \"12345678\"\n\ndata: \"1".utf8))) == ["12345678"],
       "values up to the limit are read")
expect((try? limited.read(Data("2345678".utf8))) == [],
       "a value that reaches the limit is still read")
expect((try? limited.read(Data("9".utf8))) == nil,
       "a value that grows past the limit fails the stream")
var bigSkipped = EventStreamParser(path: ["a"], limit: 8)
expect((try? bigSkipped.read(Data("data: {\"b\": \"\(String(repeating: "x", count: 100))\", \"a\": \"y\"}\n\n".utf8)))
       == ["y"], "the limit is on the value, not on what is skipped")

/// How a reader given JSON text all at once, and a byte at a time, comes
/// out, when both agree.
func outcome(at path: [String], inJSON text: String, line: Int = #line) -> JSONPathReader.Outcome? {
    func read(_ pieces: [Data]) -> JSONPathReader.Outcome? {
        var reader = JSONPathReader(path: path)
        for piece in pieces {
            guard (try? piece.withUnsafeBytes { try reader.read($0) }) != nil else { return nil }
        }
        return reader.outcome
    }
    let whole = read([Data(text.utf8)])
    let bytes = read(Data(text.utf8).map { Data([$0]) })
    expect(whole == bytes, "reading at once or a byte at a time agree on \(text): \(String(describing: whole)), "
           + String(describing: bytes), line: line)
    return whole
}

func string(at path: [String], inJSON text: String, line: Int = #line) -> String? {
    if case .found(let string)? = outcome(at: path, inJSON: text, line: line) {
        return string
    }
    return nil
}

expect(string(at: ["message", "text"], inJSON: #"{"message": {"text": "hi", "n": 1}}"#) == "hi",
       "a path through objects")
let listJSON = #"{"a": [{"b": "x"}, "y"]}"#
expect(string(at: ["a", "0", "b"], inJSON: listJSON) == "x", "a path through an array")
expect(string(at: ["a", "1"], inJSON: listJSON) == "y", "a path to an element of an array")
expect(string(at: [], inJSON: #""top""#) == "top", "an empty path is the whole text")
expect(string(at: ["0"], inJSON: #"{"0": "x"}"#) == "x", "a number can name a member of an object")
expect(string(at: ["a", "+1"], inJSON: listJSON) == nil && string(at: ["a", "01"], inJSON: listJSON)
       == nil, "an index must be written plainly")
for (path, json) in [(["a", "2"], listJSON), (["a", "-1"], listJSON), (["a", "first"], listJSON),
                     (["a", "1", "z"], listJSON), (["missing"], listJSON), (["a"], "[]"),
                     (["0"], "[]"), (["a"], #"["x"]"#),
                     (["n"], #"{"n": 1}"#), (["n"], #"{"n": null}"#), (["n"], #"{"n": {}}"#),
                     (["message"], "1"), (["message"], "not JSON")] {
    expect(string(at: path, inJSON: json) == nil, "\(path) leads to no string in \(json)")
}
expect(string(at: ["a"], inJSON: #"{"a": "\"q\" \\ \/ \b\f\n\r\t \u00e9 \ud83d\ude00 \u2603"}"#)
       == "\"q\" \\ / \u{8}\u{c}\n\r\t é 😀 ☃", "escapes stand for what they mean")
expect(string(at: [], inJSON: #""\ud800x \udc00 \ud800""#) == "\u{fffd}x \u{fffd} \u{fffd}",
       "a surrogate without its pair is a replacement character")
expect(string(at: ["é", "\""], inJSON: #"{"\u00e9": {"\"": "x"}}"#) == "x",
       "keys are compared as what their escapes stand for")
expect(string(at: ["a"], inJSON: #"{"ab": "no", "": "no", "b": "no", "a": "yes"}"#) == "yes",
       "a key must match exactly")
let skippedJSON = #"{"s": {"x": "}\"]", "y": [1, {"z": "{["}, []], "n": -1.5e3, "t": true, "f": null}, "#
    + #""l": [[], {}, "]"], "a": "found"}"#
expect(string(at: ["a"], inJSON: skippedJSON) == "found",
       "objects, arrays, strings, numbers, true, false and null off the path are skipped")
expect(string(at: ["a", "1"], inJSON: #"{"a": [{"b": ["]"]}, "y"]}"#) == "y",
       "the elements before the one on the path are skipped, whatever they hold")
expect(outcome(at: ["a"], inJSON: #"{"a": "x", and then not JSON"#) == .found("x"),
       "reading stops at the value")
expect(outcome(at: ["a"], inJSON: #"{"a": 5"#) == .absent,
       "reading stops once the path leads to something other than a string")
expect(outcome(at: ["a", "b"], inJSON: #"{"a": {"c": 1}, "#) == .absent,
       "reading stops once an object on the path ends without the next key")
expect(outcome(at: ["a"], inJSON: #"{"b": "x", "a": "#) == .reading,
       "an answer that has not arrived yet is still being read")
var limitedReader = JSONPathReader(path: [], limit: 3)
expect((try? Data(#""abc""#.utf8).withUnsafeBytes { try limitedReader.read($0) }) != nil
       && limitedReader.outcome == .found("abc"), "a string up to the limit is read")
limitedReader = JSONPathReader(path: [], limit: 3)
expect((try? Data(#""abcd""#.utf8).withUnsafeBytes { try limitedReader.read($0) }) == nil,
       "a string longer than the limit fails")

let valuePath = temporaryDirectory() + "/new/value.txt"
let valueFile = StreamValueFile(StreamSettings(url: URL(fileURLWithPath: "/"),
                                               path: ["message", "text"], file: valuePath))

/// Gives the value file a value, and returns what it says changed, as the
/// log would say it.
func take(_ value: String, at time: Date = Date()) -> String? {
    var change: ValueFileChange?
    waitForMain { done in
        valueFile.take(value, at: time) {
            change = $0
            done()
        }
    }
    return change?.description
}

/// The file's identity, which stays the same while it is changed in place.
func inode(_ path: String) -> ino_t? {
    var status = stat()
    return stat(path, &status) == 0 ? status.st_ino : nil
}

let firstWrite = Date(timeIntervalSince1970: 1000)
expect(valueFile.latest == nil, "a new value file has seen no value")
var firstChange: ValueFileChange?
waitForMain { done in
    valueFile.take("first") {
        firstChange = $0
        done()
    }
}
if case .wrote(let file, let value)? = firstChange {
    expect(file == valuePath && value == "first", "a write says what it wrote: \(value)")
} else {
    expect(false, "the first value is written: \(String(describing: firstChange))")
}
expect(take("hello\n", at: firstWrite) == "wrote \(valuePath)"
       && valueFile.latest == "hello\n"
       && contents(valuePath) == "hello\n" && valueFile.writtenAt == firstWrite,
       "the value is written, and its directory made: \(String(describing: contents(valuePath)))")
expect(take("hello\n") == nil
       && valueFile.writtenAt == firstWrite,
       "a value the file already holds is not written again")
try? "changed by hand".write(toFile: valuePath, atomically: false, encoding: .utf8)
expect(take("hello\n") == "wrote \(valuePath)"
       && contents(valuePath) == "hello\n",
       "a file changed by something else gets the value again")
let firstInode = inode(valuePath)
expect(take("") == "wrote \(valuePath)" && inode(valuePath) == firstInode
       && contents(valuePath) == "",
       "an empty value empties the file")
try? FileManager.default.removeItem(atPath: valuePath)
try? FileManager.default.createDirectory(atPath: valuePath + "/in-the-way",
                                         withIntermediateDirectories: true)
let writeProblem = take("x")
expect(writeProblem?.hasPrefix("could not write the value file: cannot open \(valuePath): ") == true
       && valueFile.problem != nil,
       "a file that cannot be written is a problem: \(String(describing: writeProblem))")
expect(take("x") == nil && valueFile.problem != nil,
       "the same problem again is no change")
try? FileManager.default.removeItem(atPath: valuePath)
expect(take("x") == "wrote \(valuePath)"
       && valueFile.problem == nil && contents(valuePath) == "x", "writing the file once it can be written clears the problem")
try? FileManager.default.removeItem(atPath: valuePath)
try? FileManager.default.createDirectory(atPath: valuePath + "/in-the-way",
                                         withIntermediateDirectories: true)
_ = take("y")
try? FileManager.default.removeItem(atPath: valuePath)
try? "x".write(toFile: valuePath, atomically: false, encoding: .utf8)
let heldWrittenAt = valueFile.writtenAt
expect(valueFile.problem != nil && take("x")
       == "\(valuePath) already holds the latest value" && valueFile.problem == nil
       && valueFile.writtenAt == heldWrittenAt,
       "a file that already holds the value clears the problem, without saying it was written")

// MARK: - Event stream connections

/// Listens on a port of its own on the loopback interface, and answers each
/// connection's first piece of request with `response`, closing the
/// connection afterwards when `closes` is true. Returns the listener, which
/// keeps listening until it is cancelled, and its port.
func startServer(_ response: String, closes: Bool = true) -> (NWListener, UInt16) {
    let parameters = NWParameters.tcp
    parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
    guard let listener = try? NWListener(using: parameters) else {
        fatalError("could not make a listener")
    }
    listener.newConnectionHandler = { connection in
        connection.start(queue: .main)
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 16) { _, _, _, _ in
            connection.send(content: Data(response.utf8),
                            contentContext: closes ? .finalMessage : .defaultMessage,
                            isComplete: closes, completion: .idempotent)
        }
    }
    waitForMain { done in
        listener.stateUpdateHandler = { state in
            if case .ready = state {
                done()
            }
        }
        listener.start(queue: .main)
    }
    guard let port = listener.port?.rawValue else {
        fatalError("the listener has no port")
    }
    return (listener, port)
}

/// Follows the stream at a server that answers with `response` until the
/// connection ends, and returns whether it was ready, the values at the path
/// `a` in its events, and why it ended.
func follow(_ response: String, closes: Bool = true,
            idleLimit: TimeInterval = 60) -> (Bool, [String], String?) {
    let (server, port) = startServer(response, closes: closes)
    defer { server.cancel() }
    let connection = EventStreamConnection(url: URL(string: "http://127.0.0.1:\(port)/events")!,
                                           path: ["a"], idleLimit: idleLimit)
    var ready = false
    var events: [String] = []
    var reason: String?
    waitForMain { done in
        connection.onReady = { ready = true }
        connection.onValue = { events.append($0) }
        connection.onEnded = {
            reason = $0
            done()
        }
        connection.start()
    }
    return (ready, events, reason)
}

let streamHead = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
    + "Connection: close\r\n\r\n"
let (streamReady, streamed, streamEnd) = follow(streamHead + streamText)
expect(streamReady && streamed == values(streamText), "the stream's values arrive: \(streamed)")
expect(streamEnd == "the server ended the stream",
       "the stream ends: \(String(describing: streamEnd))")

let (missingReady, missingEvents, missingEnd) = follow(
    "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
expect(!missingReady && missingEvents.isEmpty
       && missingEnd == "the server answered with status 404",
       "a stream that is not there: \(String(describing: missingEnd))")
let (pageReady, pageEvents, pageEnd) = follow(
    "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 13\r\n\r\ndata: a\n\nxyz")
expect(!pageReady && pageEvents.isEmpty
       && pageEnd == "the server answered with content type text/html, not text/event-stream",
       "a response that is not an event stream: \(String(describing: pageEnd))")
let (idleReady, idleEvents, idleEnd) = follow(streamHead + "data: {\"a\": \"a\"}\n\n", closes: false,
                                              idleLimit: 0.2)
expect(idleReady && idleEvents == ["a"] && idleEnd != nil
       && idleEnd != "the server ended the stream",
       "a stream that goes quiet ends: \(String(describing: idleEnd))")

// A value that cancels the connection is the last, and the connection does
// not then say it ended.
let (cancelServer, cancelPort) = startServer(streamHead + "data: {\"a\": \"a\"}\n\ndata: {\"a\": \"b\"}\n\n")
let cancelled = EventStreamConnection(url: URL(string: "http://127.0.0.1:\(cancelPort)/")!,
                                      path: ["a"])
var cancelledEvents: [String] = []
var cancelledEnded = false
waitForMain { done in
    cancelled.onValue = {
        cancelledEvents.append($0)
        cancelled.cancel()
        done()
    }
    cancelled.onEnded = { _ in cancelledEnded = true }
    cancelled.start()
}
cancelServer.cancel()
_ = follow(streamHead)
expect(cancelledEvents == ["a"] && !cancelledEnded,
       "nothing arrives after cancelling: \(cancelledEvents), \(cancelledEnded)")

// MARK: - Persistent connections

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

var clock = Date(timeIntervalSince1970: 1000)
var fakes: [FakeConnection] = []
var stateChanges = 0
let persistent = PersistentConnection(now: { clock })
persistent.onChange = { stateChanges += 1 }

func open() -> Connection {
    let connection = FakeConnection()
    fakes.append(connection)
    return connection
}

/// The state's description, with times as seconds after the clock.
func persistentState() -> String {
    switch persistent.state {
    case .connecting:
        return "connecting"
    case .connected(let since):
        return "connected \(since.timeIntervalSince(clock))"
    case .disconnected(let reason, let retryAt):
        return "\(reason), again at \(retryAt.map { "\($0.timeIntervalSince(clock))" } ?? "never")"
    }
}

expect(persistentState() == "not started, again at never", "a new connection has not started")
persistent.start(open)
expect(fakes.count == 1 && fakes[0].started && persistentState() == "connecting",
       "starting connects: \(persistentState())")
fakes[0].onReady?()
expect(persistentState() == "connected 0.0", "the connection is ready: \(persistentState())")
persistent.wake()
expect(fakes.count == 1, "waking while connected does not reconnect")
var expectedDelays: [TimeInterval] = []
var delays: [String] = []
clock += 0.5
for delay in [1.0, 2, 4, 8, 16, 32, 64, 64] {
    fakes[fakes.count - 1].onEnded?("gone")
    expectedDelays.append(delay)
    delays.append(persistentState())
    persistent.wake()
}
expect(delays == expectedDelays.map { "gone, again at \($0)" },
       "each failure doubles the wait, up to a maximum: \(delays)")
expect(fakes.count == 9 && persistentState() == "connecting",
       "waking connects straight away: \(fakes.count)")
fakes[8].onReady?()
clock += 64.5
fakes[8].onEnded?("gone")
expect(persistentState() == "gone, again at 1.0",
       "a connection that lasted longer than the next wait starts the waits again: "
       + persistentState())
persistent.wake()
fakes[0].onReady?()
fakes[1].onEnded?("old")
expect(persistentState() == "connecting", "old connections are not heard: \(persistentState())")
persistent.start(open)
expect(fakes[9].cancelled && fakes.count == 11, "starting again replaces the connection")
let changesBeforeStop = stateChanges
persistent.stop("stopped")
expect(fakes[10].cancelled && persistentState() == "stopped, again at never"
       && stateChanges == changesBeforeStop + 1,
       "stopping ends the connection: \(persistentState())")
persistent.wake()
expect(fakes.count == 11, "waking does not connect once stopped")

// A connection that ends as soon as it is ready still makes the next wait
// longer.
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
/// Makes the latest of the brief connections ready, ends it after `lasting`
/// seconds, and returns the wait after it.
func endBrief(lasting: TimeInterval) -> TimeInterval? {
    brief.wake()
    briefFakes[briefFakes.count - 1].onReady?()
    clock += lasting
    briefFakes[briefFakes.count - 1].onEnded?("gone")
    if case .disconnected(_, let retryAt?) = brief.state {
        return retryAt.timeIntervalSince(clock)
    }
    return nil
}
let afterShort = endBrief(lasting: 7.5)
expect(afterShort == 8, "a connection no longer than the next wait makes the wait longer: "
       + String(describing: afterShort))
let afterLong = endBrief(lasting: 16.5)
expect(afterLong == 1, "a connection longer than the next wait starts the waits again: "
       + String(describing: afterLong))

// The waits are real.
let quick = PersistentConnection(firstRetryDelay: 0.01)
var quickOpened = 0
waitForMain { done in
    quick.start {
        quickOpened += 1
        let connection = FakeConnection()
        if quickOpened == 1 {
            DispatchQueue.main.async { connection.onEnded?("gone") }
        } else {
            done()
        }
        return connection
    }
}
expect(quickOpened == 2, "a connection that ends is opened again after the wait")

// MARK: - The train

/// Ten points for every character.
func tenEach(_ text: String) -> CGFloat { CGFloat(text.count * 10) }

for (text, width, expected, message) in [
    ("the quick brown fox", 100, ["the quick", "brown fox"], "words share cars while they fit"),
    ("exactly", 70, ["exactly"], "a word exactly as wide as a car fits"),
    ("  a\n\nb\t c ", 100, ["a b c"], "runs of white space, line breaks included, separate words"),
    ("abcdefghijkl xy", 50, ["abcde", "fghij", "kl xy"], "a word too wide for a car is split"),
    (" \n ", 100, [], "white space alone gives no cars"),
] as [(String, CGFloat, [String], String)] {
    let loads = freightLoads(text, maximumWidth: width, width: tenEach)
    expect(loads == expected, "\(message): \(loads)")
}

let long = String(repeating: "word ", count: 10_000)
let longLoads = trainLoads(long)
expect(longLoads.count == maximumCars && longLoads.last == "…"
       && longLoads.dropLast().allSatisfy { $0.hasPrefix("word") },
       "a long text is cut short, and its last car says so: \(longLoads.count)")
let fullLoads = trainLoads(String(repeating: "word ", count: 40))
expect(fullLoads.joined(separator: " ") == String(repeating: "word ", count: 40).dropLast(),
       "a text that fits is carried whole: \(fullLoads.count)")
// Each of these words is too wide to share a car.
let wide = "WWWWWWWWWW"
let exactlyFull = trainLoads(Array(repeating: wide, count: maximumCars).joined(separator: " "))
expect(exactlyFull.count == maximumCars && !exactlyFull.contains("…"),
       "a train can be exactly full: \(exactlyFull.count)")
let overFull = trainLoads(Array(repeating: wide, count: maximumCars + 1).joined(separator: " "))
expect(overFull.count == maximumCars && overFull.last == "…"
       && overFull.dropLast().allSatisfy { $0 == wide },
       "one car too many is cut short: \(overFull.count)")
let invisible = trainLoads(String(repeating: "\u{200B}", count: 100_000))
expect(invisible.count == maximumCars
       && invisible.dropLast().allSatisfy { $0.count == maximumLoadCharacters },
       "characters that take up no width are shared out by count: \(invisible.count)")
let accented = "e" + String(repeating: "\u{301}", count: 3) + "x"
expect(trainLoads(accented) == [accented], "combining marks stay with their letter")
expect(trainLoads("e" + String(repeating: "\u{301}", count: 200)) == ["…"],
       "a letter under too many marks to draw is carried as an ellipsis")
expect(trainLoads(String(repeating: "\u{1100}", count: 20_000)) == ["…"],
       "a character too long to draw is carried as an ellipsis")
let joined = Array(repeating: "👍", count: 40).joined(separator: "\u{200D}")
expect(trainLoads("go " + joined) == ["go …"],
       "a long chain of joined emoji is one character, carried as an ellipsis")
expect(freightLoads("a", maximumWidth: 5, width: tenEach) == ["…"],
       "a character wider than a car is carried as an ellipsis")
expect(freightLoads("ab cdefg", maximumWidth: 1000, maximumCharacters: 3, width: tenEach)
       == ["ab cde", "fg"], "the start of a split word can share a car with the word before")
expect(trainLoads(" \n").isEmpty, "white space sends no train")
let padded = String(repeating: " ", count: 5000) + "b"
expect(trainLoads(padded) == ["b"], "white space before the words is skipped: \(trainLoads(padded))")
let gap = "a" + String(repeating: " ", count: 5000) + "b"
expect(trainLoads(gap) == ["a b"], "a long gap between words loses no words: \(trainLoads(gap))")
let huge = trainLoads(String(repeating: "x", count: 100_000))
expect(huge.count == maximumCars && huge.last == "…",
       "one huge word is cut short too: \(huge.count)")
expect(freightLoads("a b c d", maximumWidth: 10, limit: 2, width: tenEach) == ["a", "b"],
       "sharing out stops at the limit")
expect(freightLoads("abcdefg", maximumWidth: 1000, maximumCharacters: 3, width: tenEach)
       == ["abc", "def", "g"], "a word is split after the most characters a car carries")

for width: CGFloat in [800, 1512, 3008] {
    let lead = lineLead(width: width)
    expect(abs(lineFront(at: 0, width: width) - width) < 1e-9
           && abs(lineFront(at: lead.crossing, width: width) + lineEdge) < 1e-9,
           "the front of the line crosses the whole window: \(width)")
    expect(abs(lineFront(at: lead.delay, width: width) - width * 3 / 4) < 1e-6,
           "the train comes in a quarter of the window behind the front of the line: \(width)")
    // The front of the train, from when it comes in, against the line.
    let behind = stride(from: lead.delay, through: lead.crossing, by: 0.01).allSatisfy {
        width - trainSpeed * CGFloat($0 - lead.delay) - lineFront(at: $0, width: width)
            >= width / 4 - 1e-6
    }
    expect(behind, "the train never catches up with the front of the line: \(width)")
}
expect(railTop + engineHeight + trainRock <= trainHeight, "the engine fits in the window")
let knee = pantographKnee(foot: pantographFoot)
expect(hypot(knee.x - pantographKneeAtRest.x, knee.y - pantographKneeAtRest.y) < 1e-9,
       "at rest, the pantograph's knee is where it is drawn: \(knee)")
for rise in trainRockSteps {
    let foot = CGPoint(x: pantographFoot.x, y: pantographFoot.y + rise)
    let bent = pantographKnee(foot: foot)
    expect(abs(hypot(bent.x - foot.x, bent.y - foot.y)
               - hypot(pantographKneeAtRest.x - pantographFoot.x,
                       pantographKneeAtRest.y - pantographFoot.y)) < 1e-9
           && abs(hypot(pantographHead.x - bent.x, pantographHead.y - bent.y)
                  - hypot(pantographHead.x - pantographKneeAtRest.x,
                          pantographHead.y - pantographKneeAtRest.y)) < 1e-9,
           "as the engine rises \(rise), the pantograph's arms keep their length")
    expect(bent.x > pantographHead.x, "the knee stays behind the head: \(bent)")
}
expect(railTop + mastHeight <= trainHeight, "the masts fit in the window")
expect(contactWireHeight + 1 <= engineHeight, "the raised pantograph reaches the contact wire")
expect(engineWheels.allSatisfy { $0 - engineWheelRadius >= 0 && $0 + engineWheelRadius <= engineLength },
       "the engine's wheels are under it: \(engineWheels)")

do {
    var random = SystemRandomNumberGenerator()
    let empty = trainLayer([], width: 1000, scale: 2, using: &random)
    let loaded = trainLayer(["hi"], width: 1000, scale: 2, using: &random)
    expect(abs(empty.duration - lineLead(width: 1000).delay
               - Double((1000 + engineLength + lineTrail + lineEdge) / trainSpeed)) < 1e-9,
           "an engine alone takes as long as it needs to cross: \(empty.duration)")
    let car = max(minimumCarLength, letteringWidth("hi") + 2 * carPadding) + couplingLength
    expect(abs(loaded.duration - empty.duration - Double(car / trainSpeed)) < 1e-9,
           "each car adds the time it takes to pass: \(loaded.duration)")
}

/// A random number generator that gives the same numbers every time.
struct SeededRandom: RandomNumberGenerator {
    var state: UInt64

    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}

var seeded = SeededRandom(state: 1)
let sparks = sparkTimes(from: 10, to: 100_010, using: &seeded)
expect(sparks.first.map { $0 >= 10 + sparkLife } == true && sparks.last.map { $0 + sparkLife <= 100_010 } == true,
       "sparks come within the run")
expect(zip(sparks, sparks.dropFirst()).allSatisfy { $0 < $1 }, "sparks come in order")
let perBurst = Double(sparks.count) * (sparkInterval + sparkLife) / 100_000
expect(abs(perBurst - 4) < 0.2,
       "bursts begin about once every \(sparkInterval) seconds, four sparks each: \(perBurst)")

var bursts: [[CFTimeInterval]] = []
for _ in 0..<20_000 {
    bursts.append(sparkBurst(at: 5, using: &seeded))
}
let meanBurst = Double(bursts.map(\.count).reduce(0, +)) / Double(bursts.count)
expect(abs(meanBurst - 4) < 0.15, "a burst has four sparks on average: \(meanBurst)")
expect(bursts.allSatisfy { $0.first == 5 && zip($0, $0.dropFirst()).allSatisfy { $0 <= $1 } },
       "a burst starts with its first spark and comes in order")
expect(bursts.allSatisfy { burst in
    burst.indices.dropFirst().allSatisfy { index in
        burst[..<index].contains { (0.03...1.5).contains(burst[index] - $0) }
    }
}, "every later spark in a burst follows an earlier one by 30 to 1,500 milliseconds")
let lone = Double(bursts.filter { $0.count == 1 }.count) / Double(bursts.count)
expect(abs(lone - 0.375) < 0.02,
       "a spark with no followers, half the time and three quarters of the time: \(lone)")
let quickly = Double(bursts.filter { $0.count > 1 && $0[1] - $0[0] <= 0.15 }.count)
    / Double(bursts.count)
expect(quickly >= 0.5, "half of all first sparks are followed within 150 milliseconds: \(quickly)")
expect(sparkTimes(from: 0, to: sparkLife, using: &seeded).isEmpty, "a run too short for a spark has none")

if failures > 0 {
    fputs("\(failures) failed\n", stderr)
    exit(1)
}
print("all passed")
