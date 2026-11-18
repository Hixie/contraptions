import Foundation

// Keeping a connection open, by opening a new one whenever one ends.

/// After a connection fails, the first attempt to connect again waits this
/// long, and each failure after that doubles the wait, up to the maximum.
let firstRetryDelay: TimeInterval = 1
let maximumRetryDelay: TimeInterval = 64

/// One connection, which reports once when it is up and once when it has
/// ended. It reports nothing after it is cancelled.
protocol Connection: AnyObject {
    var onReady: (() -> Void)? { get set }
    var onEnded: ((String) -> Void)? { get set }
    func start()
    func cancel()
}

enum ConnectionState {
    case connecting
    case connected(since: Date)
    case disconnected(reason: String, retryAt: Date?)
}

/// A connection that is opened again whenever it ends: after a wait that
/// starts at `firstRetryDelay` and doubles with each failure, up to
/// `maximumRetryDelay`. A connection that lasted longer than the next wait
/// would be counts as a success, and starts the waits again from the first;
/// one that ended sooner, as one does when a proxy accepts it and then finds
/// the server behind it down, makes the next wait longer. All methods and
/// callbacks run on the main thread.
final class PersistentConnection {
    private(set) var state = ConnectionState.disconnected(reason: "not started", retryAt: nil)
    /// Called whenever the state changes.
    var onChange: (() -> Void)?
    private let firstDelay: TimeInterval
    private let now: () -> Date
    private var open: (() -> Connection)?
    private var connection: Connection?
    private var retryDelay: TimeInterval
    private var retry: DispatchWorkItem?

    init(firstRetryDelay: TimeInterval = firstRetryDelay, now: @escaping () -> Date = Date.init) {
        firstDelay = firstRetryDelay
        retryDelay = firstRetryDelay
        self.now = now
    }

    /// Ends any connection there is, and opens one with `open` now, and
    /// again whenever one ends.
    func start(_ open: @escaping () -> Connection) {
        self.open = open
        connect()
    }

    /// Ends any connection there is, and opens no more until `start`.
    func stop(_ reason: String) {
        open = nil
        end()
        set(.disconnected(reason: reason, retryAt: nil))
    }

    /// Connects straight away if not connected and not stopped, as is worth
    /// doing when the computer wakes.
    func wake() {
        if case .disconnected = state, open != nil {
            connect()
        }
    }

    private func end() {
        retry?.cancel()
        retry = nil
        connection?.cancel()
        connection = nil
    }

    private func connect() {
        guard let open else { return }
        end()
        let connection = open()
        connection.onReady = { [weak self, weak connection] in
            guard let self, let connection, connection === self.connection else { return }
            set(.connected(since: now()))
        }
        connection.onEnded = { [weak self, weak connection] reason in
            guard let self, let connection, connection === self.connection else { return }
            ended(reason)
        }
        self.connection = connection
        set(.connecting)
        connection.start()
    }

    private func ended(_ reason: String) {
        connection = nil
        if case .connected(let since) = state, now().timeIntervalSince(since) > retryDelay {
            retryDelay = firstDelay
        }
        let retry = DispatchWorkItem { [weak self] in
            self?.connect()
        }
        self.retry = retry
        DispatchQueue.main.asyncAfter(deadline: .now() + retryDelay, execute: retry)
        set(.disconnected(reason: reason, retryAt: now() + retryDelay))
        retryDelay = min(retryDelay * 2, maximumRetryDelay)
    }

    private func set(_ state: ConnectionState) {
        self.state = state
        onChange?()
    }
}
