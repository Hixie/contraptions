import Foundation

// What quotabar does with its accounts, apart from showing them: reading and
// saving them, checking them, and signing in to them; and making sure only
// one quotabar runs.

/// Takes an exclusive lock on the file at `path`, creating it if need be,
/// and holds it until the process ends. Returns false when another process
/// holds it. Two quotabars would spend each other's refresh tokens, so only
/// the one holding the lock runs.
func takeLock(at path: String) throws -> Bool {
    let descriptor = open(path, O_CREAT | O_RDWR | O_CLOEXEC, 0o600)
    guard descriptor >= 0 else {
        throw Problem(description: "cannot open \(path): \(String(cString: strerror(errno)))")
    }
    guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
        let reason = errno
        close(descriptor)
        guard reason == EWOULDBLOCK else {
            throw Problem(description: "cannot lock \(path): \(String(cString: strerror(reason)))")
        }
        return false
    }
    return true
}

@MainActor
final class Watcher {
    private(set) var roster = Roster()
    /// Why the accounts could not be read. While this is set, the watcher
    /// holds no accounts, no failed save to retry, and no sign-in, so it has
    /// nothing to save over the accounts it never read.
    private(set) var loadProblem: String?
    private(set) var saveProblem: String?
    /// What the last sign-in needs to say: why it failed, or that it signed
    /// in to another account than the one asked for.
    private(set) var signInNote: String?
    var signingIn: Bool { signIn != nil }
    /// How many token refreshes are under way.
    private(set) var refreshing = 0

    private let service: Service
    private let store: AccountKeeper
    private let open: @MainActor (URL) -> Bool
    private let changed: () -> Void
    private var signIn: (listener: CallbackListener, task: Task<Void, Never>)?
    /// The checks running now, so that no account is checked twice at once
    /// with the same tokens, which would spend its refresh token twice.
    private var checking: Set<Roster.Ticket> = []
    private var afterRefreshing: [() -> Void] = []
    /// Whether the Mac can reach the network, which nothing has said yet.
    private var online = false
    /// Whether checks were asked for while the Mac was offline, or failed
    /// for want of a network.
    private var checkWhenOnline = false
    /// How many times macOS has said whether the Mac is online.
    private var networkChanges = 0
    /// Whether quotabar is quitting, so that no new checks start.
    private var closing = false

    /// `open` shows a sign-in page, and `changed` is called whenever any of
    /// the watcher's state changes.
    init(service: Service, store: AccountKeeper, open: @escaping @MainActor (URL) -> Bool,
         changed: @escaping () -> Void) {
        self.service = service
        self.store = store
        self.open = open
        self.changed = changed
    }

    /// Reads the accounts.
    func load() {
        do {
            roster = Roster(try store.load())
            loadProblem = nil
        } catch {
            cancelSignIn()
            roster = Roster()
            loadProblem = describe(error)
            saveProblem = nil
        }
        changed()
    }

    /// Records whether the Mac can reach the network. Checks asked for while
    /// it could not run once it can; the task ends when they end.
    @discardableResult
    func setOnline(_ isOnline: Bool) -> Task<Void, Never>? {
        online = isOnline
        networkChanges += 1
        guard online, checkWhenOnline else {
            return nil
        }
        return checkAll()
    }

    /// Checks every account that is not being checked already, except those
    /// that need signing in again, whose refresh tokens would only be
    /// refused again. While the Mac is offline, the checks wait until it is
    /// online. The task ends when the checks it started end.
    @discardableResult
    func checkAll() -> Task<Void, Never> {
        retrySave()
        guard online, !closing else {
            checkWhenOnline = !closing
            return Task {}
        }
        checkWhenOnline = false
        let checks = roster.accounts.map { (account: $0, ticket: roster.ticket(for: $0)) }
            .filter {
                !checking.contains($0.ticket) && roster.statuses[$0.account.id]?.signInNeeded != true
            }
        checking.formUnion(checks.map(\.ticket))
        return Task {
            await withTaskGroup(of: Void.self) { group in
                for check in checks {
                    group.addTask { await self.check(check.account, with: check.ticket) }
                }
            }
        }
    }

    private func check(_ account: Account, with ticket: Roster.Ticket) async {
        let networkChangesBefore = networkChanges
        let outcome: Result<WeeklyUsage, Error>
        do {
            outcome = .success(try await service.usage(of: account, refreshing: { started in
                self.refreshing += started ? 1 : -1
                if self.refreshing == 0 {
                    let actions = self.afterRefreshing
                    self.afterRefreshing = []
                    actions.forEach { $0() }
                }
            }, save: { tokens in
                if self.roster.replaceTokens(tokens, for: ticket) {
                    self.save()
                }
            }))
        } catch {
            outcome = .failure(error)
        }
        checking.remove(ticket)
        if roster.record(outcome, for: ticket, at: service.now()) {
            save()
        }
        changed()
        // A check that failed at the network level, as it can just after
        // waking, before macOS says the network has gone, runs again once it
        // is back: now, if macOS has said so while the check ran, and
        // otherwise when it next does.
        if case .failure(let error as URLError) = outcome, Watcher.networkFailures.contains(error.code) {
            if networkChanges != networkChangesBefore {
                await checkAll().value
            } else {
                checkWhenOnline = true
            }
        }
    }

    /// Signs in through the browser, asking for `expected` when signing in
    /// to an account again, and then checks the accounts, which includes the
    /// new one. Starting a sign-in cancels any other. The task ends when the
    /// sign-in and its checks end.
    @discardableResult
    func startSignIn(expecting expected: Account? = nil) -> Task<Void, Never>? {
        cancelSignIn()
        signInNote = nil
        guard loadProblem == nil else {
            return nil
        }
        let listener: CallbackListener
        do {
            listener = try CallbackListener()
        } catch {
            signInNote = "Could not sign in: \(describe(error))"
            changed()
            return nil
        }
        let task = Task {
            do {
                let account = try await service.signIn(through: listener, hint: expected?.email,
                                                       open: open)
                try Task.checkCancellation()
                signIn = nil
                roster.add(account)
                save()
                if let expected, expected.id != account.id {
                    signInNote = "Signed in to \(account.email) (\(account.organization)), not "
                        + "\(expected.email) (\(expected.organization)), so quotabar added or "
                        + "updated \(account.email)."
                }
                changed()
                await checkAll().value
            } catch {
                if !Task.isCancelled {
                    signIn = nil
                    signInNote = "Could not sign in: \(describe(error))"
                    changed()
                }
            }
        }
        signIn = (listener, task)
        changed()
        return task
    }

    func cancelSignIn() {
        signIn?.listener.cancel()
        signIn?.task.cancel()
        signIn = nil
        changed()
    }

    /// Stops new checks starting, for quitting.
    func close() {
        closing = true
        retrySave()
    }

    /// Runs `action` once no token refresh is under way: now, or when the
    /// last one ends. Quitting waits for this, since a refresh interrupted
    /// after the server answers loses the only working refresh token.
    func whenRefreshesEnd(_ action: @escaping () -> Void) {
        if refreshing == 0 {
            action()
        } else {
            afterRefreshing.append(action)
        }
    }

    /// Clears the sign-in note, if it is still `shown`, the one the menu
    /// showed.
    func dismissSignInNote(_ shown: String?) {
        if signInNote == shown {
            signInNote = nil
        }
    }

    func remove(_ account: Account) {
        roster.remove(account.id)
        save()
        changed()
    }

    /// Failures that mean the network is not there.
    private static let networkFailures: Set<URLError.Code> = [
        .notConnectedToInternet, .networkConnectionLost, .dataNotAllowed, .timedOut, .cannotFindHost,
        .cannotConnectToHost, .dnsLookupFailed, .internationalRoamingOff, .callIsActive,
    ]

    /// Saves the accounts again if saving them last failed, since a refresh
    /// token that was not saved is lost when quotabar next starts.
    private func retrySave() {
        if saveProblem != nil {
            save()
        }
    }

    private func save() {
        let before = saveProblem
        do {
            try store.save(roster.accounts)
            saveProblem = nil
        } catch {
            saveProblem = describe(error)
        }
        if saveProblem != before {
            changed()
        }
    }
}
