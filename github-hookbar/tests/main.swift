import Foundation

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

/// Runs `searchScript` with a fake gh that records its arguments and answers
/// with `response` filtered through the jq program it is given, and returns
/// the script's exit status, what it wrote to its output file, and the
/// arguments gh was given.
func runSearchScript(_ search: String, response: String) -> (Int32, String?, [String]) {
    let directory = temporaryDirectory()
    let fake = directory + "/gh"
    FileManager.default.createFile(atPath: fake, contents: Data("""
        #!/bin/sh
        printf '%s\\n' "$@" > "$FAKE_GH_ARGUMENTS"
        while [ $# -gt 0 ]; do [ "$1" = --jq ] && filter=$2; shift; done
        jq -r "$filter" "$FAKE_GH_RESPONSE"
        """.utf8), attributes: [.posixPermissions: 0o755])
    FileManager.default.createFile(atPath: directory + "/response.json",
                                   contents: Data(response.utf8))
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/sh")
    process.arguments = ["-c", searchScript, "sh", directory + "/output.tsv", search]
    process.environment = ["PATH": "\(directory):/usr/bin:/bin",
                           "FAKE_GH_ARGUMENTS": directory + "/arguments.txt",
                           "FAKE_GH_RESPONSE": directory + "/response.json"]
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
    } catch {
        expect(false, "could not run the search script: \(error)")
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
let (searchStatus, searchOutput, searchArguments) = runSearchScript(awkwardSearch, response:
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

let (tooManyStatus, _, _) = runSearchScript("is:pr", response: searchResponse(count: 1001, ""))
expect(tooManyStatus != 0, "the script fails when the search finds more than GitHub returns")
let (enoughStatus, enoughOutput, _) = runSearchScript("is:pr",
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
] {
    expect(problem(text) == expected, "\(expected): \(String(describing: problem(text)))")
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

if failures > 0 {
    fputs("\(failures) failed\n", stderr)
    exit(1)
}
print("all passed")
