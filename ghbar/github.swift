import Foundation

// Asking GitHub's REST API about a branch: its head commit, the workflow
// runs and commit statuses on it, and, when it turns red, what failed.

// MARK: - Constants

let applicationName = "ghbar"
let apiRoot = "https://api.github.com"
let apiVersion = "2022-11-28"
let pageSize = 100

/// How many answers the client keeps, to ask again conditionally.
let remembered = 128

/// How many commits since the last green one the hand-off names.
let handOffCommitLimit = 20

let timeOfDayFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.timeStyle = .medium
    formatter.dateStyle = .none
    return formatter
}()

// MARK: - The client

enum GetOutcome {
    case ok(data: Data, rate: RateLimit?, nextPage: URL?)
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

/// GETs against the GitHub REST API. Every request carries the ETag of the
/// last answer to it, and an answer of "nothing has changed", which costs
/// nothing against the rate limit, is given to the caller as the answer it
/// repeats. Completions run on the main thread.
final class GitHubClient {
    private let session: URLSession
    private var answers: [URL: (etag: String, data: Data, nextPage: URL?)] = [:]
    private var order: [URL] = []

    /// `protocolClasses` stand in for the network in tests.
    init(protocolClasses: [AnyClass]? = nil) {
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
        if let protocolClasses {
            configuration.protocolClasses = protocolClasses
        }
        session = URLSession(configuration: configuration)
    }

    func get(path: String, token: String?, completion: @escaping (GetOutcome) -> Void) {
        guard let url = URL(string: "\(apiRoot)/\(path)") else {
            completion(.failure(FetchFailure(message: "\(path) is not a usable address.",
                                             authenticationFailed: false)))
            return
        }
        get(url: url, token: token, completion: completion)
    }

    func get(url: URL, token: String?, completion: @escaping (GetOutcome) -> Void) {
        var request = URLRequest(url: url)
        if let token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let known = answers[url] {
            request.setValue(known.etag, forHTTPHeaderField: "If-None-Match")
        }
        let task = session.dataTask(with: request) { data, response, error in
            DispatchQueue.main.async {
                completion(self.interpret(url: url, data: data, response: response, error: error))
            }
        }
        task.resume()
    }

    private func remember(_ url: URL, etag: String, data: Data, nextPage: URL?) {
        if answers[url] == nil {
            order.append(url)
            if order.count > remembered {
                answers[order.removeFirst()] = nil
            }
        }
        answers[url] = (etag, data, nextPage)
    }

    /// GitHub explains a refusal in the body of its answer, and names the
    /// permission the request wanted in a header.
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

    private func interpret(url: URL, data: Data?, response: URLResponse?, error: Error?) -> GetOutcome {
        if let error {
            return .failure(FetchFailure(message: error.localizedDescription,
                                         authenticationFailed: false))
        }
        guard let response = response as? HTTPURLResponse else {
            return .failure(FetchFailure(message: "GitHub sent a reply this app cannot read.",
                                         authenticationFailed: false))
        }
        let rate = parseRateLimit(response)
        let outcome = interpret(response, rate: rate, url: url, data: data)
        if case .failure(var failure) = outcome {
            failure.status = response.statusCode
            return .failure(failure)
        }
        return outcome
    }

    private func interpret(_ response: HTTPURLResponse, rate: RateLimit?, url: URL, data: Data?) -> GetOutcome {
        switch response.statusCode {
        case 200:
            let body = data ?? Data()
            let nextPage = nextPageURL(from: response.value(forHTTPHeaderField: "Link"))
            if let etag = response.value(forHTTPHeaderField: "ETag") {
                remember(url, etag: etag, data: body, nextPage: nextPage)
            }
            return .ok(data: body, rate: rate, nextPage: nextPage)
        case 304:
            guard let known = answers[url] else {
                return .failure(FetchFailure(
                    message: "GitHub reported no change to something this app never read.",
                    authenticationFailed: false))
            }
            return .ok(data: known.data, rate: rate, nextPage: known.nextPage)
        case 401:
            let (said, _) = GitHubClient.explanation(data, response)
            let short = "GitHub rejected the token."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: true))
        case 403, 429:
            // A secondary rate limit leaves requests to spare, and says when
            // to try again instead.
            if response.value(forHTTPHeaderField: "retry-after") != nil {
                return .failure(FetchFailure(
                    message: "GitHub asked for fewer requests for a while.",
                    authenticationFailed: false, rateLimited: true))
            }
            if let rate, rate.remaining == 0 {
                let when = timeOfDayFormatter.string(from: rate.reset)
                return .failure(FetchFailure(
                    message: "GitHub's rate limit is used up until \(when).",
                    authenticationFailed: false, rateLimited: true))
            }
            let (said, needs) = GitHubClient.explanation(data, response)
            let short = needs.map { "GitHub refused: the token needs \($0)." }
                ?? "GitHub refused the request."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: false))
        case 404:
            let (said, needs) = GitHubClient.explanation(data, response)
            let short = "GitHub has no such repository or branch, or the token cannot see it."
            var long = short
            if let said { long += " GitHub said: \(said)" }
            if let needs { long += " The request wanted \(needs)." }
            return .failure(FetchFailure(message: short,
                                         detail: long == short ? nil : long,
                                         authenticationFailed: false))
        case 422:
            let (said, _) = GitHubClient.explanation(data, response)
            let short = "GitHub could not use that branch name."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: false))
        default:
            let (said, _) = GitHubClient.explanation(data, response)
            let short = "GitHub answered with status \(response.statusCode)."
            return .failure(FetchFailure(message: short,
                                         detail: said.map { "\(short) GitHub said: \($0)" },
                                         authenticationFailed: false))
        }
    }

    /// GETs every page of a list, starting at `path`.
    func getAll<Item>(path: String, token: String?, read: @escaping (Data) throws -> [Item],
                      completion: @escaping (Result<(items: [Item], rate: RateLimit?), FetchFailure>) -> Void) {
        guard let url = URL(string: "\(apiRoot)/\(path)") else {
            completion(.failure(FetchFailure(message: "\(path) is not a usable address.",
                                             authenticationFailed: false)))
            return
        }
        getPages(url: url, token: token, read: read, gathered: [], rate: nil, completion: completion)
    }

    private func getPages<Item>(url: URL, token: String?, read: @escaping (Data) throws -> [Item],
                                gathered: [Item], rate: RateLimit?,
                                completion: @escaping (Result<(items: [Item], rate: RateLimit?), FetchFailure>) -> Void) {
        get(url: url, token: token) { outcome in
            switch outcome {
            case .failure(let failure):
                completion(.failure(failure))
            case .ok(let data, let pageRate, let nextPage):
                let items: [Item]
                do {
                    items = try read(data)
                } catch {
                    completion(.failure(FetchFailure(
                        message: "GitHub's answer did not parse.",
                        detail: "GitHub's answer to \(url.path) did not parse: \(error.localizedDescription)",
                        authenticationFailed: false)))
                    return
                }
                guard let nextPage else {
                    completion(.success((gathered + items, pageRate ?? rate)))
                    return
                }
                self.getPages(url: nextPage, token: token, read: read, gathered: gathered + items,
                              rate: pageRate ?? rate, completion: completion)
            }
        }
    }
}

// MARK: - What GitHub answers

let gitHubDecoder: JSONDecoder = {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    decoder.dateDecodingStrategy = .iso8601
    return decoder
}()

struct CombinedStatus: Decodable {
    struct Entry: Decodable {
        let id: Int
        let context: String
        let state: String
        let description: String?
        let targetUrl: String?
    }
    struct Named: Decodable {
        let fullName: String
    }
    let sha: String
    let statuses: [Entry]
    let repository: Named?
}

struct WorkflowRun: Decodable {
    let id: Int
    let name: String?
    let status: String
    let conclusion: String?
    let checkSuiteId: Int
    let htmlUrl: String?
    let runStartedAt: Date?
    let updatedAt: Date
}

struct WorkflowRuns: Decodable {
    let workflowRuns: [WorkflowRun]
}

struct Job: Decodable {
    struct Step: Decodable {
        let name: String
        let conclusion: String?
    }
    let name: String
    let status: String
    let conclusion: String?
    let completedAt: Date?
    let htmlUrl: String?
    let steps: [Step]?
}

struct Jobs: Decodable {
    let jobs: [Job]
}

struct Comparison: Decodable {
    struct Commit: Decodable {
        struct Details: Decodable {
            let message: String
        }
        let sha: String
        let commit: Details
    }
    let commits: [Commit]
}

struct CommitResponse: Decodable {
    struct Details: Decodable {
        let message: String
    }
    let commit: Details
}

func firstLine(_ message: String) -> String {
    message.components(separatedBy: .newlines).first?.trimmingCharacters(in: .whitespaces) ?? ""
}

/// The commit's checks as the workflow runs, the jobs of the runs that have
/// not finished, and the commit statuses describe them.
func commitChecks(runs: [WorkflowRun], jobs: [Int: [Job]], statuses: [CombinedStatus.Entry]) -> CommitChecks {
    var checks = CommitChecks()
    for run in runs {
        let url = run.htmlUrl.flatMap(URL.init(string:))
        let name = run.name ?? "Workflow run \(run.id)"
        if run.status == "completed" {
            checks.suiteFinished(suite: run.checkSuiteId,
                                 state: checkState(runStatus: run.status, conclusion: run.conclusion),
                                 detail: run.conclusion ?? run.status, at: run.updatedAt,
                                 workflow: name, url: url)
            continue
        }
        checks.runStarted(suite: run.checkSuiteId, run: name, url: url,
                          at: run.runStartedAt ?? run.updatedAt, workflow: name)
        for job in jobs[run.id] ?? []
        where checkState(runStatus: job.status, conclusion: job.conclusion) == .failing {
            checks.runFailed(suite: run.checkSuiteId, run: job.name,
                             url: job.htmlUrl.flatMap(URL.init(string:)),
                             at: job.completedAt ?? run.updatedAt)
        }
    }
    for entry in statuses {
        checks.statusSet(context: entry.context,
                         StatusRecord(id: entry.id, state: checkState(commitStatus: entry.state),
                                      detail: entry.description ?? entry.state,
                                      url: entry.targetUrl.flatMap(URL.init(string:))))
    }
    return checks
}

// MARK: - Reading a branch

/// Asks GitHub about one branch: the commit it is on and that commit's
/// statuses, the workflow runs on the commit, and the jobs of the runs that
/// have not finished, so that a job that has already failed is not missed.
/// The Actions API serves a fine-grained token that has the Actions
/// permission, which, unlike the Checks permission, such a token can be
/// given.
final class BranchReader {
    private let client: GitHubClient
    private var headlines: [String: String] = [:]

    init(client: GitHubClient = GitHubClient()) {
        self.client = client
    }

    func fetch(repository: RepositoryReference, branch: String, token: String?,
               completion: @escaping (Result<BranchSnapshot, FetchFailure>) -> Void) {
        let path = "repos/\(repository.slug)/commits/\(escapePathComponent(branch))/status?per_page=\(pageSize)"
        client.get(path: path, token: token) { outcome in
            switch outcome {
            case .failure(let failure):
                completion(.failure(failure))
            case .ok(let data, let rate, _):
                let status: CombinedStatus
                do {
                    status = try gitHubDecoder.decode(CombinedStatus.self, from: data)
                } catch {
                    completion(.failure(FetchFailure(
                        message: "GitHub's answer about \(branch) did not parse.",
                        detail: error.localizedDescription, authenticationFailed: false)))
                    return
                }
                let named = status.repository.flatMap { parseRepository($0.fullName)?.repository }
                    ?? repository
                self.fetchRuns(repository: named, sha: status.sha, token: token) { result in
                    switch result {
                    case .failure(let failure):
                        completion(.failure(failure))
                    case .success(let found):
                        self.fetchHeadline(repository: named, sha: status.sha, token: token) { headline in
                            completion(.success(BranchSnapshot(
                                repository: named, sha: status.sha, headline: headline,
                                checks: commitChecks(runs: found.runs, jobs: found.jobs,
                                                     statuses: status.statuses),
                                rateLimit: found.rate ?? rate)))
                        }
                    }
                }
            }
        }
    }

    private func fetchRuns(repository: RepositoryReference, sha: String, token: String?,
                           completion: @escaping (Result<(runs: [WorkflowRun], jobs: [Int: [Job]], rate: RateLimit?),
                                                         FetchFailure>) -> Void) {
        client.getAll(path: "repos/\(repository.slug)/actions/runs?head_sha=\(sha)&per_page=\(pageSize)",
                      token: token,
                      read: { try gitHubDecoder.decode(WorkflowRuns.self, from: $0).workflowRuns }) { result in
            switch result {
            case .failure(let failure):
                completion(.failure(failure))
            case .success(let listed):
                let unfinished = listed.items.filter { $0.status != "completed" }
                self.fetchJobs(repository: repository, runs: unfinished, token: token) { jobs in
                    switch jobs {
                    case .failure(let failure):
                        completion(.failure(failure))
                    case .success(let jobs):
                        completion(.success((listed.items, jobs, listed.rate)))
                    }
                }
            }
        }
    }

    /// The jobs of each run, by run.
    private func fetchJobs(repository: RepositoryReference, runs: [WorkflowRun], token: String?,
                           gathered: [Int: [Job]] = [:],
                           completion: @escaping (Result<[Int: [Job]], FetchFailure>) -> Void) {
        guard let run = runs.first else {
            completion(.success(gathered))
            return
        }
        client.getAll(path: "repos/\(repository.slug)/actions/runs/\(run.id)/jobs?filter=latest&per_page=\(pageSize)",
                      token: token,
                      read: { try gitHubDecoder.decode(Jobs.self, from: $0).jobs }) { result in
            switch result {
            case .failure(let failure):
                completion(.failure(failure))
            case .success(let listed):
                var gathered = gathered
                gathered[run.id] = listed.items
                self.fetchJobs(repository: repository, runs: Array(runs.dropFirst()), token: token,
                               gathered: gathered, completion: completion)
            }
        }
    }

    /// The first line of the commit message, asked for once per commit.
    private func fetchHeadline(repository: RepositoryReference, sha: String, token: String?,
                               completion: @escaping (String?) -> Void) {
        if let known = headlines[sha] {
            completion(known.isEmpty ? nil : known)
            return
        }
        client.get(path: "repos/\(repository.slug)/commits/\(sha)", token: token) { outcome in
            switch outcome {
            case .ok(let data, _, _):
                let headline = (try? gitHubDecoder.decode(CommitResponse.self, from: data))
                    .map { firstLine($0.commit.message) } ?? ""
                self.headlines[sha] = headline
                completion(headline.isEmpty ? nil : headline)
            case .failure(let failure):
                // A token that may not read the commit is not asked again.
                if failure.status == 403 || failure.status == 404, !failure.rateLimited {
                    self.headlines[sha] = ""
                }
                completion(nil)
            }
        }
    }

    // MARK: When the branch turns red

    /// Describes what failed on the branch's head, for an agent asked to fix
    /// it: each failed job and the step it failed at, each failed commit
    /// status, and the commits since `lastGreen`. Whatever GitHub will not
    /// say is left out.
    func failureReport(_ status: BranchStatus, lastGreen: String?, token: String?,
                       completion: @escaping (String) -> Void) {
        let failing = status.checks.filter { $0.state == .failing }
        var lines: [String] = []
        let group = DispatchGroup()
        var jobLines: [Int: [String]] = [:]
        for check in failing {
            guard let suite = check.suite else {
                lines.append("- \(check.name): \(check.detail)" + (check.url.map { " \($0.absoluteString)" } ?? ""))
                continue
            }
            group.enter()
            failedJobs(repository: status.repository, suite: suite, workflow: check.name,
                       token: token) { found in
                jobLines[suite] = found.isEmpty
                    ? ["- \(check.name): \(check.detail)" + (check.url.map { " \($0.absoluteString)" } ?? "")]
                    : found
                group.leave()
            }
        }
        var commits: [String] = []
        if let lastGreen, lastGreen != status.sha {
            group.enter()
            client.get(path: "repos/\(status.repository.slug)/compare/\(lastGreen)...\(status.sha)",
                       token: token) { outcome in
                if case .ok(let data, _, _) = outcome,
                   let comparison = try? gitHubDecoder.decode(Comparison.self, from: data) {
                    commits = comparison.commits.suffix(handOffCommitLimit).reversed().map {
                        "- \($0.sha.prefix(7)) \(firstLine($0.commit.message))"
                    }
                }
                group.leave()
            }
        }
        group.notify(queue: .main) {
            guard !failing.isEmpty else {
                completion("Nothing on \(status.shortSHA) has failed.")
                return
            }
            var report = "What failed on \(status.shortSHA):\n"
            report += (failing.compactMap(\.suite).flatMap { jobLines[$0] ?? [] } + lines).joined(separator: "\n")
            if !commits.isEmpty, let lastGreen {
                report += "\n\nCommits since \(lastGreen.prefix(7)), the last commit that passed, newest first:\n"
                report += commits.joined(separator: "\n")
            }
            completion(report)
        }
    }

    /// The failed jobs of the workflow run behind a check suite, each with
    /// the step it failed at and a link to it.
    private func failedJobs(repository: RepositoryReference, suite: Int, workflow: String,
                            token: String?, completion: @escaping ([String]) -> Void) {
        client.get(path: "repos/\(repository.slug)/actions/runs?check_suite_id=\(suite)",
                   token: token) { outcome in
            guard case .ok(let data, _, _) = outcome,
                  let run = (try? gitHubDecoder.decode(WorkflowRuns.self, from: data))?.workflowRuns.first else {
                completion([])
                return
            }
            self.client.getAll(path: "repos/\(repository.slug)/actions/runs/\(run.id)/jobs?filter=latest&per_page=\(pageSize)",
                               token: token,
                               read: { try gitHubDecoder.decode(Jobs.self, from: $0).jobs }) { result in
                guard case .success(let listed) = result else {
                    completion([])
                    return
                }
                completion(listed.items.compactMap { job in
                    guard checkState(runStatus: job.status, conclusion: job.conclusion) == .failing else {
                        return nil
                    }
                    let step = job.steps?.first {
                        checkState(runStatus: "completed", conclusion: $0.conclusion) == .failing
                    }
                    return "- \(workflow) / \(job.name)"
                        + (step.map { ", at the step \"\($0.name)\"" } ?? "")
                        + ": \(job.conclusion ?? job.status)"
                        + (job.htmlUrl.map { " \($0)" } ?? "")
                })
            }
        }
    }
}
