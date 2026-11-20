import Foundation
import Network

// The connection to a relay of GitHub webhook deliveries, and the parts of
// those deliveries that say a branch's checks may have changed.

// MARK: - Relay addresses

/// GitHub caps webhook payloads at 25 MB, and the request around one adds a
/// few kilobytes of headers.
let maximumRequestLength = 32 * 1024 * 1024

struct RelayAddress: Equatable, CustomStringConvertible {
    let host: String
    let port: UInt16

    var description: String { host.contains(":") ? "[\(host)]:\(port)" : "\(host):\(port)" }
}

/// Reads "host:port", with an IPv6 address written in brackets.
func parseRelayAddress(_ text: String) -> RelayAddress? {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let colon = trimmed.lastIndex(of: ":"),
          let port = UInt16(trimmed[trimmed.index(after: colon)...]), port > 0 else {
        return nil
    }
    var host = trimmed[..<colon]
    let bracketed = host.hasPrefix("[") && host.hasSuffix("]")
    if bracketed {
        host = host.dropFirst().dropLast()
    }
    guard !host.isEmpty,
          bracketed || !host.contains(":"),
          host.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || ".-:%".contains($0)) }) else {
        return nil
    }
    return RelayAddress(host: String(host), port: port)
}

// MARK: - Deliveries

/// One delivery: the headers of the HTTP request that carried it, and its
/// body, exactly as GitHub sent it.
struct Delivery {
    /// The header values, by header name in lowercase. Proxies on the way
    /// can change the case of header names.
    let headers: [String: String]
    /// The part of the request after the headers, sharing its storage.
    let body: Data

    /// Splits an HTTP request into its headers and body. A line ends at a
    /// line feed, and a carriage return before it is dropped. The headers
    /// end at the first empty line; a request with no empty line is not a
    /// delivery.
    init?(_ request: Data) {
        var headers: [String: String] = [:]
        var lineStart = request.startIndex
        var isRequestLine = true
        while let end = request[lineStart...].firstIndex(of: UInt8(ascii: "\n")) {
            var line = request[lineStart..<end]
            lineStart = end + 1
            if line.last == UInt8(ascii: "\r") {
                line = line.dropLast()
            }
            if line.isEmpty {
                // An empty line before the request line is passed over, as
                // RFC 9112 says.
                if isRequestLine { continue }
                self.headers = headers
                body = request[lineStart...]
                return
            }
            // The first line is the request line, not a header. The values
            // are bytes, read as Latin-1, which every byte is.
            if isRequestLine {
                isRequestLine = false
                continue
            }
            let text = String(String.UnicodeScalarView(line.lazy.map { Unicode.Scalar($0) }))
            let parts = text.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            if parts.count == 2 {
                headers[parts[0].lowercased()] = parts[1].trimmingCharacters(in: .whitespaces)
            }
        }
        return nil
    }
}

// MARK: - Events

/// A delivery of one of the events ghbar reads that did not parse.
struct UnreadableDelivery: Error, CustomStringConvertible {
    let event: String
    let reason: String

    var description: String { "A \(event) delivery could not be read: \(reason)" }
}

/// A delivery that says something about the checks on a commit, or about
/// where a branch is.
struct WebhookEvent {
    enum Kind {
        /// The branch now points at `sha`, or, if `deleted`, no longer
        /// exists. `at` is when the push happened, if GitHub said.
        case push(headline: String?, deleted: Bool, at: Date?)
        case runStarted(suite: Int, run: String, url: URL?, at: Date)
        case runFailed(suite: Int, run: String, url: URL?, at: Date)
        case suiteFinished(suite: Int, state: CheckState, detail: String, at: Date)
        /// A check run, or a whole check suite, was cancelled. Whether that
        /// is a failure turns on whether a runner was running the job it
        /// stopped, which the delivery does not say.
        case cancelled(suite: Int)
        case status(context: String, StatusRecord)
    }

    /// The repository, as "owner/name".
    let repository: String
    /// The commit the delivery is about.
    let sha: String
    /// The branch the delivery names: the branch a push moved, or the
    /// branch a check suite was started for. GitHub names no branch for a
    /// check suite started in a fork, or for a commit status.
    let branch: String?
    /// The commit the branch was on before the push that the delivery
    /// tells of, or that started the check suite it tells of.
    let before: String?
    let kind: Kind

    /// Reads a check run, check suite, commit status or push delivery. A
    /// check run or suite that was cancelled is read as a cancellation, for
    /// GitHub's answer to settle. Returns nil for any other delivery; for a
    /// check run that finished without failing, which says nothing about its
    /// suite until the suite finishes; and for check runs and suites of apps
    /// other than GitHub Actions, which GitHub's answers to a fine-grained
    /// token leave out.
    init?(_ delivery: Delivery) throws(UnreadableDelivery) {
        let name = delivery.headers["x-github-event"] ?? ""
        do throws(JSONError) {
            switch name {
            case "check_run":
                let payload = try checkRunPaths.read(delivery.body)
                guard try payload.string("check_run.app.slug") == actionsApp else { return nil }
                let suite = try payload.int("check_run.check_suite.id")
                let run = try payload.string("check_run.name")
                let details = try payload.optionalString("check_run.details_url")
                let page = try payload.optionalString("check_run.html_url")
                let url = (details ?? page).flatMap(URL.init(string:))
                let conclusion = try payload.optionalString("check_run.conclusion")
                if conclusion == "cancelled" {
                    kind = .cancelled(suite: suite)
                } else {
                    switch checkState(runStatus: try payload.string("check_run.status"), conclusion: conclusion) {
                    case .running:
                        kind = .runStarted(suite: suite, run: run, url: url,
                                           at: try payload.optionalDate("check_run.started_at") ?? Date())
                    case .failing:
                        kind = .runFailed(suite: suite, run: run, url: url,
                                          at: try payload.optionalDate("check_run.completed_at") ?? Date())
                    case .passing, .other:
                        return nil
                    }
                }
                repository = try payload.string("repository.full_name")
                sha = try payload.string("check_run.head_sha")
                branch = try payload.optionalString("check_run.check_suite.head_branch")
                before = try payload.optionalString("check_run.check_suite.before")
            case "check_suite":
                let payload = try checkSuitePaths.read(delivery.body)
                guard try payload.optionalString("check_suite.status") == "completed",
                      try payload.string("check_suite.app.slug") == actionsApp else { return nil }
                let suite = try payload.int("check_suite.id")
                let conclusion = try payload.optionalString("check_suite.conclusion")
                let updated = try payload.date("check_suite.updated_at")
                repository = try payload.string("repository.full_name")
                sha = try payload.string("check_suite.head_sha")
                branch = try payload.optionalString("check_suite.head_branch")
                before = try payload.optionalString("check_suite.before")
                kind = conclusion == "cancelled"
                    ? .cancelled(suite: suite)
                    : .suiteFinished(suite: suite,
                                     state: checkState(runStatus: "completed", conclusion: conclusion),
                                     detail: conclusion ?? "completed", at: updated)
            case "status":
                let payload = try statusPaths.read(delivery.body)
                let state = try payload.string("state")
                repository = try payload.string("repository.full_name")
                sha = try payload.string("sha")
                branch = nil
                before = nil
                kind = .status(context: try payload.string("context"),
                               StatusRecord(id: try payload.int("id"), state: checkState(commitStatus: state),
                                            detail: try payload.optionalString("description") ?? state,
                                            url: try payload.optionalString("target_url").flatMap(URL.init(string:))))
            case "push":
                let payload = try pushPaths.read(delivery.body)
                let ref = try payload.string("ref")
                let prefix = "refs/heads/"
                guard ref.hasPrefix(prefix) else { return nil }
                repository = try payload.string("repository.full_name")
                sha = try payload.string("after")
                branch = String(ref.dropFirst(prefix.count))
                before = try payload.string("before")
                // The time of the push is read if it is in a form GitHub uses
                // for times, and left out otherwise, since the push is worth
                // reading without it.
                kind = .push(headline: try payload.optionalString("head_commit.message").map(firstLine),
                             deleted: try payload.optionalBool("deleted") ?? false,
                             at: payload.value("repository.pushed_at")?.date)
            default:
                return nil
            }
        } catch {
            throw UnreadableDelivery(event: name, reason: error.description)
        }
    }
}

/// The app that runs GitHub Actions workflows.
let actionsApp = "github-actions"

private let checkRunPaths = JSONPaths([
    "check_run.head_sha", "check_run.name", "check_run.status", "check_run.conclusion",
    "check_run.started_at", "check_run.completed_at", "check_run.details_url", "check_run.html_url",
    "check_run.check_suite.id", "check_run.check_suite.head_branch", "check_run.check_suite.before",
    "check_run.app.slug", "repository.full_name",
])

private let checkSuitePaths = JSONPaths([
    "check_suite.id", "check_suite.head_sha", "check_suite.head_branch", "check_suite.before",
    "check_suite.status", "check_suite.conclusion", "check_suite.updated_at", "check_suite.app.slug",
    "repository.full_name",
])

private let statusPaths = JSONPaths([
    "id", "sha", "context", "state", "description", "target_url", "repository.full_name",
])

private let pushPaths = JSONPaths([
    "ref", "before", "after", "deleted", "head_commit.message", "repository.full_name",
    "repository.pushed_at",
])

// MARK: - The connection

/// One connection to the relay, which sends each delivery as a frame and
/// nothing in between. A frame is the length of an HTTP request, as a 32-bit
/// big-endian integer, followed by the request. The connection reports when
/// it is up, each delivery, each frame that is not an HTTP request, and once
/// that it has ended; a frame that the end of the connection cuts short is
/// not a delivery. All methods and callbacks run on the main thread.
final class RelayConnection {
    var onReady: (() -> Void)?
    var onDelivery: ((Delivery) -> Void)?
    var onUnreadable: ((String) -> Void)?
    var onEnded: ((String) -> Void)?
    private let connection: NWConnection
    private var ended = false

    init(_ address: RelayAddress) {
        // The relay sends nothing while no deliveries arrive, so keepalive
        // probes are what notice a connection that died silently, for
        // example while the computer slept.
        let tcp = NWProtocolTCP.Options()
        tcp.enableKeepalive = true
        tcp.keepaliveIdle = 60
        tcp.keepaliveInterval = 10
        tcp.keepaliveCount = 3
        connection = NWConnection(host: NWEndpoint.Host(address.host),
                                  port: NWEndpoint.Port(integerLiteral: address.port),
                                  using: NWParameters(tls: nil, tcp: tcp))
    }

    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                self?.onReady?()
            case .waiting(let error), .failed(let error):
                self?.end(describe(error))
            default:
                break
            }
        }
        connection.start(queue: .main)
        receive()
    }

    func cancel() {
        ended = true
        connection.cancel()
    }

    deinit {
        connection.cancel()
    }

    private func end(_ reason: String) {
        guard !ended else { return }
        cancel()
        onEnded?(reason)
    }

    private func receive() {
        read(4) { [weak self] prefix in
            guard let self else { return }
            let length = prefix.reduce(0) { $0 << 8 | Int($1) }
            guard length <= maximumRequestLength else {
                return end("the relay sent a frame of \(length) bytes")
            }
            read(length) { [weak self] request in
                guard let self else { return }
                if let delivery = Delivery(request) {
                    onDelivery?(delivery)
                } else {
                    onUnreadable?("A frame from the relay was not an HTTP request")
                }
                receive()
            }
        }
    }

    /// Hands the next `count` bytes to `then` once they have all arrived,
    /// or ends the connection if it fails or closes first.
    private func read(_ count: Int, then: @escaping (Data) -> Void) {
        guard count > 0 else { return then(Data()) }
        connection.receive(minimumIncompleteLength: count, maximumLength: count) {
            [weak self] data, _, _, error in
            guard let self, !ended else { return }
            if let data, data.count == count {
                then(data)
            } else if let error {
                end(describe(error))
            } else {
                end("the relay closed the connection")
            }
        }
    }
}

func describe(_ error: NWError) -> String {
    switch error {
    case .posix(let code):
        return String(cString: strerror(code.rawValue))
    case .dns(let code):
        return "could not look up the relay's address (\(code))"
    default:
        return error.localizedDescription
    }
}
