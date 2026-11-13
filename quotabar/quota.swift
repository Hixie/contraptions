import Foundation

// The accounts quotabar watches, what their weekly quotas look like, and the
// order it shows them in.

struct Problem: LocalizedError, CustomStringConvertible {
    let description: String
    var errorDescription: String? { description }
}

/// The first line of an error's description, at most 200 characters long,
/// for a menu item.
func describe(_ error: Error) -> String {
    let line = error.localizedDescription.split(separator: "\n").first ?? ""
    return line.count > 200 ? String(line.prefix(199)) + "…" : String(line)
}

/// A field's place in JSON, such as "grants[0].ends_at", or "the whole
/// answer" for the top level.
func fieldPath(_ keys: [CodingKey]) -> String {
    let path = keys.map { $0.intValue.map { "[\($0)]" } ?? ".\($0.stringValue)" }.joined()
    return path.isEmpty ? "the whole answer" : String(path.drop { $0 == "." })
}

/// What is wrong with JSON that could not be decoded, naming the field.
func explain(_ error: Error) -> String {
    func kind(_ type: Any.Type) -> String {
        switch type {
        case is String.Type: "text"
        case is Int.Type, is Double.Type: "a number"
        case is Bool.Type: "true or false"
        case is [Any].Type: "a list"
        case is [String: Any].Type: "an object"
        default: "\(type)"
        }
    }
    switch error {
    case DecodingError.keyNotFound(let key, let context):
        return "\(fieldPath(context.codingPath + [key])) is missing"
    case DecodingError.valueNotFound(_, let context):
        return "\(fieldPath(context.codingPath)) is null"
    case DecodingError.typeMismatch(let type, let context):
        return "\(fieldPath(context.codingPath)) is not \(kind(type))"
    case DecodingError.dataCorrupted(let context):
        return context.codingPath.isEmpty ? "it is not JSON" : "\(fieldPath(context.codingPath)) cannot be read"
    default:
        return "\(error)"
    }
}

/// Decodes `type` from `data`, explaining a failure as "the `what` is not
/// what quotabar expects".
func decode<T: Decodable>(_ type: T.Type, from data: Data, what: String) throws -> T {
    do {
        return try JSONDecoder().decode(type, from: data)
    } catch let refusal as Problem {
        throw refusal
    } catch {
        throw Problem(description: "the \(what) is not what quotabar expects: \(explain(error))")
    }
}

/// Any key at all, for listing the keys a decoder holds.
private struct AnyKey: CodingKey {
    let stringValue: String
    var intValue: Int? { nil }
    init(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
}

/// Throws when what a decoder holds has keys other than `known`, so that
/// saved data a newer quotabar wrote is refused, not silently cut down when
/// it is saved again.
func refuseUnknownKeys<Key: CodingKey & CaseIterable>(in decoder: Decoder, known: Key.Type) throws {
    let names = Set(Key.allCases.map(\.stringValue))
    for key in try decoder.container(keyedBy: AnyKey.self).allKeys where !names.contains(key.stringValue) {
        throw Problem(description: "the saved accounts have a field quotabar does not know: "
                      + fieldPath(decoder.codingPath + [key]))
    }
}

struct Tokens: Codable, Equatable {
    var access: String
    var refresh: String
    var expires: Date

    enum CodingKeys: String, CodingKey, CaseIterable {
        case access, refresh, expires
    }
}

/// One Claude account in one organization, with the tokens quotabar's own
/// sign-in to it gave.
struct Account: Codable, Equatable {
    /// The account's UUID and the organization's UUID, joined by a slash.
    var id: String
    var email: String
    var organization: String
    var tokens: Tokens
    /// The last reset time a check found, which places the account in the
    /// menu bar.
    var resets: Date?

    enum CodingKeys: String, CodingKey, CaseIterable {
        case id, email, organization, tokens, resets
    }
}

// Decoding is declared in extensions so that the structures keep their
// memberwise initializers.

extension Tokens {
    init(from decoder: Decoder) throws {
        try refuseUnknownKeys(in: decoder, known: CodingKeys.self)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(access: container.decode(String.self, forKey: .access),
                      refresh: container.decode(String.self, forKey: .refresh),
                      expires: container.decode(Date.self, forKey: .expires))
    }
}

extension Account {
    init(from decoder: Decoder) throws {
        try refuseUnknownKeys(in: decoder, known: CodingKeys.self)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(id: container.decode(String.self, forKey: .id),
                      email: container.decode(String.self, forKey: .email),
                      organization: container.decode(String.self, forKey: .organization),
                      tokens: container.decode(Tokens.self, forKey: .tokens),
                      resets: container.decodeIfPresent(Date.self, forKey: .resets))
    }
}

/// How much of an account's weekly quota is used, when it next resets, and
/// what resets it has banked. A week whose quota nobody has started using
/// yet may have no reset time.
struct WeeklyUsage: Equatable {
    let percent: Double
    let resets: Date?
    var banked: [BankedReset] = []
    /// Why the answer lists no banked resets, as it says, or why they could
    /// not be read.
    var bankedReason: String?
    /// Kinds of banked reset in the answer that quotabar does not know.
    var unknownKinds: [String] = []

    /// The banked resets that can still be used.
    func banked(at now: Date) -> [BankedReset] {
        banked.filter { $0.ends.map { $0 > now } ?? true }
    }

    /// How many resets are banked and can still be used.
    func bankedCount(at now: Date) -> Int {
        banked(at: now).reduce(0) { $0 + $1.left }
    }

    /// What the menu should say about banked resets besides listing them.
    func bankedNotes(at now: Date) -> [String] {
        let unknown = unknownKinds.isEmpty ? []
            : ["Banked resets of a kind quotabar does not know: \(unknownKinds.joined(separator: ", "))"]
        let none = banked(at: now).isEmpty ? "No banked resets that clear the weekly limit" : nil
        return unknown + [bankedReason ?? none].compactMap { $0 }
    }
}

/// Resets an account can use to clear its weekly limit early, such as
/// "Weekly reset", with how many are left and when they expire, if they do.
struct BankedReset: Equatable {
    let label: String
    let left: Int
    let ends: Date?
}

let week: TimeInterval = 7 * 24 * 60 * 60

/// Reads the usage endpoint's answer, which looks like
/// {"seven_day": {"utilization": 37.0, "resets_at": "2026-11-17T14:00:00.123456+00:00"},
///  "cedar_ember": {"eligible": true, "grants": [{"label": "Weekly reset", "type": "weekly",
///  "resets_left": 1, "ends_at": "2026-12-01T00:00:00Z", "paused": false}]}, ...}.
/// The reset time is rounded to the minute, since the server's fractions of
/// a second say nothing about when in the week the quota resets. Banked
/// resets are an experiment, so an answer whose banked resets cannot be read
/// still gives the weekly quota, with the problem.
func parseUsage(_ data: Data) throws -> WeeklyUsage {
    struct Window: Decodable {
        let utilization: Double?
        let resets_at: String?
    }
    struct Answer: Decodable {
        let seven_day: Window?
    }
    let answer = try decode(Answer.self, from: data, what: "usage answer")
    guard let window = answer.seven_day, let percent = window.utilization else {
        throw Problem(description: "the usage answer has no weekly quota")
    }
    var usage = WeeklyUsage(percent: percent, resets: try window.resets_at.map {
        try parseTime($0, at: "seven_day.resets_at")
    }.map {
        Date(timeIntervalSince1970: ($0.timeIntervalSince1970 / 60).rounded() * 60)
    })
    do {
        (usage.banked, usage.unknownKinds, usage.bankedReason) = try parseBanked(data)
    } catch {
        usage.bankedReason = "The banked resets are not what quotabar expects: \(explain(error))"
    }
    return usage
}

/// The banked resets an answer lists that clear the weekly limit, the kinds
/// it lists that quotabar does not know, and why it lists none, if it says.
private func parseBanked(_ data: Data) throws -> ([BankedReset], [String], String?) {
    struct Grant: Decodable {
        let label: String?
        let type: String?
        let resets_left: Int
        let ends_at: String?
        let paused: Bool?
        let gives_back_weekly: Bool?
        let clears: [String]?

        /// What the reset clears, worked out as Claude Code 2.1.311 does: "fable"
        /// for a reset that gives back what Fable used, "session" for the
        /// five-hour limit, "weekly" for the weekly limit, and "full" for
        /// both.
        var kind: String {
            if gives_back_weekly == true {
                return "fable"
            }
            if let type, !type.isEmpty {
                return type
            }
            let session = clears?.contains("five_hour") == true
            let other = clears?.contains { $0 != "five_hour" } == true
            return session && !other ? "session" : other && !session ? "weekly" : "full"
        }
    }
    struct Grants: Decodable {
        let eligible: Bool
        let ineligible_reason: String?
        let grants: [Grant]?
    }
    struct Answer: Decodable {
        let cedar_ember: Grants?
    }
    guard let status = try JSONDecoder().decode(Answer.self, from: data).cedar_ember else {
        return ([], [], "The usage answer says nothing about banked resets")
    }
    guard status.eligible else {
        return ([], [], "Not eligible for banked resets" + (status.ineligible_reason.map { " (\($0))" } ?? ""))
    }
    let usable = (status.grants ?? []).filter { $0.resets_left > 0 && $0.paused != true }
    let counted = usable.filter { ["weekly", "full"].contains($0.kind) }
    // A count this large cannot be real, and adding such counts up could
    // overflow.
    if let absurd = counted.first(where: { $0.resets_left > 1000 }) {
        throw Problem(description: "\(absurd.resets_left) banked resets cannot be right")
    }
    let banked = try counted.map {
        BankedReset(label: $0.label.flatMap { $0.isEmpty ? nil : $0 } ?? "\($0.kind) reset",
                    left: $0.resets_left, ends: try $0.ends_at.map { try parseTime($0, at: "a banked reset's ends_at") })
    }
    let unknown = Set(usable.map(\.kind)).subtracting(["session", "weekly", "fable", "full"])
    return (banked, unknown.sorted(), nil)
}

/// Reads an ISO 8601 time, with or without fractions of a second, from the
/// field `field`, if it is given.
func parseTime(_ text: String, at field: String? = nil) throws -> Date {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: text) {
        return date
    }
    formatter.formatOptions = [.withInternetDateTime]
    guard let date = formatter.date(from: text) else {
        throw Problem(description: (field.map { "\($0): " } ?? "") + "\(text) is not a time")
    }
    return date
}

/// How long after Monday midnight a reset time falls in its week. Monday
/// midnight is taken in the time zone's standard time, so that a daylight
/// saving change does not move it, and accounts whose quotas reset a week
/// apart keep their order.
func weekOffset(of time: Date, in timeZone: TimeZone) -> TimeInterval {
    let standard = TimeInterval(timeZone.secondsFromGMT(for: time))
        - timeZone.daylightSavingTimeOffset(for: time)
    // 5 January 1970 was a Monday.
    let sinceMonday = time.timeIntervalSince1970 + standard - 4 * 24 * 60 * 60
    let offset = sinceMonday.truncatingRemainder(dividingBy: week)
    return offset < 0 ? offset + week : offset
}

/// The accounts in the order quotabar shows them: by when in the week each
/// one's quota resets, then those whose reset time is not known yet, in the
/// order they were added.
func displayOrder(_ accounts: [Account], in timeZone: TimeZone) -> [Account] {
    let known = accounts.compactMap { account in
        account.resets.map { (account: account, offset: weekOffset(of: $0, in: timeZone)) }
    }
    let sorted = known.sorted {
        ($0.offset, $0.account.email, $0.account.id) < ($1.offset, $1.account.email, $1.account.id)
    }
    return sorted.map(\.account) + accounts.filter { $0.resets == nil }
}

/// What quotabar last learned about an account.
struct Status {
    /// What the last successful check found.
    var usage: WeeklyUsage?
    var checked: Date?
    /// Why the last check failed, if it did.
    var problem: String?
    var signInNeeded = false
}

/// The accounts, and what quotabar has learned about each. A check of an
/// account holds a ticket from when it began, and what it learns is dropped
/// if the account has been signed in to again, or removed, since then.
struct Roster {
    struct Ticket: Hashable {
        let id: String
        let signIn: Int
    }

    private(set) var accounts: [Account]
    private(set) var statuses: [String: Status] = [:]
    private var signIns: [String: Int] = [:]

    init(_ accounts: [Account] = []) {
        self.accounts = accounts
    }

    func ticket(for account: Account) -> Ticket {
        Ticket(id: account.id, signIn: signIns[account.id, default: 0])
    }

    /// Adds an account just signed in to. A new account goes at the end. An
    /// account already in the list keeps its place, and its last reset time,
    /// with its new details.
    mutating func add(_ account: Account) {
        signIns[account.id, default: 0] += 1
        statuses[account.id] = nil
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else {
            accounts.append(account)
            return
        }
        let resets = accounts[index].resets
        accounts[index] = account
        accounts[index].resets = resets
    }

    mutating func remove(_ id: String) {
        signIns[id, default: 0] += 1
        statuses[id] = nil
        accounts.removeAll { $0.id == id }
    }

    /// Keeps the tokens a check's refresh gave. Returns whether it kept them.
    mutating func replaceTokens(_ tokens: Tokens, for ticket: Ticket) -> Bool {
        guard let index = index(of: ticket) else {
            return false
        }
        accounts[index].tokens = tokens
        return true
    }

    /// Records how a check ended. Returns whether the accounts changed, and
    /// so need saving.
    mutating func record(_ outcome: Result<WeeklyUsage, Error>, for ticket: Ticket, at time: Date)
        -> Bool {
        guard let index = index(of: ticket) else {
            return false
        }
        switch outcome {
        case .success(let usage):
            statuses[ticket.id] = Status(usage: usage, checked: time)
            guard let resets = usage.resets, resets != accounts[index].resets else {
                return false
            }
            accounts[index].resets = resets
            return true
        case .failure(let error):
            var status = statuses[ticket.id] ?? Status()
            status.problem = describe(error)
            status.signInNeeded = error is SignInNeeded
            statuses[ticket.id] = status
            return false
        }
    }

    func ordered(in timeZone: TimeZone) -> [Account] {
        displayOrder(accounts, in: timeZone)
    }

    /// How the menu and the tooltip describe an account.
    func summary(of account: Account, now: Date) -> String {
        let status = statuses[account.id]
        if status?.signInNeeded == true {
            return "sign in again"
        }
        if let usage = status?.usage {
            return describe(usage, now: now)
        }
        return status?.problem == nil ? "checking" : "check failed"
    }

    private func index(of ticket: Ticket) -> Int? {
        guard signIns[ticket.id, default: 0] == ticket.signIn else {
            return nil
        }
        return accounts.firstIndex { $0.id == ticket.id }
    }
}

/// How much of the week before a reset has passed, from 0 to 1.
func weekElapsed(until resets: Date, now: Date) -> Double {
    min(max(1 - resets.timeIntervalSince(now) / week, 0), 1)
}

/// A duration as days and hours, hours and minutes, or minutes.
func describeDuration(_ interval: TimeInterval) -> String {
    let minutes = max(Int(interval / 60), 0)
    let (days, hours) = (minutes / (24 * 60), minutes / 60 % 24)
    if days > 0 {
        return "\(days)d \(hours)h"
    }
    if hours > 0 {
        return "\(hours)h \(minutes % 60)m"
    }
    return "\(minutes)m"
}

/// What a check found, for the menu.
func describe(_ usage: WeeklyUsage, now: Date) -> String {
    let used = "\(Int(min(max(usage.percent, 0), 100).rounded(.down)))% used"
    let week = usage.resets.map {
        $0 > now ? "resets in \(describeDuration($0.timeIntervalSince(now)))" : "from before the last reset"
    } ?? "week not started"
    let count = usage.bankedCount(at: now)
    let banked = count == 0 ? [] : ["\(count) banked reset\(count == 1 ? "" : "s")"]
    return ([used, week] + banked).joined(separator: ", ")
}
