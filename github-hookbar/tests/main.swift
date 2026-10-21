import Foundation

// Tests for the parts of github-hookbar that do not need the menu bar. Run
// them with `make test`.

var failures = 0

func expect(_ condition: Bool, _ message: String, line: Int = #line) {
    if !condition {
        failures += 1
        fputs("FAIL line \(line): \(message)\n", stderr)
    }
}

// MARK: - When GitHub made a delivery

// A delivery ID from GitHub, and the time Python's uuid module reads from it.
let made = creationTime(ofDelivery: "109f6680-cd6c-11f1-9e6d-f689524137cb")
expect(made.map { abs($0.timeIntervalSince1970 - 1_792_599_944.424) < 0.001 } == true,
       "a GitHub delivery ID gives the time it was made: \(String(describing: made))")

// Differing only in the version digit, so the same bytes cannot be read as a
// time.
expect(creationTime(ofDelivery: "109f6680-cd6c-41f1-9e6d-f689524137cb") == nil,
       "a random (version 4) UUID gives no time")
expect(creationTime(ofDelivery: "109f6680-cd6c-11f1-1e6d-f689524137cb") == nil,
       "a version 1 UUID of a variant other than RFC 4122's gives no time")
expect(creationTime(ofDelivery: "not a uuid") == nil, "text that is not a UUID gives no time")

// The smallest version 1 UUID is the start of the Gregorian calendar.
let epoch = Date(timeIntervalSince1970: -12_219_292_800)
let zero = creationTime(ofDelivery: "00000000-0000-1000-8000-000000000000")
expect(zero == epoch, "the zero time is 15 October 1582: \(String(describing: zero))")

// Each field of the time lands in its place: a time_low of 1 is one tick,
// a time_mid of 1 is 2^32 ticks, and a time_hi of 1 is 2^48 ticks.
for (id, ticks) in [("00000001-0000-1000-8000-000000000000", 1.0),
                    ("00000000-0001-1000-8000-000000000000", 4_294_967_296.0),
                    ("00000000-0000-1001-8000-000000000000", 281_474_976_710_656.0)] {
    let time = creationTime(ofDelivery: id)
    expect(time.map { abs($0.timeIntervalSince(epoch) - ticks / 10_000_000) < 1e-6 } == true,
           "\(id) is \(ticks) ticks after the zero time: \(String(describing: time))")
}

// MARK: - Which deliveries are late

expect(!isLate(0.3), "a delivery that took 0.3 seconds is not late")
expect(!isLate(4.9), "a delivery that took 4.9 seconds is not late")
expect(!isLate(5), "a delivery that took exactly 5 seconds is not late")
expect(isLate(5.1), "a delivery that took 5.1 seconds is late")
expect(isLate(955), "a delivery that took 955 seconds is late")

// MARK: - How the indicator dims

/// Delivers to the dimming at each time in turn, and checks what the last
/// delivery returns.
func expectDimming(deliveries: [TimeInterval], _ expected: [(Float, TimeInterval)],
                   _ message: String, line: Int = #line) {
    var dimming = Dimming()
    var steps: [(opacity: Float, time: TimeInterval)] = []
    for time in deliveries {
        steps = dimming.deliver(at: time)
    }
    expect(steps.count == expected.count && zip(steps, expected).allSatisfy {
        abs($0.opacity - $1.0) < 1e-5 && abs($0.time - $1.1) < 1e-6
    }, "\(message): \(steps)", line: line)
}

expectDimming(deliveries: [10], [(1, 0), (0.6, 0.25), (0.6, 0.25), (1, 0.5)],
              "at full opacity, it fades down and straight back up")
expectDimming(deliveries: [10, 11], [(1, 0), (0.6, 0.25), (0.6, 0.25), (1, 0.5)],
              "once it is back to full opacity, it starts afresh")
expectDimming(deliveries: [0, 0.1], [(0.84, 0), (0.6, 0.15), (0.6, 0.25), (1, 0.5)],
              "fading down, it carries on and resets the clock")
expectDimming(deliveries: [0, 0.1, 0.3], [(0.6, 0), (0.6, 0), (0.6, 0.25), (1, 0.5)],
              "dimmed, it stays dimmed and resets the clock")
expectDimming(deliveries: [0, 0.4], [(0.84, 0), (0.6, 0.15), (0.6, 0.15), (1, 0.4)],
              "fading up, it fades down again without resetting the clock")
expectDimming(deliveries: [0, 0.4, 0.5], [(0.68, 0), (0.6, 0.05), (0.6, 0.25), (1, 0.5)],
              "fading down after turning round, it resets the clock")

if failures > 0 {
    fputs("\(failures) failed\n", stderr)
    exit(1)
}
print("all passed")
