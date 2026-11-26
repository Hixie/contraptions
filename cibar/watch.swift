import Foundation

// What cibar knows about the GitHub Actions workflows of every repository
// whose deliveries reach it: which jobs are queued or running, and how each
// job last finished on each branch. It learns all of it from webhook
// deliveries, and reports each failure, and each workflow on each branch
// that turns red or green.

/// One workflow on one branch of one repository. This is what turns red and
/// green.
struct WorkflowBranch: Hashable, Codable, Comparable {
    /// The repository, as `owner/name`.
    var repository: String
    /// The workflow's name, for showing it.
    var workflow: String
    /// The path of the file that defines the workflow, which is what tells
    /// workflows apart, so that two of the same name are two, and one
    /// renamed is still one. It is empty for every workflow on the branch.
    var path: String
    /// The branch. A branch of a fork, which pull requests from forks run
    /// on, is named `owner:branch`, after the fork's owner.
    var branch: String

    static func == (a: Self, b: Self) -> Bool {
        (a.repository, a.path, a.branch) == (b.repository, b.path, b.branch)
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(repository)
        hasher.combine(path)
        hasher.combine(branch)
    }

    static func < (a: Self, b: Self) -> Bool {
        (a.repository, a.workflow, a.branch, a.path) < (b.repository, b.workflow, b.branch, b.path)
    }

    /// How the logs and the dashboard name it: `labs CI on main`.
    var title: String {
        "\(repositoryName) \(workflow) on \(branch)"
    }

    /// The repository's name without its owner.
    var repositoryName: String {
        repository.split(separator: "/").last.map(String.init) ?? repository
    }

    var owner: String {
        ownerOf(repository)
    }

    /// Every workflow on `branch`.
    static func every(on branch: String, in repository: String) -> Self {
        WorkflowBranch(repository: repository, workflow: "", path: "", branch: branch)
    }
}

/// The owner of a repository named as `owner/name`.
func ownerOf(_ repository: String) -> String {
    repository.split(separator: "/").first.map(String.init) ?? repository
}

/// An element of an array that may or may not be readable.
struct Readable<Element: Decodable>: Decodable {
    let element: Element?

    init(from decoder: Decoder) {
        element = try? Element(from: decoder)
    }
}

extension KeyedDecodingContainer {
    /// Reads an array, leaving out the elements it cannot read, and taking
    /// one that is missing, or that is not an array, as empty.
    func decodeEach<Element: Decodable>(_ type: Element.Type, forKey key: Key) -> [Element] {
        ((try? decodeIfPresent([Readable<Element>].self, forKey: key)) ?? nil)?.compactMap(\.element) ?? []
    }
}

/// Where one attempt at a run of a workflow comes among the others. GitHub
/// gives later runs larger IDs, and a run that is run again a larger
/// attempt number.
struct RunOrder: Hashable, Comparable, Codable {
    var run: Int64
    var attempt: Int

    static func < (a: Self, b: Self) -> Bool {
        (a.run, a.attempt) < (b.run, b.attempt)
    }
}

/// How a job last finished.
struct Verdict: Codable, Equatable {
    var order: RunOrder
    var passed: Bool
    /// The job's page on GitHub.
    var url: String
}

/// The name under which a workflow run that failed without any of its jobs
/// failing, for example because the workflow file is invalid, is recorded
/// alongside the jobs.
let wholeWorkflow = ""

/// A job that has been queued or is running.
struct ActiveJob: Equatable {
    var workflowBranch: WorkflowBranch
    var name: String
    var order: RunOrder
    var running: Bool
    /// The run's page on GitHub.
    var runURL: String
    /// The job's page on GitHub.
    var url: String
    /// The labels a runner must have to take the job, as its workflow asks.
    var labels: [String] = []
    /// The runner the job runs on, and that runner's group, once it runs.
    var runner: String? = nil
    var runnerGroup: String? = nil
    /// When the job was queued, or, once it runs, when it started.
    var since: Date? = nil
    /// The commit its run runs.
    var sha = ""
}

/// How a job that has finished ended: passing, failing, or neither, as one
/// cancelled or skipped does.
enum Outcome: Equatable {
    case passed
    case failed
    case neither

    init(_ conclusion: String?) {
        switch verdict(of: conclusion) {
        case true?: self = .passed
        case false?: self = .failed
        case nil: self = .neither
        }
    }
}

/// A workflow on a branch that turned red or green.
struct Transition {
    var workflowBranch: WorkflowBranch
    var red: Bool
    var run: Int64
    /// What started the run that turned it, such as `push`.
    var event: String
    /// Whether the run ran a fork's code, for a pull request from the fork.
    var fromFork: Bool
    var sha: String
    var runURL: String
    var onDefaultBranch: Bool
    /// The jobs failing on the workflow branch, each with its page.
    var failures: [(name: String, url: String)]
}

/// An open pull request: its number, and who opened it.
struct PullRequestInfo: Equatable {
    var number: Int
    var author: String
}

/// A job that failed, or a run that failed without a job failing, with the
/// state of its workflow branch once the delivery that said so was taken in.
struct Failure {
    var job: String
    /// The job's page, or the run's for a run that failed as a whole.
    var url: String
    var context: Transition
    /// The jobs of the same run that had failed before this one was made,
    /// as GitHub makes a job that needs others only once they finish.
    var failedBefore: [String] = []
    /// The run of the same workflow on the same branch that passed the same
    /// commit, if one did.
    var passedURL: String? = nil
}

/// What a delivery changed: the workflows on branches it turned, and the
/// failures it recorded.
struct Changes {
    var turns: [Transition] = []
    var failures: [Failure] = []
}

/// What a job's or run's conclusion says: passed, failed, or nothing, as for
/// a job that was cancelled or skipped.
func verdict(of conclusion: String?) -> Bool? {
    switch conclusion {
    case "success", "neutral": return true
    case "failure", "timed_out", "startup_failure", "action_required": return false
    default: return nil
    }
}

/// How busy a branch has been: how many workflows have finished on it,
/// each counting for less the longer ago it finished, half as much every
/// `halfLife`, as of `at`.
struct Activity: Codable, Equatable {
    static let halfLife: TimeInterval = 5 * 24 * 60 * 60

    var score: Double = 0
    var at: Date = .distantPast

    /// Counts a workflow finishing at `time`, which may be before others
    /// already counted.
    mutating func count(at time: Date) {
        if time > at {
            score = value(at: time)
            at = time
        }
        score += Self.weight(at.timeIntervalSince(time))
    }

    /// How busy the branch has been, as of `time`.
    func value(at time: Date) -> Double {
        score * Self.weight(time.timeIntervalSince(at))
    }

    private static func weight(_ age: TimeInterval) -> Double {
        pow(0.5, max(age, 0) / halfLife)
    }
}

/// What is kept from one run of cibar to the next.
struct WatchState: Codable, Equatable {
    struct Entry: Codable, Equatable {
        var workflowBranch: WorkflowBranch
        var job: String
        var verdict: Verdict
    }

    struct Pass: Codable, Equatable {
        var workflowBranch: WorkflowBranch
        var order: RunOrder
        /// The commit the run ran, which older versions of cibar did not
        /// save.
        var sha: String? = nil
    }

    struct Forgetting: Codable, Equatable {
        var workflowBranch: WorkflowBranch
        var at: Date
    }

    /// What a run said: what started it, whether it ran a fork's code, the
    /// commit it ran, and its page; and, for each of its jobs that failed,
    /// the jobs of it that had failed before that one was made.
    struct RunRecord: Codable, Equatable {
        var run: Int64
        var event: String
        var fromFork: Bool
        var sha: String
        var url: String
        var failedBefore: [String: [String]]
    }

    var verdicts: [Entry] = []
    var passes: [Pass] = []
    var forgotten: [Forgetting] = []
    var defaultBranches: [String: String] = [:]
    var defaultBranchRuns: [String: Date] = [:]
    var defaultBranchActivity: [String: Activity] = [:]
    /// The runs that jobs that last failed on default branches failed in, so
    /// that their failures can be told again, as when sent to Codex by hand.
    var failedRuns: [RunRecord] = []

    init(verdicts: [Entry] = [], passes: [Pass] = [], forgotten: [Forgetting] = [],
         defaultBranches: [String: String] = [:], defaultBranchRuns: [String: Date] = [:],
         defaultBranchActivity: [String: Activity] = [:], failedRuns: [RunRecord] = []) {
        self.verdicts = verdicts
        self.passes = passes
        self.forgotten = forgotten
        self.failedRuns = failedRuns
        self.defaultBranches = defaultBranches
        self.defaultBranchRuns = defaultBranchRuns
        self.defaultBranchActivity = defaultBranchActivity
    }

    /// Reads what any version of cibar saved, taking what it left out as
    /// empty, and leaving out any entry it cannot read.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        verdicts = container.decodeEach(Entry.self, forKey: .verdicts)
        passes = container.decodeEach(Pass.self, forKey: .passes)
        forgotten = container.decodeEach(Forgetting.self, forKey: .forgotten)
        failedRuns = container.decodeEach(RunRecord.self, forKey: .failedRuns)
        defaultBranches = ((try? container.decodeIfPresent([String: Readable<String>].self,
                                                           forKey: .defaultBranches)) ?? nil)?
            .compactMapValues(\.element) ?? [:]
        defaultBranchRuns = ((try? container.decodeIfPresent([String: Readable<Date>].self,
                                                             forKey: .defaultBranchRuns)) ?? nil)?
            .compactMapValues(\.element) ?? [:]
        defaultBranchActivity = ((try? container.decodeIfPresent([String: Readable<Activity>].self,
                                                                 forKey: .defaultBranchActivity)) ?? nil)?
            .compactMapValues(\.element) ?? [:]
    }
}

final class Watch {
    /// The repository owners whose deliveries count. Empty means every
    /// owner. Narrowing it forgets the workflows of owners it leaves out.
    var owners: Set<String> {
        didSet {
            guard !owners.isEmpty, owners != oldValue else { return }
            forget { !owners.contains($0.owner) }
            let count = defaultBranches.count + defaultBranchRuns.count
            defaultBranches = defaultBranches.filter { owners.contains(ownerOf($0.key)) }
            defaultBranchRuns = defaultBranchRuns.filter { owners.contains(ownerOf($0.key)) }
            defaultBranchActivity = defaultBranchActivity.filter { owners.contains(ownerOf($0.key)) }
            if defaultBranches.count + defaultBranchRuns.count != count {
                revision += 1
            }
        }
    }
    /// How each job last finished, by job name, for each workflow on each
    /// branch.
    private(set) var verdicts: [WorkflowBranch: [String: Verdict]] = [:]
    /// The latest run of each workflow on each branch that passed, and the
    /// commit it ran. Failures from it and from earlier runs are out of
    /// date.
    private var passes: [WorkflowBranch: (order: RunOrder, sha: String?)] = [:]
    /// The workflows on branches that have been forgotten, each with when:
    /// runs of it made by then no longer count. A workflow with no name and
    /// no path stands for every workflow on its branch.
    private var forgotten: [WorkflowBranch: Date] = [:]
    /// Each repository's default branch, by `owner/name`.
    private(set) var defaultBranches: [String: String] = [:]
    /// When GitHub last said a workflow had finished on each repository's
    /// default branch, by `owner/name`.
    private var defaultBranchRuns: [String: Date] = [:]
    /// How busy each repository's default branch has been, by `owner/name`.
    private var defaultBranchActivity: [String: Activity] = [:]
    /// Each repository's open pull requests, by the name cibar gives their
    /// branches, as far as cibar has heard.
    private var pullRequests: [String: [String: PullRequestInfo]] = [:]
    /// The jobs that are queued or running, by job ID.
    private(set) var active: [Int64: ActiveJob] = [:]
    /// How the latest jobs to finish ended, by job ID.
    private(set) var outcomes: [Int64: Outcome] = [:]
    private var runs: [Int64: RunInfo] = [:]
    /// What runs that jobs failed in said, as saved, or as known when they
    /// were forgotten.
    private var savedRuns: [Int64: WatchState.RunRecord] = [:]
    /// Goes up whenever something in `state` changes.
    private(set) var revision = 0
    /// The run whose name each workflow branch is shown by.
    private var namedBy: [WorkflowBranch: Int64] = [:]
    /// The workflow branches the delivery being taken in has changed, each
    /// with whether it was red before the delivery, and the last run that
    /// changed it.
    private var touched: [WorkflowBranch: (wasRed: Bool, run: Int64)] = [:]
    /// The failures the delivery being taken in has recorded.
    private var recorded: [(workflowBranch: WorkflowBranch, job: String, verdict: Verdict)] = []

    /// What a delivery about a run says about it.
    private struct RunDetails {
        var workflowBranch: WorkflowBranch
        var event: String
        var fromFork: Bool
        var sha: String
        var url: String
        var created: Date
    }

    /// The latest attempt at a run that cibar has heard of.
    private struct RunInfo {
        var attempt: Int
        /// What the last delivery about the run said, once one has arrived.
        var details: RunDetails?
        var finished = false
        /// Whether a delivery has said a job of this attempt failed.
        var failedJob = false
        /// The jobs of this attempt that failed, and when, of those whose
        /// deliveries said.
        var failures: [(job: String, at: Date)] = []
        /// For each job of this attempt that failed, the jobs of the attempt
        /// that had failed before it was made.
        var failedBefore: [String: [String]] = [:]
        /// The attempt's failure, held back when it failed with jobs still
        /// unfinished, in case one of them says why; and those jobs.
        var heldFailure: Verdict?
        var unfinishedJobs: Set<Int64> = []
        var finishedJobs: Set<Int64> = []
        /// Deliveries about the run's jobs that arrived before any about the
        /// run, which say what started it, and whose branch it ran on.
        var pendingJobs: [Job] = []
    }

    /// How many runs to remember, so that deliveries about them that arrive
    /// late are recognised as late, and how many forgotten branches.
    static let rememberedRuns = 5000
    static let rememberedForgettings = 2000
    /// How many jobs' outcomes to remember.
    static let rememberedOutcomes = 500

    init(owners: Set<String> = [], restoring state: WatchState = WatchState()) {
        self.owners = owners
        defaultBranches = state.defaultBranches
        defaultBranchRuns = state.defaultBranchRuns
        defaultBranchActivity = state.defaultBranchActivity
        for entry in state.verdicts {
            verdicts[entry.workflowBranch, default: [:]][entry.job] = entry.verdict
        }
        for pass in state.passes {
            passes[pass.workflowBranch] = (pass.order, pass.sha)
        }
        for forgetting in state.forgotten {
            forgotten[forgetting.workflowBranch] = forgetting.at
        }
        for record in state.failedRuns {
            savedRuns[record.run] = record
        }
    }

    var state: WatchState {
        WatchState(
            verdicts: verdicts.sorted { $0.key < $1.key }.flatMap { workflowBranch, jobs in
                jobs.sorted { $0.key < $1.key }.map {
                    WatchState.Entry(workflowBranch: workflowBranch, job: $0.key, verdict: $0.value)
                }
            },
            passes: passes.sorted { $0.key < $1.key }.map {
                WatchState.Pass(workflowBranch: $0.key, order: $0.value.order, sha: $0.value.sha)
            },
            forgotten: forgotten.sorted { $0.key < $1.key }.map {
                WatchState.Forgetting(workflowBranch: $0.key, at: $0.value)
            },
            defaultBranches: defaultBranches,
            defaultBranchRuns: defaultBranchRuns,
            defaultBranchActivity: defaultBranchActivity,
            failedRuns: failingRuns.sorted().compactMap(runRecord(_:)))
    }

    // MARK: Reading deliveries

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    /// A repository, as deliveries and GitHub's API describe it.
    struct Repository: Decodable {
        struct Owner: Decodable { let login: String }
        let fullName: String
        let defaultBranch: String?
        let owner: Owner
        /// When something was last pushed to it, in the API's listings.
        var pushedAt: Date? = nil
        var archived: Bool? = nil
    }

    /// A job, as deliveries and GitHub's API describe it.
    struct Job: Decodable {
        let id: Int64
        let runId: Int64
        let runAttempt: Int?
        let htmlUrl: String?
        let name: String
        let status: String
        let conclusion: String?
        var createdAt: Date? = nil
        var startedAt: Date? = nil
        var completedAt: Date? = nil
        var labels: [String]? = nil
        var runnerName: String? = nil
        var runnerGroupName: String? = nil
    }

    /// A run, as deliveries and GitHub's API describe it.
    struct Run: Decodable {
        struct HeadRepository: Decodable {
            let fullName: String
        }
        let id: Int64
        let runAttempt: Int?
        let name: String?
        let path: String
        let createdAt: Date?
        let runStartedAt: Date?
        var updatedAt: Date? = nil
        let event: String
        let status: String?
        let conclusion: String?
        let headBranch: String?
        let headSha: String
        let headRepository: HeadRepository?
        let htmlUrl: String

        /// The name cibar gives the branch the run ran on, in `repository`:
        /// a fork's is named after the fork's owner, as a pull request's
        /// label names it. A run from a fork that has since been deleted no
        /// longer says whose branch it ran on, so it has none.
        func branch(in repository: String) -> String? {
            guard let head = headRepository?.fullName else { return nil }
            let branch = headBranch ?? String(headSha.prefix(7))
            return head == repository ? branch : "\(ownerOf(head)):\(branch)"
        }
    }

    /// What a delivery says, once it has been read.
    enum Payload {
        case job(Job)
        case run(Run)
        /// Something happened to a pull request: `action` says what.
        case pullRequest(action: String, PullRequest)
        case branchDeleted(String)
        /// Something cibar does not follow.
        case other
    }

    private struct JobPayload: Decodable {
        let workflowJob: Job
    }

    private struct RunPayload: Decodable {
        let workflowRun: Run
    }

    /// A pull request, as deliveries and GitHub's API describe it.
    struct PullRequest: Decodable {
        struct Head: Decodable {
            struct Repository: Decodable { let fullName: String }
            let ref: String?
            /// `owner:branch`, which outlasts the fork.
            let label: String
            let repo: Repository?
        }
        struct User: Decodable { let login: String }
        let number: Int?
        let user: User?
        let head: Head

        /// The name cibar gives the pull request's branch, in `repository`:
        /// a fork's is its label, as for the fork's runs.
        func branch(in repository: String) -> String {
            guard head.repo?.fullName == repository else { return head.label }
            return head.ref ?? String(head.label.drop { $0 != ":" }.dropFirst())
        }

        var info: PullRequestInfo? {
            guard let number, let user else { return nil }
            return PullRequestInfo(number: number, author: user.login)
        }
    }

    private struct PullRequestPayload: Decodable {
        let action: String
        let pullRequest: PullRequest
    }

    private struct DeletePayload: Decodable {
        let ref: String
        let refType: String
    }

    private struct RepositoryPayload: Decodable {
        let repository: Repository
    }

    /// Takes in one delivery: its `X-GitHub-Event` header, its body, and
    /// when GitHub made it. Returns every workflow on a branch that it
    /// turned, each once, and every failure it recorded. Deliveries of other
    /// events, or about repositories of other owners, change nothing. Throws
    /// when a delivery of an event cibar follows cannot be read.
    func handle(event: String, body: Data, at time: Date = Date()) throws -> Changes {
        guard ["workflow_job", "workflow_run", "pull_request", "delete"].contains(event) else {
            return Changes()
        }
        let repository = try Self.decoder.decode(RepositoryPayload.self, from: body).repository
        let payload: Payload
        switch event {
        case "workflow_job":
            payload = .job(try Self.decoder.decode(JobPayload.self, from: body).workflowJob)
        case "workflow_run":
            payload = .run(try Self.decoder.decode(RunPayload.self, from: body).workflowRun)
        case "pull_request":
            let pullRequest = try Self.decoder.decode(PullRequestPayload.self, from: body)
            payload = .pullRequest(action: pullRequest.action, pullRequest.pullRequest)
        default:
            let delete = try Self.decoder.decode(DeletePayload.self, from: body)
            payload = delete.refType == "branch" ? .branchDeleted(delete.ref) : .other
        }
        return take(payload, about: repository, at: time)
    }

    /// Takes in what a delivery, or GitHub's API, says about a repository, as
    /// of `time` by GitHub's clock. Returns every workflow on a branch that
    /// it turned, each once, and every failure it recorded. What is about a
    /// repository of another owner changes nothing.
    func take(_ payload: Payload, about repository: Repository, at time: Date) -> Changes {
        guard owners.isEmpty || owners.contains(repository.owner.login) else {
            return Changes()
        }
        touched = [:]
        recorded = []
        let name = repository.fullName
        switch payload {
        case .job(let job):
            self.job(job)
        case .run(let run):
            if let branch = repository.defaultBranch {
                if defaultBranches[name] != branch {
                    defaultBranches[name] = branch
                    revision += 1
                }
                // Saved with the next change that matters, rather than
                // with every delivery about a run.
                if run.headBranch == branch, run.headRepository?.fullName == name, run.status == "completed" {
                    defaultBranchActivity[name, default: Activity()].count(at: time)
                    if time > defaultBranchRuns[name] ?? .distantPast {
                        defaultBranchRuns[name] = time
                    }
                }
            }
            self.run(run, in: name)
        case .pullRequest(let action, let pullRequest):
            let branch = pullRequest.branch(in: name)
            if action == "closed" {
                pullRequests[name]?[branch] = nil
                // A branch of the repository itself goes when it is
                // deleted, which is delivered, and a fork's branch with its
                // pull request.
                if pullRequest.head.repo?.fullName != name {
                    forget(pullRequest.head.label, in: name, at: time)
                }
            } else if let info = pullRequest.info {
                pullRequests[name, default: [:]][branch] = info
            }
        case .branchDeleted(let branch):
            forget(branch, in: name, at: time)
        case .other:
            break
        }
        let turns = touched.sorted { $0.key < $1.key }.compactMap { workflowBranch, touch in
            isRed(workflowBranch) == touch.wasRed ? nil : transition(workflowBranch, by: touch.run)
        }
        // A job allowed to fail, in a run that passed, no longer fails once
        // the run's news has been taken in, but its failure still counts.
        let failures = recorded.compactMap { failure(of: $0.job, on: $0.workflowBranch, as: $0.verdict) }
        touched = [:]
        recorded = []
        return Changes(turns: turns, failures: failures)
    }

    /// Whether a run made at `created` on a workflow branch no longer
    /// counts, because the workflow branch, or its whole branch, was
    /// forgotten after it was made.
    private func isForgotten(_ workflowBranch: WorkflowBranch, created: Date) -> Bool {
        let wholeBranch = WorkflowBranch.every(on: workflowBranch.branch, in: workflowBranch.repository)
        return [workflowBranch, wholeBranch].contains { forgotten[$0].map { created <= $0 } ?? false }
    }

    // MARK: Following jobs and runs

    /// What cibar knows of the attempt at a run, made a record of now if it
    /// had none, or nothing when a later attempt has started since, or when
    /// the run is so old that it is not worth remembering.
    private func attempt(_ order: RunOrder) -> RunInfo? {
        guard var info = runs[order.run] else {
            runs[order.run] = RunInfo(attempt: order.attempt)
            prune()
            return runs[order.run]
        }
        if order.attempt < info.attempt {
            return nil
        }
        if order.attempt > info.attempt {
            // Jobs of the new attempt wait for news of it, which says when it
            // started. Its jobs that last failed in an earlier attempt still
            // had the same jobs fail before them there.
            let failedBefore = info.failedBefore
            info = RunInfo(attempt: order.attempt)
            info.failedBefore = failedBefore
            active = active.filter { $0.value.order.run != order.run }
            runs[order.run] = info
        }
        return info
    }

    /// Forgets the oldest runs once there are a fifth more than
    /// `rememberedRuns`, keeping what those that jobs last failed in said,
    /// and only those.
    private func prune() {
        guard runs.count > Self.rememberedRuns + Self.rememberedRuns / 5 else { return }
        let failing = failingRuns
        savedRuns = savedRuns.filter { failing.contains($0.key) }
        for run in runs.keys.sorted().prefix(runs.count - Self.rememberedRuns) {
            if failing.contains(run) {
                savedRuns[run] = runRecord(run)
            }
            runs[run] = nil
        }
    }

    /// The runs that jobs that last failed on default branches failed in.
    private var failingRuns: Set<Int64> {
        Set(failingRunsByRepository.values.joined())
    }

    /// The runs that jobs that last failed on default branches failed in, by
    /// repository.
    private var failingRunsByRepository: [String: Set<Int64>] {
        verdicts.filter { isOnDefaultBranch($0.key) }.reduce(into: [:]) { runs, entry in
            for verdict in entry.value.values where !verdict.passed {
                runs[entry.key.repository, default: []].insert(verdict.order.run)
            }
        }
    }

    /// The runs that jobs that last failed on default branches failed in and
    /// that cibar knows nothing of, by repository, to be read from GitHub.
    var unknownFailingRuns: [String: Set<Int64>] {
        failingRunsByRepository.mapValues { $0.filter { runRecord($0) == nil } }.filter { !$0.value.isEmpty }
    }

    /// Takes what a run said, as read from GitHub. What deliveries say of
    /// the run comes first.
    func learn(_ record: WatchState.RunRecord) {
        savedRuns[record.run] = record
    }

    /// What `run` and its `jobs` said, as GitHub's API gives them.
    static func runRecord(of run: Run, jobs: [Job], in repository: Repository, at time: Date)
        -> WatchState.RunRecord? {
        let scratch = Watch()
        _ = scratch.take(.run(run), about: repository, at: time)
        for job in jobs {
            _ = scratch.take(.job(job), about: repository, at: time)
        }
        return scratch.runRecord(run.id)
    }

    /// Notes that the delivery being taken in is about to change a workflow
    /// branch, because of `run`.
    private func touch(_ workflowBranch: WorkflowBranch, by run: Int64) {
        touched[workflowBranch] = (touched[workflowBranch]?.wasRed ?? isRed(workflowBranch), run)
    }

    /// Notes that job `id` ended `outcome`, forgetting the oldest outcomes
    /// once there are a fifth more than `rememberedOutcomes`.
    private func ended(_ id: Int64, _ outcome: Outcome) {
        outcomes[id] = outcome
        if outcomes.count > Self.rememberedOutcomes + Self.rememberedOutcomes / 5 {
            for old in outcomes.keys.sorted().prefix(outcomes.count - Self.rememberedOutcomes) {
                outcomes[old] = nil
            }
        }
    }

    private func job(_ job: Job) {
        let order = RunOrder(run: job.runId, attempt: job.runAttempt ?? 1)
        guard let info = attempt(order) else { return }
        guard let details = info.details else {
            runs[order.run]?.pendingJobs.append(job)
            return
        }
        guard !isForgotten(details.workflowBranch, created: details.created) else { return }
        let workflowBranch = details.workflowBranch
        if job.status == "completed" {
            runs[order.run]?.finishedJobs.insert(job.id)
            active[job.id] = nil
            ended(job.id, Outcome(job.conclusion))
            runs[order.run]?.unfinishedJobs.remove(job.id)
            let passed = verdict(of: job.conclusion)
            // GitHub makes a job that needs others only once they have
            // finished, so one made after others failed may have failed
            // because of them; jobs made together, such as a matrix's,
            // cannot have, even one that waited for a runner.
            if passed == false {
                let failures = runs[order.run]?.failures ?? []
                runs[order.run]?.failedBefore[job.name] = job.createdAt.map { created in
                    failures.filter { $0.at <= created && $0.job != job.name }.map(\.job)
                } ?? []
                if let completed = job.completedAt, !failures.contains(where: { $0.job == job.name }) {
                    runs[order.run]?.failures.append((job.name, completed))
                }
                runs[order.run]?.failedJob = true
                runs[order.run]?.heldFailure = nil
            }
            // The run failed with this job unfinished, and its last
            // unfinished job has finished without failing.
            if let held = runs[order.run]?.heldFailure, runs[order.run]?.unfinishedJobs.isEmpty == true {
                runs[order.run]?.heldFailure = nil
                record(held, for: wholeWorkflow, on: workflowBranch)
            }
            guard let passed else { return }
            // A job failing explains its run failing, and a job of a later
            // run finishing at all shows that the workflow runs again.
            if let whole = verdicts[workflowBranch]?[wholeWorkflow],
               whole.order.run < order.run || (whole.order.run == order.run && !passed) {
                touch(workflowBranch, by: order.run)
                verdicts[workflowBranch]?[wholeWorkflow] = nil
                revision += 1
            }
            record(Verdict(order: order, passed: passed, url: job.htmlUrl ?? details.url),
                   for: job.name, on: workflowBranch)
            return
        }
        // Deliveries can arrive out of order, so one that says a job is
        // waiting after another said it had finished, or that it is queued
        // after another said it was running, is out of date.
        let running = job.status == "in_progress"
        guard !info.finished, !info.finishedJobs.contains(job.id),
              running || active[job.id]?.running != true else {
            return
        }
        active[job.id] = ActiveJob(workflowBranch: workflowBranch, name: job.name,
                                   order: order, running: running, runURL: details.url,
                                   url: job.htmlUrl ?? details.url, labels: job.labels ?? [],
                                   runner: running ? job.runnerName.flatMap { $0.isEmpty ? nil : $0 } : nil,
                                   runnerGroup: running ? job.runnerGroupName.flatMap { $0.isEmpty ? nil : $0 } : nil,
                                   since: running ? job.startedAt ?? job.createdAt : job.createdAt,
                                   sha: details.sha)
    }

    private func run(_ run: Run, in repository: String) {
        // A run from a fork that has since been deleted cannot be told apart
        // from others.
        guard let head = run.headRepository?.fullName, let branch = run.branch(in: repository) else { return }
        let workflowBranch = WorkflowBranch(
            repository: repository, workflow: run.name ?? run.path, path: run.path, branch: branch)
        let order = RunOrder(run: run.id, attempt: run.runAttempt ?? 1)
        // A run that is run again keeps when it was made, so an attempt
        // after the first counts from when it started. One that does not say
        // counts as made now.
        let created = (order.attempt > 1 ? run.runStartedAt : run.createdAt) ?? Date()
        guard var info = attempt(order) else { return }
        guard !isForgotten(workflowBranch, created: created) else {
            runs[run.id]?.pendingJobs = []
            return
        }
        renameWorkflow(workflowBranch, by: run.id)
        let pending = info.pendingJobs
        info.pendingJobs = []
        info.details = RunDetails(
            workflowBranch: workflowBranch, event: run.event, fromFork: head != repository,
            sha: run.headSha, url: run.htmlUrl, created: created)
        runs[run.id] = info
        pending.forEach(job)
        guard run.status == "completed" else { return }

        runs[run.id]?.finished = true
        let unfinished = Set(active.filter { $0.value.order == order }.keys)
        // Its jobs still queued or running have ended, as it has, though
        // nothing has said so yet, or ever will, as for those of a run
        // cancelled while they were queued: all passed if it passed, and
        // otherwise, as far as is known, neither passed nor failed.
        for id in active.keys where active[id]?.order.run == run.id {
            ended(id, verdict(of: run.conclusion) == true ? .passed : .neither)
        }
        active = active.filter { $0.value.order.run != run.id }
        switch verdict(of: run.conclusion) {
        case true?:
            // The run passed, so anything still failing is from an earlier
            // run, of a job this one did not run, or is a job allowed to
            // fail.
            guard passes[workflowBranch].map({ $0.order < order }) ?? true else { return }
            touch(workflowBranch, by: run.id)
            passes[workflowBranch] = (order, run.headSha)
            let kept = verdicts[workflowBranch]?.filter { $0.value.passed || $0.value.order > order }
            verdicts[workflowBranch] = kept?.isEmpty == false ? kept : nil
            revision += 1
        case false?:
            // A run can fail without a job failing, for example when its
            // workflow file is invalid, or a failure can go undelivered.
            // cibar may have restarted since a job failed, or forgotten the
            // run, so the record of the job's failure counts too. A job of a
            // later run that finished shows that the workflow runs again.
            let explained = runs[run.id]?.failedJob == true
                || verdicts[workflowBranch]?.values.contains {
                    $0.order.run > run.id || ($0.order.run == run.id && !$0.passed)
                } == true
            // A run's completion can be delivered before its jobs', so while
            // any of them is unfinished, the run's failure waits for them.
            let failure = Verdict(order: order, passed: false, url: run.htmlUrl)
            if !explained && unfinished.isEmpty {
                record(failure, for: wholeWorkflow, on: workflowBranch)
            } else if !explained {
                runs[run.id]?.heldFailure = failure
                runs[run.id]?.unfinishedJobs = unfinished
            }
        case nil:
            break
        }
    }

    /// Shows a workflow by the name the latest of its runs gave, should it
    /// have been renamed.
    private func renameWorkflow(_ workflowBranch: WorkflowBranch, by run: Int64) {
        let latest = ([namedBy[workflowBranch], passes[workflowBranch]?.order.run]
            + (verdicts[workflowBranch]?.values.map { $0.order.run } ?? [])).compactMap { $0 }.max()
        guard run >= latest ?? run else { return }
        namedBy[workflowBranch] = run
        for (id, job) in active where job.workflowBranch == workflowBranch {
            active[id]?.workflowBranch = workflowBranch
        }
        func rename<Value>(in dictionary: inout [WorkflowBranch: Value]) {
            guard let index = dictionary.index(forKey: workflowBranch),
                  dictionary.keys[index].workflow != workflowBranch.workflow,
                  let value = dictionary.removeValue(forKey: workflowBranch) else { return }
            dictionary[workflowBranch] = value
            revision += 1
        }
        rename(in: &verdicts)
        rename(in: &passes)
        rename(in: &forgotten)
    }

    /// Records how a job finished, unless a later attempt has already
    /// finished it, or it failed in a run no later than one that passed, and
    /// notes a failure it records.
    private func record(_ verdict: Verdict, for job: String, on workflowBranch: WorkflowBranch) {
        if let earlier = verdicts[workflowBranch]?[job], earlier.order > verdict.order {
            return
        }
        if !verdict.passed, let pass = passes[workflowBranch]?.order, verdict.order <= pass {
            return
        }
        // The same delivery made again changes nothing.
        guard verdicts[workflowBranch]?[job] != verdict else { return }
        touch(workflowBranch, by: verdict.order.run)
        verdicts[workflowBranch, default: [:]][job] = verdict
        if !verdict.passed {
            recorded.append((workflowBranch, job, verdict))
        }
        revision += 1
    }

    /// The failure of `job` on a workflow branch, as it was delivered, if
    /// the job last failed and cibar knows what the run it failed in said.
    func failure(of job: String, on workflowBranch: WorkflowBranch) -> Failure? {
        guard let verdict = verdicts[workflowBranch]?[job], !verdict.passed else { return nil }
        return failure(of: job, on: workflowBranch, as: verdict)
    }

    /// The failure of `job` on a workflow branch that `verdict` says, if
    /// cibar knows what the run it failed in said.
    private func failure(of job: String, on workflowBranch: WorkflowBranch, as verdict: Verdict) -> Failure? {
        guard let record = runRecord(verdict.order.run) else { return nil }
        let context = transition(workflowBranch, by: record)
        let pass = passes[workflowBranch].flatMap { $0.sha == context.sha ? $0.order.run : nil }
        return Failure(job: job, url: verdict.url, context: context, failedBefore: record.failedBefore[job] ?? [],
                       passedURL: pass.map { "https://github.com/\(workflowBranch.repository)/actions/runs/\($0)" })
    }

    /// What a run said, as a delivery about it did, or as was saved; and,
    /// for each of its jobs that failed, the jobs that failed before it, as
    /// deliveries said or else as was saved.
    private func runRecord(_ run: Int64) -> WatchState.RunRecord? {
        let saved = savedRuns[run]
        guard let info = runs[run], let details = info.details else { return saved }
        return WatchState.RunRecord(run: run, event: details.event, fromFork: details.fromFork, sha: details.sha,
                                    url: details.url,
                                    failedBefore: (saved?.failedBefore ?? [:]).merging(info.failedBefore) { $1 })
    }

    private func transition(_ workflowBranch: WorkflowBranch, by run: Int64) -> Transition? {
        runRecord(run).map { transition(workflowBranch, by: $0) }
    }

    private func transition(_ workflowBranch: WorkflowBranch, by record: WatchState.RunRecord) -> Transition {
        Transition(
            workflowBranch: workflowBranch, red: isRed(workflowBranch), run: record.run,
            event: record.event, fromFork: record.fromFork, sha: record.sha, runURL: record.url,
            onDefaultBranch: isOnDefaultBranch(workflowBranch),
            failures: failures(on: workflowBranch))
    }

    /// Forgets a branch, once its pull request has closed or it has been
    /// deleted, unless it is the repository's default branch.
    private func forget(_ branch: String, in repository: String, at time: Date) {
        guard defaultBranches[repository] != branch else { return }
        forget(.every(on: branch, in: repository), at: time)
    }

    /// Forgets a workflow on a branch at `time`, as is right for one that
    /// will never run again, for example because its file has been deleted,
    /// or, given `WorkflowBranch.every`, every workflow on the branch. Runs
    /// of it made by then stop counting, so that a job that finishes after
    /// its branch was deleted does not bring the branch back.
    func forget(_ workflowBranch: WorkflowBranch, at time: Date) {
        forgotten[workflowBranch] = max(time, forgotten[workflowBranch] ?? time)
        if forgotten.count > Self.rememberedForgettings {
            for (oldest, _) in forgotten.sorted(by: { $0.value < $1.value })
                .prefix(forgotten.count - Self.rememberedForgettings) {
                forgotten[oldest] = nil
            }
        }
        revision += 1
        let everyWorkflow = workflowBranch == .every(on: workflowBranch.branch, in: workflowBranch.repository)
        forget {
            $0 == workflowBranch
                || (everyWorkflow && $0.repository == workflowBranch.repository && $0.branch == workflowBranch.branch)
        }
    }

    /// Forgets every workflow branch that `matches`.
    func forget(where matches: (WorkflowBranch) -> Bool) {
        let count = verdicts.count + passes.count
        verdicts = verdicts.filter { !matches($0.key) }
        passes = passes.filter { !matches($0.key) }
        active = active.filter { !matches($0.value.workflowBranch) }
        if verdicts.count + passes.count != count {
            revision += 1
        }
    }

    /// Forgets every workflow branch `previous` had forgotten, as of when it
    /// did, since nothing else would say those branches had gone.
    func forget(asOf previous: Watch) {
        forgotten.merge(previous.forgotten) { mine, _ in mine }
    }

    /// Takes on what `previous` knew that this watch does not: how the jobs
    /// of each workflow branch `keeps` names last finished, which jobs are
    /// under way in each repository `keepsActive` names, each repository's
    /// default branch and when a workflow last ran on it, and every run this
    /// watch has not heard of, so that deliveries about them go on from
    /// where they were.
    func keep(_ previous: Watch, where keeps: (WorkflowBranch) -> Bool, activeIn keepsActive: (String) -> Bool) {
        verdicts.merge(previous.verdicts.filter { keeps($0.key) }) { mine, _ in mine }
        passes.merge(previous.passes.filter { keeps($0.key) }) { mine, _ in mine }
        namedBy.merge(previous.namedBy.filter { keeps($0.key) }) { mine, _ in mine }
        active.merge(previous.active.filter { keepsActive($0.value.workflowBranch.repository) }) { mine, _ in mine }
        defaultBranches.merge(previous.defaultBranches) { mine, _ in mine }
        defaultBranchRuns.merge(previous.defaultBranchRuns, uniquingKeysWith: max)
        // How busy a branch has been is as it was: what was read was counted
        // when it happened, and a read sees only the latest runs.
        defaultBranchActivity.merge(previous.defaultBranchActivity) { _, previous in previous }
        runs.merge(previous.runs) { mine, _ in mine }
        // A run read afresh says nothing of which of its jobs failed before
        // others, when its jobs were not read.
        for run in previous.failingRuns where savedRuns[run] == nil {
            savedRuns[run] = previous.runRecord(run)
        }
        pullRequests.merge(previous.pullRequests) { mine, _ in mine }
        revision += 1
    }

    /// The open pull request whose branch a workflow branch is on, if
    /// cibar has heard of one.
    func pullRequest(for workflowBranch: WorkflowBranch) -> PullRequestInfo? {
        pullRequests[workflowBranch.repository]?[workflowBranch.branch]
    }

    /// Takes a repository's open pull requests, by their branches, as all
    /// there are.
    func setPullRequests(_ open: [String: PullRequestInfo], in repository: String) {
        pullRequests[repository] = open
    }

    /// Whether this watch already knows that a job failed as `failure` says.
    func holds(_ failure: Failure) -> Bool {
        verdicts[failure.context.workflowBranch]?[failure.job].map { !$0.passed && $0.url == failure.url } ?? false
    }

    /// Forgets which jobs are queued and running, as is right once
    /// deliveries may have been missed, since nothing else would say when
    /// those jobs finished.
    func forgetActiveJobs() {
        active = [:]
    }

    // MARK: Summing up

    func isRed(_ workflowBranch: WorkflowBranch) -> Bool {
        verdicts[workflowBranch]?.values.contains { !$0.passed } ?? false
    }

    /// The job that last failed on a workflow branch, queued or running in
    /// a later run, if it is, so that how it finishes there will replace its
    /// failure.
    func rerun(of job: String, on workflowBranch: WorkflowBranch) -> ActiveJob? {
        guard let failed = verdicts[workflowBranch]?[job]?.order else { return nil }
        return active.values.filter { $0.workflowBranch == workflowBranch && $0.name == job && $0.order > failed }
            .max { $0.order < $1.order }
    }

    /// The jobs of `run` on a workflow branch that last failed in it.
    func failures(on workflowBranch: WorkflowBranch, in run: Int64) -> [(name: String, url: String)] {
        (verdicts[workflowBranch] ?? [:]).filter { !$0.value.passed && $0.value.order.run == run }
            .sorted { $0.key < $1.key }.map { (name: $0.key, url: $0.value.url) }
    }

    func failures(on workflowBranch: WorkflowBranch) -> [(name: String, url: String)] {
        (verdicts[workflowBranch] ?? [:]).filter { !$0.value.passed }.sorted { $0.key < $1.key }
            .map { (name: $0.key, url: $0.value.url) }
    }

    /// Every workflow on any branch with a job that last failed.
    var redWorkflowBranches: [WorkflowBranch] {
        verdicts.keys.filter(isRed).sorted()
    }

    /// Every workflow on a repository's default branch with a job that last
    /// failed. Other branches are left out: they start no sessions and fly no
    /// planes, and their failures are their authors' to fix.
    var failingWorkflowBranches: [WorkflowBranch] {
        redWorkflowBranches.filter(isOnDefaultBranch)
    }

    /// How many jobs last failed on default branches.
    var failingJobCount: Int {
        failingWorkflowBranches.reduce(0) { $0 + failures(on: $1).count }
    }

    /// How many jobs are running, not counting those queued, on default
    /// branches, and, unless `defaultBranchesOnly`, on every other branch.
    func runningCount(defaultBranchesOnly: Bool) -> Int {
        active.values.filter { $0.running && (!defaultBranchesOnly || isOnDefaultBranch($0.workflowBranch)) }.count
    }

    func isOnDefaultBranch(_ workflowBranch: WorkflowBranch) -> Bool {
        defaultBranches[workflowBranch.repository] == workflowBranch.branch
    }

    /// How long a default branch that is green stays listed after a
    /// workflow last ran on it.
    static let quietDefaultBranchListing: TimeInterval = 24 * 60 * 60

    /// The default branches worth listing, the busiest first:
    /// each one that is red, or that a workflow finished on in the day before
    /// `now`.
    func listedDefaultBranches(at now: Date) -> [(repository: String, branch: String, red: Bool)] {
        let red = Set(failingWorkflowBranches.map(\.repository))
        // The busiest first, over the last week or so, which changes little
        // from one finish to the next, and those as busy in order of their
        // names.
        let order = defaultBranches.sorted { a, b in
            let busy = (defaultBranchActivity[a.key]?.value(at: now) ?? 0,
                        defaultBranchActivity[b.key]?.value(at: now) ?? 0)
            return busy.0 != busy.1 ? busy.0 > busy.1 : a.key < b.key
        }
        return order.compactMap { repository, branch in
            let recent = defaultBranchRuns[repository].map {
                now.timeIntervalSince($0) < Self.quietDefaultBranchListing
            } ?? false
            return red.contains(repository) || recent ? (repository, branch, red.contains(repository)) : nil
        }
    }

    /// Whether any workflow on a repository's default branch is red.
    var defaultBranchesRed: Bool {
        !failingWorkflowBranches.isEmpty
    }
}
