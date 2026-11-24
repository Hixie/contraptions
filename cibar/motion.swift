import CoreGraphics

// The paths of balls moving on their own across the runners board, rather
// than in a heap: to the runner that takes a job, and off the board when a
// job ends. Each is worked out ahead of time, as the heaps are, so that Core
// Animation can move the balls along them while cibar draws nothing; and each
// is the path of a ball moved only by forces, from where it is, moving as it
// moves, so that no ball ever changes speed or direction all at once.

enum Flight {
    /// How many times a second a path says where its ball is, how many steps
    /// it takes between, and how hard gravity pulls, in points a second each
    /// second, down the board.
    static let rate = Heap.rate
    static let steps = 8
    static let gravity: CGFloat = 1800

    /// Where a ball is, and how far it moves a second.
    struct State: Equatable {
        var position: CGPoint
        var velocity: CGVector = .zero
    }

    /// Where a ball on `path`, `rate` samples a second, is `time` seconds
    /// along it, between samples as much as at them, and how it is moving
    /// then; at rest at its end once it is past it.
    static func state(on path: [CGPoint], at time: Double) -> State {
        let at = max(0, time * rate)
        let i = min(Int(at), path.count - 1), j = min(i + 1, path.count - 1)
        let part = CGFloat(min(1, at - Double(i)))
        return State(position: CGPoint(x: path[i].x + (path[j].x - path[i].x) * part,
                                       y: path[i].y + (path[j].y - path[i].y) * part),
                     velocity: CGVector(dx: (path[j].x - path[i].x) * CGFloat(rate),
                                        dy: (path[j].y - path[i].y) * CGFloat(rate)))
    }

    /// The path, `rate` samples a second, from `state`, of a ball pushed by
    /// `force`, given where it is and how fast it is moving and how long it
    /// has been moving, in points a second each second, and kept in bounds
    /// by `bounds`, which may move it and change its speed as a wall or
    /// floor would, until `done` says it is done, or for `longest` seconds.
    static func path(from state: State, longest: Double, force: (State, Double) -> CGVector,
                     bounds: (inout State) -> Void = { _ in }, done: (State, Double) -> Bool) -> [CGPoint] {
        let dt = 1 / (rate * Double(steps))
        var state = state, points = [state.position]
        for step in 1...Int(longest * rate * Double(steps)) {
            let time = Double(step) * dt
            let push = force(state, time)
            state.velocity.dx += push.dx * dt
            state.velocity.dy += push.dy * dt
            state.position.x += state.velocity.dx * dt
            state.position.y += state.velocity.dy * dt
            bounds(&state)
            if step % steps == 0 {
                points.append(state.position)
                if done(state, time) {
                    break
                }
            }
        }
        return points
    }

    /// The path of a ball, from `state`, drawn to `target`, as a job's is to
    /// the runner that takes it: the pull of the place comes on over a
    /// quarter of a second, while gravity goes on pulling down a ball that is
    /// falling, unless it is `supported`, as one at rest in a heap or a slot
    /// is, until the pull holds it up, so that a ball dropping into its queue
    /// as its job starts goes on falling a moment, then curves round to the
    /// runner; it comes to rest there in about a second, the last of it too
    /// slow to see, and the path ends there. Pulled as a damped spring pulls,
    /// a ball at rest goes straight there, but one already rushing towards
    /// the place may overshoot it a little, and come back.
    static func steer(from state: State, to target: CGPoint, supported: Bool) -> [CGPoint] {
        let stiffness: CGFloat = 9, weight = supported ? 0 : gravity
        var points = path(from: state, longest: 3, force: { state, time in
            let pull = CGFloat(min(1, time / 0.25))
            let toward = CGVector(dx: stiffness * stiffness * (target.x - state.position.x) - 2 * stiffness * state.velocity.dx,
                                  dy: stiffness * stiffness * (target.y - state.position.y) - 2 * stiffness * state.velocity.dy)
            return CGVector(dx: pull * toward.dx, dy: weight + pull * (toward.dy - weight))
        }, done: { state, _ in
            hypot(state.position.x - target.x, state.position.y - target.y) < 0.25
                && hypot(state.velocity.dx, state.velocity.dy) < 10
        })
        points[points.count - 1] = target
        return points
    }

    /// The board a ball leaves, for `leaving`: how wide it is, where its
    /// bottom is, and the line along its bottom, from `line.lowerBound` to
    /// `line.upperBound`, on which a ball can land.
    struct Board {
        var width, floor: CGFloat
        var line: ClosedRange<CGFloat>
    }

    /// How a job's ball leaves the board, from `state`, `diameter` across,
    /// once its job has ended `outcome`: for the first `pause` seconds, while
    /// it swells, it slows to a stop where it is, as if held there; then it
    /// goes. One that passed is thrown up and to the right, off the board.
    /// One that failed is thrown up and to the right harder, hard enough to
    /// reach the right side while still in the air, bounces off it, falls to
    /// the line along the bottom, bounces there, each time lower, and slides
    /// off to the left, as on a floor both bouncy and slippery, until, past
    /// the line's left end, nothing holds it up, and it falls. And one that
    /// neither passed nor failed rises like a puff of smoke, slowly at first,
    /// then faster, straight up, off the top.
    static func leaving(from state: State, diameter: CGFloat, outcome: Outcome, pause: Double,
                        board: Board) -> [CGPoint] {
        // The side it bounces off is the board's; one that comes down past
        // the line's right end, between it and the side, falls there.
        let r = diameter / 2, start = state.position, wall = board.width - r
        // How fast it is thrown, in the tenth of a second after the pause
        // that it is pushed: one that passed so as to leave the right side
        // of the board a little lower than it was, in less than a second.
        // Pushed steadily for `push` seconds, a ball goes as far as it would
        // have going at its final speed from halfway through the push.
        let push = 0.1, lag = CGFloat(push / 2)
        let away = board.width + diameter - start.x, flying = min(0.9, max(0.5, away / 900))
        let thrown: CGVector = switch outcome {
        case .passed: CGVector(dx: away / (flying - lag), dy: (60 - gravity * flying * flying / 2) / (flying - lag))
        case .failed: CGVector(dx: max(900, (wall - start.x) / 0.45), dy: -420)
        case .neither: .zero
        }
        // The push stops once the ball hits the side, as a throw would; and
        // the path is long enough for one that failed to slide the whole
        // width of the board at the slowest the side sends it back, forty
        // points a second, as one thrown from right beside it is.
        var hit = false
        return path(from: state, longest: pause + 4 + Double(board.width) / 40, force: { state, time in
            if time < pause {
                // Drag, with the ball held up, slows it to a stop.
                return CGVector(dx: -12 * state.velocity.dx, dy: -12 * state.velocity.dy)
            }
            switch outcome {
            case .neither:
                // Buoyancy lifts it, and the air holds it back, so that it
                // rises ever faster to a steady speed, and goes on sideways
                // only a little way.
                return CGVector(dx: -3 * state.velocity.dx, dy: -700 - 3 * state.velocity.dy)
            case .passed, .failed:
                let pushing = time < pause + push && !hit
                return CGVector(dx: pushing ? thrown.dx / CGFloat(push) : 0,
                                dy: gravity + (pushing ? thrown.dy / CGFloat(push) : 0))
            }
        }, bounds: { state in
            guard outcome == .failed else { return }
            if state.position.x >= wall && state.velocity.dx > 0 {
                state.position.x = wall
                // It bounces back at a fifth of the speed it hit at.
                state.velocity.dx *= -0.2
                hit = true
            }
            if state.position.y >= board.floor - r && board.line.contains(state.position.x)
                && state.position.y - state.velocity.dy / CGFloat(rate * Double(steps)) <= board.floor - r {
                state.position.y = board.floor - r
                // It bounces back up at half the speed it came down at,
                // until it comes down too slowly to bounce at all.
                state.velocity.dy = state.velocity.dy > 90 ? -state.velocity.dy * 0.5 : 0
            }
        }, done: { state, time in
            time > pause && (state.position.x > board.width + diameter || state.position.x < -diameter
                             || state.position.y < -diameter * 2 || state.position.y > board.floor + 400)
        })
    }
}
