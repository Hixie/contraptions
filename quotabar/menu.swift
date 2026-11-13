import AppKit

// The menu quotabar shows when its circles are clicked.

/// A menu item that runs a closure when chosen.
final class ActionItem: NSMenuItem {
    private let handler: () -> Void

    init(_ title: String, key: String = "", handler: @escaping () -> Void) {
        self.handler = handler
        super.init(title: title, action: #selector(run), keyEquivalent: key)
        target = self
    }

    required init(coder: NSCoder) {
        fatalError("ActionItem is not archived")
    }

    @objc private func run() {
        handler()
    }
}

/// The menu bar's picture and tooltip: a circle for each account, in the
/// order of `timeZone`'s week, followed by a warning when the accounts
/// could not be saved; or only a warning when they could not be read.
func menuBarPicture(of roster: Roster, loadProblem: String?, saveProblem: String?, now: Date,
                    in timeZone: TimeZone) -> (image: NSImage, toolTip: String) {
    if let loadProblem {
        return (glyphImage([], now: now, warning: true), loadProblem)
    }
    let ordered = roster.ordered(in: timeZone)
    let lines = ordered.map { "\($0.email): \(roster.summary(of: $0, now: now))" }
    return (glyphImage(ordered.map { roster.statuses[$0.id] }, now: now, warning: saveProblem != nil),
            ([saveProblem].compactMap { $0 } + lines).joined(separator: "\n"))
}

/// Fills `menu` with a line for each account, in the order of `timeZone`'s
/// week, each with a submenu of details and actions, followed by the
/// watcher's problems and actions, and `quit`.
@MainActor
func fill(_ menu: NSMenu, from watcher: Watcher, now: Date, in timeZone: TimeZone, locale: Locale,
          quit: @escaping () -> Void) {
    menu.removeAllItems()
    func noteItem(_ title: String, in menu: NSMenu) {
        menu.addItem(withTitle: title, action: nil, keyEquivalent: "").isEnabled = false
    }
    // A reset still to come is within the week; other times may not be.
    let timeFormat = Date.FormatStyle(locale: locale, timeZone: timeZone).weekday(.wide).hour().minute()
    let dateFormat = Date.FormatStyle(locale: locale, timeZone: timeZone).month(.abbreviated).day().hour().minute()
    let roster = watcher.roster
    for account in roster.ordered(in: timeZone) {
        let status = roster.statuses[account.id]
        let item = menu.addItem(withTitle: "\(account.email): \(roster.summary(of: account, now: now))",
                                action: nil, keyEquivalent: "")
        item.image = glyphImage([status], now: now)
        let details = NSMenu()
        if !account.organization.isEmpty {
            noteItem(account.organization, in: details)
        }
        if let resets = status?.usage?.resets {
            noteItem(resets > now ? "Resets \(resets.formatted(timeFormat))"
                                  : "Reset \(resets.formatted(dateFormat))", in: details)
        }
        for reset in status?.usage?.banked(at: now) ?? [] {
            noteItem("\(reset.label): \(reset.left) left"
                 + (reset.ends.map { ", use by \($0.formatted(dateFormat))" } ?? ""), in: details)
        }
        for note in status?.usage?.bankedNotes(at: now) ?? [] {
            noteItem(note, in: details)
        }
        if let checked = status?.checked {
            noteItem("Last checked successfully \(checked.formatted(dateFormat))", in: details)
        }
        if let problem = status?.problem {
            noteItem(problem, in: details)
        }
        details.addItem(.separator())
        details.addItem(ActionItem("Sign In Again…") { watcher.startSignIn(expecting: account) })
        details.addItem(ActionItem("Remove") { watcher.remove(account) })
        item.submenu = details
    }
    if !roster.accounts.isEmpty {
        menu.addItem(.separator())
    }
    for problem in [watcher.loadProblem, watcher.saveProblem, watcher.signInNote].compactMap({ $0 }) {
        noteItem(problem, in: menu)
    }
    if watcher.loadProblem != nil {
        menu.addItem(ActionItem("Read the Keychain Again") {
            watcher.load()
            watcher.checkAll()
        })
    } else if watcher.signingIn {
        noteItem("Waiting for the browser to sign in…", in: menu)
        menu.addItem(ActionItem("Cancel Sign-In") { watcher.cancelSignIn() })
    } else {
        menu.addItem(ActionItem("Add Account…") { watcher.startSignIn() })
    }
    if !roster.accounts.isEmpty {
        menu.addItem(ActionItem("Check Now") { watcher.checkAll() })
    }
    menu.addItem(.separator())
    menu.addItem(ActionItem("Quit", key: "q", handler: quit))
}
