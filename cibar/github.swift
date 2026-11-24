import Foundation

// Reading from GitHub's API what is going on now, which cibar does whenever
// it connects to the relay and when asked to, so that it knows what happened
// while it was not listening, and forgets what no longer is. Deliveries from
// the relay keep it up to date from then on, so the API is asked nothing
// more.

/// How recently a repository must have been pushed to for cibar to read its
/// runs from the API, unless one of its workflows is red. What cibar knew of
/// quieter repositories is kept.
let primedRepositoryAge: TimeInterval = 7 * 24 * 60 * 60

/// Something GitHub's API says, to be taken in by `Watch.take`.
struct Primed {
    var payload: Watch.Payload
    var repository: Watch.Repository
    var time: Date
}

/// What a read of GitHub's API found: what it says, what it covered of each
/// repository it was read for, and why anything else it should have read
/// could not be.
struct PrimedState {
    /// What a read covered of a repository: the paths of its workflows that
    /// are still there and active, its default branch and the branches of
    /// its open pull requests, and each workflow branch, as "path branch", it
    /// saw a run of.
    struct Coverage {
        var workflows: Set<String> = []
        var branches: Set<String> = []
        var seen: Set<String> = []
        /// The open pull requests, by the names cibar gives their branches.
        var pullRequests: [String: PullRequestInfo] = [:]
    }

    var primed: [Primed] = []
    var coverage: [String: Coverage] = [:]
    var problems: [String] = []

    var repositories: Set<String> { Set(coverage.keys) }

    /// Whether what was known of a workflow branch before the read still
    /// stands: it is in a repository not read, or its workflow and branch
    /// are still there and the read saw no run of it, as it may not of one
    /// that runs seldom.
    func keeps(_ workflowBranch: WorkflowBranch) -> Bool {
        guard let coverage = coverage[workflowBranch.repository] else { return true }
        return coverage.workflows.contains(workflowBranch.path) && coverage.branches.contains(workflowBranch.branch)
            && !coverage.seen.contains("\(workflowBranch.path) \(workflowBranch.branch)")
    }
}

struct GitHubProblem: Error, CustomStringConvertible {
    var description: String
}

/// GitHub refusing to answer more until later, which ends a read.
struct RateLimited: Error, CustomStringConvertible {
    var description: String
}

extension Watch {
    /// A watch that knows what `read` says, what `previous` knew that the
    /// read did not contradict, and then what `deliveries` say. `previous`
    /// has been told of `deliveries` already, so they call for nothing more,
    /// except the failures they tell of that `previous` did not know of,
    /// for runs it had not heard of, which are returned.
    static func refreshed(from read: PrimedState, previous: Watch,
                          deliveries: [Delivery]) -> (watch: Watch, failures: [Failure]) {
        let watch = Watch(owners: previous.owners)
        watch.forget(asOf: previous)
        for (repository, coverage) in read.coverage {
            watch.setPullRequests(coverage.pullRequests, in: repository)
        }
        for item in read.primed {
            _ = watch.take(item.payload, about: item.repository, at: item.time)
        }
        watch.keep(previous, where: read.keeps, activeIn: { read.coverage[$0] == nil })
        var failures: [Failure] = []
        for delivery in deliveries {
            let changes = (try? watch.handle(event: delivery.event, body: delivery.body,
                                             at: delivery.created ?? Date())) ?? Changes()
            failures += changes.failures.filter { !previous.holds($0) }
        }
        return (watch, failures)
    }
}

/// GitHub's API, asked with a token through `session`.
struct GitHubAPI {
    var token: String
    var session = Self.session

    /// A session that keeps GitHub's answers, so that asking again is a
    /// conditional request, which GitHub does not count against the token's
    /// requests when nothing has changed.
    static let session: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.urlCache = URLCache(memoryCapacity: 16 << 20, diskCapacity: 256 << 20,
                                          directory: FileManager.default.urls(for: .cachesDirectory,
                                                                              in: .userDomainMask).first?
                                              .appendingPathComponent("\(applicationName)/github"))
        configuration.requestCachePolicy = .useProtocolCachePolicy
        return URLSession(configuration: configuration)
    }()

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    private struct RunsPage: Decodable {
        let workflowRuns: [Watch.Run]
    }

    private struct JobsPage: Decodable {
        let jobs: [Watch.Job]
    }

    private struct WorkflowsPage: Decodable {
        struct Workflow: Decodable {
            let path: String
            /// "active", or why the workflow will not run: deleted,
            /// disabled by hand or for inactivity.
            let state: String
        }
        let workflows: [Workflow]
    }

    private struct User: Decodable {
        let login: String
    }

    private struct RunnersPage: Decodable {
        struct Runner: Decodable {
            struct Label: Decodable { let name: String }
            let name: String
            let status: String
            let labels: [Label]
        }
        let runners: [Runner]
    }

    private struct LargerRunnersPage: Decodable {
        struct Runner: Decodable {
            struct Size: Decodable {
                let cpuCores: Int?
                let memoryGb: Int?
            }
            let name: String
            let maximumRunners: Int?
            let machineSizeDetails: Size?
        }
        let runners: [Runner]
    }

    /// What GitHub lists of each of `owners`' runners: its self-hosted
    /// runners, and its larger runners, each by owner. An owner that is not
    /// an organization has none; any other answer that cannot be read, as
    /// when the token cannot see an organization's runners, is a problem.
    func runners(of owners: [String]) async throws
        -> (runners: [String: [Runner]], larger: [String: [LargerRunner]], problems: [String]) {
        var runners: [String: [Runner]] = [:]
        var larger: [String: [LargerRunner]] = [:]
        var problems: [String] = []
        /// What `read` lists, none for an owner that is not an organization,
        /// or nothing, when it cannot be read.
        func list<Item>(_ what: String, of owner: String, _ read: () async throws -> [Item]) async throws -> [Item]? {
            do {
                return try await read()
            } catch let error as RateLimited {
                throw error
            } catch is NotFound {
                return []
            } catch {
                problems.append("\(owner)'s \(what): \(error)")
                return nil
            }
        }
        for owner in owners.sorted() {
            runners[owner] = try await list("runners", of: owner) {
                try await all(RunnersPage.self, "/orgs/\(owner)/actions/runners?per_page=100", \.runners).map {
                    Runner(owner: owner, name: $0.name, labels: $0.labels.map(\.name), online: $0.status == "online")
                }
            }
            larger[owner] = try await list("larger runners", of: owner) {
                try await all(LargerRunnersPage.self, "/orgs/\(owner)/actions/hosted-runners?per_page=100",
                              \.runners).map {
                    LargerRunner(owner: owner, name: $0.name, maximum: $0.maximumRunners,
                                 cores: $0.machineSizeDetails?.cpuCores, memory: $0.machineSizeDetails?.memoryGb)
                }
            }
        }
        return (runners, larger, problems)
    }

    private struct Refusal: Decodable {
        let message: String
    }

    /// Everything cibar follows in the repositories of `owners` that have
    /// been pushed to lately, and in `alsoReading`, a few repositories at a
    /// time: the latest runs of their workflows, on their default branches
    /// and on the branches of their open pull requests, and on any branch
    /// while under way, and the jobs of those under way and of the latest to
    /// fail of each workflow on each branch. Stops when GitHub will answer no
    /// more. Tells `progress` how many of how many repositories it has read,
    /// once it knows how many there are, and as each is read.
    func prime(owners: [String], alsoReading: Set<String> = [], now: Date,
               progress: @escaping @Sendable (Int, Int) -> Void = { _, _ in }) async throws -> PrimedState {
        var read = PrimedState()
        var repositories: [Watch.Repository] = []
        for owner in owners.sorted() {
            do {
                repositories += try await self.repositories(of: owner).filter {
                    $0.archived != true && (alsoReading.contains($0.fullName)
                        || $0.pushedAt.map { now.timeIntervalSince($0) < primedRepositoryAge } == true)
                }
            } catch let error as RateLimited {
                throw error
            } catch {
                read.problems.append("\(owner): \(error)")
            }
        }
        progress(0, repositories.count)
        // A few at a time, so as not to ask GitHub too much at once, each
        // started as another is done.
        let parts = await withTaskGroup(of: (String, Result<RepositoryRead, Error>).self) { group in
            var next = 0
            func start(_ group: inout TaskGroup<(String, Result<RepositoryRead, Error>)>) {
                let repository = repositories[next]
                group.addTask { (repository.fullName, await Result { try await prime(repository, now: now) }) }
                next += 1
            }
            while next < min(4, repositories.count) {
                start(&group)
            }
            var parts: [(String, Result<RepositoryRead, Error>)] = []
            while let part = await group.next() {
                parts.append(part)
                progress(parts.count, repositories.count)
                if next < repositories.count {
                    start(&group)
                }
            }
            return parts
        }
        for (name, result) in parts.sorted(by: { $0.0 < $1.0 }) {
            switch result {
            case .success(let part):
                read.primed += part.primed
                read.problems += part.problems
                read.coverage[name] = part.coverage
            case .failure(let error as RateLimited):
                throw error
            case .failure(let error):
                read.problems.append("\(name): \(error)")
            }
        }
        return read
    }

    private struct RepositoryRead {
        var primed: [Primed] = []
        var coverage = PrimedState.Coverage()
        var problems: [String] = []
    }

    private func prime(_ repository: Watch.Repository, now: Date) async throws -> RepositoryRead {
        let name = repository.fullName
        var read = RepositoryRead()
        // Runs of workflows whose files are gone, or that are disabled, and
        // of branches whose pull requests have closed, would otherwise come
        // back as red for good.
        read.coverage.workflows = Set(
            try await all(WorkflowsPage.self, "/repos/\(name)/actions/workflows?per_page=100", \.workflows)
                .filter { $0.state == "active" }.map(\.path))
        for pullRequest in try await all([Watch.PullRequest].self, "/repos/\(name)/pulls?state=open&per_page=100",
                                         { $0 }) {
            let branch = pullRequest.branch(in: name)
            read.coverage.branches.insert(branch)
            read.coverage.pullRequests[branch] = pullRequest.info
        }
        var paths = ["/repos/\(name)/actions/runs?per_page=100"]
        if let branch = repository.defaultBranch {
            read.coverage.branches.insert(branch)
            // The default branch's runs reach further back than every
            // branch's, so that more of its workflows are seen.
            if let query = branch.addingPercentEncoding(withAllowedCharacters: queryValue) {
                paths.append("/repos/\(name)/actions/runs?per_page=100&branch=\(query)")
            }
        }
        var runs: [Int64: Watch.Run] = [:]
        for path in paths {
            for run in try await get(RunsPage.self, path).value.workflowRuns
            where read.coverage.workflows.contains(run.path) {
                guard let branch = run.branch(in: name),
                      run.status != "completed" || read.coverage.branches.contains(branch) else { continue }
                runs[run.id] = run
                read.coverage.seen.insert("\(run.path) \(branch)")
            }
        }
        let ordered = runs.values.sorted { $0.id < $1.id }
        // The latest run of each workflow on each branch that passed or
        // failed, which is what that workflow's verdicts come from.
        var latest: [String: Watch.Run] = [:]
        for run in ordered where run.status == "completed" && verdict(of: run.conclusion) != nil {
            latest["\(run.path) \(run.branch(in: name) ?? "")"] = run
        }
        let wanted = ordered.filter { $0.status != "completed" }
            + latest.values.filter { verdict(of: $0.conclusion) == false }.sorted { $0.id < $1.id }
        read.primed = ordered.map { Primed(payload: .run($0), repository: repository, time: time(of: $0, now: now)) }
        // A few at a time, so as not to ask GitHub too much at once.
        for start in stride(from: 0, to: wanted.count, by: 6) {
            let batch = wanted[start..<min(start + 6, wanted.count)]
            let results = await withTaskGroup(of: Result<[Primed], Error>.self) { group in
                for run in batch {
                    group.addTask {
                        await Result {
                            try await all(JobsPage.self, "/repos/\(name)/actions/runs/\(run.id)/jobs?per_page=100",
                                          \.jobs)
                                .map {
                                    Primed(payload: .job($0), repository: repository, time: time(of: run, now: now))
                                }
                        }
                    }
                }
                return await group.reduce(into: []) { $0.append($1) }
            }
            for result in results {
                switch result {
                case .success(let jobs): read.primed += jobs
                case .failure(let error as RateLimited): throw error
                case .failure(let error): read.problems.append("\(name): \(error)")
                }
            }
        }
        return read
    }

    private func time(of run: Watch.Run, now: Date) -> Date {
        run.updatedAt ?? run.runStartedAt ?? run.createdAt ?? now
    }

    /// The repositories of an organization, or of a user: all of the
    /// token's own user's, and the public ones of anyone else.
    private func repositories(of owner: String) async throws -> [Watch.Repository] {
        do {
            return try await all([Watch.Repository].self, "/orgs/\(owner)/repos?per_page=100&type=all", { $0 })
        } catch is NotFound {
            let me = try await get(User.self, "/user").value.login
            return try await all([Watch.Repository].self, me.lowercased() == owner.lowercased()
                                 ? "/user/repos?per_page=100&affiliation=owner"
                                 : "/users/\(owner)/repos?per_page=100&type=owner", { $0 })
        }
    }

    private struct NotFound: Error, CustomStringConvertible {
        var path: String
        var description: String { "GitHub has no \(path), or the token cannot see it" }
    }

    /// Every page of a listing.
    private func all<Page: Decodable, Item>(_ type: Page.Type, _ path: String,
                                            _ items: (Page) -> [Item]) async throws -> [Item] {
        var gathered: [Item] = []
        var next = URL(string: "https://api.github.com" + path)
        while let url = next {
            let page = try await get(type, url)
            gathered += items(page.value)
            next = page.next
        }
        return gathered
    }

    private func get<Value: Decodable>(_ type: Value.Type, _ path: String) async throws -> (value: Value, next: URL?) {
        guard let url = URL(string: "https://api.github.com" + path) else {
            throw GitHubProblem(description: "cannot ask GitHub for \(path)")
        }
        return try await get(type, url)
    }

    private func get<Value: Decodable>(_ type: Value.Type, _ url: URL) async throws -> (value: Value, next: URL?) {
        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("2022-11-28", forHTTPHeaderField: "X-GitHub-Api-Version")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await session.data(for: request)
        let http = response as? HTTPURLResponse
        switch http?.statusCode {
        case 200:
            return (try Self.decoder.decode(type, from: data),
                    nextPageURL(link: http?.value(forHTTPHeaderField: "Link")))
        case 404:
            throw NotFound(path: url.path)
        case let status:
            var problem = "GitHub answered \(status.map(String.init) ?? "nothing") for \(url.path)"
            let message = (try? Self.decoder.decode(Refusal.self, from: data))?.message
            if let message {
                problem += ": \(message)"
            }
            let exhausted = http?.value(forHTTPHeaderField: "x-ratelimit-remaining") == "0"
            if exhausted,
               let reset = http?.value(forHTTPHeaderField: "x-ratelimit-reset").flatMap(TimeInterval.init) {
                problem += " (no requests left until "
                    + DateFormatter.localizedString(from: Date(timeIntervalSince1970: reset), dateStyle: .none,
                                                    timeStyle: .short) + ")"
            }
            if status == 429 || (status == 403 && (exhausted || message?.lowercased().contains("rate limit") == true)) {
                throw RateLimited(description: problem)
            }
            throw GitHubProblem(description: problem)
        }
    }
}

/// What may stand in a query's value as it is.
private let queryValue = CharacterSet.urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&+=#"))

/// The URL a `Link` header, such as
/// `<https://api.github.com/...&page=2>; rel="next", <...>; rel="last"`,
/// gives for the next page, if it gives one.
func nextPageURL(link: String?) -> URL? {
    link?.split(separator: ",").lazy.compactMap { part -> URL? in
        let pieces = part.split(separator: ";").map { $0.trimmingCharacters(in: .whitespaces) }
        guard pieces.dropFirst().contains("rel=\"next\""), let target = pieces.first,
              target.hasPrefix("<"), target.hasSuffix(">") else { return nil }
        return URL(string: String(target.dropFirst().dropLast()))
    }.first
}

/// Asks the user's login shell for a GitHub token: `GH_TOKEN`, then
/// `GITHUB_TOKEN`, then what `gh auth token` prints. launchd starts cibar
/// with almost no environment, so this is how it sees the token a terminal
/// sees. The shell writes the token to a file only this user can read,
/// rather than to a pipe, which anything the profile leaves running could
/// hold open. Blocks while the shell runs.
func gitHubToken() -> String? {
    let script = """
        token="${GH_TOKEN-}"
        if [ -z "$token" ]; then token="${GITHUB_TOKEN-}"; fi
        if [ -z "$token" ] && command -v gh >/dev/null 2>&1; then token="$(gh auth token 2>/dev/null)"; fi
        umask 077
        printf '%s' "$token" > "$1"
        """
    let file = FileManager.default.temporaryDirectory.appendingPathComponent("\(applicationName)-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: file) }
    let shell = getpwuid(getuid()).flatMap { $0.pointee.pw_shell.map { String(cString: $0) } } ?? "/bin/sh"
    let process = Process()
    process.executableURL = URL(fileURLWithPath: shell.isEmpty ? "/bin/sh" : shell)
    process.arguments = ["-ilc", script, applicationName, file.path]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    process.standardInput = FileHandle.nullDevice
    guard (try? process.run()) != nil else { return nil }
    process.waitUntilExit()
    let token = ((try? String(contentsOf: file, encoding: .utf8)) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    return token.isEmpty ? nil : token
}
