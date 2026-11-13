import CryptoKit
import Foundation
import Network

// Signing in to Claude accounts, and asking how much of their weekly quotas
// they have used. quotabar signs in as Claude Code does, through the browser,
// and keeps tokens of its own, so it never shares a refresh token with Claude
// Code.

/// A refusal that only signing in again can fix: the token endpoint refused
/// a refresh token or a sign-in code.
struct SignInNeeded: LocalizedError, CustomStringConvertible {
    let description: String
    var errorDescription: String? { description }
}

/// An answer from a server other than a success.
struct HTTPFailure: LocalizedError, CustomStringConvertible {
    let host: String
    let status: Int
    let body: Data
    var description: String {
        let line = String(decoding: body.prefix(200), as: UTF8.self).split(separator: "\n").first ?? ""
        return "\(host) answered \(status): \(line)"
    }
    var errorDescription: String? { description }
}

/// A random string of 43 URL-safe characters, for a PKCE verifier or an
/// OAuth state.
func randomToken() -> String {
    base64URL(Data((0..<32).map { _ in UInt8.random(in: .min ... .max) }))
}

/// Base64url without padding, as PKCE (RFC 7636) spells its values.
func base64URL(_ data: Data) -> String {
    data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
}

func challenge(of verifier: String) -> String {
    base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
}

/// The "error" of an OAuth error answer, which the token endpoint sends
/// either as a string or as an object with a "type".
func oauthError(_ data: Data) -> String? {
    struct Named: Decodable {
        let type: String
    }
    struct Answer: Decodable {
        let error: String?
        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            error = (try? container.decode(String.self, forKey: .error))
                ?? (try? container.decode(Named.self, forKey: .error))?.type
        }
        enum CodingKeys: CodingKey {
            case error
        }
    }
    return (try? JSONDecoder().decode(Answer.self, from: data))?.error
}

struct Service: Sendable {
    /// Claude Code's sign-in client.
    static let clientID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    /// The usage endpoint needs only this scope, so quotabar does not ask
    /// for the scope for running models.
    static let scope = "user:profile"
    static let authorizeURL = URL(string: "https://claude.com/cai/oauth/authorize")!
    static let tokenURL = URL(string: "https://platform.claude.com/v1/oauth/token")!
    /// The usage endpoint, asked to include banked resets as it does for
    /// Claude Code.
    static let usageURL =
        URL(string: "https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1")!
    /// The server reports banked resets only to the surfaces it offers them
    /// on, which it tells apart by these headers, so quotabar sends Claude
    /// Code's, from the version of Claude Code they were read from, with its
    /// own name added.
    static let identity = ["User-Agent": "claude-cli/2.1.311 (external, cli) quotabar/1", "x-app": "cli"]

    /// Requests fail at once while the Mac is offline; the watcher holds
    /// checks back until the network is there.
    var session = URLSession.shared
    var now: @Sendable () -> Date = { Date() }

    /// The sign-in page, asking for the account with the email address
    /// `hint`, when there is one.
    static func signInPage(redirect: String, challenge: String, state: String, hint: String?) -> URL {
        var components = URLComponents(url: Service.authorizeURL, resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "code", value: "true"),
            URLQueryItem(name: "client_id", value: Service.clientID),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "redirect_uri", value: redirect),
            URLQueryItem(name: "scope", value: Service.scope),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "state", value: state),
        ] + (hint.map { [URLQueryItem(name: "login_hint", value: $0)] } ?? [])
        return components.url!
    }

    /// Signs in to an account: has `open` show the sign-in page, waits for
    /// the browser to come back to the listener, and trades the code it
    /// brings for tokens.
    func signIn(through listener: CallbackListener, hint: String? = nil,
                open: @MainActor (URL) -> Bool) async throws -> Account {
        let redirect = "http://localhost:\(try await listener.port())/callback"
        let verifier = randomToken()
        try Task.checkCancellation()
        guard await open(Service.signInPage(redirect: redirect, challenge: challenge(of: verifier),
                                            state: listener.state, hint: hint)) else {
            listener.cancel()
            throw Problem(description: "the sign-in page did not open in a browser")
        }
        let answer = try decode(SignInAnswer.self, from: try await postTokens([
            "grant_type": "authorization_code",
            "code": try await listener.code(),
            "redirect_uri": redirect,
            "client_id": Service.clientID,
            "code_verifier": verifier,
            "state": listener.state,
        ]), what: "sign-in answer")
        guard let account = answer.account, let organization = answer.organization else {
            throw Problem(description: "the sign-in answer does not say which account it is for")
        }
        guard let access = answer.access_token, let expiresIn = answer.expires_in,
              let refresh = answer.refresh_token else {
            throw Problem(description: "the sign-in answer is missing tokens")
        }
        return Account(id: "\(account.uuid)/\(organization.uuid)", email: account.email_address,
                       organization: organization.name ?? "",
                       tokens: Tokens(access: access, refresh: refresh,
                                      expires: now().addingTimeInterval(expiresIn)))
    }

    /// New tokens in place of `tokens`. The old refresh token stops working
    /// once the server answers, so `save` gets the new one as soon as it
    /// arrives, even from an answer whose other parts cannot be read.
    func refresh(_ tokens: Tokens, save: @MainActor (Tokens) -> Void) async throws -> Tokens {
        struct Rotated: Decodable {
            let refresh_token: String?
        }
        struct Fresh: Decodable {
            let access_token: String
            let expires_in: TimeInterval
        }
        let data = try await postTokens([
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh,
            "client_id": Service.clientID,
            "scope": Service.scope,
        ])
        var renewed = tokens
        // An answer without a refresh token leaves the old one working, as
        // OAuth allows. One whose refresh token cannot be read leaves none,
        // since the server has spent the old one.
        if let rotated = try decode(Rotated.self, from: data, what: "refresh answer").refresh_token {
            guard !rotated.isEmpty else {
                throw Problem(description: "the refresh answer's refresh token is empty")
            }
            renewed.refresh = rotated
        }
        let fresh: Fresh
        do {
            fresh = try decode(Fresh.self, from: data, what: "refresh answer")
        } catch {
            if renewed != tokens {
                renewed.expires = now()
                await save(renewed)
            }
            throw error
        }
        renewed.access = fresh.access_token
        renewed.expires = now().addingTimeInterval(fresh.expires_in)
        await save(renewed)
        return renewed
    }

    func usage(_ accessToken: String) async throws -> WeeklyUsage {
        var request = URLRequest(url: Service.usageURL)
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("oauth-2025-04-20", forHTTPHeaderField: "anthropic-beta")
        return try parseUsage(try await send(request))
    }

    /// The account's weekly usage, refreshing its tokens when they have
    /// expired or the usage endpoint refuses them (401). `refreshing` hears
    /// when a refresh starts and ends, and `save` gets new tokens as soon as
    /// they arrive, before anything else can fail, because the old refresh
    /// token no longer works.
    func usage(of account: Account, refreshing: @MainActor (Bool) -> Void = { _ in },
               save: @MainActor (Tokens) -> Void) async throws -> WeeklyUsage {
        func renew(_ tokens: Tokens) async throws -> Tokens {
            await refreshing(true)
            do {
                let renewed = try await refresh(tokens, save: save)
                await refreshing(false)
                return renewed
            } catch {
                await refreshing(false)
                throw error
            }
        }
        var tokens = account.tokens
        let expired = tokens.expires <= now()
        if expired {
            tokens = try await renew(tokens)
        }
        do {
            return try await usage(tokens.access)
        } catch let failure as HTTPFailure where failure.status == 401 && !expired {
            return try await usage(try await renew(tokens).access)
        }
    }

    private struct SignInAnswer: Decodable {
        struct Who: Decodable {
            let uuid: String
            let email_address: String
        }
        struct Organization: Decodable {
            let uuid: String
            let name: String?
        }
        let access_token: String?
        let refresh_token: String?
        let expires_in: TimeInterval?
        let account: Who?
        let organization: Organization?
    }

    /// Posts to the token endpoint, and returns the answer. Throws
    /// SignInNeeded when it refuses the grant, which Claude Code judges by a
    /// 400 or 401 answer whose error is "invalid_grant".
    private func postTokens(_ fields: [String: String]) async throws -> Data {
        var request = URLRequest(url: Service.tokenURL)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(fields)
        do {
            return try await send(request)
        } catch let failure as HTTPFailure
            where [400, 401].contains(failure.status) && oauthError(failure.body) == "invalid_grant" {
            throw SignInNeeded(description: "\(failure)")
        }
    }

    /// The body of a successful answer. Throws HTTPFailure for any other.
    private func send(_ request: URLRequest) async throws -> Data {
        var request = request
        for (name, value) in Service.identity {
            request.setValue(value, forHTTPHeaderField: name)
        }
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw HTTPFailure(host: request.url!.host!, status: status, body: data)
        }
        return data
    }
}

/// What a request that reaches the sign-in listener says.
enum Callback: Equatable {
    /// The browser brought back a code for this sign-in.
    case code(String)
    /// The browser came back for this sign-in without a code.
    case refused(String)
    /// The request is not about this sign-in.
    case ignored
}

/// Reads the request line of an HTTP request to the sign-in listener, such
/// as "GET /callback?code=abc&state=xyz HTTP/1.1".
func callback(from requestLine: String, state: String) -> Callback {
    let parts = requestLine.split(separator: " ")
    guard parts.count == 3, parts[0] == "GET",
          let url = URLComponents(string: "http://localhost\(parts[1])"),
          url.path == "/callback" else {
        return .ignored
    }
    func value(_ name: String) -> String? {
        url.queryItems?.first(where: { $0.name == name })?.value
    }
    guard value("state") == state else {
        return .ignored
    }
    if let code = value("code"), !code.isEmpty {
        return .code(code)
    }
    return .refused(value("error_description") ?? value("error") ?? "no code came back")
}

/// A value that arrives once, for any number of waiters. Used only on one
/// dispatch queue.
private final class Eventually<Value: Sendable>: @unchecked Sendable {
    private var result: Result<Value, Error>?
    private var waiting: [CheckedContinuation<Value, Error>] = []

    var isSettled: Bool { result != nil }

    /// Gives the value, or the failure, to the waiters. Only the first call
    /// does anything.
    func settle(_ outcome: Result<Value, Error>) {
        guard result == nil else {
            return
        }
        result = outcome
        for waiter in waiting {
            waiter.resume(with: outcome)
        }
        waiting = []
    }

    func wait(on queue: DispatchQueue) async throws -> Value {
        try await withCheckedThrowingContinuation { continuation in
            queue.async {
                if let result = self.result {
                    continuation.resume(with: result)
                } else {
                    self.waiting.append(continuation)
                }
            }
        }
    }
}

/// Listens on the loopback interface for the browser to come back from
/// signing in, until it brings a code, or the sign-in fails or is
/// cancelled.
final class CallbackListener: @unchecked Sendable {
    let state = randomToken()

    private let listener: NWListener
    private let queue = DispatchQueue(label: "quotabar.callback")
    // These are used only on the queue.
    private let started = Eventually<UInt16>()
    private let finished = Eventually<String>()
    private var connections: [ObjectIdentifier: NWConnection] = [:]

    /// Starts listening.
    init() throws {
        let parameters = NWParameters.tcp
        parameters.requiredInterfaceType = .loopback
        listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { [weak self] connection in
            guard let self, !finished.isSettled else {
                return connection.cancel()
            }
            connections[ObjectIdentifier(connection)] = connection
            connection.start(queue: queue)
            read(connection, received: Data())
        }
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else {
                return
            }
            switch state {
            case .ready:
                started.settle(.success(listener.port!.rawValue))
            case .waiting(let error), .failed(let error):
                finish(.failure(error))
            case .cancelled:
                finish(.failure(CancellationError()))
            default:
                return
            }
        }
        listener.start(queue: queue)
    }

    /// The port the listener is on, once it knows.
    func port() async throws -> UInt16 {
        try await started.wait(on: queue)
    }

    /// The code the browser brings back. Throws when the sign-in fails or
    /// is cancelled.
    func code() async throws -> String {
        try await finished.wait(on: queue)
    }

    func cancel() {
        queue.async {
            self.finish(.failure(CancellationError()))
        }
    }

    /// Ends the sign-in, closing the listener and every connection to it.
    private func finish(_ outcome: Result<String, Error>) {
        if case .failure(let error) = outcome {
            started.settle(.failure(error))
        }
        finished.settle(outcome)
        listener.cancel()
        for connection in connections.values {
            connection.cancel()
        }
        connections = [:]
    }

    private func close(_ connection: NWConnection) {
        connections[ObjectIdentifier(connection)] = nil
        connection.cancel()
    }

    private func read(_ connection: NWConnection, received: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 8192) {
            data, _, complete, error in
            let received = received + (data ?? Data())
            // Answering only once the whole request has arrived means closing
            // the connection leaves nothing unread, which would reset it.
            if received.firstRange(of: Data("\r\n\r\n".utf8)) != nil {
                let line = received.prefix { $0 != UInt8(ascii: "\r") }
                self.answer(connection, callback(from: String(decoding: line, as: UTF8.self),
                                                 state: self.state))
            } else if complete || error != nil || received.count > 1 << 20 {
                self.close(connection)
            } else {
                self.read(connection, received: received)
            }
        }
    }

    private func answer(_ connection: NWConnection, _ outcome: Callback) {
        let (status, text): (String, String)
        switch outcome {
        case .code:
            (status, text) = ("200 OK", "quotabar is signed in. You can close this tab.")
        case .refused(let reason):
            (status, text) = ("400 Bad Request", "quotabar could not sign in: \(reason)")
        case .ignored:
            (status, text) = ("404 Not Found", "quotabar is not waiting for this.")
        }
        let body = Data(text.utf8)
        let head = "HTTP/1.1 \(status)\r\nContent-Type: text/plain; charset=utf-8\r\n"
            + "Content-Length: \(body.count)\r\nConnection: close\r\n\r\n"
        connection.send(content: Data(head.utf8) + body, completion: .contentProcessed { _ in
            self.close(connection)
            switch outcome {
            case .code(let code):
                self.finish(.success(code))
            case .refused(let reason):
                self.finish(.failure(Problem(description: "the sign-in failed: \(reason)")))
            case .ignored:
                return
            }
        })
    }
}
