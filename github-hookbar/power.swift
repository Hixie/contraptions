import Foundation
import IOKit.ps

// Noticing when the computer switches between battery and wall power.

/// Where the computer draws its power from.
enum PowerSource: String {
    case battery
    case wall

    /// Where the computer draws its power from now. IOKit says that the
    /// time left is unlimited when the computer draws power from a wall
    /// outlet, which includes a computer with no battery.
    static var current: PowerSource {
        IOPSGetTimeRemainingEstimate() == kIOPSTimeRemainingUnlimited ? .wall : .battery
    }
}

/// Calls `onChange` on the main thread whenever the computer switches
/// between battery and wall power, and when it first notes the power
/// source.
final class PowerSourceMonitor {
    var onChange: ((PowerSource) -> Void)?
    /// The power source the monitor last noted, if it has noted one.
    private(set) var source: PowerSource?
    private var notification: CFRunLoopSource?

    deinit {
        if let notification {
            CFRunLoopSourceInvalidate(notification)
        }
    }

    /// Asks IOKit to say when the computer switches between battery and wall
    /// power, until the monitor goes away.
    func start() throws {
        let monitor = Unmanaged.passUnretained(self).toOpaque()
        guard let notification = IOPSCreateLimitedPowerNotification({ monitor in
            guard let monitor else { return }
            Unmanaged<PowerSourceMonitor>.fromOpaque(monitor).takeUnretainedValue().check()
        }, monitor)?.takeRetainedValue() else {
            throw Problem(description: "IOKit cannot say when the power source changes")
        }
        // The common modes include the one the run loop is in while a menu is
        // open, so a switch is noticed then too.
        CFRunLoopAddSource(CFRunLoopGetMain(), notification, .commonModes)
        self.notification = notification
        check()
    }

    /// Notes where the computer draws its power from now, in case IOKit said
    /// nothing, for example while the computer slept.
    func check() {
        note(.current)
    }

    /// Notes a power source, and calls `onChange` when it differs from the
    /// one noted before, or when it is the first.
    func note(_ source: PowerSource) {
        guard source != self.source else { return }
        self.source = source
        onChange?(source)
    }
}

/// Runs a job for one value at a time. A value that comes while a job runs
/// waits for it to finish, in place of any value already waiting, so the
/// jobs finish in order and the last job is for the latest value.
final class LatestRunner<Value> {
    /// Runs the job for a value, and calls its second argument once the job
    /// has finished.
    private let job: (Value, @escaping () -> Void) -> Void
    private var running = false
    private var waiting: Value?

    init(_ job: @escaping (Value, @escaping () -> Void) -> Void) {
        self.job = job
    }

    func run(_ value: Value) {
        guard !running else {
            waiting = value
            return
        }
        running = true
        job(value) { [self] in
            running = false
            if let next = waiting {
                waiting = nil
                run(next)
            }
        }
    }
}
