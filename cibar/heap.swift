import CoreGraphics

// A heap of balls under gravity, in a box open at the top, worked out ahead
// of time: where each ball goes, from where it is, moving as it moves, until
// they all come to rest, so that Core Animation can move them along those
// paths while cibar draws nothing.
//
// The box is the queue's space, `width` by `height`, with its top left as the
// origin and down the way y grows. Its right side and its floor go on for
// ever; its left side is only as tall as the box; and from its top left
// corner a slope at forty-five degrees leads up and away to the left, down
// which balls dropped far off to the left roll into the box.

enum Heap {
    /// How many times a second a path says where its ball is, how long the
    /// simulation runs at most, and how fast, in points a second, a ball
    /// may go.
    static let rate: Double = 60
    static let longest: Double = 5
    static let fastest: Double = 550
    static let growing: Double = 0.5

    struct Ball {
        var id: Int64
        /// Where its centre is, with the box's top left as the origin.
        var position: CGPoint
        /// How far it moves a second.
        var velocity: CGVector = .zero
    }

    /// How big the balls are so that `count` of them fit in `area`, heaped
    /// as loosely as poured balls heap, but no bigger than `largest`: the
    /// biggest, in half points, for which rows of as many as fit across,
    /// with room to spare, fit from bottom to top.
    static func diameter(for count: Int, in area: CGSize, largest: CGFloat) -> CGFloat {
        var d = max(1, largest)
        while d > 1 {
            let across = max(1, Int(area.width / d))
            let rows = (count + across - 1) / across
            if CGFloat(rows) * d * 1.15 <= area.height {
                break
            }
            d = max(1, d - 0.5)
        }
        return d
    }

    /// How big `balls` are, and the path of each, as `paths` says, when
    /// heaped in a box `size` big: `diameter` across, or, if a heap of them
    /// that big comes to rest with any in the box sticking out of its top, as
    /// poured heaps lie less flat than they might, the biggest, in half
    /// points, that comes to rest inside; growing, as `paths` says, from
    /// `grownFrom`.
    static func fit(_ balls: [Ball], in size: CGSize, diameter: CGFloat, grownFrom: CGFloat? = nil)
        -> (diameter: CGFloat, paths: [Int64: [CGPoint]]) {
        var d = diameter
        while true {
            let paths = paths(of: balls, in: size, diameter: d, grownFrom: grownFrom)
            if d <= 1 || !paths.values.contains(where: { $0[$0.count - 1].x >= 0 && $0[$0.count - 1].y < d / 2 - 0.5 }) {
                return (d, paths)
            }
            d = max(1, d - 0.5)
        }
    }

    /// The path of each of `balls`, `diameter` across, in a box `size` big,
    /// as the file's opening says, `rate` samples a second, the first where it is and
    /// the last where it comes to rest, by its ball's ID. Balls that were
    /// `grownFrom` across grow or shrink to `diameter` over the first
    /// `growing` seconds, as their circles do on the board, rather than at
    /// once, so that balls that grow into each other part gently instead of
    /// bursting apart.
    static func paths(of balls: [Ball], in size: CGSize, diameter: CGFloat,
                      grownFrom: CGFloat? = nil) -> [Int64: [CGPoint]] {
        let count = balls.count
        guard count > 0 else { return [:] }
        let world = World(balls, size: size, diameter: Double(diameter), from: Double(grownFrom ?? diameter))
        // Every sample of every ball, a frame at a time.
        var frames: [CGPoint] = []
        frames.reserveCapacity(count * Int(rate * 2))
        frames.append(contentsOf: repeatElement(.zero, count: count))
        world.sample(into: &frames, at: 0)
        // The balls are at rest once all have grown and are in the box, and
        // none has moved as much as a quarter of a point, or, for balls
        // smaller than three points, a twelfth of a ball, between samples for
        // a tenth of a second: slower than any ball falls, or rises when
        // pushed up, whatever they do between samples. The simulation stops
        // then, or at `longest` seconds once they have grown, if all are in
        // the box by then, or else when they are, or at twice that at most.
        let waiting = grownFrom.map { $0 == diameter ? 0 : growing } ?? 0
        var still = 0
        let steps = world.steps
        for step in 1...Int(2 * (longest + waiting) * rate * Double(steps)) {
            world.step(step)
            if step % steps == 0 {
                frames.append(contentsOf: repeatElement(.zero, count: count))
                let (moved, outside) = world.sample(into: &frames, at: frames.count - count)
                still = moved < min(0.25, Double(diameter) / 12) && world.time >= waiting ? still + 1 : 0
                if outside == 0 && (still >= 6 || world.time >= longest + waiting) {
                    break
                }
            }
        }
        // Each path ends where its ball comes to rest, rather than staying
        // there until the last ball does.
        let samples = frames.count / count
        var paths: [Int64: [CGPoint]] = [:]
        paths.reserveCapacity(count)
        for (i, ball) in balls.enumerated() {
            let rest = frames[(samples - 1) * count + i]
            var last = samples - 1
            while last > 0 && frames[(last - 1) * count + i] == rest {
                last -= 1
            }
            paths[ball.id] = (0...last).map { frames[$0 * count + i] }
        }
        return paths
    }

    /// A ball as the simulation has it: where it is and was a step ago, and
    /// which of the balls it is.
    private struct Body {
        var x, y, px, py: Double
        var index: Int32
    }

    /// Balls moving under gravity in a box open at the top, each step a
    /// Verlet step followed by pushing apart those that overlap, found by
    /// the square of a grid each lies in. Each step it puts the balls in
    /// order of the squares they are in, so that those near each other are
    /// near each other in memory too.
    private final class World {
        let count, steps: Int
        /// How big the balls are to start with and to end with, how big they
        /// are now, and how big the grid's squares are, as big as the balls
        /// are at their biggest.
        let from, to: Double
        private(set) var d, r: Double
        let square: Double
        let width, height: Double
        let dt, gravity, rise, fastest: Double
        private(set) var time: Double = 0
        private let bodies: UnsafeMutableBufferPointer<Body>
        /// Which square of the grid each ball is in, as its column times
        /// `rows` and its row from the bottom, the balls being kept in that
        /// order: the grid's squares lie along lines a square apart from the
        /// box's top left, so that a ball moves from one square to the next
        /// only when it moves that far, and the order changes little from
        /// one step to the next.
        private let squares: UnsafeMutableBufferPointer<Int>
        private var rows = 0

        init(_ balls: [Ball], size: CGSize, diameter: Double, from: Double) {
            count = balls.count
            self.from = from
            to = diameter
            d = from
            r = from / 2
            square = max(from, diameter)
            width = Double(size.width)
            height = Double(size.height)
            // As many steps between samples as keep the fastest ball from
            // moving much more than its own width in one, and so from passing
            // through another, but at least three.
            steps = max(3, Int((Heap.fastest / rate / min(from, diameter)).rounded(.up)))
            dt = 1 / (rate * Double(steps))
            gravity = 1800 * dt * dt
            // How far a step may carry a ball up, about six of its widths a
            // second, so that a ball pushed up by those under it creeps up
            // rather than hopping, however small; and how far at all.
            rise = 6 * diameter * dt
            fastest = Heap.fastest * dt
            let dt = dt
            bodies = .allocate(capacity: count)
            _ = bodies.initialize(from: balls.enumerated().lazy.map { i, ball in
                Body(x: Double(ball.position.x), y: Double(ball.position.y),
                     px: Double(ball.position.x - ball.velocity.dx * dt), py: Double(ball.position.y - ball.velocity.dy * dt),
                     index: Int32(i))
            })
            squares = .allocate(capacity: count)
            squares.initialize(repeating: 0)
        }

        deinit {
            bodies.deallocate()
            squares.deallocate()
        }

        /// Puts where each ball is into `frames`, by its index among the
        /// balls, from `base`, and says how far the ball that moved furthest
        /// since the sample before it moved, and how many are not yet in the
        /// box.
        @discardableResult
        func sample(into frames: inout [CGPoint], at base: Int) -> (moved: Double, outside: Int) {
            frames.withUnsafeMutableBufferPointer { frames in
                var moved = 0.0, outside = 0
                for body in bodies {
                    let i = base + Int(body.index)
                    frames[i] = CGPoint(x: body.x, y: body.y)
                    if base >= count {
                        moved = max(moved, abs(body.x - frames[i - count].x), abs(body.y - frames[i - count].y))
                    }
                    if body.x < 0 {
                        outside += 1
                    }
                }
                return (moved, outside)
            }
        }

        /// Takes the `step`th step.
        func step(_ step: Int) {
            time = Double(step) * dt
            d = from + (to - from) * min(1, time / Heap.growing)
            r = d / 2
            let bodies = bodies
            for i in 0..<count {
                let vx = (bodies[i].x - bodies[i].px) * 0.995, vy = (bodies[i].y - bodies[i].py) * 0.995
                bodies[i].px = bodies[i].x
                bodies[i].py = bodies[i].y
                bodies[i].x += vx
                bodies[i].y += vy + gravity
            }
            sort()
            let body = self.bodies
            for _ in 0..<2 {
                separate()
                for i in 0..<count {
                    confine(&body[i])
                }
            }
            // Pushing balls apart moves them, but must not fling them: a ball
            // pushed up by those under it rises only a little at a time, and
            // none goes faster than a fast fall.
            for i in 0..<count {
                var vx = body[i].x - body[i].px, vy = max(body[i].y - body[i].py, -rise)
                var v = (vx * vx + vy * vy).squareRoot()
                if v > fastest {
                    vx *= fastest / v
                    vy *= fastest / v
                    v = fastest
                }
                body[i].px = body[i].x - vx
                body[i].py = body[i].y - vy
            }
        }

        /// Keeps `body` out of the box's sides and floor, and the slope that
        /// leads into it, each of which takes up a ball's speed into it as it
        /// stops it; the floor slows a ball rolling along it, too.
        private func confine(_ body: inout Body) {
            body.x = min(body.x, width - r)
            if body.y > height - r {
                body.y = height - r
                body.py = body.y
                body.px = body.x - (body.x - body.px) * 0.95
            }
            if body.y >= 0 {
                // Beside the box's left side: nothing is ever outside the box
                // beside it, below the slope.
                body.x = max(body.x, r)
            } else if body.x + body.y <= 0 {
                // Beside the slope, the line y = x up and to the left of the
                // box's top left corner, which a ball must stay above.
                let clear = (body.x - body.y) / 2.squareRoot()
                if clear < r {
                    body.x += (r - clear) / 2.squareRoot()
                    body.y -= (r - clear) / 2.squareRoot()
                }
            } else {
                // Above the box's top left corner, which it rolls over.
                let reach = (body.x * body.x + body.y * body.y).squareRoot()
                if reach < r && reach > 0 {
                    body.x *= r / reach
                    body.y *= r / reach
                }
            }
        }

        /// Puts the balls in order of the grid's squares they are in, by
        /// column from the left, and in each column from the floor up, so
        /// that each step pushes balls apart from the floor up, as a heap
        /// bears its weight; by insertion, which, as the order changes little
        /// from one step to the next, takes little more than a look at each.
        private func sort() {
            var top = 0.0, left = 0.0
            for body in bodies {
                top = min(top, body.y)
                left = min(left, body.x)
            }
            top = (top / square).rounded(.down) * square
            left = (left / square).rounded(.down) * square
            rows = Int((height - top) / square) + 2
            for i in 0..<count {
                let column = max(0, Int((bodies[i].x - left) / square))
                let row = rows - 1 - min(rows - 1, max(0, Int((bodies[i].y - top) / square)))
                squares[i] = column * rows + row
            }
            for i in 1..<max(1, count) where squares[i] < squares[i - 1] {
                let square = squares[i], body = bodies[i]
                var j = i
                while j > 0 && squares[j - 1] > square {
                    squares[j] = squares[j - 1]
                    bodies[j] = bodies[j - 1]
                    j -= 1
                }
                squares[j] = square
                bodies[j] = body
            }
        }

        /// Pushes apart every two balls that overlap, by the overlap, the
        /// one below giving way less than the one above, looking for each
        /// ball's neighbours in its own square and the eight around it, from
        /// the floor up. As the balls are taken in order, where the balls of
        /// the squares beside a ball's start and end, in each of the three
        /// columns, only ever moves on, so each is found by moving on from
        /// where it was for the ball before.
        private func separate() {
            let d = d, width = width, rows = rows, body = bodies, squares = squares, count = count
            var starts = (0, 0, 0), ends = (0, 0, 0)
            for i in 0..<count {
                let column = squares[i] / rows, row = squares[i] % rows
                let odd = body[i].index % 2 == 1
                for side in 0..<3 where column + side - 1 >= 0 {
                    let c = column + side - 1
                    let low = c * rows + max(0, row - 1), high = c * rows + min(rows - 1, row + 1)
                    var start = side == 0 ? starts.0 : side == 1 ? starts.1 : starts.2
                    var end = side == 0 ? ends.0 : side == 1 ? ends.1 : ends.2
                    while start < count && squares[start] < low {
                        start += 1
                    }
                    end = max(end, start)
                    while end < count && squares[end] <= high {
                        end += 1
                    }
                    switch side {
                    case 0: (starts.0, ends.0) = (start, end)
                    case 1: (starts.1, ends.1) = (start, end)
                    default: (starts.2, ends.2) = (start, end)
                    }
                    for j in stride(from: max(i + 1, start), to: end, by: 1) {
                        var nx = body[j].x - body[i].x, ny = body[j].y - body[i].y
                        var distance2 = nx * nx + ny * ny
                        guard distance2 < d * d else { continue }
                        if distance2 < 1e-8 {
                            // Two balls in one spot part sideways.
                            nx = odd ? -1e-3 : 1e-3
                            ny = 0
                            distance2 = 1e-6
                        }
                        let distance = distance2.squareRoot()
                        // One ball balanced right on top of another rolls off
                        // it, as a real one would, away from the nearer side
                        // of the box.
                        if abs(nx) < distance * 0.05 {
                            let inward: Double = body[i].x + body[j].x < width ? 1 : -1
                            nx += (ny > 0 ? -inward : inward) * 0.08 * distance
                        }
                        // And of two side by side, squeezed together, one
                        // rides up over the other, rather than both staying
                        // level for ever.
                        if abs(ny) < distance * 0.05 {
                            ny -= 0.08 * distance
                        }
                        // The ball below gives way less than the one on top
                        // of it, as if heavier, so that the weight of a deep
                        // heap does not squeeze the bottom of it out sideways.
                        let push = (d - distance) / distance
                        let upper = 0.5 + 0.4 * ny / distance
                        body[i].x -= nx * push * upper
                        body[i].y -= ny * push * upper
                        body[j].x += nx * push * (1 - upper)
                        body[j].y += ny * push * (1 - upper)
                    }
                }
            }
        }
    }
}
