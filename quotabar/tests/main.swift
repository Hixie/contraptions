import AppKit
import Network

// Tests for everything in quotabar but main.swift. Run them with `make
// test`. They use the network only to reach sign-in listeners on the
// loopback interface, and they add, and then delete, items in the login
// keychain, also deleting any that earlier runs left behind.

var failures = 0

/// A name for a keychain item of this run. It names the run's process, so
/// that other runs leave the item alone while the run lives.
func temporaryService() -> String {
    "quotabar-tests-\(getpid())-\(UUID().uuidString)"
}

/// Runs the security tool, and returns its exit status.
func security(_ arguments: [String]) throws -> Int32 {
    let tool = Process()
    tool.executableURL = URL(fileURLWithPath: "/usr/bin/security")
    tool.arguments = arguments
    tool.standardOutput = FileHandle.nullDevice
    tool.standardError = FileHandle.nullDevice
    try tool.run()
    tool.waitUntilExit()
    return tool.terminationStatus
}

/// Deletes the keychain items of runs whose processes have gone, which
/// were stopped before they could clean up. Only an item's owner may delete
/// it through the Security framework, and the run that made it was a
/// different program, so the security tool deletes it.
func deleteLeftovers() throws {
    var found: CFTypeRef?
    let status = SecItemCopyMatching([kSecClass as String: kSecClassGenericPassword,
                                      kSecMatchLimit as String: kSecMatchLimitAll,
                                      kSecReturnAttributes as String: true] as CFDictionary, &found)
    guard status != errSecItemNotFound else {
        return
    }
    expect(status == errSecSuccess, "the keychain can be searched for leftovers: \(status)")
    let prefix = "quotabar-tests-"
    guard let items = found as? [[String: Any]] else {
        return expect(false, "the keychain search gives a list of items")
    }
    for item in items {
        guard let service = item[kSecAttrService as String] as? String, service.hasPrefix(prefix),
              let pid = service.dropFirst(prefix.count).split(separator: "-", omittingEmptySubsequences: false)
                  .first.flatMap({ Int32($0) }), pid > 0, kill(pid, 0) == -1, errno == ESRCH else {
            continue
        }
        // Another run may delete it at the same moment, so what matters is
        // that it is gone.
        _ = try security(["delete-generic-password", "-s", service])
        expect(try security(["find-generic-password", "-s", service]) != 0, "the leftover \(service) is deleted")
    }
}

func expect(_ condition: Bool, _ message: String, line: Int = #line) {
    if !condition {
        failures += 1
        fputs("FAIL line \(line): \(message)\n", stderr)
    }
}

try deleteLeftovers()

func expectThrows<T>(_ message: String, line: Int = #line, _ body: () async throws -> T,
                     where matches: (Error) -> Bool = { _ in true }) async {
    do {
        let value = try await body()
        expect(false, "\(message): returned \(value)", line: line)
    } catch {
        expect(matches(error), "\(message): threw \(error)", line: line)
    }
}

func time(_ text: String) -> Date {
    try! parseTime(text)
}

let utc = TimeZone(identifier: "UTC")!
let pacific = TimeZone(identifier: "America/Los_Angeles")!

// MARK: - Reading the usage endpoint's answer

let answer = """
    {"five_hour": {"utilization": 12.0, "resets_at": "2026-11-13T19:00:00.000000+00:00"},
     "seven_day": {"utilization": 37.5, "resets_at": "2026-11-17T14:00:00.943648+00:00"},
     "seven_day_opus": null, "extra_usage": {"is_enabled": false}}
    """
let usage = try parseUsage(Data(answer.utf8))
expect(usage.percent == 37.5, "the weekly percentage is read, not the five-hour one: \(usage)")
expect(usage.resets == time("2026-11-17T14:00:00Z"),
       "the weekly reset time is read, to the nearest minute: \(usage)")
expect(usage.banked == [], "an answer without banked resets has none")
let lateInMinute = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": "2026-11-17T13:59:31Z"}}"#.utf8))
expect(lateInMinute.resets == time("2026-11-17T14:00:00Z"), "a reset time rounds up to the next minute")

let withGrants = try parseUsage(Data(#"""
    {"seven_day": {"utilization": 90, "resets_at": "2026-11-17T14:00:00Z"},
     "cedar_ember": {"eligible": true, "weekly_resets_at": "2026-11-17T14:00:00Z", "grants": [
       {"id": "g1", "label": "Weekly reset", "type": "weekly", "resets_left": 2, "resets_total": 2,
        "ends_at": "2026-12-01T00:00:00Z", "paused": false, "usable_now": false},
       {"id": "g2", "label": "", "type": "full", "resets_left": 1, "ends_at": null},
       {"id": "g3", "type": "weekly", "resets_left": 1, "paused": true},
       {"id": "g4", "label": "Used up", "type": "weekly", "resets_left": 0},
       {"id": "g5", "label": "Fable reset", "type": "weekly", "gives_back_weekly": true, "resets_left": 1},
       {"id": "g6", "label": "Session", "type": "session", "resets_left": 1},
       {"id": "g7", "label": "Clears the week", "clears": ["seven_day"], "resets_left": 1},
       {"id": "g8", "label": "Clears the session", "clears": ["five_hour"], "resets_left": 1},
       {"id": "g9", "clears": ["five_hour", "seven_day"], "resets_left": 1}]}}
    """#.utf8))
expect(withGrants.banked == [BankedReset(label: "Weekly reset", left: 2, ends: time("2026-12-01T00:00:00Z")),
                             BankedReset(label: "full reset", left: 1, ends: nil),
                             BankedReset(label: "Clears the week", left: 1, ends: nil),
                             BankedReset(label: "full reset", left: 1, ends: nil)],
       "banked resets are those that clear the weekly limit, with resets left, not paused: \(withGrants.banked)")
expect(withGrants.banked(at: time("2026-12-02T00:00:00Z")).map(\.label) == ["full reset", "Clears the week", "full reset"],
       "an expired banked reset can no longer be used")
let ineligible = try parseUsage(Data(#"""
    {"seven_day": {"utilization": 90, "resets_at": null},
     "cedar_ember": {"eligible": false, "grants": [{"label": "Weekly reset", "resets_left": 1}]}}
    """#.utf8))
expect(ineligible.banked == [], "an account not eligible for banked resets has none")
let malformedGrant = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": null}, "cedar_ember": {"eligible": true, "grants": [{"label": "x"}]}}"#.utf8))
expect(malformedGrant.percent == 1 && malformedGrant.banked == []
       && malformedGrant.bankedReason?.contains("not what quotabar expects") == true,
       "banked resets that cannot be read are reported, and the weekly quota still read: \(malformedGrant)")
let badExpiry = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": null}, "cedar_ember": {"eligible": true, "grants": [{"resets_left": 1, "ends_at": "soon"}]}}"#.utf8))
expect(badExpiry.bankedReason?.contains("a banked reset's ends_at: soon is not a time") == true,
       "a banked reset whose expiry is not a time is reported: \(badExpiry.bankedReason ?? "")")
expect(withGrants.bankedReason == nil, "an answer with banked resets has no note")
let oddKinds = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": null}, "cedar_ember": {"eligible": true, "grants": [{"type": "weekly", "resets_left": 1}, {"type": "seven_day", "resets_left": 1}, {"resets_left": 1}]}}"#.utf8))
expect(oddKinds.banked.map(\.label) == ["weekly reset", "full reset"]
       && oddKinds.unknownKinds == ["seven_day"] && oddKinds.bankedReason == nil,
       "a reset with no type or clears counts as full, as in Claude Code, and an unknown type is reported: \(oddKinds)")
let absurd = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": null}, "cedar_ember": {"eligible": true, "grants": [{"resets_left": 9223372036854775807}, {"resets_left": 9223372036854775807}]}}"#.utf8))
expect(absurd.banked == [] && absurd.bankedReason?.contains("cannot be right") == true,
       "an impossible number of banked resets is refused: \(absurd.bankedReason ?? "")")
expect(usage.bankedReason == "The usage answer says nothing about banked resets",
       "an answer without banked resets says so: \(usage.bankedReason ?? "")")
expect(ineligible.bankedReason == "Not eligible for banked resets",
       "an account not eligible for banked resets says so: \(ineligible.bankedReason ?? "")")
let ineligibleWhy = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": null}, "cedar_ember": {"eligible": false, "ineligible_reason": "tier"}}"#.utf8))
expect(ineligibleWhy.bankedReason == "Not eligible for banked resets (tier)", "the reason given is shown")
let soon = time("2026-11-13T12:00:00Z")
expect(ineligibleWhy.bankedNotes(at: soon) == ["Not eligible for banked resets (tier)"]
       && usage.bankedNotes(at: soon) == ["The usage answer says nothing about banked resets"]
       && malformedGrant.bankedNotes(at: soon).count == 1,
       "a reason the answer gives, or a failure to read it, is the only note, without claiming there are none")
expect(malformedGrant.bankedReason?.contains("cedar_ember.grants[0].resets_left is missing") == true,
       "a banked reset that cannot be read names what is wrong: \(malformedGrant.bankedReason ?? "")")
let usedUp = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": null}, "cedar_ember": {"eligible": true, "grants": [{"type": "weekly", "resets_left": 0}, {"type": "session", "resets_left": 3}]}}"#.utf8))
expect(usedUp.banked == [] && usedUp.bankedNotes(at: time("2026-11-13T12:00:00Z")) == ["No banked resets that clear the weekly limit"],
       "an account with none that clear the weekly limit says so: \(usedUp.bankedNotes(at: time("2026-11-13T12:00:00Z")))")
let expiredOnly = WeeklyUsage(percent: 1, resets: nil, banked: [BankedReset(label: "r", left: 1, ends: time("2026-11-01T00:00:00Z"))])
expect(expiredOnly.bankedNotes(at: time("2026-11-13T12:00:00Z")) == ["No banked resets that clear the weekly limit"],
       "an account whose banked resets have all expired says so")
expect(withGrants.bankedNotes(at: time("2026-11-13T00:00:00Z")) == [], "an account with banked resets has no note")
let onlyUnknown = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": null}, "cedar_ember": {"eligible": true, "grants": [{"type": "seven_day", "resets_left": 1}, {"type": "", "clears": ["seven_day"], "resets_left": 1, "paused": true}]}}"#.utf8))
expect(onlyUnknown.bankedNotes(at: time("2026-11-13T00:00:00Z"))
       == ["Banked resets of a kind quotabar does not know: seven_day", "No banked resets that clear the weekly limit"],
       "an account with only unknown kinds gives both notes: \(onlyUnknown.bankedNotes(at: time("2026-11-13T00:00:00Z")))")
let emptyType = try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": null}, "cedar_ember": {"eligible": true, "grants": [{"type": "", "clears": ["seven_day"], "resets_left": 1}]}}"#.utf8))
expect(emptyType.banked.map(\.label) == ["weekly reset"] && emptyType.unknownKinds == [] && emptyType.bankedReason == nil,
       "an empty type counts as no type: \(emptyType)")

let unstarted = try parseUsage(Data(#"{"seven_day": {"utilization": 0, "resets_at": null}}"#.utf8))
expect(unstarted.percent == 0 && unstarted.resets == nil, "a week not started has no reset time")

expect(time("2026-11-17T14:00:00+01:00") == time("2026-11-17T13:00:00Z"),
       "a time without a fraction of a second is read, with its offset from UTC")
await expectThrows("text that is not a time is refused") { try parseTime("next Tuesday") }
await expectThrows("an answer without a weekly quota is refused") {
    try parseUsage(Data(#"{"five_hour": {"utilization": 3, "resets_at": null}}"#.utf8))
}
await expectThrows("a weekly quota without a percentage is refused") {
    try parseUsage(Data(#"{"seven_day": {"utilization": null, "resets_at": null}}"#.utf8))
}
await expectThrows("an answer that is not JSON is refused") { try parseUsage(Data("<html>".utf8)) }
await expectThrows("a reset time that is not a time is refused") {
    try parseUsage(Data(#"{"seven_day": {"utilization": 1, "resets_at": "soon"}}"#.utf8))
} where: { "\($0)" == "seven_day.resets_at: soon is not a time" }

// MARK: - Where in the week a reset falls

expect(weekOffset(of: time("2026-11-16T00:00:00Z"), in: utc) == 0, "Monday midnight is the start")
expect(weekOffset(of: time("2026-11-17T14:00:00Z"), in: utc) == 38 * 3600, "Tuesday 14:00")
expect(weekOffset(of: time("2026-11-22T23:59:00Z"), in: utc) == week - 60, "Sunday 23:59 is the end")
expect(weekOffset(of: time("1969-12-29T00:00:00Z"), in: utc) == 0,
       "Monday midnight before 1970 is the start too")
expect(weekOffset(of: time("1969-12-28T12:00:00Z"), in: utc) == week - 12 * 3600,
       "Sunday noon before 1970 is half a day before the end")
expect(weekOffset(of: time("2026-11-16T08:00:00Z"), in: pacific) == 0,
       "Monday midnight is taken in the time zone given")
expect(weekOffset(of: time("2026-07-13T07:00:00Z"), in: pacific) == week - 3600,
       "in summer, Monday midnight on the clock is still Sunday 23:00 in standard time")
let beforeChange = time("2026-03-02T07:30:00Z")
expect(weekOffset(of: beforeChange, in: pacific)
       == weekOffset(of: beforeChange.addingTimeInterval(week), in: pacific),
       "a reset keeps its place in the week across the change to daylight saving time")

// MARK: - The order of the accounts

func account(_ email: String, id: String? = nil, refresh: String = "refresh", resets: String? = nil)
    -> Account {
    Account(id: id ?? email, email: email, organization: "Organization",
            tokens: Tokens(access: "access", refresh: refresh, expires: Date(timeIntervalSince1970: 0)),
            resets: resets.map(time))
}

// On Monday 16 November 2026, these resets are all still to come, and the
// first to come is Tuesday's.
let sunday = account("sunday", resets: "2026-11-22T10:00:00Z")
var tuesday = account("tuesday", resets: "2026-11-17T10:00:00Z")
let friday = account("friday", resets: "2026-11-20T10:00:00Z")
let (unknown, later) = (account("unknown"), account("later"))
let order = displayOrder([unknown, friday, sunday, later, tuesday], in: utc)
expect(order.map(\.email) == ["tuesday", "friday", "sunday", "unknown", "later"],
       "accounts go by when in the week they reset, then unknown ones as added: \(order.map(\.email))")

// Once Tuesday's reset passes, its next one is the latest of the three to
// come, but it keeps its place.
tuesday.resets = tuesday.resets!.addingTimeInterval(week)
let afterReset = displayOrder([unknown, friday, sunday, later, tuesday], in: utc)
expect(afterReset.map(\.email) == order.map(\.email),
       "an account keeps its place when its reset moves on a week: \(afterReset.map(\.email))")

let twins = displayOrder([account("b", resets: "2026-11-24T10:00:00Z"),
                          account("a", resets: "2026-11-17T10:00:00Z")], in: utc)
expect(twins.map(\.email) == ["a", "b"], "accounts resetting at the same time go by email")

// Sunday 23:30 and Monday 00:30 on Pacific clocks in winter. In summer the
// same instants are an hour later on the clock, Monday 00:30 and 01:30, and
// the order stays the same.
let lateSunday = account("late sunday", resets: "2026-11-23T07:30:00Z")
let earlyMonday = account("early monday", resets: "2026-11-23T08:30:00Z")
for (name, shift) in [("winter", 0.0), ("summer", -20 * week)] {
    func moved(_ account: Account) -> Account {
        var account = account
        account.resets = account.resets!.addingTimeInterval(shift)
        return account
    }
    let pacificOrder = displayOrder([moved(lateSunday), moved(earlyMonday)], in: pacific)
    expect(pacificOrder.map(\.email) == ["early monday", "late sunday"],
           "in \(name), Monday 00:30 standard time comes first: \(pacificOrder.map(\.email))")
}

// MARK: - The roster of accounts

enum Failure: Error {
    case offline
}
let usageNow = WeeklyUsage(percent: 10, resets: time("2026-11-17T10:00:00Z"))
let checkedAt = time("2026-11-16T09:00:00Z")
var roster = Roster([sunday, tuesday])
roster.add(account("new"))
expect(roster.accounts.map(\.email) == ["sunday", "tuesday", "new"], "a new account goes at the end")
roster.add(account("sunday", refresh: "fresh"))
expect(roster.accounts.map(\.email) == ["sunday", "tuesday", "new"]
       && roster.accounts[0].tokens.refresh == "fresh" && roster.accounts[0].resets == sunday.resets,
       "signing in again replaces the account in its place, keeping its reset time")
roster.add(account("sunday", id: "other organization"))
expect(roster.accounts.count == 4, "the same email in another organization is another account")
roster.remove("other organization")
expect(roster.accounts.map(\.email) == ["sunday", "tuesday", "new"], "an account can be removed")

let newAccountTicket = roster.ticket(for: roster.accounts[2])
let renewedTokens = Tokens(access: "renewed", refresh: "renewed", expires: checkedAt)
expect(roster.replaceTokens(renewedTokens, for: newAccountTicket)
       && roster.accounts[2].tokens == renewedTokens, "a check's refreshed tokens are kept")
expect(roster.summary(of: roster.accounts[2], now: checkedAt) == "checking", "an account not checked yet")
expect(roster.record(.success(usageNow), for: newAccountTicket, at: checkedAt),
       "a check that finds a reset time for the first time changes the accounts")
expect(roster.accounts[2].resets == usageNow.resets, "the reset time found is kept with the account")
expect(!roster.record(.success(usageNow), for: newAccountTicket, at: checkedAt),
       "a check that finds the same reset time does not change the accounts")
expect(roster.summary(of: roster.accounts[2], now: checkedAt) == "10% used, resets in 1d 1h",
       "a checked account: \(roster.summary(of: roster.accounts[2], now: checkedAt))")
expect(!roster.record(.success(WeeklyUsage(percent: 0, resets: nil)), for: newAccountTicket, at: checkedAt)
       && roster.accounts[2].resets == usageNow.resets,
       "a week not started keeps the last reset time, and so the account's place")
_ = roster.record(.success(usageNow), for: newAccountTicket, at: checkedAt)
_ = roster.record(.failure(Failure.offline), for: newAccountTicket, at: checkedAt)
let failed = roster.statuses[roster.accounts[2].id]
expect(failed?.usage == usageNow && failed?.problem != nil && failed?.signInNeeded == false,
       "a failed check keeps what the last good check found: \(String(describing: failed))")
expect(roster.summary(of: roster.accounts[2], now: checkedAt) == "10% used, resets in 1d 1h",
       "an account whose check failed still shows the last good check")
_ = roster.record(.failure(SignInNeeded(description: "refused")), for: newAccountTicket, at: checkedAt)
expect(roster.statuses[roster.accounts[2].id]?.signInNeeded == true
       && roster.summary(of: roster.accounts[2], now: checkedAt) == "sign in again",
       "an account whose tokens were refused needs signing in again")
let tuesdayTicket = roster.ticket(for: tuesday)
_ = roster.record(.failure(Failure.offline), for: tuesdayTicket, at: checkedAt)
expect(roster.summary(of: tuesday, now: checkedAt) == "check failed",
       "an account whose only check failed")

// A check that began before the account was signed in to again, or removed,
// changes nothing when it ends.
roster.add(account("new", refresh: "signed in again"))
expect(!roster.replaceTokens(renewedTokens, for: newAccountTicket)
       && roster.accounts[2].tokens.refresh == "signed in again",
       "an older check's refreshed tokens do not replace a new sign-in's")
expect(!roster.record(.failure(SignInNeeded(description: "old")), for: newAccountTicket, at: checkedAt)
       && roster.statuses[roster.accounts[2].id] == nil,
       "an older check's result does not replace what is known after a new sign-in")
expect(roster.accounts[2].resets == usageNow.resets, "signing in again keeps the reset time")
roster.remove(tuesday.id)
expect(!roster.replaceTokens(renewedTokens, for: tuesdayTicket)
       && !roster.record(.success(usageNow), for: tuesdayTicket, at: checkedAt)
       && roster.statuses[tuesday.id] == nil && !roster.accounts.contains { $0.id == tuesday.id },
       "a check of a removed account changes nothing")
roster.add(tuesday)
expect(!roster.record(.success(usageNow), for: tuesdayTicket, at: checkedAt),
       "a check from before an account was removed changes nothing once it is added back")
expect(roster.ordered(in: utc).map(\.email) == ["new", "tuesday", "sunday"],
       "the roster is shown in the order of its reset times: \(roster.ordered(in: utc).map(\.email))")

// MARK: - Describing errors

expect(describe(Problem(description: "short")) == "short", "a short problem is described as it is")
expect(describe(Problem(description: "first\nsecond")) == "first", "only the first line is kept")
struct Shape: Decodable {
    struct Inner: Decodable {
        let count: Int
    }
    let items: [Inner]
}
for (json, explanation) in [(#"{"items": [{"count": "two"}]}"#, "items[0].count is not a number"),
                            (#"{"items": [{}]}"#, "items[0].count is missing"),
                            (#"{"items": null}"#, "items is null"),
                            (#"{"items": {}}"#, "items is not a list"),
                            ("[]", "the whole answer is not an object"),
                            ("not JSON", "it is not JSON")] {
    do {
        _ = try JSONDecoder().decode(Shape.self, from: Data(json.utf8))
        expect(false, "\(json) does not decode")
    } catch {
        expect(explain(error) == explanation, "a decoding failure names the field: \(explain(error))")
    }
}
let long = describe(Problem(description: String(repeating: "x", count: 500)))
expect(long.count == 200 && long.hasSuffix("…"), "a long description is cut to 200 characters")

// MARK: - How the week and quota are described

let now = time("2026-11-13T12:00:00Z")
expect(weekElapsed(until: now.addingTimeInterval(week), now: now) == 0, "a week just started")
expect(weekElapsed(until: now.addingTimeInterval(week / 4), now: now) == 0.75, "three quarters gone")
expect(weekElapsed(until: now.addingTimeInterval(-60), now: now) == 1, "a reset already passed")
expect(weekElapsed(until: now.addingTimeInterval(2 * week), now: now) == 0, "a reset too far ahead")

expect(describeDuration(2 * 86400 + 5 * 3600 + 59 * 60) == "2d 5h", "days and hours")
expect(describeDuration(3 * 3600 + 4 * 60 + 59) == "3h 4m", "hours and minutes")
expect(describeDuration(59 * 60 + 59) == "59m", "minutes")
expect(describeDuration(-30) == "0m", "a time already passed")

expect(describe(WeeklyUsage(percent: 37.9, resets: now.addingTimeInterval(90 * 60)), now: now)
       == "37% used, resets in 1h 30m", "a week with a reset time")
expect(describe(WeeklyUsage(percent: 0, resets: nil), now: now) == "0% used, week not started",
       "a week not started")
expect(describe(WeeklyUsage(percent: 37.9, resets: now.addingTimeInterval(-60)), now: now)
       == "37% used, from before the last reset", "usage from before a reset that has since passed")
let bankedUsage = WeeklyUsage(percent: 5, resets: nil, banked: [
    BankedReset(label: "a", left: 2, ends: nil), BankedReset(label: "b", left: 1, ends: now.addingTimeInterval(60)),
    BankedReset(label: "expired", left: 4, ends: now.addingTimeInterval(-60))])
expect(describe(bankedUsage, now: now) == "5% used, week not started, 3 banked resets",
       "banked resets that can still be used are counted: \(describe(bankedUsage, now: now))")
let oneBanked = WeeklyUsage(percent: 5, resets: nil, banked: [BankedReset(label: "a", left: 1, ends: nil)])
expect(describe(oneBanked, now: now) == "5% used, week not started, 1 banked reset", "one banked reset")
expect(bankedUsage.bankedCount(at: now) == 3 && oneBanked.bankedCount(at: now) == 1
       && usage.bankedCount(at: now) == 0, "banked resets that can still be used are counted")
expect(describe(WeeklyUsage(percent: 1e20, resets: nil), now: now).hasPrefix("100% used"),
       "an impossible percentage is shown as 100%")

// MARK: - PKCE and the sign-in page

// The example in RFC 7636, appendix B.
expect(challenge(of: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
       == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", "the challenge is RFC 7636's")
let tokens = (0..<20).map { _ in randomToken() }
expect(tokens.allSatisfy { $0.count == 43 && $0.allSatisfy { $0.isLetter || $0.isNumber || "-_".contains($0) } },
       "random tokens are 43 URL-safe characters: \(tokens[0])")
expect(Set(tokens).count == tokens.count, "random tokens differ")

func query(_ url: URL) -> [String: String] {
    Dictionary(uniqueKeysWithValues: URLComponents(url: url, resolvingAgainstBaseURL: false)!
        .queryItems!.map { ($0.name, $0.value ?? "") })
}

let page = Service.signInPage(redirect: "http://localhost:1234/callback", challenge: "C",
                                state: "S", hint: nil)
expect(page.absoluteString.hasPrefix("https://claude.com/cai/oauth/authorize?"),
       "the sign-in page is Claude's: \(page)")
let pageQuery = ["code": "true", "client_id": Service.clientID, "response_type": "code",
                 "redirect_uri": "http://localhost:1234/callback", "scope": "user:profile",
                 "code_challenge": "C", "code_challenge_method": "S256", "state": "S"]
expect(query(page) == pageQuery,
       "the sign-in page is asked for a code for the profile scope only: \(query(page))")
let hinted = Service.signInPage(redirect: "http://localhost:1234/callback", challenge: "C",
                                  state: "S", hint: "someone@example.com")
expect(query(hinted) == pageQuery.merging(["login_hint": "someone@example.com"]) { $1 },
       "signing in again asks for the account by its email address: \(query(hinted))")

// MARK: - Reading the browser's return

expect(callback(from: "GET /callback?code=abc&state=xyz HTTP/1.1", state: "xyz") == .code("abc"),
       "a code for this sign-in")
expect(callback(from: "GET /callback?code=abc&state=other HTTP/1.1", state: "xyz") == .ignored,
       "a code for another sign-in")
expect(callback(from: "GET /callback?code=abc HTTP/1.1", state: "xyz") == .ignored,
       "a code for no sign-in in particular")
expect(callback(from: "GET /favicon.ico HTTP/1.1", state: "xyz") == .ignored, "another path")
expect(callback(from: "POST /callback?code=abc&state=xyz HTTP/1.1", state: "xyz") == .ignored,
       "another method")
expect(callback(from: "nonsense", state: "xyz") == .ignored, "not HTTP")
expect(callback(from: "GET /callback?error=access_denied&error_description=No%20thanks&state=xyz HTTP/1.1",
                state: "xyz") == .refused("No thanks"), "a refusal, with its description")
expect(callback(from: "GET /callback?error=access_denied&state=xyz HTTP/1.1", state: "xyz")
       == .refused("access_denied"), "a refusal without a description")
expect(callback(from: "GET /callback?code=&state=xyz HTTP/1.1", state: "xyz")
       == .refused("no code came back"), "an empty code")

// MARK: - The sign-in listener

func get(_ url: String) async throws -> (Int, String) {
    let (data, response) = try await URLSession.shared.data(from: URL(string: url)!)
    return ((response as! HTTPURLResponse).statusCode, String(decoding: data, as: UTF8.self))
}

/// Chooses the item called `title` in `menu`, and returns whether there was
/// one.
@MainActor
func choose(_ title: String, in menu: NSMenu) -> Bool {
    guard let index = menu.items.firstIndex(where: { $0.title == title }) else {
        expect(false, "the menu has \(title): \(menu.items.map(\.title))")
        return false
    }
    menu.performActionForItem(at: index)
    return true
}

/// Does what the browser does once someone signs in on the page at `url`.
func comeBack(from url: URL, with code: String) -> Bool {
    let parameters = query(url)
    Task { @MainActor in
        do {
            _ = try await get("\(parameters["redirect_uri"]!)?code=\(code)&state=\(parameters["state"]!)")
        } catch {
            // The sign-in waiting for this would wait for ever. The next
            // run deletes the keychain items this one leaves.
            fputs("FAIL: the browser cannot reach the listener: \(error)\n", stderr)
            exit(1)
        }
    }
    return true
}

/// A connection to the listener that sends a request in pieces, as a
/// browser's request can arrive.
final class Client: @unchecked Sendable {
    private let connection: NWConnection

    init(port: UInt16) {
        connection = NWConnection(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!,
                                  using: .tcp)
        connection.start(queue: DispatchQueue(label: "client"))
    }

    func send(_ text: String) async throws {
        try await withCheckedThrowingContinuation { (done: CheckedContinuation<Void, Error>) in
            connection.send(content: Data(text.utf8), completion: .contentProcessed { error in
                done.resume(with: error.map { .failure($0) } ?? .success(()))
            })
        }
    }

    /// Everything the listener sends until it closes the connection.
    func answer() async throws -> String {
        var answer = Data()
        while true {
            let (data, complete) = try await withCheckedThrowingContinuation {
                (done: CheckedContinuation<(Data, Bool), Error>) in
                connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
                    data, _, complete, error in
                    done.resume(with: error.map { .failure($0) } ?? .success((data ?? Data(), complete)))
                }
            }
            answer += data
            if complete {
                connection.cancel()
                return String(decoding: answer, as: UTF8.self)
            }
        }
    }
}

let listener = try CallbackListener()
let port = try await listener.port()
expect(port != 0, "the listener has a port")
let waiting = [Task { try await listener.code() }, Task { try await listener.code() }]
let base = "http://localhost:\(port)"
let stray = try await get("\(base)/callback?code=stolen&state=wrong")
expect(stray.0 == 404, "a request for another sign-in is turned away: \(stray)")
// The pieces of this request usually reach the listener as separate reads,
// with another request answered in between. However they arrive, the
// listener must answer only once the request is whole.
let split = Client(port: port)
try await split.send("GET /callback?code=the-co")
let icon = try await get("\(base)/favicon.ico")
expect(icon.0 == 404, "other paths are turned away: \(icon)")
try await split.send("de&state=\(listener.state) HTTP/1.1\r\nHost: localhost\r\n")
try await split.send("Cookie: \(String(repeating: "c", count: 20000))\r\n\r\n")
let good = try await split.answer()
expect(good.hasPrefix("HTTP/1.1 200 OK\r\n") && good.hasSuffix("quotabar is signed in. You can close this tab."),
       "a code that arrives in pieces is taken: \(good)")
for wait in waiting {
    let received = try await wait.value
    expect(received == "the-code", "everyone waiting gets the code for this sign-in: \(received)")
}
let lateCode = try await listener.code()
expect(lateCode == "the-code", "asking for the code again gives the same code")

let idleListener = try CallbackListener()
let idlePort = try await idleListener.port()
let idle = Client(port: idlePort)
try await idle.send("GET /callback?co")
_ = try await get("http://localhost:\(idlePort)/favicon.ico")
_ = try await get("http://localhost:\(idlePort)/callback?code=c&state=\(idleListener.state)")
// The connection is closed either way: answered with nothing once the
// listener has taken it, or refused if the listener ended first.
let idleAnswer = (try? await idle.answer()) ?? ""
let oversizedListener = try CallbackListener()
let oversized = Client(port: try await oversizedListener.port())
try await oversized.send("GET /callback?code=" + String(repeating: "x", count: 1_100_000))
let oversizedAnswer = (try? await oversized.answer()) ?? ""
expect(oversizedAnswer == "", "a request that never ends is closed unanswered: \(oversizedAnswer.prefix(40))")
oversizedListener.cancel()
expect(idleAnswer == "", "a connection still open when the sign-in ends is closed: \(idleAnswer)")

let refusedListener = try CallbackListener()
let refusal = try await get("http://localhost:\(try await refusedListener.port())/callback?error=access_denied&state=\(refusedListener.state)")
expect(refusal.0 == 400 && refusal.1.contains("access_denied"), "a refusal is reported: \(refusal)")
await expectThrows("a refused sign-in fails") { try await refusedListener.code() } where: {
    "\($0)".contains("access_denied")
}

let cancelledListener = try CallbackListener()
let cancelledWait = Task { try await cancelledListener.code() }
cancelledListener.cancel()
await expectThrows("a cancelled sign-in fails") { try await cancelledWait.value } where: {
    $0 is CancellationError
}

// MARK: - Talking to the server

/// Answers every request quotabar's service makes, recording them.
final class Stub: URLProtocol {
    static var answer: (URLRequest, Data) -> (Int, String) = { _, _ in (500, "") }
    static var requests: [(request: URLRequest, body: [String: String])] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        var body = Data()
        if let stream = request.httpBodyStream {
            stream.open()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while case let count = stream.read(&buffer, maxLength: buffer.count), count > 0 {
                body.append(buffer, count: count)
            }
            stream.close()
        }
        Stub.requests.append((request, (try? JSONDecoder().decode([String: String].self, from: body)) ?? [:]))
        let (status, text) = Stub.answer(request, body)
        // A status of 0 or less stands for a request that never reached a
        // server: 0 for a Mac with no network, and otherwise the URLError
        // code.
        guard status > 0 else {
            client?.urlProtocol(self, didFailWithError: URLError(status == 0 ? .notConnectedToInternet
                                                                 : URLError.Code(rawValue: status)))
            return
        }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status,
                                                              httpVersion: nil, headerFields: nil)!,
                            cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(text.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
}

let configuration = URLSessionConfiguration.ephemeral
configuration.protocolClasses = [Stub.self]
var service = Service()
service.session = URLSession(configuration: configuration)
service.now = { now }

func respond(_ answers: [String: [(Int, String)]]) {
    var remaining = answers
    Stub.requests = []
    Stub.answer = { request, _ in
        let key = request.url!.host!
        guard let next = remaining[key]?.first else {
            return (500, "no answer prepared for \(key)")
        }
        remaining[key]!.removeFirst()
        return next
    }
}

let tokenHost = "platform.claude.com", usageHost = "api.anthropic.com"
let signedIn = #"""
    {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 28800,
     "token_type": "Bearer", "scope": "user:profile",
     "account": {"uuid": "account-uuid", "email_address": "someone@example.com"},
     "organization": {"uuid": "organization-uuid", "name": "Example"}}
    """#

// Signing in: the browser comes back with a code, which is traded for tokens.
respond([tokenHost: [(200, signedIn)]])
let signInListener = try CallbackListener()
var opened: URL?
let newAccount = try await service.signIn(through: signInListener) { url in
    opened = url
    return comeBack(from: url, with: "browser-code")
}
expect(newAccount == Account(id: "account-uuid/organization-uuid", email: "someone@example.com",
                             organization: "Example",
                             tokens: Tokens(access: "access-1", refresh: "refresh-1",
                                            expires: now.addingTimeInterval(28800))),
       "signing in gives the account the server names: \(newAccount)")
let exchange = Stub.requests.first
expect(exchange?.request.httpMethod == "POST"
       && exchange?.request.url?.absoluteString == "https://platform.claude.com/v1/oauth/token"
       && exchange?.request.value(forHTTPHeaderField: "Content-Type") == "application/json",
       "the code is posted as JSON to the token endpoint")
let sent = exchange?.body ?? [:]
let signInPort = try await signInListener.port()
expect(sent["grant_type"] == "authorization_code" && sent["code"] == "browser-code"
       && sent["redirect_uri"] == "http://localhost:\(signInPort)/callback"
       && sent["redirect_uri"] == opened.map { query($0)["redirect_uri"] }
       && sent["state"] == signInListener.state
       && sent["client_id"] == Service.clientID,
       "the code is traded for tokens with the details of this sign-in: \(sent)")
expect(opened.map { query($0)["code_challenge"] } == challenge(of: sent["code_verifier"] ?? ""),
       "the verifier sent with the code matches the challenge on the sign-in page")

expect(opened.map { query($0)["login_hint"] } == .some(nil), "a new sign-in asks for no account")

respond([tokenHost: [(200, #"{"access_token": "a", "refresh_token": "r", "expires_in": 60}"#)]])
await expectThrows("a sign-in that does not name its account fails") {
    try await service.signIn(through: try CallbackListener()) { comeBack(from: $0, with: "c") }
} where: { "\($0)".contains("which account") }

respond([tokenHost: [(200, #"""
    {"access_token": "a", "expires_in": 60, "account": {"uuid": "u", "email_address": "e"},
     "organization": {"uuid": "o"}}
    """#)]])
await expectThrows("a sign-in without a refresh token fails") {
    try await service.signIn(through: try CallbackListener(), hint: "e") { url in
        opened = url
        return comeBack(from: url, with: "c")
    }
} where: { "\($0)".contains("missing tokens") }
expect(opened.map { query($0)["login_hint"] } == "e", "signing in again asks for the account")

respond([tokenHost: [(400, #"{"error": "invalid_grant", "error_description": "code expired"}"#)]])
await expectThrows("a refused code needs signing in again") {
    try await service.signIn(through: try CallbackListener()) { comeBack(from: $0, with: "c") }
} where: { $0 is SignInNeeded }

let unopened = try CallbackListener()
await expectThrows("a sign-in page that does not open fails") {
    try await service.signIn(through: unopened) { _ in false }
} where: { "\($0)".contains("did not open") }
await expectThrows("a sign-in page that does not open ends the sign-in") {
    try await unopened.code()
} where: { $0 is CancellationError }

// Refreshing tokens.
let old = Tokens(access: "old-access", refresh: "old-refresh", expires: now)
respond([tokenHost: [(200, #"{"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}"#)]])
var refreshSaved: [Tokens] = []
let renewed = try await service.refresh(old) { refreshSaved.append($0) }
expect(renewed == Tokens(access: "new-access", refresh: "new-refresh", expires: now.addingTimeInterval(3600))
       && refreshSaved == [renewed], "a refresh gives new tokens, and saves them: \(renewed)")
expect(Stub.requests.first?.body == ["grant_type": "refresh_token", "refresh_token": "old-refresh",
                                     "client_id": Service.clientID, "scope": "user:profile"],
       "a refresh sends the refresh token and asks for the profile scope only")
respond([tokenHost: [(200, #"{"access_token": "new-access", "expires_in": 3600}"#)]])
let kept = try await service.refresh(old) { _ in }
expect(kept.refresh == "old-refresh", "a refresh that gives no refresh token keeps the old one")
for (body, why) in [(#"{"access_token": "a", "refresh_token": "", "expires_in": 60}"#, "an empty refresh token"),
                    (#"{"access_token": "a", "refresh_token": 7, "expires_in": 60}"#, "a refresh token that is not text")] {
    respond([tokenHost: [(200, body)]])
    refreshSaved = []
    await expectThrows("a refresh answer with \(why) fails") { try await service.refresh(old) { refreshSaved.append($0) } }
    expect(refreshSaved.isEmpty, "a refresh answer with \(why) saves nothing")
}
respond([tokenHost: [(400, #"{"error": "invalid_grant"}"#)]])
await expectThrows("a refused refresh token needs signing in again") { try await service.refresh(old) { _ in } }
    where: { $0 is SignInNeeded && "\($0)".contains("invalid_grant") }
respond([tokenHost: [(401, #"{"error": {"type": "invalid_grant", "message": "revoked"}}"#)]])
await expectThrows("a refusal whose error is an object needs signing in again") {
    try await service.refresh(old) { _ in }
} where: { $0 is SignInNeeded }
respond([tokenHost: [(400, #"{"error": "invalid_client"}"#)]])
await expectThrows("a refusal for another reason is not a reason to sign in again") {
    try await service.refresh(old) { _ in }
} where: { !($0 is SignInNeeded) && "\($0)".contains("400") }
respond([tokenHost: [(500, "<html>\n<body>Bad gateway</body>\n</html>")]])
await expectThrows("a server failure's answer is reported by its first line") {
    try await service.refresh(old) { _ in }
} where: { !($0 is SignInNeeded) && "\($0)" == "platform.claude.com answered 500: <html>" }
respond([tokenHost: [(200, "not JSON")]])
await expectThrows("a token answer that cannot be read fails") { try await service.refresh(old) { _ in } }
    where: { !($0 is SignInNeeded) && "\($0)".contains("not what quotabar expects") }
respond([tokenHost: [(200, "{}")]])
refreshSaved = []
await expectThrows("a token answer without tokens fails") {
    try await service.refresh(old) { refreshSaved.append($0) }
} where: { "\($0)".contains("refresh answer is not what quotabar expects") }
expect(refreshSaved.isEmpty, "an answer without tokens saves nothing")
for (body, why) in [(#"{"refresh_token": "rotated"}"#, "only a refresh token"),
                    (#"{"access_token": "a", "refresh_token": "rotated", "expires_in": "3600"}"#,
                     "an expiry that is not a number"),
                    (#"{"refresh_token": "rotated", "account": {"uuid": "u", "email_address": null}}"#,
                     "an account that cannot be read")] {
    respond([tokenHost: [(200, body)]])
    refreshSaved = []
    await expectThrows("a token answer with \(why) fails") {
        try await service.refresh(old) { refreshSaved.append($0) }
    } where: { "\($0)".contains("not what quotabar expects") }
    expect(refreshSaved == [Tokens(access: "old-access", refresh: "rotated", expires: now)],
           "from an answer with \(why), the new refresh token is saved even so: \(refreshSaved)")
}

expect(oauthError(Data(#"{"error": "invalid_grant"}"#.utf8)) == "invalid_grant", "an error as a string")
expect(oauthError(Data(#"{"error": {"type": "invalid_grant"}}"#.utf8)) == "invalid_grant",
       "an error as an object")
expect(oauthError(Data(#"{"message": "no"}"#.utf8)) == nil, "an answer with no error")
expect(oauthError(Data("not JSON".utf8)) == nil, "an answer that is not JSON")

// Asking for usage.
let weekly = #"{"seven_day": {"utilization": 50, "resets_at": "2026-11-17T14:00:00Z"}}"#
respond([usageHost: [(200, weekly)]])
let fetched = try await service.usage("an-access-token")
expect(fetched.percent == 50 && fetched.resets == time("2026-11-17T14:00:00Z"), "usage: \(fetched)")
let usageRequest = Stub.requests.first?.request
expect(usageRequest?.url?.absoluteString
       == "https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1"
       && usageRequest?.value(forHTTPHeaderField: "Authorization") == "Bearer an-access-token"
       && usageRequest?.value(forHTTPHeaderField: "anthropic-beta") == "oauth-2025-04-20"
       && usageRequest?.value(forHTTPHeaderField: "User-Agent") == "claude-cli/2.1.311 (external, cli) quotabar/1"
       && usageRequest?.value(forHTTPHeaderField: "x-app") == "cli"
       && exchange?.request.value(forHTTPHeaderField: "x-app") == "cli",
       "usage, with banked resets, is asked for with the access token")
respond([usageHost: [(500, "overloaded")]])
await expectThrows("a server failure is not a reason to sign in again") { try await service.usage("t") }
    where: { !($0 is SignInNeeded) && "\($0)".contains("500: overloaded") }
respond([usageHost: [(400, "bad request")]])
await expectThrows("a bad request is not a reason to sign in again") { try await service.usage("t") }
    where: { !($0 is SignInNeeded) && "\($0)".contains("400: bad request") }
respond([usageHost: [(401, "expired")]])
await expectThrows("a refused access token is reported as refused") { try await service.usage("t") }
    where: { ($0 as? HTTPFailure)?.status == 401 }

// Asking for an account's usage, refreshing its tokens as needed.
let fresh = account("fresh")
var current = fresh
current.tokens = Tokens(access: "current", refresh: "current-refresh", expires: now.addingTimeInterval(60))
var expired = current
expired.tokens.expires = now
let newTokens = #"{"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}"#
let rotated = Tokens(access: "new-access", refresh: "new-refresh", expires: now.addingTimeInterval(3600))
var saved: [Tokens] = []

func usedTokens() -> [String] {
    Stub.requests.compactMap { $0.request.value(forHTTPHeaderField: "Authorization") }
}

respond([usageHost: [(200, weekly)]])
saved = []
_ = try await service.usage(of: current) { saved.append($0) }
expect(saved.isEmpty && usedTokens() == ["Bearer current"], "unexpired tokens are used as they are")

respond([tokenHost: [(200, newTokens)], usageHost: [(200, weekly)]])
saved = []
_ = try await service.usage(of: expired) { saved.append($0) }
expect(saved == [rotated] && usedTokens() == ["Bearer new-access"],
       "expired tokens are refreshed first, and the new ones kept: \(saved)")

respond([tokenHost: [(200, newTokens)], usageHost: [(401, "expired"), (200, weekly)]])
saved = []
_ = try await service.usage(of: current) { saved.append($0) }
expect(saved == [rotated] && usedTokens() == ["Bearer current", "Bearer new-access"],
       "a refused access token is refreshed and the usage asked for again: \(usedTokens())")

respond([tokenHost: [(200, newTokens)], usageHost: [(401, "no"), (401, "still no")]])
saved = []
await expectThrows("fresh tokens the usage endpoint refuses are not a reason to sign in again") {
    try await service.usage(of: current) { saved.append($0) }
} where: { !($0 is SignInNeeded) && "\($0)".contains("401: still no") }
expect(saved == [rotated], "the new tokens are kept even when the usage fails afterwards")

respond([tokenHost: [(200, newTokens), (200, newTokens)], usageHost: [(401, "no"), (200, weekly)]])
saved = []
await expectThrows("tokens refused just after a refresh are not refreshed again") {
    try await service.usage(of: expired) { saved.append($0) }
} where: { !($0 is SignInNeeded) && "\($0)".contains("401: no") }
expect(saved == [rotated], "only one refresh happens: \(saved)")

var events: [String] = []
respond([tokenHost: [(200, newTokens)], usageHost: [(200, weekly)]])
_ = try await service.usage(of: expired, refreshing: { events.append($0 ? "start" : "end") }) { _ in
    events.append("save")
}
expect(events == ["start", "save", "end"], "a refresh is under way until its tokens are saved: \(events)")
respond([tokenHost: [(500, "down")]])
events = []
await expectThrows("a failed refresh fails the check") {
    try await service.usage(of: expired, refreshing: { events.append($0 ? "start" : "end") }) { _ in
        events.append("save")
    }
}
expect(events == ["start", "end"], "a failed refresh ends too: \(events)")

respond([tokenHost: [(400, #"{"error": "invalid_grant"}"#)]])
saved = []
await expectThrows("a refused refresh token needs signing in again") {
    try await service.usage(of: expired) { saved.append($0) }
} where: { $0 is SignInNeeded }
expect(saved.isEmpty, "nothing is kept when the refresh fails")

respond([tokenHost: [(500, "overloaded")]])
saved = []
await expectThrows("a refresh the server fails is not a reason to sign in again") {
    try await service.usage(of: expired) { saved.append($0) }
} where: { !($0 is SignInNeeded) }
expect(saved.isEmpty, "nothing is kept when the server fails the refresh")

respond([tokenHost: [(200, newTokens)], usageHost: [(400, "bad request")]])
saved = []
await expectThrows("a bad request is reported") {
    try await service.usage(of: current) { saved.append($0) }
} where: { !($0 is SignInNeeded) }
expect(saved.isEmpty && Stub.requests.count == 1, "a bad request does not refresh the tokens")

// MARK: - Drawing

/// The opacity of each point of a glyph drawn at one pixel per point, by
/// point coordinates with the origin at the bottom left.
func render(_ statuses: [Status?]) -> (CGFloat, CGFloat) -> CGFloat {
    let image = glyphImage(statuses, now: now)
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(image.size.width),
                                  pixelsHigh: Int(image.size.height), bitsPerSample: 8,
                                  samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                  colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    image.draw(in: NSRect(origin: .zero, size: image.size))
    NSGraphicsContext.restoreGraphicsState()
    return { x, y in bitmap.colorAt(x: Int(x), y: bitmap.pixelsHigh - 1 - Int(y))!.alphaComponent }
}

// Points on the ring (radius 7 around the centre at 8, 8) at three o'clock,
// six o'clock and nine o'clock, and on the disc at half past one and seven.
let (threeOClock, sixOClock, nineOClock) = ((14.0, 8.0), (8.0, 1.0), (1.0, 8.0))
let (discHalfOne, discSeven) = ((10.0, 10.0), (6.0, 5.0))
func checked(_ percent: Double, weekLeft: Double, banked: [BankedReset] = [], problem: String? = nil) -> Status {
    Status(usage: WeeklyUsage(percent: percent, resets: now.addingTimeInterval(weekLeft * week), banked: banked),
           checked: now, problem: problem)
}
let quarter = render([checked(30, weekLeft: 0.6)])
expect(quarter(threeOClock.0, threeOClock.1) > 0.9, "the ring is solid where the week has passed")
expect(abs(quarter(sixOClock.0, sixOClock.1) - 0.25) < 0.05, "the ring is faint where the week is to come")
expect(quarter(discHalfOne.0, discHalfOne.1) > 0.9, "the disc is solid where the quota is used")
expect(abs(quarter(discSeven.0, discSeven.1) - 0.25) < 0.05, "the disc is faint where the quota is left")
let full = render([checked(100, weekLeft: 0.6)])
let fullDisc = (6...10).flatMap { x in (6...10).map { y in full(CGFloat(x), CGFloat(y)) } }
expect(fullDisc.allSatisfy { $0 > 0.9 }, "a used-up quota is a solid disc")
let late = render([checked(80, weekLeft: 0.1)])
expect(late(nineOClock.0, nineOClock.1) > 0.9 && late(discSeven.0, discSeven.1) > 0.9,
       "late in the week, with most of the quota used, most of both is solid")
let withBanked = render([checked(100, weekLeft: 0.6, banked: [BankedReset(label: "r", left: 2, ends: nil)])])
let digitDisc = (6...10).flatMap { x in (6...10).map { y in withBanked(CGFloat(x), CGFloat(y)) } }
expect(digitDisc.contains { $0 > 0.9 } && digitDisc.contains { $0 < 0.1 },
       "banked resets show a digit in the middle of the disc, clear where the disc is solid")
expect(withBanked(threeOClock.0, threeOClock.1) > 0.9, "the digit stays clear of the ring")
func disc(banked count: Int) -> [CGFloat] {
    let drawn = render([checked(100, weekLeft: 0.6, banked: [BankedReset(label: "r", left: count, ends: nil)])])
    return (4...12).flatMap { x in (4...12).map { y in drawn(CGFloat(x), CGFloat(y)) } }
}
expect(disc(banked: 2) != disc(banked: 3), "different counts draw different digits")
expect(disc(banked: 12) == disc(banked: 15) && disc(banked: 12) != disc(banked: 9),
       "more than nine banked resets are all drawn alike, and unlike nine")
let failedDrawing = render([checked(30, weekLeft: 0.6, problem: "offline")])
let failedUsed = failedDrawing(discHalfOne.0, discHalfOne.1)
expect(failedUsed > 0.5 && failedUsed < 0.75, "a failed check is drawn faintly: \(failedUsed)")
var refused = Status()
refused.signInNeeded = true
let exclamation = render([refused])
expect(exclamation(8, 8) > 0.9 && exclamation(8, 5) == 0 && exclamation(8, 4) > 0.9,
       "an account needing sign-in shows an exclamation mark")
let past = render([checked(30, weekLeft: -0.1)])
expect(abs(past(discHalfOne.0, discHalfOne.1) - 0.625) < 0.1,
       "usage from before a reset that has passed is drawn faintly: \(past(discHalfOne.0, discHalfOne.1))")
func midTones(_ drawn: (CGFloat, CGFloat) -> CGFloat) -> Int {
    var count = 0
    for x in 4...12 {
        for y in 4...12 where drawn(CGFloat(x), CGFloat(y)) > 0.6 && drawn(CGFloat(x), CGFloat(y)) < 0.9 {
            count += 1
        }
    }
    return count
}
let partUsed = render([checked(30, weekLeft: 0.6, banked: [BankedReset(label: "r", left: 2, ends: nil)])])
expect(midTones(partUsed) > midTones(render([checked(30, weekLeft: 0.6)])) + 3,
       "the digit is dark where it crosses the faint part of the disc")
let warningImage = glyphImage([], now: now, warning: true)
let warningBitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 16, pixelsHigh: 16, bitsPerSample: 8,
                                     samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                     bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: warningBitmap)
warningImage.draw(in: NSRect(x: 0, y: 0, width: 16, height: 16))
NSGraphicsContext.restoreGraphicsState()
func warningAlpha(_ x: Int, _ y: Int) -> CGFloat { warningBitmap.colorAt(x: x, y: 15 - y)!.alphaComponent }
expect(warningImage.size.width == glyphSize && warningAlpha(8, 10) > 0.9 && warningAlpha(8, 1) > 0.9
       && warningAlpha(8, 4) == 0 && warningAlpha(14, 8) == 0,
       "a warning is a bare exclamation mark, without an account's ring")
let failedFirst = render([Status(problem: "offline")])
expect(abs(failedFirst(8, 8) - 0.25) < 0.05, "an account whose only check failed shows a faint disc")
let unchecked = render([nil])
expect(abs(unchecked(threeOClock.0, threeOClock.1) - 0.25) < 0.05 && unchecked(8, 8) == 0,
       "an account not checked yet shows only a faint ring")
let pair = glyphImage([nil, nil], now: now)
expect(pair.size == NSSize(width: 36, height: 16) && pair.isTemplate,
       "glyphs sit side by side, drawn as a template for the menu bar")
expect(glyphImage([], now: now).size == NSSize(width: 16, height: 16), "no accounts still shows one glyph")

// MARK: - The watcher

let watcherItem = KeychainItem(service: temporaryService(), account: "accounts")
let watcherStore = AccountStore(item: watcherItem)
var changes = 0
var browserOpens = true
var browserComesBack = true
var pagesOpened = 0
var lastPage: URL?
/// Resumed the next time the watcher says something changed, as it does
/// when a check ends.
var checkEnded: CheckedContinuation<Void, Never>?
var pageOpened: CheckedContinuation<Void, Never>?
let watcher = Watcher(service: service, store: watcherStore, open: { url in
    pagesOpened += 1
    lastPage = url
    pageOpened?.resume()
    pageOpened = nil
    return browserOpens && (!browserComesBack || comeBack(from: url, with: "watcher-code"))
}, changed: {
    changes += 1
    checkEnded?.resume()
    checkEnded = nil
})
let someone = "account-uuid/organization-uuid"

do {
    watcher.load()
    expect(watcher.roster.accounts.isEmpty && watcher.loadProblem == nil, "no accounts are kept yet")
    expect(watcher.setOnline(true) == nil, "going online with no checks waiting starts none")

    respond([tokenHost: [(200, signedIn)], usageHost: [(200, weekly)]])
    changes = 0
    await watcher.startSignIn()?.value
    expect(watcher.roster.accounts.map(\.email) == ["someone@example.com"] && !watcher.signingIn
           && watcher.signInNote == nil && changes > 0, "signing in adds the account")
    expect(watcher.roster.statuses[someone]?.usage?.percent == 50, "an account is checked once signed in")
    expect(try watcherStore.load().map(\.resets) == [time("2026-11-17T14:00:00Z")],
           "the account is saved with the reset time its check found")

    watcher.setOnline(false)
    respond([usageHost: [(200, weekly)]])
    await watcher.checkAll().value
    expect(Stub.requests.isEmpty, "while the Mac is offline, checks wait")
    await watcher.setOnline(true)?.value
    expect(Stub.requests.count == 1, "checks asked for while offline run once the Mac is online")
    respond([usageHost: [(0, ""), (200, weekly)]])
    await watcher.checkAll().value
    expect(watcher.roster.statuses[someone]?.problem != nil, "a check that finds no network fails")
    await watcher.setOnline(true)?.value
    expect(Stub.requests.count == 2 && watcher.roster.statuses[someone]?.problem == nil,
           "a check that found no network runs again once the Mac says it is online")
    respond([usageHost: [(500, "down")]])
    await watcher.checkAll().value
    expect(watcher.setOnline(true) == nil, "a check the server failed does not run again when the network changes")
    respond([usageHost: [(URLError.badServerResponse.rawValue, "")]])
    await watcher.checkAll().value
    expect(watcher.setOnline(true) == nil, "a garbled answer is not a reason to wait for the network")
    respond([usageHost: [(URLError.timedOut.rawValue, ""), (200, weekly)]])
    await watcher.checkAll().value
    await watcher.setOnline(true)?.value
    expect(Stub.requests.count == 2 && watcher.roster.statuses[someone]?.problem == nil,
           "a check that timed out runs again once the network is back")
    respond([usageHost: [(0, ""), (200, weekly)]])
    await watcher.checkAll().value
    await watcher.checkAll().value
    expect(watcher.setOnline(true) == nil, "a check that runs clears any wait for the network")

    // macOS says the network went and came back while a check is under way,
    // which then fails for want of it.
    let networkGate = DispatchSemaphore(value: 0)
    var usageRequested: CheckedContinuation<Void, Never>?
    var networkAnswers = [(0, ""), (200, weekly)]
    Stub.requests = []
    Stub.answer = { _, _ in
        if networkAnswers.count == 2 {
            DispatchQueue.main.async { usageRequested?.resume() }
            networkGate.wait()
        }
        return networkAnswers.removeFirst()
    }
    let interrupted = watcher.checkAll()
    await withCheckedContinuation { usageRequested = $0 }
    watcher.setOnline(false)
    watcher.setOnline(true)
    networkGate.signal()
    await interrupted.value
    expect(Stub.requests.count == 2 && watcher.roster.statuses[someone]?.problem == nil,
           "a check that failed while the network went and came back runs again at once")

    // macOS says the network went while a check is under way, which then
    // fails for want of it, and the network stays away.
    let awayGate = DispatchSemaphore(value: 0)
    var awayRequested: CheckedContinuation<Void, Never>?
    var awayAnswers = [(0, ""), (200, weekly)]
    Stub.requests = []
    Stub.answer = { _, _ in
        if awayAnswers.count == 2 {
            DispatchQueue.main.async { awayRequested?.resume() }
            awayGate.wait()
        }
        return awayAnswers.removeFirst()
    }
    let stranded = watcher.checkAll()
    await withCheckedContinuation { awayRequested = $0 }
    watcher.setOnline(false)
    awayGate.signal()
    await stranded.value
    expect(Stub.requests.count == 1, "a check that failed while the network went does not run again while it is away")
    await watcher.setOnline(true)?.value
    expect(Stub.requests.count == 2 && watcher.roster.statuses[someone]?.problem == nil,
           "it runs again once the network is back")

    respond([usageHost: [(200, weekly), (200, weekly)]])
    let first = watcher.checkAll(), second = watcher.checkAll()
    await first.value
    await second.value
    expect(Stub.requests.count == 1, "an account being checked is not checked again at the same time")

    respond([tokenHost: [(200, newTokens)], usageHost: [(401, "expired"), (200, weekly)]])
    await watcher.checkAll().value
    expect(try watcherStore.load().first?.tokens == rotated, "tokens a check refreshes are saved at once")

    let expected = account("other@example.com", id: "other/organization")
    let weeklyWithGrant = #"""
        {"seven_day": {"utilization": 50, "resets_at": "2026-11-17T14:00:00Z"},
         "cedar_ember": {"eligible": true, "grants": [
           {"label": "Weekly reset", "resets_left": 1, "ends_at": "2026-12-01T00:00:00Z"}]}}
        """#
    respond([tokenHost: [(200, signedIn)], usageHost: [(200, weeklyWithGrant)]])
    await watcher.startSignIn(expecting: expected)?.value
    expect(watcher.signInNote?.contains("not other@example.com") == true
           && watcher.roster.accounts.map(\.id) == [someone],
           "signing in to another account than the one asked for says so: \(watcher.signInNote ?? "")")
    _ = NSApplication.shared
    NSApp.setActivationPolicy(.prohibited)
    let menu = NSMenu()
    var quitChosen = false
    fill(menu, from: watcher, now: now, in: utc, locale: Locale(identifier: "en_US")) { quitChosen = true }
    var titles = menu.items.map(\.title)
    expect(titles.first == "someone@example.com: 50% used, resets in 4d 2h, 1 banked reset"
           && menu.items[0].image != nil,
           "each account has a line with its picture: \(titles)")
    expect(titles.contains { $0.hasPrefix("Signed in to someone@example.com") },
           "the menu gives the sign-in's note: \(titles)")
    expect(titles.suffix(4) == ["Add Account…", "Check Now", "", "Quit"],
           "the menu ends with its actions: \(titles)")
    let details = menu.items[0].submenu?.items.map(\.title) ?? []
    expect(details.first == "Example" && details.contains { $0.hasPrefix("Resets ") }
           && details.contains { $0.hasPrefix("Last checked successfully Nov 13") }
           && details.contains { $0.hasPrefix("Weekly reset: 1 left, use by Dec 1") }
           && details.suffix(2) == ["Sign In Again…", "Remove"],
           "an account's submenu gives its details and actions: \(details)")
    _ = choose("Quit", in: menu)
    expect(quitChosen, "choosing Quit quits")
    let normal = menuBarPicture(of: watcher.roster, loadProblem: nil, saveProblem: nil, now: now, in: utc)
    expect(normal.toolTip == titles.first && normal.image.size.width == glyphSize,
           "the menu bar shows each account's circle, with its line as the tooltip: \(normal.toolTip)")
    let unsaved = menuBarPicture(of: watcher.roster, loadProblem: nil, saveProblem: "cannot write", now: now, in: utc)
    expect(unsaved.toolTip == "cannot write\n" + (titles.first ?? "")
           && unsaved.image.size.width == 2 * glyphSize + glyphGap,
           "accounts that cannot be saved add a warning after the circles: \(unsaved.toolTip)")
    fill(menu, from: watcher, now: time("2026-11-20T00:00:00Z"), in: utc, locale: Locale(identifier: "en_US")) {}
    let stale = menu.items[0]
    expect(stale.title.contains("from before the last reset")
           && stale.submenu?.items.contains { $0.title.hasPrefix("Reset Nov 17") } == true,
           "a reset that has passed since the last good check is shown with its date: \(stale.title)")
    watcher.dismissSignInNote("an older note")
    expect(watcher.signInNote != nil, "a note the menu did not show stays")
    watcher.dismissSignInNote(watcher.signInNote)
    expect(watcher.signInNote == nil, "the note the menu showed can be dismissed")
    expect(Stub.requests.compactMap { $0.body["code"] } == ["watcher-code"], "the browser's code is used")
    respond([usageHost: [(200, weekly)]])
    if choose("Check Now", in: menu) {
        await withCheckedContinuation { checkEnded = $0 }
        expect(Stub.requests.count == 1, "choosing Check Now checks the accounts")
    }
    browserComesBack = false
    if let accountActions = menu.items.first?.submenu, choose("Sign In Again…", in: accountActions) {
        await withCheckedContinuation { pageOpened = $0 }
        expect(watcher.signingIn && lastPage.map { query($0)["login_hint"] } == "someone@example.com",
               "Sign In Again… signs in again, asking for the account")
    }
    watcher.cancelSignIn()
    browserComesBack = true

    // A refresh held open by the fake token endpoint until the test lets it
    // answer.
    let gate = DispatchSemaphore(value: 0)
    var tokenRequested: CheckedContinuation<Void, Never>?
    var usageAnswers = [(401, "expired"), (200, weekly)]
    Stub.requests = []
    Stub.answer = { request, _ in
        if request.url!.host! == tokenHost {
            DispatchQueue.main.async { tokenRequested?.resume() }
            gate.wait()
            return (200, #"{"access_token": "held-access", "refresh_token": "held-refresh", "expires_in": 3600}"#)
        }
        return usageAnswers.isEmpty ? (500, "unexpected") : usageAnswers.removeFirst()
    }
    let held = watcher.checkAll()
    await withCheckedContinuation { tokenRequested = $0 }
    var savedWhenQuitting: String?
    watcher.whenRefreshesEnd { savedWhenQuitting = (try? watcherStore.load())?.first?.tokens.refresh }
    expect(watcher.refreshing == 1 && savedWhenQuitting == nil, "quitting waits while a refresh is under way")
    gate.signal()
    await held.value
    expect(savedWhenQuitting == "held-refresh" && watcher.refreshing == 0,
           "quitting goes ahead once the refresh's tokens are saved: \(savedWhenQuitting ?? "nothing")")

    var quit = false
    watcher.whenRefreshesEnd { quit = true }
    expect(quit && watcher.refreshing == 0, "with no refresh under way, quitting goes ahead at once")

    respond([tokenHost: [(400, #"{"error": "invalid_grant"}"#)], usageHost: [(401, "revoked")]])
    await watcher.checkAll().value
    expect(watcher.roster.statuses[someone]?.signInNeeded == true, "a refused refresh token needs signing in")
    respond([:])
    await watcher.checkAll().value
    expect(Stub.requests.isEmpty, "an account that needs signing in again is not checked")

    browserComesBack = false
    let abandoned = watcher.startSignIn()
    expect(watcher.signingIn, "a sign-in waits for the browser")
    fill(menu, from: watcher, now: now, in: utc, locale: Locale(identifier: "en_US")) {}
    titles = menu.items.map(\.title)
    expect(titles.contains("Waiting for the browser to sign in…") && !titles.contains("Add Account…"),
           "while a sign-in waits, the menu says so: \(titles)")
    if !choose("Cancel Sign-In", in: menu) {
        watcher.cancelSignIn()
    }
    await abandoned?.value
    expect(!watcher.signingIn && watcher.signInNote == nil, "a cancelled sign-in leaves nothing behind")

    let replaced = watcher.startSignIn()
    let replacement = watcher.startSignIn()
    await replaced?.value
    expect(watcher.signingIn && watcher.signInNote == nil, "a new sign-in replaces one still waiting")
    watcher.cancelSignIn()
    await replacement?.value

    browserOpens = false
    await watcher.startSignIn()?.value
    expect(watcher.signInNote?.contains("did not open") == true && !watcher.signingIn,
           "a sign-in page that cannot be shown is reported: \(watcher.signInNote ?? "")")
    browserOpens = true

    fill(menu, from: watcher, now: now, in: utc, locale: Locale(identifier: "en_US")) {}
    if let actions = menu.items.first?.submenu {
        _ = choose("Remove", in: actions)
    }
    let afterRemoval = try watcherStore.load()
    expect(watcher.roster.accounts.isEmpty && afterRemoval == [], "removing an account forgets it")

    try watcherItem.write(Data("damaged".utf8))
    watcher.load()
    pagesOpened = 0
    expect(watcher.loadProblem?.contains("not what quotabar expects") == true && watcher.startSignIn() == nil && pagesOpened == 0,
           "accounts that cannot be read cannot be replaced by signing in")
    fill(menu, from: watcher, now: now, in: utc, locale: Locale(identifier: "en_US")) {}
    titles = menu.items.map(\.title)
    expect(titles.contains { $0.contains("not what quotabar expects") } && titles.contains("Read the Keychain Again")
           && !titles.contains("Add Account…"), "the menu offers to read the keychain again: \(titles)")
    try watcherItem.delete()
    let picture = menuBarPicture(of: watcher.roster, loadProblem: watcher.loadProblem, saveProblem: nil,
                                 now: now, in: utc)
    expect(picture.toolTip.contains("not what quotabar expects") && picture.image.size.width == glyphSize,
           "accounts that cannot be read show as one exclamation mark, with the problem as the tooltip")
    _ = choose("Read the Keychain Again", in: menu)
    expect(watcher.loadProblem == nil, "reading the keychain again clears the problem")

    /// Accounts kept in memory, failing to save while `refusing` is set.
    final class Refusing: AccountKeeper {
        var accounts: [Account]
        var refusing = true
        var unreadable = false
        init(_ accounts: [Account]) { self.accounts = accounts }
        func load() throws -> [Account] {
            guard !unreadable else {
                throw Problem(description: "cannot read")
            }
            return accounts
        }
        func save(_ accounts: [Account]) throws {
            guard !refusing else {
                throw Problem(description: "cannot write")
            }
            self.accounts = accounts
        }
    }
    let refusing = Refusing([expired])
    var unsavedChanges = 0
    let unsavedWatcher = Watcher(service: service, store: refusing, open: { _ in false },
                                 changed: { unsavedChanges += 1 })
    unsavedWatcher.load()
    unsavedWatcher.setOnline(true)
    respond([tokenHost: [(200, newTokens)], usageHost: [(200, weekly)]])
    await unsavedWatcher.checkAll().value
    expect(unsavedWatcher.saveProblem == "cannot write" && refusing.accounts.first?.tokens == expired.tokens,
           "a save that fails is reported")
    refusing.refusing = false
    unsavedWatcher.setOnline(false)
    respond([:])
    unsavedChanges = 0
    await unsavedWatcher.checkAll().value
    expect(unsavedChanges == 1, "a save that succeeds on retry redraws the menu bar")
    expect(unsavedWatcher.saveProblem == nil && refusing.accounts.first?.tokens == rotated && Stub.requests.isEmpty,
           "the next check saves the accounts again, even while offline: \(String(describing: refusing.accounts.first?.tokens))")
    respond([usageHost: [(200, weekly)]])
    await unsavedWatcher.setOnline(true)?.value
    refusing.refusing = true
    respond([tokenHost: [(200, newTokens)], usageHost: [(401, "expired"), (200, weekly)]])
    await unsavedWatcher.checkAll().value
    refusing.refusing = false
    unsavedWatcher.close()
    expect(unsavedWatcher.saveProblem == nil, "quitting saves the accounts again")
    let blocked = Refusing([expired])
    let blockedWatcher = Watcher(service: service, store: blocked, open: { _ in false }, changed: {})
    blockedWatcher.load()
    blockedWatcher.setOnline(true)
    respond([tokenHost: [(200, newTokens)], usageHost: [(200, weekly)]])
    await blockedWatcher.checkAll().value
    blocked.unreadable = true
    blockedWatcher.load()
    blocked.refusing = false
    await blockedWatcher.checkAll().value
    blockedWatcher.close()
    expect(blocked.accounts == [expired] && blockedWatcher.saveProblem == nil,
           "while the accounts cannot be read, nothing is saved over them: \(blocked.accounts.count) accounts")
    let waiting = Watcher(service: service, store: refusing, open: { _ in true }, changed: {})
    waiting.load()
    let interruptedSignIn = waiting.startSignIn()
    refusing.unreadable = true
    waiting.load()
    expect(!waiting.signingIn, "accounts that cannot be read end a sign-in under way")
    waiting.cancelSignIn()
    await interruptedSignIn?.value
    unsavedWatcher.load()
    expect(unsavedWatcher.loadProblem == "cannot read" && unsavedWatcher.roster.accounts.isEmpty,
           "accounts that cannot be read are forgotten")

    try watcherStore.save([current])
    let closing = Watcher(service: service, store: watcherStore, open: { _ in false }, changed: {})
    closing.load()
    closing.setOnline(true)
    closing.close()
    respond([usageHost: [(200, weekly)]])
    await closing.checkAll().value
    expect(closing.roster.accounts.count == 1 && Stub.requests.isEmpty, "once quitting, no new checks start")
} catch {
    expect(false, "the watcher works: \(error)")
}
try? watcherItem.delete()

// MARK: - Running only once

let lockPath = FileManager.default.temporaryDirectory.appendingPathComponent("quotabar-\(UUID()).lock").path
expect(try takeLock(at: lockPath), "the first to ask takes the lock")
expect(try !takeLock(at: lockPath), "the lock cannot be taken twice")
await expectThrows("a lock that cannot be created is reported") { try takeLock(at: "/nonexistent/quotabar.lock") }
try FileManager.default.removeItem(atPath: lockPath)

// MARK: - The keychain

let item = KeychainItem(service: temporaryService(), account: "accounts")
let store = AccountStore(item: item)
do {
    expect(try item.read() == nil, "an item that does not exist reads as nil")
    expect(try store.load() == [], "no item means no accounts")
    try store.save([sunday, tuesday])
    expect(try store.load() == [sunday, tuesday], "accounts are kept")
    try store.save([newAccount])
    expect(try store.load() == [newAccount], "saving again replaces the accounts")
    try item.write(Data(#"""
        [{"id": "i", "email": "e", "organization": "o",
          "tokens": {"access": "a", "refresh": "r", "expires": 0}}]
        """#.utf8))
    expect(try store.load().first?.resets == nil, "an account saved without a reset time is read")
    try item.write(Data(#"""
        [{"id": "i", "email": "e", "organization": "o", "colour": "blue",
          "tokens": {"access": "a", "refresh": "r", "expires": 0}}]
        """#.utf8))
    await expectThrows("an account with a field quotabar does not know is refused") { try store.load() }
        where: { "\($0)" == "the saved accounts have a field quotabar does not know: [0].colour" }
    try item.write(Data(#"""
        [{"id": "i", "email": "e", "organization": "o",
          "tokens": {"access": "a", "refresh": "r", "expires": 0, "scope": "x"}}]
        """#.utf8))
    await expectThrows("tokens with a field quotabar does not know are refused") { try store.load() }
        where: { "\($0)" == "the saved accounts have a field quotabar does not know: [0].tokens.scope" }
    try store.save([sunday, sunday])
    await expectThrows("the same account saved twice is refused") { try store.load() }
        where: { "\($0)".hasSuffix("lists the account sunday twice") }
    try item.write(Data("damaged".utf8))
    await expectThrows("a damaged item is reported") { try store.load() } where: {
        "\($0)".hasSuffix("is not what quotabar expects: it is not JSON")
    }
    try item.delete()
    expect(try item.read() == nil, "a deleted item is gone")
    try item.delete()
} catch {
    expect(false, "the keychain works: \(error)")
    try? item.delete()
}

// A leftover from a run whose process has gone is deleted; one from a run
// still going, this one, is left alone.
let gone = "quotabar-tests-2147483647-\(UUID().uuidString)", mine = temporaryService()
for service in [gone, mine] {
    expect(try security(["add-generic-password", "-s", service, "-a", "accounts", "-w", "x"]) == 0, "\(service) is added")
}
try deleteLeftovers()
expect(try security(["find-generic-password", "-s", gone]) != 0, "a leftover from a run that has gone is deleted")
expect(try security(["find-generic-password", "-s", mine]) == 0, "an item of a run still going is left alone")
expect(try security(["delete-generic-password", "-s", mine]) == 0, "the test item is deleted")

if failures > 0 {
    fputs("\(failures) failed\n", stderr)
    exit(1)
}
print("all passed")
