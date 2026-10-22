import Foundation

// Keeping track of one branch: asking GitHub about it at an interval, or,
// while a webhook relay is connected, following what GitHub delivers.

// MARK: - The ledger

/// How many commits the ledger keeps deliveries about. Deliveries about a
/// commit can arrive before the push that puts the branch on it.
let rememberedCommits = 64

/// What to do about a webhook delivery.
enum LedgerReaction {
    case ignore
    /// Show the branch, and ask GitHub too if `fetch`.
    case changed(BranchStatus, fetch: Bool = false)
    /// Ask GitHub, because the delivery shows that something went
    /// undelivered, or says something only GitHub's answer settles.
    case fetch
}

/// One branch: the commit it is on, and what GitHub has said about the
/// checks on that commit and on others that it may move to, in its answers
/// and in its deliveries alike.
///
/// Pushes move the branch: the latest push, by GitHub's time, wins, so
/// pushes delivered out of order or delivered again leave the branch where
/// the last of them put it. A push GitHub gives no time for, the first one
/// heard of, or one in the same second as the last, moves the branch only
/// if it starts from the commit the branch is on and does not lead back to
/// one it has left; otherwise it asks GitHub. Check suites never move the
/// branch; one on the branch at a commit it has not been on means a push
/// went undelivered, and asks GitHub, which says where the branch is. An
/// answer that disagrees with the deliveries is checked once more, and
/// believed if it says the same again. Whether a cancelled check run or suite
/// failed is left to GitHub's answer, which says whether a runner was running
/// what it stopped, and GitHub is asked until an answer shows the suite's
/// latest attempt finished.
final class BranchLedger {
    let repository: RepositoryReference
    let branch: String
    private var head: String?
    private var headline: String?
    /// Commits the branch has moved away from, as deliveries since the
    /// relay connection came up showed.
    private var formerHeads: Set<String> = []
    /// When the latest push delivered happened.
    private var lastPush: Date?
    /// Commits that pushes or check suites made the ledger ask GitHub
    /// about; those that GitHub's last answer said the branch is not on,
    /// which the next answer checks; and those that two answers running
    /// said it is not on, about which deliveries no longer ask until the
    /// branch moves.
    private var askedAbout: Set<String> = []
    private var doubted: Set<String> = []
    private var passedOver: Set<String> = []
    /// A commit GitHub's last answer named that deliveries had shown the
    /// branch leaving. If the next answer names it too, it is believed.
    private var disputed: String?
    private var commits: [String: CommitChecks] = [:]
    private var order: [String] = []
    /// The repository's name in GitHub's last answer, which differs from
    /// the one in the settings once the repository is renamed or moved.
    private var currentName: RepositoryReference?
    private var rateLimit: RateLimit?
    private var fetched = Date.distantPast
    /// Whether a delivery since GitHub's last answer could not be read.
    private var missed = false

    init(repository: RepositoryReference, branch: String) {
        self.repository = repository
        self.branch = branch
    }

    /// Forgets where deliveries said the branch has been, once deliveries
    /// may have gone missing.
    func forgetPushes() {
        formerHeads.removeAll()
        passedOver.removeAll()
        doubted.removeAll()
        disputed = nil
    }

    /// Notes a delivery that could not be read, which may have concerned
    /// the branch.
    func missedDelivery() {
        missed = true
    }

    /// Takes in an answer from GitHub, to a request sent after every
    /// delivery taken in so far arrived. `complete` says whether every
    /// change made after the request was sent will be delivered. The answer
    /// is added to what deliveries have said, or, if `replacing`, replaces
    /// what is known about its commit. Returns what to show, and
    /// whether deliveries can be left to say when to ask GitHub again: they
    /// cannot when the answer may have missed changes, when a delivery that
    /// could not be read arrived before the request, when the answer
    /// replaced what deliveries said, when it disagrees with them, by
    /// naming a commit they showed the branch leaving or not naming one they
    /// asked about, or when it does not yet show finished a suite they said
    /// was cancelled.
    func adopt(_ answer: BranchSnapshot, complete: Bool,
               replacing: Bool) -> (shown: BranchStatus, settled: Bool) {
        currentName = answer.repository
        // An answer can lag the deliveries, so one that disagrees with them
        // is checked once more, and believed if it says the same again.
        let behind = !replacing && formerHeads.contains(answer.sha) && disputed != answer.sha
        disputed = behind ? answer.sha : nil
        if !behind, answer.sha != head {
            move(to: answer.sha)
        }
        passedOver.formUnion(doubted.subtracting([answer.sha]))
        doubted = askedAbout.subtracting([answer.sha])
        askedAbout.removeAll()
        if answer.sha == head {
            headline = answer.headline ?? headline
        }
        // A cancellation is settled by an answer that shows its suite finished
        // no earlier than the suite's latest known start, or that no longer
        // lists the suite, as when the run was deleted, in which case what
        // deliveries said about it goes too.
        let known = commits[answer.sha]
        let gone = (known?.undecided ?? []).filter { answer.checks.suites[$0] == nil }
        let undecided = (known?.undecided ?? []).filter { suite in
            guard let theirs = answer.checks.suites[suite] else { return false }
            guard let finished = theirs.finished else { return true }
            return finished.at < known?.suites[suite]?.started ?? .distantPast
        }
        if replacing {
            record(answer.sha) { $0 = answer.checks }
        } else {
            record(answer.sha) { $0.merge(answer.checks) }
        }
        commits[answer.sha]?.undecided = undecided
        for suite in gone {
            commits[answer.sha]?.suites[suite] = nil
        }
        rateLimit = answer.rateLimit
        fetched = Date()
        let quiet = !missed
        missed = false
        // An answer that replaced what deliveries said may lag them, so one
        // more is asked for at the interval, and added to them.
        return (status(at: head ?? answer.sha),
                complete && quiet && !behind && !replacing && doubted.isEmpty && undecided.isEmpty)
    }

    func take(_ event: WebhookEvent) -> LedgerReaction {
        let name = event.repository.lowercased()
        guard name == repository.slug.lowercased() || name == currentName?.slug.lowercased() else {
            return .ignore
        }
        let previous = head
        var ask = false
        switch event.kind {
        case .push(let headline, let deleted, let time):
            guard event.branch == branch else { return .ignore }
            if deleted { return .fetch }
            var newer = false
            if let time {
                if let lastPush {
                    // An older push, delivered late or again.
                    if time < lastPush { return .ignore }
                    newer = time > lastPush
                }
                lastPush = time
            }
            if let before = event.before, before != head, before != event.sha {
                formerHeads.insert(before)
            }
            guard event.sha != head else { return .ignore }
            // Without a later time, a push moves the branch only from where
            // it is, and not back onto a commit it has left, which a push
            // delivered late would also do.
            if newer || head != nil && event.before == head && !formerHeads.contains(event.sha) {
                move(to: event.sha)
                self.headline = headline
                let undecided = !(commits[event.sha]?.undecided.isEmpty ?? true)
                return status.map { .changed($0, fetch: undecided) } ?? .ignore
            }
            askedAbout.insert(event.sha)
            return .fetch
        case .runStarted(let suite, let run, let url, let time):
            ask = asks(event)
            record(event.sha) { $0.runStarted(suite: suite, run: run, url: url, at: time) }
        case .runFailed(let suite, let run, let url, let time):
            ask = asks(event)
            record(event.sha) { $0.runFailed(suite: suite, run: run, url: url, at: time) }
        case .suiteFinished(let suite, let state, let detail, let time):
            ask = asks(event)
            record(event.sha) { $0.suiteFinished(suite: suite, state: state, detail: detail, at: time) }
        case .cancelled(let suite):
            record(event.sha) { $0.suiteCancelled(suite: suite) }
            return event.sha == head || asks(event) ? .fetch : .ignore
        case .status(let context, let status):
            record(event.sha) { $0.statusSet(context: context, status) }
        }
        guard event.sha == head || head != previous else { return ask ? .fetch : .ignore }
        return status.map { .changed($0) } ?? .ignore
    }

    /// The branch as GitHub last answered and has delivered since.
    var status: BranchStatus? {
        head.map(status(at:))
    }

    private func status(at sha: String) -> BranchStatus {
        BranchStatus(repository: currentName ?? repository, branch: branch, sha: sha,
                     headline: headline, checks: commits[sha] ?? CommitChecks(),
                     fetched: fetched, rateLimit: rateLimit)
    }

    /// Whether a check suite delivery means a push went undelivered: it is
    /// on the branch at a commit other than the head, that the branch has
    /// not left, or that a push from the head started, and that GitHub has
    /// not already shown the branch is not on.
    private func asks(_ event: WebhookEvent) -> Bool {
        guard event.branch == branch, event.sha != head, !passedOver.contains(event.sha),
              !formerHeads.contains(event.sha) || event.before == head else {
            return false
        }
        askedAbout.insert(event.sha)
        return true
    }

    private func move(to sha: String) {
        if let head {
            formerHeads.insert(head)
        }
        formerHeads.remove(sha)
        passedOver.removeAll()
        doubted.removeAll()
        disputed = nil
        head = sha
        headline = nil
    }

    private func record(_ sha: String, _ change: (inout CommitChecks) -> Void) {
        if commits[sha] == nil {
            order.append(sha)
            if order.count > rememberedCommits, let oldest = order.first(where: { $0 != head }) {
                order.removeAll { $0 == oldest }
                commits[oldest] = nil
            }
        }
        change(&commits[sha, default: CommitChecks()])
    }
}

// MARK: - The monitor

enum MonitorState {
    case unconfigured
    case waiting
    case ready(BranchStatus)
    case failed(FetchFailure, BranchStatus?)
}

enum RelayState: Equatable {
    case off
    case connecting
    case connected
    case unavailable(String)
}

/// Asks GitHub about one branch for whoever calls it.
typealias BranchFetch = (RepositoryReference, String,
                         @escaping (Result<BranchSnapshot, FetchFailure>) -> Void) -> Void

/// Keeps track of the configured branch. Without a relay, or while the relay
/// cannot be reached, it asks GitHub, waits, and asks again. While the relay
/// is connected, it asks GitHub once, and then only when a delivery calls
/// for it. All methods run on the main thread.
final class BranchMonitor {
    var onChange: ((MonitorState) -> Void)?
    var onRelayChange: (() -> Void)?
    private(set) var state: MonitorState = .unconfigured
    private(set) var relayState: RelayState = .off
    /// Why the last delivery that could not be read was not read, since the
    /// relay last connected.
    private(set) var unreadableDelivery: String?
    /// When the monitor will next ask GitHub, if it is waiting to.
    private(set) var nextPoll: Date?

    private let makeFetch: () -> BranchFetch
    private var fetch: BranchFetch
    private var ledger: BranchLedger?
    private var relay: RelayAddress?
    private var connection: RelayConnection?
    private var refreshSeconds = 60.0
    private var timer: Timer?
    private var busy = false
    private var fetchAgain = false
    /// Whether the next answer is to replace what deliveries have said,
    /// because the user asked GitHub, or the computer woke.
    private var believeNext = false
    /// Deliveries that arrived while GitHub was being asked, which are
    /// taken in once its answer has been.
    private var held: [WebhookEvent] = []
    /// Whether a delivery that could not be read arrived while GitHub was
    /// being asked.
    private var missedWhileAsking = false
    private var lastGood: BranchStatus?
    /// When GitHub was last asked.
    private var lastAsked = Date.distantPast

    /// `makeFetch` is called again whenever the branch changes, so that
    /// nothing remembered about one branch is used for another.
    init(makeFetch: @escaping () -> BranchFetch) {
        self.makeFetch = makeFetch
        fetch = makeFetch()
    }

    func configure(repository: RepositoryReference?, branch: String, refreshSeconds: Double,
                   relay: RelayAddress?) {
        self.refreshSeconds = refreshSeconds
        if repository != ledger?.repository || branch != ledger?.branch {
            fetch = makeFetch()
            ledger = repository.map { BranchLedger(repository: $0, branch: branch) }
            held.removeAll()
            lastGood = nil
            publish(repository == nil ? .unconfigured : .waiting)
        }
        if relay != self.relay || ledger == nil {
            dropConnection()
            self.relay = relay
        }
        askNow()
    }

    /// Asks GitHub now, believes its answer over what deliveries have
    /// said, and tries the relay again if it is not connected.
    func refreshNow() {
        believeNext = true
        askNow()
    }

    /// Asks GitHub now, as the scheduled poll would. The tests stand in for
    /// the timer with it.
    func askNow() {
        connectIfNeeded()
        pollNow()
    }

    /// Connects to the relay again, since the connection may have died while
    /// the computer slept, and asks GitHub.
    func woke() {
        dropConnection()
        refreshNow()
    }

    private func pollNow() {
        timer?.invalidate()
        timer = nil
        if nextPoll != nil {
            nextPoll = nil
            onRelayChange?()
        }
        poll()
    }

    private func poll() {
        guard let ledger else {
            publish(.unconfigured)
            return
        }
        guard !busy else {
            fetchAgain = true
            return
        }
        busy = true
        fetchAgain = false
        let replacing = believeNext
        believeNext = false
        lastAsked = Date()
        let listening = relayState == .connected ? connection : nil
        fetch(ledger.repository, ledger.branch) { [weak self] result in
            guard let self else { return }
            busy = false
            guard ledger === self.ledger else {
                // The settings changed while this answer was on its way,
                // and the request for the new branch waited for it.
                poll()
                return
            }
            var settled = false
            switch result {
            case .success(let status):
                let complete = relayState == .connected && listening != nil && listening === connection
                let adopted = ledger.adopt(status, complete: complete, replacing: replacing || !complete)
                settled = adopted.settled
                show(adopted.shown)
            case .failure(let failure):
                publish(.failed(failure, lastGood))
            }
            var delivered = missedWhileAsking
            if missedWhileAsking {
                ledger.missedDelivery()
                missedWhileAsking = false
            }
            for event in held {
                if take(event) {
                    delivered = true
                }
            }
            held.removeAll()
            if fetchAgain {
                poll()
            } else if !settled {
                // Deliveries say when to ask again only when the answer
                // is up to date with them and every change will be
                // delivered.
                scheduleNextPoll(at: Date() + refreshSeconds)
            }
            if delivered {
                askSoon()
            }
        }
    }

    private func scheduleNextPoll(at date: Date) {
        timer?.invalidate()
        let timer = Timer(fire: date, interval: 0, repeats: false) { [weak self] _ in
            self?.askNow()
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
        nextPoll = timer.fireDate
        onRelayChange?()
    }

    private func connectIfNeeded() {
        guard let relay, ledger != nil, connection == nil else { return }
        let connection = RelayConnection(relay)
        connection.onReady = { [weak self] in
            self?.relayReady()
        }
        connection.onDelivery = { [weak self] delivery in
            self?.receive(delivery)
        }
        connection.onUnreadable = { [weak self] reason in
            self?.unreadable(reason)
        }
        connection.onEnded = { [weak self] reason in
            self?.relayEnded(reason)
        }
        self.connection = connection
        setRelayState(.connecting)
        connection.start()
    }

    private func dropConnection() {
        connection?.cancel()
        connection = nil
        ledger?.forgetPushes()
        setRelayState(.off)
    }

    private func relayReady() {
        ledger?.forgetPushes()
        unreadableDelivery = nil
        setRelayState(.connected)
        askSoon()
    }

    private func relayEnded(_ reason: String) {
        let wasListening = relayState == .connected
        connection = nil
        ledger?.forgetPushes()
        setRelayState(.unavailable(reason))
        // Anything that changed between the connection dying and this
        // moment was never delivered.
        if wasListening {
            askSoon()
        }
    }

    private func receive(_ delivery: Delivery) {
        let event: WebhookEvent
        do {
            guard let read = try WebhookEvent(delivery) else { return }
            event = read
        } catch {
            unreadable(error.description)
            return
        }
        if busy {
            held.append(event)
        } else if take(event) {
            askSoon()
        }
    }

    /// Hands a delivery to the ledger, shows what it changed, and returns
    /// whether to ask GitHub.
    private func take(_ event: WebhookEvent) -> Bool {
        switch ledger?.take(event) {
        case .changed(let status, let fetch):
            show(status)
            return fetch
        case .fetch:
            return true
        case .ignore, nil:
            return false
        }
    }

    /// Notes a delivery that could not be read, which may have been about
    /// the branch.
    private func unreadable(_ reason: String) {
        unreadableDelivery = reason
        onRelayChange?()
        if busy {
            missedWhileAsking = true
        } else {
            ledger?.missedDelivery()
            askSoon()
        }
    }

    /// Asks GitHub as soon as the polling interval since the last request
    /// allows, so that deliveries never make the monitor ask more often
    /// than polling would.
    private func askSoon() {
        guard !busy else { return }
        let allowed = lastAsked + refreshSeconds
        if allowed <= Date() {
            askNow()
        } else if nextPoll.map({ $0 > allowed }) ?? true {
            scheduleNextPoll(at: allowed)
        }
    }

    private func show(_ status: BranchStatus) {
        lastGood = status
        publish(.ready(status))
    }

    private func publish(_ state: MonitorState) {
        self.state = state
        onChange?(state)
    }

    private func setRelayState(_ state: RelayState) {
        guard state != relayState else { return }
        relayState = state
        onRelayChange?()
    }
}
