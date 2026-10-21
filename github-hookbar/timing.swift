import Foundation

/// Whether a delivery that arrived this many seconds after GitHub made it
/// arrived late, which turns the indicator orange.
func isLate(_ age: TimeInterval) -> Bool {
    age > 5
}

/// When GitHub made a delivery, from its X-GitHub-Delivery header, or nothing
/// when the header is not a time-based (version 1) RFC 4122 UUID. Such a UUID
/// holds a count of 100-nanosecond intervals since the start of the Gregorian
/// calendar, 15 October 1582, split across its first eight bytes.
func creationTime(ofDelivery id: String) -> Date? {
    guard let bytes = UUID(uuidString: id)?.uuid, bytes.6 >> 4 == 1, bytes.8 >> 6 == 2 else {
        return nil
    }
    let parts: [(UInt8, Int)] = [(bytes.6 & 0x0f, 56), (bytes.7, 48), (bytes.4, 40), (bytes.5, 32),
                                 (bytes.0, 24), (bytes.1, 16), (bytes.2, 8), (bytes.3, 0)]
    let ticks = parts.reduce(UInt64(0)) { $0 | UInt64($1.0) << $1.1 }
    let secondsFromGregorianStartToUnixEpoch: TimeInterval = 12_219_292_800
    return Date(timeIntervalSince1970: TimeInterval(ticks) / 10_000_000
                - secondsFromGregorianStartToUnixEpoch)
}

/// How long the indicator takes to fade between full and dimmed opacity.
let fadeDuration: TimeInterval = 0.25

/// The indicator's opacity when dimmed.
let dimmedOpacity: Float = 0.6

/// The indicator's dimming, which follows a clock that deliveries start and
/// reset. A delivery while the indicator is at full opacity starts the clock
/// and fades the indicator down to the dimmed opacity, which takes
/// `fadeDuration`. Once dimmed, it stays dimmed until the clock reaches
/// `fadeDuration`, and then takes `fadeDuration` to fade back up. A delivery
/// while it fades down or stays dimmed resets the clock. A delivery while it
/// fades back up turns it round to fade down again, and leaves the clock as
/// it is.
struct Dimming {
    /// The opacities the indicator moves between in straight lines, each with
    /// the time it reaches that opacity: where it was at the last delivery,
    /// dimmed, still dimmed, and full again.
    private var steps: [(opacity: Float, time: TimeInterval)] = []
    private var clockStart: TimeInterval = 0

    /// The indicator's opacity at a time no earlier than the last delivery.
    func opacity(at time: TimeInterval) -> Float {
        for (from, to) in zip(steps, steps.dropFirst()) where time < to.time {
            guard time > from.time else { return from.opacity }
            return from.opacity + (to.opacity - from.opacity)
                * Float((time - from.time) / (to.time - from.time))
        }
        return 1
    }

    /// Takes in a delivery at a time no earlier than the last one, and
    /// returns the opacities the indicator moves between from then on, each
    /// with the time it reaches that opacity, in seconds after the delivery.
    mutating func deliver(at now: TimeInterval) -> [(opacity: Float, time: TimeInterval)] {
        let opacity = opacity(at: now)
        if steps.isEmpty || now < steps[2].time || now >= steps[3].time {
            clockStart = now
        }
        let dimmed = now + fadeDuration * TimeInterval((opacity - dimmedOpacity) / (1 - dimmedOpacity))
        let fadeBack = max(dimmed, clockStart + fadeDuration)
        steps = [(opacity, now), (dimmedOpacity, dimmed), (dimmedOpacity, fadeBack),
                 (1, fadeBack + fadeDuration)]
        return steps.map { ($0.opacity, $0.time - now) }
    }
}
