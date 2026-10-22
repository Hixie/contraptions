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

// MARK: - Frames

struct FrameTooLong: Error {
    let length: Int
}

/// Takes every complete frame out of the buffer, leaving the start of any
/// frame that has not all arrived. A frame is the length of an HTTP request,
/// as a 32-bit big-endian integer, followed by the request. Consumed bytes
/// are removed rather than sliced off, so that their storage is freed.
func takeFrames(from buffer: inout Data) throws(FrameTooLong) -> [Data] {
    var frames: [Data] = []
    var offset = buffer.startIndex
    while buffer.endIndex - offset >= 4 {
        let length = buffer[offset..<offset + 4].reduce(0) { $0 << 8 | Int($1) }
        guard length <= maximumRequestLength else {
            throw FrameTooLong(length: length)
        }
        guard buffer.endIndex - offset - 4 >= length else { break }
        frames.append(Data(buffer[offset + 4..<offset + 4 + length]))
        offset += 4 + length
    }
    buffer.removeSubrange(buffer.startIndex..<offset)
    return frames
}

/// One delivery: the headers of the HTTP request that carried it, and its
/// body, exactly as GitHub sent it.
struct Delivery {
    /// The header values, by header name in lowercase. Proxies on the way
    /// can change the case of header names.
    let headers: [String: String]
    let body: Data

    /// Splits an HTTP request into its headers and body.
    init?(_ request: Data) {
        guard let blank = request.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        var headers: [String: String] = [:]
        // The first line is the request line, not a header. The values are
        // bytes, read as Latin-1, which every byte is.
        for line in request[..<blank.lowerBound].split(separator: Data("\r\n".utf8)).dropFirst() {
            let text = String(String.UnicodeScalarView(line.map { Unicode.Scalar($0) }))
            let parts = text.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            if parts.count == 2 {
                headers[parts[0].lowercased()] = parts[1].trimmingCharacters(in: .whitespaces)
            }
        }
        self.headers = headers
        body = Data(request[blank.upperBound...])
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
        do {
            switch name {
            case "check_run":
                let payload = try gitHubDecoder.decode(CheckRunPayload.self, from: delivery.body)
                let run = payload.checkRun
                guard run.app.slug == actionsApp else { return nil }
                let url = (run.detailsUrl ?? run.htmlUrl).flatMap(URL.init(string:))
                if run.conclusion == "cancelled" {
                    kind = .cancelled(suite: run.checkSuite.id)
                } else {
                    switch checkState(runStatus: run.status, conclusion: run.conclusion) {
                    case .running:
                        kind = .runStarted(suite: run.checkSuite.id, run: run.name, url: url,
                                           at: run.startedAt ?? Date())
                    case .failing:
                        kind = .runFailed(suite: run.checkSuite.id, run: run.name, url: url,
                                          at: run.completedAt ?? Date())
                    case .passing, .other:
                        return nil
                    }
                }
                repository = payload.repository.fullName
                sha = run.headSha
                branch = run.checkSuite.headBranch
                before = run.checkSuite.before
            case "check_suite":
                let payload = try gitHubDecoder.decode(CheckSuitePayload.self, from: delivery.body)
                let suite = payload.checkSuite
                guard suite.status == "completed", suite.app.slug == actionsApp else { return nil }
                repository = payload.repository.fullName
                sha = suite.headSha
                branch = suite.headBranch
                before = suite.before
                kind = suite.conclusion == "cancelled"
                    ? .cancelled(suite: suite.id)
                    : .suiteFinished(suite: suite.id,
                                     state: checkState(runStatus: "completed", conclusion: suite.conclusion),
                                     detail: suite.conclusion ?? "completed", at: suite.updatedAt)
            case "status":
                let payload = try gitHubDecoder.decode(StatusPayload.self, from: delivery.body)
                repository = payload.repository.fullName
                sha = payload.sha
                branch = nil
                before = nil
                kind = .status(context: payload.context,
                               StatusRecord(id: payload.id, state: checkState(commitStatus: payload.state),
                                            detail: payload.description ?? payload.state,
                                            url: payload.targetUrl.flatMap(URL.init(string:))))
            case "push":
                let payload = try gitHubDecoder.decode(PushPayload.self, from: delivery.body)
                let prefix = "refs/heads/"
                guard payload.ref.hasPrefix(prefix) else { return nil }
                repository = payload.repository.fullName
                sha = payload.after
                branch = String(payload.ref.dropFirst(prefix.count))
                before = payload.before
                kind = .push(headline: payload.headCommit.map { firstLine($0.message) },
                             deleted: payload.deleted ?? false, at: payload.repository.pushedAt)
            default:
                return nil
            }
        } catch {
            throw UnreadableDelivery(event: name, reason: error.localizedDescription)
        }
    }
}

/// The app that runs GitHub Actions workflows.
let actionsApp = "github-actions"

private struct RepositoryField: Decodable {
    let fullName: String
}

private struct AppField: Decodable {
    let slug: String
}

private struct CheckRunPayload: Decodable {
    struct Run: Decodable {
        struct Suite: Decodable {
            let id: Int
            let headBranch: String?
            let before: String?
        }
        let headSha: String
        let name: String
        let status: String
        let conclusion: String?
        let startedAt: Date?
        let completedAt: Date?
        let detailsUrl: String?
        let htmlUrl: String?
        let checkSuite: Suite
        let app: AppField
    }
    let checkRun: Run
    let repository: RepositoryField
}

private struct CheckSuitePayload: Decodable {
    struct Suite: Decodable {
        let id: Int
        let headSha: String
        let headBranch: String?
        let before: String?
        let status: String?
        let conclusion: String?
        let updatedAt: Date
        let app: AppField
    }
    let checkSuite: Suite
    let repository: RepositoryField
}

private struct StatusPayload: Decodable {
    let id: Int
    let sha: String
    let context: String
    let state: String
    let description: String?
    let targetUrl: String?
    let repository: RepositoryField
}

private struct PushPayload: Decodable {
    struct Commit: Decodable {
        let message: String
    }
    struct Repository: Decodable {
        let fullName: String
        let pushedAt: Date?

        private enum CodingKeys: String, CodingKey {
            case fullName, pushedAt
        }

        /// The push's time is read if it is in a form GitHub uses for times,
        /// and left out otherwise, since the push is worth reading without it.
        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            fullName = try container.decode(String.self, forKey: .fullName)
            pushedAt = (try? container.decodeIfPresent(FlexibleTime.self, forKey: .pushedAt))??.date
        }
    }
    let ref: String
    let before: String
    let after: String
    let deleted: Bool?
    let headCommit: Commit?
    let repository: Repository
}

/// A time GitHub gives either as seconds since 1970, as it does for when a
/// push happened, or as an ISO 8601 string.
private struct FlexibleTime: Decodable {
    let date: Date

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let seconds = try? container.decode(Double.self) {
            date = Date(timeIntervalSince1970: seconds)
        } else {
            date = try container.decode(Date.self)
        }
    }
}

// MARK: - The connection

/// One connection to the relay, which sends each delivery as a frame and
/// nothing in between. It reports when the connection is up, each delivery,
/// each frame that is not an HTTP request, and once that the connection has
/// ended; a frame that the end of the connection cuts short is not a
/// delivery. All methods and callbacks run on the main thread.
final class RelayConnection {
    var onReady: (() -> Void)?
    var onDelivery: ((Delivery) -> Void)?
    var onUnreadable: ((String) -> Void)?
    var onEnded: ((String) -> Void)?
    private let connection: NWConnection
    private var buffer = Data()
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

    private func end(_ reason: String) {
        guard !ended else { return }
        cancel()
        onEnded?(reason)
    }

    private func receive() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) {
            [weak self] data, _, isComplete, error in
            guard let self, !ended else { return }
            if let data {
                buffer.append(data)
                do throws(FrameTooLong) {
                    for frame in try takeFrames(from: &buffer) {
                        guard !ended else { return }
                        if let delivery = Delivery(frame) {
                            onDelivery?(delivery)
                        } else {
                            onUnreadable?("A frame from the relay was not an HTTP request")
                        }
                    }
                } catch {
                    end("the relay sent a frame of \(error.length) bytes")
                }
            }
            if ended {
                return
            } else if let error {
                end(describe(error))
            } else if isComplete {
                end("the relay closed the connection")
            } else {
                receive()
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
