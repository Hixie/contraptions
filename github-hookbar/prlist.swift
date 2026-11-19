import Foundation

// Pull request deliveries, and the pull request list: a file listing the open
// pull requests that github-hookbar tracks, so that other programs can find
// the pull request for a branch. The pull requests it tracks are the ones whose
// author and repository owner the configuration chooses.

/// One line of the pull request list: the repository's full name, a tab, the
/// pull request's branch, a tab, and the pull request's number.
struct TrackedPullRequest: Equatable {
    let repository: String
    let branch: String
    let number: Int

    var line: String { "\(repository)\t\(branch)\t\(number)" }

    /// Whether a line of the list is about this pull request, whatever
    /// branch it names.
    func isListed(by line: String) -> Bool {
        TrackedPullRequest(line: line[...]).map {
            $0.repository == repository && $0.number == number
        } == true
    }
}

extension TrackedPullRequest {
    init?(line: Substring) {
        let fields = line.split(separator: "\t", omittingEmptySubsequences: false)
        guard fields.count == 3, !fields[0].isEmpty, !fields[1].isEmpty,
              let number = Int(fields[2]), number > 0 else {
            return nil
        }
        self.init(repository: String(fields[0]), branch: String(fields[1]), number: number)
    }
}

enum PullRequestListChange: Equatable, CustomStringConvertible {
    case add(TrackedPullRequest)
    case remove(TrackedPullRequest)

    var description: String {
        switch self {
        case .add: return "adding it to the pull request list"
        case .remove: return "taking it off the pull request list"
        }
    }

    /// The list's lines after the change. Adding a pull request replaces any
    /// line about it, so a delivery that arrives twice adds one line. Lines
    /// about other pull requests, and lines that are not about a pull
    /// request, stay as they are.
    func applied(to lines: [String]) -> [String] {
        switch self {
        case .add(let pullRequest):
            return lines.filter { !pullRequest.isListed(by: $0) } + [pullRequest.line]
        case .remove(let pullRequest):
            return lines.filter { !pullRequest.isListed(by: $0) }
        }
    }
}

/// Replaces the lines of the file at `path` with the lines `transform`
/// returns, under an exclusive lock as `withExclusiveLock` describes.
/// `transform` is given a function that reads the file's lines, which fails
/// when the file is not UTF-8 text, so a transform that ignores them can
/// replace a file whatever it holds.
func rewriteLines(ofFileAt path: String,
                  _ transform: (() throws -> [String]) throws -> [String]) throws {
    try withExclusiveLock(onFileAt: path) { file in
        let data = Data(try transform {
            guard let text = String(data: try file.readToEnd() ?? Data(), encoding: .utf8) else {
                throw Problem(description: "\(path) is not UTF-8 text")
            }
            var lines = text.components(separatedBy: "\n")
            if lines.last == "" {
                lines.removeLast()
            }
            return lines
        }.map { $0 + "\n" }.joined().utf8)
        try replaceContents(of: file, with: data)
    }
}

/// How long GitHub's search can take to include a pull request that was just
/// opened, or to leave out one that was just merged.
let searchDelay: TimeInterval = 10 * 60

/// The pull request list, which changes in order on a queue of its own, so
/// that waiting for a reader's lock does not hold up the menu bar. Its
/// methods, and the callbacks they make, run on the main thread.
final class PullRequestList {
    let path: String
    private let queue = DispatchQueue(label: "local.github-hookbar.pull-request-list")
    /// The changes a refresh may need to make again, with when each was
    /// made, oldest first.
    private var recentChanges: [(time: Date, change: PullRequestListChange)] = []
    /// When the running refresh started, if one is running.
    private var refreshStart: Date?
    /// How many refreshes have started, which identifies the latest.
    private var refreshCount = 0

    init(path: String) {
        self.path = path
    }

    var isRefreshing: Bool { refreshStart != nil }

    func make(_ change: PullRequestListChange, at time: Date = Date(),
              completion: @escaping (Error?) -> Void) {
        dispatchPrecondition(condition: .onQueue(.main))
        let oldest = min(time, refreshStart ?? time) - searchDelay
        recentChanges.removeAll { $0.time < oldest }
        recentChanges.append((time, change))
        rewrite({ change.applied(to: try $0()) }, completion: completion)
    }

    /// Replaces the list with the pull requests that `lookUp` finds.
    /// `lookUp` may miss what happened shortly before it looked, so the
    /// changes made from `searchDelay` before the refresh started are made
    /// again on the new list. The old list is not read, so a refresh can
    /// replace a list that is not UTF-8 text. A refresh replaces any that is
    /// still looking, which then neither changes the list nor calls back.
    func refresh(at start: Date = Date(),
                 _ lookUp: (@escaping (Result<[TrackedPullRequest], Error>) -> Void) -> Void,
                 completion: @escaping (Error?) -> Void) {
        dispatchPrecondition(condition: .onQueue(.main))
        refreshCount += 1
        let number = refreshCount
        refreshStart = start
        let finish = { [self] (error: Error?) in
            if number == refreshCount {
                refreshStart = nil
            }
            completion(error)
        }
        lookUp { [self] result in
            guard number == refreshCount else { return }
            let changes = recentChanges.filter { $0.time >= start - searchDelay }.map(\.change)
            switch result {
            case .failure(let error):
                finish(error)
            case .success(let pullRequests):
                rewrite({ _ in
                    changes.reduce(pullRequests.map(\.line)) { $1.applied(to: $0) }
                }, completion: finish)
            }
        }
    }

    private func rewrite(_ transform: @escaping (() throws -> [String]) throws -> [String],
                         completion: @escaping (Error?) -> Void) {
        let path = path
        queue.async {
            var problem: Error?
            do {
                try rewriteLines(ofFileAt: path, transform)
            } catch {
                problem = error
            }
            DispatchQueue.main.async {
                completion(problem)
            }
        }
    }
}

/// The GitHub search that finds the open pull requests by the given authors
/// in repositories of the given owners, either list being empty to accept
/// anyone. GitHub's search matches any of the values given for the `author:`
/// qualifier, and likewise for `user:`, which accepts users and
/// organizations. There is no search when both lists are empty, since that
/// would find every open pull request on GitHub.
func openPullRequestSearch(authors: [String], owners: [String]) -> String? {
    guard !authors.isEmpty || !owners.isEmpty else { return nil }
    return (["is:pr", "is:open"] + authors.map { "author:\($0)" } + owners.map { "user:\($0)" })
        .joined(separator: " ")
}

/// A shell script that runs a GitHub search with the gh command, and writes
/// each pull request it finds to the file named by its first argument, as a
/// line of the pull request list. Its second argument is the search. GitHub's
/// search returns at most 1000 results, so the script fails when there are
/// more.
let searchScript = """
    gh api graphql --paginate -f q="$2" -f query='
      query($q: String!, $endCursor: String) {
        search(query: $q, type: ISSUE, first: 100, after: $endCursor) {
          issueCount
          nodes { ... on PullRequest { number headRefName repository { nameWithOwner } } }
          pageInfo { hasNextPage endCursor }
        }
      }' > "$1" --jq '
      if .data.search.issueCount > 1000 then
        error("the search found \\(.data.search.issueCount) pull requests, more than the 1000 GitHub returns")
      else
        .data.search.nodes[] | [.repository.nameWithOwner, .headRefName, .number] | @tsv
      end'
    """

/// The pull requests in what `searchScript` writes.
func trackedPullRequests(fromSearchOutput text: String) throws -> [TrackedPullRequest] {
    try text.split(separator: "\n").map { line in
        guard let pullRequest = TrackedPullRequest(line: line) else {
            throw Problem(description: "the search found something that is not a pull request: \(line)")
        }
        return pullRequest
    }
}

/// How long before github-hookbar last heard from the relay a pull request
/// may have been opened without its delivery having arrived yet.
let catchUpMargin: TimeInterval = 10 * 60

/// When github-hookbar may have missed deliveries, which the relay does not
/// keep: from shortly before it last heard from the relay, by connecting or
/// by a delivery arriving, until it connects again after the connection
/// ended. Opening a new connection while connected, as reconnecting and
/// saving the settings do, is not taken to miss anything, so that new
/// settings do not run the command for pull requests opened before them.
/// It looks no earlier than when it first connected, so that it does not run
/// the command again for pull requests opened before it started.
struct MissedDeliveries {
    /// When github-hookbar last heard from the relay, while connected.
    private var heard: Date?
    private var firstConnected: Date?
    /// Where to start looking for what was missed, while there is something
    /// to look for.
    private var lookFrom: Date?

    mutating func heard(at time: Date) {
        heard = time
    }

    /// Notes a change in the connection to the relay, and returns, when it
    /// connects after missing deliveries, when to look for them from.
    mutating func changed(to state: ConnectionState) -> Date? {
        switch state {
        case .connected(let since):
            heard = since
            firstConnected = firstConnected ?? since
            defer { lookFrom = nil }
            return lookFrom
        case .connecting:
            heard = nil
        case .disconnected:
            if let heard, let firstConnected {
                notLooked(from: max(heard - catchUpMargin, firstConnected))
            }
            heard = nil
        }
        return nil
    }

    /// Notes that looking for what was missed from a time failed, so the next
    /// connection looks from then.
    mutating func notLooked(from time: Date) {
        lookFrom = min(lookFrom ?? time, time)
    }
}

/// The most pull requests each part of `openedPullRequestsQuery` asks for.
let catchUpLimit = 100

/// The GraphQL query that finds open pull requests, newest first: those of
/// each of the given authors, or, without authors, those GitHub's search
/// finds in the owners' repositories. GitHub lists an author's pull requests
/// as soon as they are opened, while its search can take a while to include
/// them. There is no query when both lists are empty, since that would cover
/// every pull request on GitHub.
func openedPullRequestsQuery(authors: [String], owners: [String]) -> String? {
    func literal(_ text: String) -> String {
        // JSON's strings are GraphQL's.
        String(decoding: try! JSONEncoder().encode(text), as: UTF8.self)
    }
    let fields = "nodes { ... on PullRequest { url number title createdAt headRefName "
        + "author { login } repository { nameWithOwner owner { login } } } }"
    let order = "orderBy: {field: CREATED_AT, direction: DESC}"
    if !authors.isEmpty {
        return "query { " + authors.enumerated().map { index, author in
            "author\(index): user(login: \(literal(author))) { "
                + "pullRequests(first: \(catchUpLimit), states: OPEN, \(order)) { \(fields) } }"
        }.joined(separator: " ") + " }"
    }
    guard let search = openPullRequestSearch(authors: [], owners: owners) else { return nil }
    return "query { owners: search(query: \(literal(search + " sort:created-desc")), type: ISSUE, "
        + "first: \(catchUpLimit)) { \(fields) } }"
}

/// The jq program that turns the answer to `openedPullRequestsQuery` into
/// the pull_request deliveries GitHub sent when the pull requests made since
/// `since` were opened, one line of JSON each. An author GitHub does not
/// know, such as a bot, which is not a user, has no pull requests. The
/// program fails when GitHub answers with any other error, and when a list
/// holds as many pull requests as the query asks for and all were made since
/// then, since there may be more.
func openedPullRequestsFilter(since: Date) -> String {
    """
    "\(ISO8601DateFormatter().string(from: since))" as $since
    | if .data == null or any(.errors[]?; .type != "NOT_FOUND") then
        error("GitHub answered: " + ([.message?, .errors[]?.message] | map(strings) | join("; ")))
      else
        [.data[] | ((.pullRequests? // .) | .nodes?) // []] as $lists
        | if any($lists[]; length == \(catchUpLimit) and all(.[]; .createdAt >= $since)) then
            error("more than \(catchUpLimit) pull requests may have been opened since \\($since)")
          else
            $lists[][] | select(.createdAt >= $since)
            | {action: "opened",
               pull_request: {html_url: .url, number, title,
                              user: {login: (.author.login // "ghost")},
                              head: {ref: .headRefName}, state: "open"},
               repository: {full_name: .repository.nameWithOwner,
                            owner: {login: .repository.owner.login}}}
            | tojson
          end
      end
    """
}

/// A shell script that runs the GraphQL query in its second argument with
/// the gh command, filters the answer through the jq program in its third,
/// and writes the result to the file named by its first. gh fails whenever
/// GitHub's answer holds an error, so the filter, not gh, decides which
/// answers fail; an answer that never arrives gives jq no input, which fails.
let catchUpScript = #"gh api graphql -f query="$2" | jq -n -r "input | $3" > "$1""#

/// The deliveries in what `catchUpScript` writes.
func openedEvents(fromCatchUpOutput text: String) throws -> [PullRequestEvent] {
    try text.split(separator: "\n").map { try PullRequestEvent(Data($0.utf8)) }
}

/// The parts of a pull_request delivery that decide whether to run the
/// command, what to run it with, and how it changes the pull request list.
struct PullRequestEvent: Decodable {
    struct Account: Decodable {
        let login: String
    }

    struct Branch: Decodable {
        let ref: String
    }

    struct PullRequest: Decodable {
        let htmlUrl: URL
        let number: Int
        let title: String
        let user: Account
        let head: Branch
        let state: String
    }

    struct Repository: Decodable {
        let fullName: String
        let owner: Account
    }

    let action: String
    let pullRequest: PullRequest
    let repository: Repository

    init(_ body: Data) throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        self = try decoder.decode(PullRequestEvent.self, from: body)
    }

    /// Whether the configuration chooses the pull request's author and
    /// repository owner.
    func isTracked(by configuration: Configuration) -> Bool {
        func allows(_ list: [String], _ value: String) -> Bool {
            list.isEmpty || list.contains(value.lowercased())
        }
        return allows(configuration.authors, pullRequest.user.login)
            && allows(configuration.orgs, repository.owner.login)
    }

    /// Whether the delivery runs the command.
    func matches(_ configuration: Configuration) -> Bool {
        isTracked(by: configuration) && configuration.actions.contains(action.lowercased())
    }

    /// How the delivery changes the pull request list: whatever its action,
    /// a tracked pull request that is open goes on it, and one that is
    /// closed, merged or not, comes off it.
    func listChange(_ configuration: Configuration) -> PullRequestListChange? {
        guard isTracked(by: configuration) else { return nil }
        let listing = TrackedPullRequest(repository: repository.fullName,
                                         branch: pullRequest.head.ref, number: pullRequest.number)
        return pullRequest.state == "open" ? .add(listing) : .remove(listing)
    }
}
