import Foundation

// The state of the checks on one commit: what GitHub has said about each
// check suite and commit status on it, put in order by GitHub's own times.

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

// MARK: - Check results

enum CheckState: String {
    case passing
    case failing
    case running
    /// Reported, but neither a pass nor a failure: skipped, neutral, stale,
    /// or cancelled before a runner ran it.
    case other
}

struct CheckSummary {
    let name: String
    let state: CheckState
    let detail: String
    let url: URL?
    /// The check suite this is, or nil for a commit status.
    let suite: Int?
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

// MARK: - One commit's checks

/// What GitHub has said about one check suite: when it last finished, and
/// when a check run in it last started or failed. Every report carries
/// GitHub's own time, so reports that arrive out of order still leave the
/// suite in the state of the latest one.
struct SuiteRecord {
    /// The name of the workflow the suite runs, when GitHub has given it.
    var workflow: String?
    /// The name of the first check run reported in it.
    var run: String?
    /// The workflow run's page, or else the first check run's.
    var url: URL?
    var finished: (state: CheckState, detail: String, at: Date)?
    var started: Date?
    var failed: (run: String, at: Date)?

    /// A check run that failed or started after the suite last finished
    /// decides the suite's state until it finishes again.
    var state: CheckState {
        let since = finished?.at ?? .distantPast
        if let failed, failed.at > since { return .failing }
        if let started, started > since { return .running }
        return finished?.state ?? .running
    }

    var detail: String {
        let since = finished?.at ?? .distantPast
        if let failed, failed.at > since { return "\(failed.run) failed" }
        if let started, started > since { return "running" }
        return finished?.detail ?? "running"
    }

    mutating func merge(_ other: SuiteRecord) {
        if workflow == nil, let theirs = other.workflow {
            workflow = theirs
            url = other.url ?? url
        }
        run = run ?? other.run
        url = url ?? other.url
        if let theirs = other.finished, theirs.at >= finished?.at ?? .distantPast {
            finished = theirs
        }
        if let theirs = other.started, theirs > started ?? .distantPast {
            started = theirs
        }
        if let theirs = other.failed, theirs.at > failed?.at ?? .distantPast {
            failed = theirs
        }
    }
}

/// The newest commit status in one context. GitHub numbers statuses in the
/// order they are set.
struct StatusRecord {
    let id: Int
    let state: CheckState
    let detail: String
    let url: URL?
}

/// Every check suite and commit status reported on one commit.
struct CommitChecks {
    var suites: [Int: SuiteRecord] = [:]
    var statuses: [String: StatusRecord] = [:]
    /// Suites a delivery said a check run or the suite itself was cancelled
    /// in, and whose latest attempt no answer from GitHub has shown finished
    /// since. Only GitHub's answer says whether a runner was running a job a
    /// cancellation stopped.
    var undecided: Set<Int> = []

    mutating func suiteCancelled(suite: Int) {
        undecided.insert(suite)
    }

    mutating func runStarted(suite: Int, run: String, url: URL?, at time: Date,
                             workflow: String? = nil) {
        merge(suite, SuiteRecord(workflow: workflow, run: run, url: url, started: time))
    }

    mutating func runFailed(suite: Int, run: String, url: URL?, at time: Date) {
        merge(suite, SuiteRecord(run: run, url: url, failed: (run, time)))
    }

    mutating func suiteFinished(suite: Int, state: CheckState, detail: String, at time: Date,
                                workflow: String? = nil, url: URL? = nil) {
        merge(suite, SuiteRecord(workflow: workflow, url: url, finished: (state, detail, time)))
    }

    mutating func statusSet(context: String, _ status: StatusRecord) {
        if status.id >= statuses[context]?.id ?? .min {
            statuses[context] = status
        }
    }

    mutating func merge(_ other: CommitChecks) {
        for (suite, record) in other.suites {
            merge(suite, record)
        }
        for (context, status) in other.statuses {
            statusSet(context: context, status)
        }
    }

    private mutating func merge(_ suite: Int, _ record: SuiteRecord) {
        if suites[suite] == nil {
            suites[suite] = record
        } else {
            suites[suite]?.merge(record)
        }
    }

    /// Each suite and each status, for the menu.
    var summaries: [CheckSummary] {
        let suiteSummaries = suites.map { id, record in
            CheckSummary(name: record.workflow ?? record.run ?? "Check suite \(id)",
                         state: record.state, detail: record.detail, url: record.url, suite: id)
        }
        let statusSummaries = statuses.map { context, status in
            CheckSummary(name: context, state: status.state, detail: status.detail,
                         url: status.url, suite: nil)
        }
        return (suiteSummaries + statusSummaries)
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
}

/// What GitHub answered about a branch.
struct BranchSnapshot {
    /// The repository under the name GitHub now gives it.
    let repository: RepositoryReference
    let sha: String
    let headline: String?
    let checks: CommitChecks
    let rateLimit: RateLimit?
}

struct BranchStatus {
    let repository: RepositoryReference
    let branch: String
    let sha: String
    let headline: String?
    let checks: [CheckSummary]
    let health: Health
    let fetched: Date
    let rateLimit: RateLimit?

    init(repository: RepositoryReference, branch: String, sha: String, headline: String?,
         checks: CommitChecks, fetched: Date, rateLimit: RateLimit?) {
        self.repository = repository
        self.branch = branch
        self.sha = sha
        self.headline = headline
        self.checks = checks.summaries
        health = rollUp(self.checks)
        self.fetched = fetched
        self.rateLimit = rateLimit
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
    /// The HTTP status GitHub answered with, when it answered.
    var status: Int? = nil
    /// Whether the refusal was for want of rate limit.
    var rateLimited = false
}
