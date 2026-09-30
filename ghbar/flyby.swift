import AppKit

// When a branch turns red, a small plane flies out from behind the status
// item towing a banner that says so. It dives away to the left, does a few
// aerobatics chosen afresh each time, turns round, and climbs back in behind
// the status item it came from.
//
// The whole flight is worked out before it starts, as Core Animation
// keyframes. The window server plays keyframes against the clock, so on a
// busy machine the flight drops frames and still takes the time it was
// given.
//
// The banner is cut into narrow strips, each of which follows the plane
// along the same path a little later, so the banner bends around the loop
// behind the plane rather than sticking out from it like a board.

// MARK: - Shape and timing

/// How fast the plane flies, in points a second: slow enough for the banner
/// to be read as it goes by.
let flybySpeed: CGFloat = 360
/// How far each flight goes, in points, chosen afresh each time.
let flybyDistance: ClosedRange<CGFloat> = 1800...2250
/// How much larger the plane and banner are drawn than the sizes given
/// below, which are the sizes they are designed at.
let flybyArtScale: CGFloat = 1.2
/// At least this share of every flight is spent cruising: flying straight,
/// and no steeper than `cruiseSlope`.
let cruisingShare: CGFloat = 1.0 / 3
let cruiseSlope: CGFloat = .pi / 6

/// The plane is drawn facing left in a box this size, centred on the path.
let planeSize = CGSize(width: 50, height: 34)
/// How far behind the middle of the plane the tow line is tied on.
let planeTail: CGFloat = 23

let bannerHeight: CGFloat = 22
let bannerFontSize: CGFloat = 13
let towLineLength: CGFloat = 14
/// The width of each strip the banner is cut into. Neighbouring strips
/// overlap by `bannerStripOverlap` so that the outside of a bend has no
/// gaps.
let bannerStripWidth: CGFloat = 4
let bannerStripOverlap: CGFloat = 1.5
/// How far the tail of the banner flaps up and down, how long a flap takes,
/// and how far apart the crests of the wave running down it are.
let bannerFlutter: CGFloat = 2
let bannerFlutterPeriod: CFTimeInterval = 0.18
let bannerFlutterWavelength: CGFloat = 70

let flybyKeyframesPerSecond: Double = 120

let flybyOutline = NSColor(srgbRed: 0.23, green: 0.18, blue: 0.18, alpha: 1)
let flybyBody = NSColor(srgbRed: 1.00, green: 0.79, blue: 0.24, alpha: 1)
let flybyTrim = NSColor(srgbRed: 0.90, green: 0.25, blue: 0.27, alpha: 1)
let flybyGlass = NSColor(srgbRed: 0.60, green: 0.84, blue: 0.96, alpha: 1)

// MARK: - The path

/// Where the plane is at one point along its flight, and which way up.
struct FlightPose {
    var point: CGPoint
    /// The direction of travel, in radians anticlockwise from rightwards.
    /// It is counted continuously, so a loop adds a whole turn to it.
    var heading: CGFloat
    /// How far the plane has rolled, as the vertical scale to draw it at: 1
    /// the way it was drawn, -1 rolled onto its back, 0 edge on.
    var roll: CGFloat
}

/// A flight path built up the way a turtle draws: from a starting point and
/// heading, by going straight and turning, one point of distance at a time.
struct FlightPath {
    private(set) var poses: [FlightPose]

    init(from point: CGPoint, heading: CGFloat) {
        poses = [FlightPose(point: point, heading: heading, roll: 1)]
    }

    /// The distance along the path from the first pose to the last. The
    /// poses are one point apart.
    var length: CGFloat { CGFloat(poses.count - 1) }

    private var last: FlightPose { poses[poses.count - 1] }

    private mutating func step(turning angle: CGFloat = 0, roll: CGFloat? = nil) {
        var pose = last
        let middle = pose.heading + angle / 2
        pose.point.x += cos(middle)
        pose.point.y += sin(middle)
        pose.heading += angle
        pose.roll = roll ?? pose.roll
        poses.append(pose)
    }

    mutating func straight(_ distance: CGFloat) {
        for _ in 0..<Int(distance.rounded()) { step() }
    }

    /// Turns through `angle`, anticlockwise when it is positive, on a circle
    /// of `radius`.
    mutating func turn(_ angle: CGFloat, radius: CGFloat) {
        let steps = max(1, Int((abs(angle) * radius).rounded()))
        for _ in 0..<steps { step(turning: angle / CGFloat(steps)) }
    }

    /// Turns through `angle` towards the top of the plane, which is what a
    /// pilot pulling back on the stick does, or away from it when `angle` is
    /// negative.
    mutating func pull(_ angle: CGFloat, radius: CGFloat) {
        turn(last.roll < 0 ? angle : -angle, radius: radius)
    }

    /// Flies straight for `distance` while rolling half way round, so the
    /// plane ends the other way up from how it started.
    mutating func halfRoll(_ distance: CGFloat) {
        let from = last.roll
        let steps = max(1, Int(distance.rounded()))
        for index in 1...steps {
            step(roll: from * cos(.pi * CGFloat(index) / CGFloat(steps)))
        }
    }

    /// Turns anticlockwise on a circle of `radius` until pointing at
    /// `target`, flies to it, and carries on for `overrun` beyond it.
    mutating func climb(to target: CGPoint, radius: CGFloat, overrun: CGFloat) {
        for _ in 0..<Int(2 * .pi * radius) {
            let toward = CGPoint(x: target.x - last.point.x, y: target.y - last.point.y)
            let bearing = atan2(toward.y, toward.x)
            guard sin(bearing - last.heading) > 0 else { break }
            step(turning: 1 / radius)
        }
        straight(hypot(target.x - last.point.x, target.y - last.point.y) + overrun)
    }

    /// Where the plane is after flying `distance` along the path.
    func pose(at distance: CGFloat) -> FlightPose {
        let clamped = min(max(distance, 0), length)
        let index = min(Int(clamped), poses.count - 2)
        let fraction = clamped - CGFloat(index)
        let from = poses[index]
        let to = poses[index + 1]
        func mix(_ a: CGFloat, _ b: CGFloat) -> CGFloat { a + (b - a) * fraction }
        return FlightPose(point: CGPoint(x: mix(from.point.x, to.point.x),
                                         y: mix(from.point.y, to.point.y)),
                          heading: mix(from.heading, to.heading),
                          roll: mix(from.roll, to.roll))
    }

    /// How much of the path the plane spends flying straight, level or no
    /// more than `cruiseSlope` off it, rather than turning or diving.
    var cruisingLength: CGFloat {
        CGFloat(poses.indices.dropFirst().filter {
            poses[$0].heading == poses[$0 - 1].heading
                && abs(sin(poses[$0].heading)) <= sin(cruiseSlope)
        }.count)
    }

    /// The fractions of the way along the path at which the plane passes
    /// edge on, going from one side showing to the other.
    var sideChanges: [CGFloat] {
        poses.indices.dropFirst()
            .filter { (poses[$0].roll < 0) != (poses[$0 - 1].roll < 0) }
            .map { CGFloat($0) / length }
    }

    /// A flight for a status item whose bottom middle is at `anchor`, over a
    /// screen covering `bounds`, with its aerobatics chosen by `random`. It
    /// starts and ends far enough above `anchor` for the plane to be hidden
    /// behind the menu bar. It flies with the plane the right way up heading
    /// left, and rolled over heading right, so that it looks the right way up
    /// both ways.
    static func flyby(from anchor: CGPoint, within bounds: CGRect,
                      using random: inout some RandomNumberGenerator) -> FlightPath {
        func chance(_ odds: Double) -> Bool { Double.random(in: 0..<1, using: &random) < odds }
        let loopRadius = CGFloat.random(in: 45...70, using: &random)
        let turnRadius = CGFloat.random(in: 40...60, using: &random)
        let diveRadius: CGFloat = 90
        let dive = CGFloat.random(in: 0.2...0.33, using: &random) * .pi
        let rise = CGFloat.random(in: 0.1...0.18, using: &random) * .pi
        let back = CGFloat.random(in: 60...160, using: &random)
        let steepness = CGFloat.random(in: 0.4...0.9, using: &random)
        let length = CGFloat.random(in: flybyDistance, using: &random)
        let turnsDown = chance(0.5)
        let loopsBack = chance(0.4)

        var manoeuvres: [(inout FlightPath) -> Void] = []
        if chance(0.75) {
            manoeuvres.append { $0.pull(2 * .pi, radius: loopRadius) }
        }
        if chance(0.5) {
            manoeuvres.append {
                $0.pull(rise, radius: 200)
                $0.pull(-2 * rise, radius: 200)
                $0.pull(rise, radius: 200)
            }
        }
        manoeuvres.shuffle(using: &random)
        let shares = (0...manoeuvres.count).map { _ in CGFloat.random(in: 0.5...1, using: &random) }
        let deepest = 2 * max(loopRadius, turnRadius) + 70

        /// How much of what was chosen is given up so that enough of the
        /// flight is spent cruising, and so that it is not too long: from 1,
        /// the dive is made shallow enough to count as cruising; from 2, the
        /// climb back is too, and the plane always turns round upwards; from
        /// 3, there is no loop on the way back; and each step beyond that
        /// leaves out one more of the aerobatics on the way out.
        var plainness = 0
        var diveAngle: CGFloat { plainness >= 1 ? min(dive, cruiseSlope * 0.9) : dive }
        var slope: CGFloat { plainness >= 2 ? min(steepness, tan(cruiseSlope) * 0.6) : steepness }
        var flown: ArraySlice<(inout FlightPath) -> Void> {
            manoeuvres.dropLast(max(0, plainness - 3))
        }

        /// The flight that goes `reach` to the left of the status item and
        /// `slope` times that far down, as far as the screen allows.
        func flight(reach: CGFloat) -> FlightPath {
            let depth = max(deepest, min(reach * slope, anchor.y - bounds.minY - 60))
            var path = FlightPath(from: CGPoint(x: anchor.x + 40, y: anchor.y + 60),
                                  heading: .pi + diveAngle)
            path.straight((60 + depth - diveRadius * (1 - cos(diveAngle))) / sin(diveAngle))
            path.pull(diveAngle, radius: diveRadius)
            let cruise = max(40, path.last.point.x - turnRadius - (anchor.x - reach))
            let legs = shares.prefix(flown.count + 1)
            let total = legs.reduce(0, +)
            for (index, share) in legs.enumerated() {
                path.straight(cruise * share / total)
                if index < flown.count {
                    flown[index](&path)
                }
            }
            // Turning round is either a half loop up and a half roll the
            // right way up, or a half roll onto its back and a half loop
            // down, when there is room below for it.
            if turnsDown, plainness < 2, path.last.point.y - 2 * turnRadius - 60 > bounds.minY {
                path.halfRoll(60)
                path.pull(.pi, radius: turnRadius)
            } else {
                path.pull(.pi, radius: turnRadius)
                path.halfRoll(60)
            }
            path.straight(back)
            if loopsBack, plainness < 3, anchor.y - path.last.point.y > 2 * loopRadius + 90 {
                path.pull(2 * .pi, radius: loopRadius)
            }
            path.climb(to: CGPoint(x: anchor.x, y: anchor.y + 34), radius: 90, overrun: 0)
            return path
        }

        /// The flight sized to take its time at its speed. A flight is longer
        /// the further out it goes, so the distance out is found by halving
        /// the range it could lie in until it is within a fraction of a point.
        func sizedFlight() -> FlightPath {
            var near: CGFloat = 150
            var far = max(near, anchor.x - bounds.minX - 60)
            for _ in 0..<16 {
                let middle = (near + far) / 2
                if flight(reach: middle).length < length {
                    near = middle
                } else {
                    far = middle
                }
            }
            return flight(reach: near)
        }

        // A flight that cannot be made short enough however near it stays
        // gives things up as well.
        var path = sizedFlight()
        while path.cruisingLength < path.length * cruisingShare || path.length > length * 1.1,
              plainness < manoeuvres.count + 3 {
            plainness += 1
            path = sizedFlight()
        }
        return path
    }
}

// MARK: - Drawing

/// Draws an image `size` points across at `scale` pixels to the point, with
/// the origin at the bottom left, through AppKit's drawing calls.
func flybyImage(_ size: CGSize, scale: CGFloat, _ draw: (CGContext) -> Void) -> CGImage? {
    guard let space = CGColorSpace(name: CGColorSpace.sRGB),
          let context = CGContext(data: nil,
                                  width: Int((size.width * scale).rounded(.up)),
                                  height: Int((size.height * scale).rounded(.up)),
                                  bitsPerComponent: 8, bytesPerRow: 0, space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return nil }
    context.scaleBy(x: scale, y: scale)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
    draw(context)
    NSGraphicsContext.restoreGraphicsState()
    return context.makeImage()
}

private func paint(_ path: NSBezierPath, _ color: NSColor) {
    color.setFill()
    path.fill()
    flybyOutline.setStroke()
    path.lineWidth = 1.2
    path.lineJoinStyle = .round
    path.stroke()
}

/// A little yellow plane with red trim, side on, facing left, without its
/// propeller.
func planeImage(scale: CGFloat) -> CGImage? {
    flybyImage(planeSize, scale: scale) { _ in
        let strut = NSBezierPath()
        strut.move(to: CGPoint(x: 19, y: 10))
        strut.line(to: CGPoint(x: 17, y: 4.5))
        strut.lineWidth = 1.5
        flybyOutline.setStroke()
        strut.stroke()
        flybyOutline.setFill()
        NSBezierPath(ovalIn: CGRect(x: 14, y: 1, width: 6, height: 6)).fill()

        let fin = NSBezierPath()
        fin.move(to: CGPoint(x: 37, y: 18))
        fin.line(to: CGPoint(x: 43, y: 30))
        fin.line(to: CGPoint(x: 48, y: 30))
        fin.line(to: CGPoint(x: 48, y: 18))
        fin.close()
        paint(fin, flybyTrim)

        paint(NSBezierPath(ovalIn: CGRect(x: 16, y: 17, width: 12, height: 10)), flybyGlass)

        let body = NSBezierPath()
        body.move(to: CGPoint(x: 7, y: 15))
        body.curve(to: CGPoint(x: 18, y: 22.5), controlPoint1: CGPoint(x: 7, y: 20),
                   controlPoint2: CGPoint(x: 12, y: 22.5))
        body.line(to: CGPoint(x: 30, y: 22.5))
        body.curve(to: CGPoint(x: 49, y: 19.5), controlPoint1: CGPoint(x: 38, y: 22.5),
                   controlPoint2: CGPoint(x: 44, y: 21))
        body.line(to: CGPoint(x: 49, y: 16))
        body.curve(to: CGPoint(x: 28, y: 9), controlPoint1: CGPoint(x: 43, y: 12),
                   controlPoint2: CGPoint(x: 36, y: 9))
        body.line(to: CGPoint(x: 18, y: 9))
        body.curve(to: CGPoint(x: 7, y: 15), controlPoint1: CGPoint(x: 11, y: 9),
                   controlPoint2: CGPoint(x: 7, y: 11))
        body.close()
        paint(body, flybyBody)

        paint(NSBezierPath(ovalIn: CGRect(x: 38, y: 15.5, width: 11, height: 4)), flybyTrim)
        paint(NSBezierPath(ovalIn: CGRect(x: 13, y: 11.5, width: 22, height: 6)), flybyTrim)
        paint(NSBezierPath(ovalIn: CGRect(x: 3, y: 12.5, width: 6, height: 7)), flybyTrim)
    }
}

/// The attributes the banner's lettering is set in.
let bannerTextAttributes: [NSAttributedString.Key: Any] = [
    .font: NSFont.systemFont(ofSize: bannerFontSize, weight: .heavy),
    .foregroundColor: NSColor.white,
]

/// How long the banner is, tow line included, for `text`.
func bannerLength(_ text: String) -> CGFloat {
    towLineLength + (text as NSString).size(withAttributes: bannerTextAttributes).width.rounded(.up) + 16
}

/// One side of the banner, tow line on the left. The side seen from behind
/// carries its lettering mirrored, so that it reads correctly once the plane
/// has turned round and that side faces the screen.
func bannerImage(_ text: String, fromBehind: Bool, scale: CGFloat) -> CGImage? {
    let length = bannerLength(text)
    return flybyImage(CGSize(width: length, height: bannerHeight), scale: scale) { context in
        let middle = bannerHeight / 2
        let line = NSBezierPath()
        line.move(to: CGPoint(x: 0, y: middle))
        line.line(to: CGPoint(x: towLineLength - 5, y: middle))
        line.line(to: CGPoint(x: towLineLength, y: 2))
        line.move(to: CGPoint(x: towLineLength - 5, y: middle))
        line.line(to: CGPoint(x: towLineLength, y: bannerHeight - 2))
        line.lineWidth = 0.9
        flybyOutline.setStroke()
        line.stroke()

        flybyTrim.setFill()
        NSBezierPath(rect: CGRect(x: towLineLength, y: 1, width: length - towLineLength,
                                  height: bannerHeight - 2)).fill()
        flybyOutline.setFill()
        NSBezierPath(rect: CGRect(x: towLineLength - 0.5, y: 0, width: 2,
                                  height: bannerHeight)).fill()

        let lettering = NSAttributedString(string: text, attributes: bannerTextAttributes)
        let size = lettering.size()
        let clothMiddle = (towLineLength + length) / 2
        if fromBehind {
            context.translateBy(x: clothMiddle * 2, y: 0)
            context.scaleBy(x: -1, y: 1)
        }
        lettering.draw(at: CGPoint(x: clothMiddle - size.width / 2,
                                   y: middle - size.height / 2))
    }
}

// MARK: - The layers

/// The plane and its banner towing `text` along `path`, seen only within
/// `visible`, which is in screen coordinates. `frame` is where on the screen
/// the layer goes. Nothing moves until `launch` is called with the time to
/// start at, in the time of the layer it is added to, and every animation is
/// over `duration` after that.
func flybyLayer(_ text: String, along path: FlightPath, visible: CGRect, scale: CGFloat)
    -> (frame: CGRect, duration: CFTimeInterval, layer: CALayer,
        launch: (CFTimeInterval) -> Void)? {
    let reach = hypot(planeSize.width, planeSize.height) / 2 * flybyArtScale
    let frame = path.poses
        .reduce(CGRect.null) { $0.union(CGRect(origin: $1.point, size: .zero)) }
        .insetBy(dx: -reach, dy: -reach)
        .intersection(visible)
    let length = bannerLength(text)
    let pixels = scale * flybyArtScale
    guard !frame.isEmpty,
          let plane = planeImage(scale: pixels),
          let front = bannerImage(text, fromBehind: false, scale: pixels),
          let back = bannerImage(text, fromBehind: true, scale: pixels) else {
        return nil
    }

    // Every part of the plane and banner covers the whole path, starting
    // when the plane has flown as far as that part is behind it, so the last
    // strip of the banner reaches the end as the time runs out.
    let speed = flybySpeed
    let duration = CFTimeInterval((path.length + (planeTail + length) * flybyArtScale) / speed)
    let travelTime = CFTimeInterval(path.length / speed)
    let count = max(2, Int((travelTime * flybyKeyframesPerSecond).rounded(.up)) + 1)
    let poses = (0..<count).map {
        path.pose(at: path.length * CGFloat($0) / CGFloat(count - 1))
    }
    func keyframes(_ keyPath: String, _ values: [Any]) -> CAKeyframeAnimation {
        let animation = CAKeyframeAnimation(keyPath: keyPath)
        animation.values = values
        animation.duration = travelTime
        animation.fillMode = .backwards
        return animation
    }
    let origin = frame.origin
    let position = keyframes("position", poses.map {
        NSValue(point: CGPoint(x: $0.point.x - origin.x, y: $0.point.y - origin.y))
    })
    // Drawn facing left, so heading left is no rotation at all.
    let rotation = keyframes("transform.rotation.z", poses.map { $0.heading + .pi })
    let roll = keyframes("transform.scale.y", poses.map(\.roll))
    let changes = path.sideChanges
    let sides = keyframes("contents", (0...changes.count).map {
        $0.isMultiple(of: 2) ? front : back
    })
    sides.keyTimes = ([0] + changes + [1]).map { NSNumber(value: Double($0)) }
    sides.calculationMode = .discrete
    let last = path.poses[path.poses.count - 1].point

    let root = CALayer()
    root.frame = CGRect(origin: .zero, size: frame.size)
    /// Each animation, the layer it goes on, and how long after the start
    /// it begins.
    var flights: [(layer: CALayer, animation: CAAnimation, delay: CFTimeInterval)] = []
    /// Adds a part that is `behind` the middle of the plane along the path:
    /// a layer that follows the path, holding a layer that rolls, holding
    /// `drawing`.
    func part(_ drawing: CALayer, behind: CGFloat) {
        let follows = CALayer()
        follows.bounds = drawing.bounds
        follows.position = CGPoint(x: last.x - origin.x, y: last.y - origin.y)
        let rolls = CALayer()
        rolls.frame = drawing.bounds
        rolls.sublayerTransform = CATransform3DMakeScale(flybyArtScale, flybyArtScale, 1)
        drawing.frame = rolls.bounds
        rolls.addSublayer(drawing)
        follows.addSublayer(rolls)
        root.addSublayer(follows)
        for (layer, animation) in [(follows, position), (follows, rotation), (rolls, roll)] {
            flights.append((layer, animation, CFTimeInterval(behind * flybyArtScale / speed)))
        }
    }
    /// Repeats `animation` until the flight is over.
    func repeating(_ animation: CAAnimation, on layer: CALayer, from: CFTimeInterval = 0) {
        animation.timeOffset = from
        animation.repeatDuration = duration
        flights.append((layer, animation, 0))
    }

    for along in stride(from: 0, to: length, by: bannerStripWidth) {
        let from = max(0, along - bannerStripOverlap / 2)
        let to = min(length, along + bannerStripWidth + bannerStripOverlap / 2)
        let strip = CALayer()
        strip.bounds = CGRect(x: 0, y: 0, width: to - from, height: bannerHeight)
        strip.contents = front
        strip.contentsScale = pixels
        strip.contentsRect = CGRect(x: from / length, y: 0, width: (to - from) / length,
                                    height: 1)
        let flutter = CAKeyframeAnimation(keyPath: "transform.translation.y")
        let height = bannerFlutter * along / length
        flutter.values = (0...12).map { height * sin(2 * .pi * CGFloat($0) / 12) }
        flutter.duration = bannerFlutterPeriod
        let lag = (along / bannerFlutterWavelength).truncatingRemainder(dividingBy: 1)
        repeating(flutter, on: strip, from: bannerFlutterPeriod * CFTimeInterval(1 - lag))
        let behind = planeTail + (from + to) / 2
        flights.append((strip, sides, CFTimeInterval(behind * flybyArtScale / speed)))
        part(strip, behind: behind)
    }

    let body = CALayer()
    body.bounds = CGRect(origin: .zero, size: planeSize)
    body.contents = plane
    body.contentsScale = pixels
    let propeller = CALayer()
    propeller.frame = CGRect(x: 2.75, y: 3, width: 2.5, height: 26)
    propeller.cornerRadius = 1.25
    propeller.backgroundColor = flybyOutline.withAlphaComponent(0.55).cgColor
    let spin = CABasicAnimation(keyPath: "transform.scale.y")
    spin.fromValue = 1
    spin.toValue = -1
    spin.duration = 0.04
    spin.autoreverses = true
    repeating(spin, on: propeller)
    body.addSublayer(propeller)
    part(body, behind: 0)

    return (CGRect(origin: origin, size: frame.size), duration, root, { start in
        for flight in flights {
            flight.animation.beginTime = start + flight.delay
            flight.layer.add(flight.animation, forKey: nil)
        }
    })
}

// MARK: - The window

final class FlybyController {
    private var panel: NSPanel?
    private var whenFinished: (() -> Void)?

    /// Flies the plane out of `button` towing `text`, and calls
    /// `whenFinished` once it has gone back in, or once another flight has
    /// taken its place.
    func show(_ text: String, from button: NSStatusBarButton?,
              whenFinished: (() -> Void)? = nil) {
        dismiss()

        guard let button, let hostWindow = button.window,
              let screen = hostWindow.screen ?? NSScreen.main else {
            whenFinished?()
            return
        }
        let item = hostWindow.convertToScreen(button.convert(button.bounds, to: nil))
        let bounds = screen.frame
        // The status item says where along the bar to fly from. How far down
        // the bar reaches is asked of the screen instead: a status item that
        // has only just been made has not been given its place yet. A menu
        // bar that hides itself takes up none of the screen, and the plane
        // then comes in from the top edge.
        let anchor = CGPoint(x: item.midX, y: screen.visibleFrame.maxY)
        var random = SystemRandomNumberGenerator()
        let path = FlightPath.flyby(from: anchor, within: bounds, using: &random)

        let panel = NSPanel(contentRect: .zero,
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.isMovable = false
        panel.isExcludedFromWindowsMenu = true
        panel.isReleasedWhenClosed = false
        panel.animationBehavior = .none
        panel.level = .statusBar
        var behaviour: NSWindow.CollectionBehavior = [
            .canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary,
        ]
        if #available(macOS 13.0, *) {
            behaviour.insert(.canJoinAllApplications)
        }
        panel.collectionBehavior = behaviour

        // The completion block covers the animations added after it is set.
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        CATransaction.setCompletionBlock { [weak self, weak panel] in
            guard let self, let panel, self.panel === panel else { return }
            self.dismiss()
        }
        defer { CATransaction.commit() }
        // The window reaches up to the bottom of the menu bar and no
        // further, so the plane comes out from behind the bar and goes back
        // behind it.
        let visible = CGRect(x: bounds.minX, y: bounds.minY,
                             width: bounds.width, height: anchor.y - bounds.minY)
        guard let flyby = flybyLayer(text, along: path, visible: visible,
                                     scale: screen.backingScaleFactor) else {
            whenFinished?()
            return
        }
        let view = NSView(frame: CGRect(origin: .zero, size: flyby.frame.size))
        view.wantsLayer = true
        view.layer?.addSublayer(flyby.layer)
        panel.contentView = view
        panel.setFrame(flyby.frame, display: false)
        panel.orderFrontRegardless()
        self.panel = panel
        self.whenFinished = whenFinished
        flyby.launch(flyby.layer.convertTime(CACurrentMediaTime(), from: nil))
    }

    func dismiss() {
        panel?.orderOut(nil)
        panel = nil
        let finished = whenFinished
        whenFinished = nil
        finished?()
    }
}
