import Foundation

/// The IPv4 address to ping.
let host = "8.8.8.8"

/// Pause between the end of one ping and the start of the next.
let sampleGap = DispatchTimeInterval.seconds(2)

/// How long a ping waits for a reply before the sample counts as lost.
let pingDeadline = DispatchTimeInterval.seconds(2)

/// Replies faster than this render green.
let goodThresholdMs = 100.0

/// Replies faster than this (but at least goodThresholdMs) render orange;
/// slower replies and lost pings render red.
let poorThresholdMs = 300.0

let gridSide = 3
let historyLength = gridSide * gridSide

/// How long the pulse animation on a newly landed poor or lost sample runs.
let pulseDuration: CFTimeInterval = 0.8

/// The screen-edge warning appears once more than this many grid slots are red.
let screenWarningThreshold = 4

let preferencesDomain = "local.pingbar"
let screenWarningEnabledPreferenceKey = "screenWarningEnabled"
