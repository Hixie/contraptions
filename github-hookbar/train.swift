import AppKit

// When the event stream brings a new value, a freight train rolls along the
// bottom of the main display, from its right edge to its left, with the value
// lettered along the sides of its cars. An SBB Re 620 electric locomotive in
// SBB Cargo's blue and red pulls the cars, its wheels turning as it goes and
// the cars rocking gently on their springs.
//
// The whole run is worked out before it starts, as Core Animation
// animations. The window server plays them against the clock, so on a busy
// machine the train drops frames and still takes the time it was given.

// MARK: - Shape and timing

/// How fast the train goes, in points a second: slowly enough for the cars
/// to be read as they pass.
let trainSpeed: CGFloat = 170
/// The widest the lettering on one car can be.
let maximumLoadWidth: CGFloat = 150
/// The most characters one car carries of a word, so that a word whose
/// characters take up no width, such as zero-width spaces, is still split
/// into pieces short enough to measure quickly.
let maximumLoadCharacters = 60
/// The most Unicode scalars a character can have and still be drawn. Swift
/// counts some long runs, such as Hangul jamo or emoji joined with zero-width
/// joiners, as a single character, which no car could hold.
let maximumCharacterScalars = 32
/// The most cars a train pulls. A longer value is cut short, and its last
/// car says so.
let maximumCars = 40
/// How wide the soft edges of the railway line are, at its front as it is
/// laid and at its back as it is taken up, and how far behind the last car
/// the back edge starts.
let lineEdge: CGFloat = 160
let lineTrail: CGFloat = 40
/// How far above the bottom of the screen the tops of the rails are.
let railTop: CGFloat = 5

let engineLength: CGFloat = 200
let engineHeight: CGFloat = 72
let engineWheelRadius: CGFloat = 6.5
/// How far back the edges of the engine's blue lean for every point up.
let engineLiverySlant: CGFloat = 0.35
/// The middles of the engine's three bogies, each with two axles, from its
/// front.
let engineBogies: [CGFloat] = [32, 94, 160]
let engineWheels = engineBogies.flatMap { [$0 - 13, $0 + 13] }

let carWheelRadius: CGFloat = 7
let carBodyBottom: CGFloat = 15
let carBodyHeight: CGFloat = 34
let carPadding: CGFloat = 14
let minimumCarLength: CGFloat = 76
let couplingLength: CGFloat = 8

/// How often, on average, the pantograph sparks against the contact wire,
/// and how long a spark lasts.
let sparkInterval: CFTimeInterval = 8
let sparkLife: CFTimeInterval = 0.15
/// The chances that a spark is followed by another, each taken separately,
/// and how long after it the other comes.
let sparkFollowers: [(chance: Double, wait: ClosedRange<CFTimeInterval>)] = [
    (0.5, 0.03...0.15),
    (0.25, 0.1...1.5),
]
/// Where the raised pantograph touches the contact wire, from the front of
/// the engine and the tops of the rails.
let pantographContact = CGPoint(x: engineLength - 38, y: contactWireHeight)
/// The raised pantograph's joints, with the engine at rest: its foot on the
/// roof, its knee, and its head under the wire. The foot rises and falls
/// with the engine, the head stays under the wire, and the knee bends to
/// keep both arms their length.
let pantographFoot = CGPoint(x: engineLength - 46, y: 51)
let pantographKneeAtRest = CGPoint(x: engineLength - 28, y: 61)
let pantographHead = CGPoint(x: engineLength - 38, y: contactWireHeight - 1)

/// Where the raised pantograph's knee is when its foot is at `foot`: the
/// point, behind the line from foot to head, that keeps both arms the
/// length they have at rest.
func pantographKnee(foot: CGPoint) -> CGPoint {
    func distance(_ a: CGPoint, _ b: CGPoint) -> CGFloat { hypot(b.x - a.x, b.y - a.y) }
    let lower = distance(pantographFoot, pantographKneeAtRest)
    let upper = distance(pantographKneeAtRest, pantographHead)
    let span = distance(foot, pantographHead)
    let along = (lower * lower - upper * upper + span * span) / (2 * span)
    let off = (lower * lower - along * along).squareRoot()
    let direction = CGPoint(x: (pantographHead.x - foot.x) / span,
                            y: (pantographHead.y - foot.y) / span)
    // Of the two points that fit, the one towards the back of the engine.
    return CGPoint(x: foot.x + direction.x * along + direction.y * off,
                   y: foot.y + direction.y * along - direction.x * off)
}

/// How far the cars rock on their springs, and how long a rock takes.
let trainRock: CGFloat = 1
let trainRockPeriod: CFTimeInterval = 0.45
/// How far a vehicle has risen on its springs at even steps through a rock,
/// from its start to its end.
let trainRockSteps: [CGFloat] = (0...32).map { trainRock * sin(2 * .pi * CGFloat($0) / 32) }
/// How high above the tops of the rails the contact wire hangs, which is
/// where the raised pantograph's head touches it.
let contactWireHeight: CGFloat = 70
/// How far above the contact wire the catenary wire that carries it is tied
/// to each mast, and how far it sags between masts.
let catenaryRise: CGFloat = 10
let catenarySag: CGFloat = 4
let mastSpacing: CGFloat = 160
/// How high the masts stand above the tops of the rails.
let mastHeight = contactWireHeight + catenaryRise + 3
/// How tall the train's window is: tall enough for the masts, and for the
/// engine, the tallest part of the train, as it rocks.
let trainHeight = railTop + max(mastHeight, engineHeight + trainRock)

let trainOutline = NSColor(srgbRed: 0.23, green: 0.18, blue: 0.18, alpha: 1)
let trainRed = NSColor(srgbRed: 0.90, green: 0.25, blue: 0.27, alpha: 1)
let trainIron = NSColor(srgbRed: 0.30, green: 0.30, blue: 0.34, alpha: 1)
let trainSleeper = NSColor(srgbRed: 0.55, green: 0.40, blue: 0.28, alpha: 1)
// SBB Cargo's livery, and the rest of the engine's colours.
let cargoRed = NSColor(srgbRed: 0.86, green: 0.13, blue: 0.15, alpha: 1)
let cargoBlue = NSColor(srgbRed: 0.16, green: 0.29, blue: 0.60, alpha: 1)
let cargoRoof = NSColor(srgbRed: 0.62, green: 0.63, blue: 0.65, alpha: 1)
let cargoGlass = NSColor(srgbRed: 0.14, green: 0.17, blue: 0.21, alpha: 1)
let headlight = NSColor(srgbRed: 1, green: 0.97, blue: 0.80, alpha: 1)
/// The bright metal of the overhead wires and the pantographs, and the dark
/// edge that keeps them visible on a light background.
let wireMetal = NSColor(srgbRed: 0.66, green: 0.68, blue: 0.72, alpha: 1)
let wireEdge = NSColor(white: 0.1, alpha: 0.35)
/// The masts, a little transparent so that they stay in the background.
let mastColor = NSColor(white: 0.42, alpha: 0.85)
let tailLight = NSColor(srgbRed: 0.52, green: 0.08, blue: 0.09, alpha: 1)
/// The colours of the cars, taken in turn from the front of the train.
let carColors = [
    NSColor(srgbRed: 0.27, green: 0.55, blue: 0.86, alpha: 1),
    NSColor(srgbRed: 0.30, green: 0.68, blue: 0.42, alpha: 1),
    NSColor(srgbRed: 0.95, green: 0.55, blue: 0.20, alpha: 1),
    NSColor(srgbRed: 0.58, green: 0.40, blue: 0.80, alpha: 1),
    NSColor(srgbRed: 0.20, green: 0.64, blue: 0.68, alpha: 1),
]

/// The attributes the cars' lettering is set in.
let trainLetteringAttributes: [NSAttributedString.Key: Any] = [
    .font: NSFont.systemFont(ofSize: 13, weight: .heavy),
    .foregroundColor: NSColor.white,
]

func letteringWidth(_ text: String) -> CGFloat {
    NSAttributedString(string: text, attributes: trainLetteringAttributes).size().width
        .rounded(.up)
}

// MARK: - Loads

/// The words of `text` shared out among freight cars in order, each car's
/// share no wider than `maximumWidth` as `width` measures it, stopping once
/// there are `limit` cars. A word too wide for a car of its own, or with more
/// than `maximumCharacters` characters, is split across as many cars as it
/// needs, never within a character. A character too wide for a car by
/// itself, or with more than `maximumCharacterScalars` Unicode scalars, is
/// carried as "…". Runs of white space, line breaks
/// included, separate words; text that is only white space gives no cars.
func freightLoads(_ text: String, maximumWidth: CGFloat, maximumCharacters: Int = .max,
                  limit: Int = .max, width: (String) -> CGFloat) -> [String] {
    var loads: [String] = []
    var load = ""
    /// The part of the current word that is not on a car yet, and how many
    /// characters it has.
    var piece = ""
    var pieceLength = 0
    func carry(_ piece: String) {
        if load.isEmpty {
            load = piece
        } else if width(load + " " + piece) <= maximumWidth {
            load += " " + piece
        } else {
            loads.append(load)
            load = piece
        }
    }
    for character in text {
        guard loads.count < limit else { break }
        if character.isWhitespace {
            if !piece.isEmpty {
                carry(piece)
                piece = ""
                pieceLength = 0
            }
        } else {
            var character = character
            if character.unicodeScalars.count > maximumCharacterScalars {
                character = "…"
            }
            if !piece.isEmpty, pieceLength == maximumCharacters
                || width(piece + String(character)) > maximumWidth {
                // The rest of the word goes on the next car, with no space
                // put into the word.
                carry(piece)
                loads.append(load)
                load = ""
                piece = ""
                pieceLength = 0
            }
            if piece.isEmpty, width(String(character)) > maximumWidth {
                character = "…"
            }
            piece.append(character)
            pieceLength += 1
        }
    }
    if !piece.isEmpty {
        carry(piece)
    }
    if !load.isEmpty {
        loads.append(load)
    }
    return Array(loads.prefix(limit))
}

/// What the cars of a train carrying `text` say, as `freightLoads` shares it
/// out, cut short after `maximumCars`, with a last car that says so.
func trainLoads(_ text: String) -> [String] {
    let loads = freightLoads(text, maximumWidth: maximumLoadWidth,
                             maximumCharacters: maximumLoadCharacters, limit: maximumCars + 1,
                             width: letteringWidth)
    return loads.count > maximumCars ? loads.prefix(maximumCars - 1) + ["…"] : loads
}

// MARK: - Drawing

/// Draws an image `size` points across at `scale` pixels to the point, with
/// the origin at the bottom left, through AppKit's drawing calls. The size
/// must not be empty.
func trainImage(_ size: CGSize, scale: CGFloat, _ draw: () -> Void) -> CGImage {
    guard let space = CGColorSpace(name: CGColorSpace.sRGB),
          let context = CGContext(data: nil,
                                  width: Int((size.width * scale).rounded(.up)),
                                  height: Int((size.height * scale).rounded(.up)),
                                  bitsPerComponent: 8, bytesPerRow: 0, space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { preconditionFailure("cannot draw an image of \(size) points") }
    context.scaleBy(x: scale, y: scale)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
    draw()
    NSGraphicsContext.restoreGraphicsState()
    guard let image = context.makeImage() else {
        preconditionFailure("cannot make an image of \(size) points")
    }
    return image
}

/// Fills `path` with `color` and outlines it.
private func paint(_ path: NSBezierPath, _ color: NSColor, lineWidth: CGFloat = 1.2) {
    color.setFill()
    path.fill()
    trainOutline.setStroke()
    path.lineWidth = lineWidth
    path.lineJoinStyle = .round
    path.stroke()
}

private func rectangle(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat,
                       radius: CGFloat = 0) -> NSBezierPath {
    NSBezierPath(roundedRect: CGRect(x: x, y: y, width: width, height: height),
                 xRadius: radius, yRadius: radius)
}

/// The engine: an SBB Re 620 electric locomotive in SBB Cargo's livery,
/// side on, facing left, without its wheels, with the tops of the rails at
/// the bottom of the image. Its side is blue, lettered "cargo", between red
/// cab ends whose edges slant back towards the top, the rear end carrying
/// more red than the front; its windows all sit in one band; a band of
/// grilles runs along the top of the body; and its grey roof carries two
/// pantographs, the front one lowered and the back one raised to the wire.
func engineImage(scale: CGFloat) -> CGImage {
    trainImage(CGSize(width: engineLength, height: engineHeight), scale: scale) {
        let end = engineLength
        // Buffers at each end, the frame, and a frame for each bogie.
        paint(rectangle(0.6, 14, 6, 5, radius: 1), trainIron)
        paint(rectangle(end - 6.6, 14, 6, 5, radius: 1), trainIron)
        paint(rectangle(4, 10, end - 8, 5), trainIron)
        for bogie in engineBogies {
            paint(rectangle(bogie - 19, 4, 38, 7, radius: 2), trainIron)
        }

        // The single-arm pantograph at the front, folded down on the roof.
        // The one at the back is raised, and drawn by `trainLayer`, since it
        // flexes as the engine rocks.
        let arms = NSBezierPath()
        arms.move(to: CGPoint(x: 50, y: 52))
        arms.line(to: CGPoint(x: 32, y: 54))
        arms.line(to: CGPoint(x: 44, y: 56))
        arms.move(to: CGPoint(x: 37, y: 57))
        arms.line(to: CGPoint(x: 51, y: 57))
        arms.lineCapStyle = .round
        arms.lineJoinStyle = .round
        strokeWire(arms, width: 1.4)
        paint(rectangle(end - 50, 50, 8, 3, radius: 1), cargoRoof)
        paint(rectangle(46, 50, 8, 3, radius: 1), cargoRoof)
        paint(rectangle(10, 46, end - 20, 5, radius: 2), cargoRoof)
        // Equipment on the roof between the pantographs.
        paint(rectangle(70, 50, 50, 4, radius: 1), cargoRoof)

        // The body, with its cab ends sloping back above the windows.
        let body = NSBezierPath()
        body.move(to: CGPoint(x: 5, y: 14))
        body.line(to: CGPoint(x: 5, y: 37))
        body.line(to: CGPoint(x: 10, y: 47))
        body.line(to: CGPoint(x: end - 10, y: 47))
        body.line(to: CGPoint(x: end - 5, y: 37))
        body.line(to: CGPoint(x: end - 5, y: 14))
        body.close()
        NSGraphicsContext.saveGraphicsState()
        body.addClip()
        cargoRed.setFill()
        body.fill()
        // The blue between the cab ends reaches further back at the top than
        // at the bottom, by the same slant at both ends.
        func slanted(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: x + engineLiverySlant * (y - 18), y: y)
        }
        let blue = NSBezierPath()
        blue.move(to: slanted(12, 14))
        blue.line(to: slanted(149, 14))
        blue.line(to: slanted(149, 47))
        blue.line(to: slanted(12, 47))
        blue.close()
        cargoBlue.setFill()
        blue.fill()
        trainIron.setFill()
        NSBezierPath(rect: CGRect(x: 0, y: 14, width: end, height: 4)).fill()
        // The band of grilles along the top of the body.
        cargoRoof.setFill()
        NSBezierPath(rect: CGRect(x: 0, y: 43, width: end, height: 4)).fill()
        trainOutline.withAlphaComponent(0.45).setFill()
        for x in stride(from: CGFloat(14), to: end - 14, by: 2.5) {
            NSBezierPath(rect: CGRect(x: x, y: 43.8, width: 0.8, height: 2.4)).fill()
        }
        NSGraphicsContext.restoreGraphicsState()
        trainOutline.setStroke()
        body.lineWidth = 1.2
        body.lineJoinStyle = .round
        body.stroke()

        // The door behind the front cab.
        let door = rectangle(21, 19.5, 9, 19, radius: 1)
        cargoBlue.blended(withFraction: 0.45, of: .black)?.setStroke()
        door.lineWidth = 0.8
        door.stroke()

        // The windows, all in one band: the cab windows at each end, and four
        // along the side.
        for x in [7, end - 19] + [33, 68, 103, 138] as [CGFloat] {
            paint(rectangle(x, 30, x < 20 || x > end - 25 ? 9 : 15, 8.5, radius: 1.5),
                  cargoGlass, lineWidth: 0.9)
        }
        headlight.setFill()
        NSBezierPath(ovalIn: CGRect(x: 6, y: 20, width: 4, height: 4)).fill()
        tailLight.setFill()
        NSBezierPath(ovalIn: CGRect(x: end - 10, y: 20, width: 4, height: 4)).fill()

        let font = NSFont.systemFont(ofSize: 19, weight: .heavy)
        NSAttributedString(string: "cargo", attributes: [
            .font: font,
            .foregroundColor: NSColor.white,
        ]).draw(at: CGPoint(x: 36, y: 18.5 + font.descender))
    }
}

/// A boxcar `length` points long in `color`, side on, without its wheels,
/// with `text` lettered along it and a coupling sticking out behind.
func carImage(_ text: String, length: CGFloat, color: NSColor, scale: CGFloat) -> CGImage {
    let top = carBodyBottom + carBodyHeight
    return trainImage(CGSize(width: length + couplingLength, height: top + 4), scale: scale) {
        paint(rectangle(length - 2, 17, couplingLength + 2, 4), trainOutline)
        paint(rectangle(4, 10, length - 8, carBodyBottom - 10), trainIron)
        paint(rectangle(0, carBodyBottom, length, carBodyHeight, radius: 2), color)
        color.blended(withFraction: 0.35, of: .white)?.withAlphaComponent(0.7).setStroke()
        let inset = rectangle(4, carBodyBottom + 4, length - 8, carBodyHeight - 8, radius: 2)
        inset.lineWidth = 1
        inset.stroke()
        paint(rectangle(-1, top - 1, length + 2, 4, radius: 1.5),
              color.blended(withFraction: 0.3, of: trainOutline) ?? color)
        let lettering = NSAttributedString(string: text, attributes: trainLetteringAttributes)
        let size = lettering.size()
        lettering.draw(at: CGPoint(x: (length - size.width) / 2,
                                   y: carBodyBottom + (carBodyHeight - size.height) / 2))
    }
}

/// A wheel with spokes, so that it can be seen to turn.
func wheelImage(radius: CGFloat, scale: CGFloat) -> CGImage {
    trainImage(CGSize(width: radius * 2, height: radius * 2), scale: scale) {
        let middle = CGPoint(x: radius, y: radius)
        paint(NSBezierPath(ovalIn: CGRect(x: 0.6, y: 0.6, width: radius * 2 - 1.2,
                                          height: radius * 2 - 1.2)), trainIron)
        NSColor(white: 0.75, alpha: 1).setStroke()
        for spoke in 0..<4 {
            let angle = CGFloat(spoke) * .pi / 4
            let line = NSBezierPath()
            line.move(to: CGPoint(x: middle.x + cos(angle) * (radius - 2),
                                  y: middle.y + sin(angle) * (radius - 2)))
            line.line(to: CGPoint(x: middle.x - cos(angle) * (radius - 2),
                                  y: middle.y - sin(angle) * (radius - 2)))
            line.lineWidth = 1.1
            line.stroke()
        }
        trainRed.setFill()
        NSBezierPath(ovalIn: CGRect(x: radius - 2, y: radius - 2, width: 4, height: 4)).fill()
    }
}

/// Strokes `path` `width` points wide in bright metal, over a slightly wider
/// dark edge.
private func strokeWire(_ path: NSBezierPath, width: CGFloat) {
    path.lineWidth = width + 0.8
    wireEdge.setStroke()
    path.stroke()
    path.lineWidth = width
    wireMetal.setStroke()
    path.stroke()
}

/// The railway line, `width` points long and `trainHeight` high: rails on
/// sleepers, and above them the overhead line, a contact wire hung by
/// droppers from a catenary wire that sags between masts.
func lineImage(width: CGFloat, scale: CGFloat) -> CGImage {
    trainImage(CGSize(width: width, height: trainHeight), scale: scale) {
        trainSleeper.setFill()
        for x in stride(from: CGFloat(3), to: width, by: 14) {
            NSBezierPath(rect: CGRect(x: x, y: 0, width: 7, height: railTop - 1)).fill()
        }
        trainIron.setFill()
        NSBezierPath(rect: CGRect(x: 0, y: railTop - 2, width: width, height: 2)).fill()

        let contact = railTop + contactWireHeight
        let tied = contact + catenaryRise
        let masts = Array(stride(from: mastSpacing / 4, through: width + mastSpacing,
                                 by: mastSpacing))
        for mast in masts {
            mastColor.setFill()
            rectangle(mast - 1.25, 0, 2.5, railTop + mastHeight, radius: 1).fill()
            NSBezierPath(rect: CGRect(x: mast - 3, y: tied - 1, width: 6, height: 2)).fill()
            NSBezierPath(rect: CGRect(x: mast - 3, y: contact - 1, width: 6, height: 2)).fill()
        }
        let wires = NSBezierPath()
        wires.move(to: CGPoint(x: 0, y: contact))
        wires.line(to: CGPoint(x: width, y: contact))
        // Each span of the catenary wire hangs as a curve between two masts,
        // starting from the mast before the left edge.
        wires.move(to: CGPoint(x: masts[0] - mastSpacing, y: tied))
        for mast in masts {
            wires.curve(to: CGPoint(x: mast, y: tied),
                        controlPoint1: CGPoint(x: mast - mastSpacing * 2 / 3,
                                               y: tied - catenarySag * 4 / 3),
                        controlPoint2: CGPoint(x: mast - mastSpacing / 3,
                                               y: tied - catenarySag * 4 / 3))
        }
        strokeWire(wires, width: 0.9)
        let droppers = NSBezierPath()
        for mast in masts {
            for along in stride(from: CGFloat(16), to: mastSpacing, by: 16) {
                // The height of the catenary wire there, on the curve's
                // parabola through the masts.
                let t = along / mastSpacing
                let x = mast - mastSpacing + along
                droppers.move(to: CGPoint(x: x, y: contact))
                droppers.line(to: CGPoint(x: x, y: tied - 4 * catenarySag * t * (1 - t)))
            }
        }
        droppers.lineWidth = 0.5
        wireMetal.withAlphaComponent(0.75).setStroke()
        droppers.stroke()
    }
}

// MARK: - The layers

/// A spark at `time`, and the sparks that follow it, in order. Every spark
/// in the burst may be followed by others, as `sparkFollowers` says. Each is
/// followed by fewer than one other on average, so every burst ends, after
/// four sparks on average.
func sparkBurst(at time: CFTimeInterval,
                using random: inout some RandomNumberGenerator) -> [CFTimeInterval] {
    var burst: [CFTimeInterval] = []
    var waiting = [time]
    while let spark = waiting.popLast() {
        burst.append(spark)
        for follower in sparkFollowers where Double.random(in: 0..<1, using: &random) < follower.chance {
            waiting.append(spark + Double.random(in: follower.wait, using: &random))
        }
    }
    return burst.sorted()
}

/// When the pantograph sparks between `start` and `end` seconds into a run,
/// in order: in bursts, as `sparkBurst` describes, that begin at random,
/// about once every `sparkInterval`.
func sparkTimes(from start: CFTimeInterval, to end: CFTimeInterval,
                using random: inout some RandomNumberGenerator) -> [CFTimeInterval] {
    var times: [CFTimeInterval] = []
    var time = start
    while true {
        // The waits between bursts that begin at random at a steady rate.
        time += sparkLife - sparkInterval * log(1 - Double.random(in: 0..<1, using: &random))
        guard time + sparkLife <= end else { break }
        times += sparkBurst(at: time, using: &random)
    }
    return times.filter { $0 + sparkLife <= end }.sorted()
}

/// A spark: a pale blue flash with rays, `size` points across.
func sparkImage(size: CGFloat, scale: CGFloat) -> CGImage {
    trainImage(CGSize(width: size, height: size), scale: scale) {
        let middle = CGPoint(x: size / 2, y: size / 2)
        NSColor(srgbRed: 0.55, green: 0.75, blue: 1, alpha: 0.35).setFill()
        NSBezierPath(ovalIn: CGRect(x: 0, y: 0, width: size, height: size)).fill()
        let rays = NSBezierPath()
        for ray in 0..<8 {
            let angle = CGFloat(ray) * .pi / 4
            let reach = size / 2 * (ray.isMultiple(of: 2) ? 0.95 : 0.6)
            rays.move(to: middle)
            rays.line(to: CGPoint(x: middle.x + cos(angle) * reach,
                                  y: middle.y + sin(angle) * reach))
        }
        rays.lineWidth = 1
        rays.lineCapStyle = .round
        NSColor(srgbRed: 0.85, green: 0.93, blue: 1, alpha: 1).setStroke()
        rays.stroke()
        NSColor.white.setFill()
        NSBezierPath(ovalIn: CGRect(x: middle.x - 2, y: middle.y - 2, width: 4, height: 4)).fill()
    }
}

/// How the front of the railway line crosses a window `width` points wide:
/// starting at its right edge, it speeds up steadily until, after
/// `crossing` seconds, its soft edge has passed the left edge. The train
/// comes in at the right edge after `delay` seconds, when the front of the
/// line is a quarter of the window ahead and going twice as fast as the
/// train, and so stays ahead of it from then on.
func lineLead(width: CGFloat) -> (delay: CFTimeInterval, crossing: CFTimeInterval) {
    // With the front at width - distance * (t / crossing)², a quarter of the
    // window in and twice the train's speed at the same moment give these.
    let distance = width + lineEdge
    return (CFTimeInterval(width / (4 * trainSpeed)),
            CFTimeInterval((width * distance).squareRoot() / (2 * trainSpeed)))
}

/// Where the front of the railway line is `time` seconds into a run across
/// a window `width` points wide.
func lineFront(at time: CFTimeInterval, width: CGFloat) -> CGFloat {
    let progress = CGFloat(min(time / lineLead(width: width).crossing, 1))
    return width - (width + lineEdge) * progress * progress
}

/// A train whose cars carry `loads`, rolling across a window `width` points
/// wide and `trainHeight` high from beyond its right edge to beyond its left,
/// on a railway line, track and overhead wire, that is laid from the right
/// across the window ahead of the train, as `lineLead` describes, and taken
/// up a little behind its last car. The
/// animations start when the transaction that adds the layer commits, and
/// are all over `duration` after that, which leaves every layer as it was
/// made: the train beyond the left edge, and nothing showing.
func trainLayer(_ loads: [String], width: CGFloat, scale: CGFloat,
                using random: inout some RandomNumberGenerator)
    -> (duration: CFTimeInterval, layer: CALayer) {
    let carLengths = loads.map { max(minimumCarLength, letteringWidth($0) + 2 * carPadding) }
    let length = engineLength + carLengths.reduce(0) { $0 + $1 + couplingLength }
    let lead = lineLead(width: width)
    /// How far beyond the left edge the train goes, so that the line behind
    /// it has been taken up when it gets there.
    let beyond = length + lineTrail + lineEdge
    let duration = lead.delay + CFTimeInterval((width + beyond) / trainSpeed)
    let engine = engineImage(scale: scale)
    let engineWheel = wheelImage(radius: engineWheelRadius, scale: scale)
    let carWheel = wheelImage(radius: carWheelRadius, scale: scale)
    let line = lineImage(width: width, scale: scale)

    let root = CALayer()
    root.frame = CGRect(x: 0, y: 0, width: width, height: trainHeight)
    /// Repeats `animation` until the run is over, starting `from` that far
    /// into it.
    func repeating(_ animation: CAAnimation, on layer: CALayer, from: CFTimeInterval = 0) {
        animation.timeOffset = from
        animation.repeatDuration = duration
        layer.add(animation, forKey: nil)
    }
    func image(_ contents: CGImage, _ frame: CGRect) -> CALayer {
        let layer = CALayer()
        layer.frame = frame
        layer.contents = contents
        layer.contentsScale = scale
        return layer
    }

    /// Moves `layer`, which starts `offset` from the front of the train,
    /// with the train: still, beyond the right edge, until the train comes
    /// in, and then steadily to beyond the left.
    func roll(_ layer: CALayer, offset: CGFloat) {
        let roll = CAKeyframeAnimation(keyPath: "position.x")
        roll.values = [width, width, -beyond].map { $0 + offset }
        roll.keyTimes = [0, NSNumber(value: lead.delay / duration), 1]
        roll.duration = duration
        layer.add(roll, forKey: nil)
    }
    /// A mask `length` points long whose alpha goes through `colors` at
    /// `locations` along it, starting at `x`.
    func mask(_ x: CGFloat, length: CGFloat, _ colors: [NSColor],
              _ locations: [CGFloat]) -> CAGradientLayer {
        let mask = CAGradientLayer()
        mask.startPoint = CGPoint(x: 0, y: 0.5)
        mask.endPoint = CGPoint(x: 1, y: 0.5)
        mask.colors = colors.map(\.cgColor)
        mask.locations = locations.map { NSNumber(value: Double($0)) }
        mask.anchorPoint = .zero
        mask.frame = CGRect(x: x, y: 0, width: length, height: trainHeight)
        return mask
    }
    func holder(_ content: CALayer, maskedBy mask: CALayer) -> CALayer {
        let holder = CALayer()
        holder.frame = root.bounds
        holder.addSublayer(content)
        holder.mask = mask
        return holder
    }

    // The railway line shows only where two masks both let it through: one
    // whose soft front edge crosses the window ahead of the train, and one
    // whose soft back edge follows a little behind the last car.
    let railway = image(line, CGRect(x: 0, y: 0, width: width, height: trainHeight))
    let front = mask(-lineEdge, length: width + 2 * lineEdge, [.clear, .black, .black],
                     [0, lineEdge / (width + 2 * lineEdge), 1])
    let laying = CAKeyframeAnimation(keyPath: "position.x")
    let samples = 60
    laying.values = (0...samples).map {
        lineFront(at: lead.crossing * Double($0) / Double(samples), width: width)
    }
    laying.duration = lead.crossing
    front.add(laying, forKey: nil)
    let backLength = width + beyond
    let back = mask(-beyond - width, length: backLength, [.black, .black, .clear],
                    [0, 1 - lineEdge / backLength, 1])
    roll(back, offset: -width)
    root.addSublayer(holder(holder(railway, maskedBy: front), maskedBy: back))

    let train = CALayer()
    train.anchorPoint = .zero
    train.frame = CGRect(x: -beyond, y: railTop, width: length, height: trainHeight - railTop)
    root.addSublayer(train)
    roll(train, offset: 0)

    /// Adds a vehicle whose front is `at` along the train: its body, which
    /// rocks on its springs, and its wheels, which turn as the train rolls.
    func vehicle(_ body: CGImage, at x: CGFloat, size: CGSize, wheels: [CGFloat],
                 radius: CGFloat, wheel: CGImage, index: Int) {
        let springs = image(body, CGRect(origin: CGPoint(x: x, y: 0), size: size))
        let rock = CAKeyframeAnimation(keyPath: "transform.translation.y")
        rock.values = trainRockSteps
        rock.duration = trainRockPeriod
        repeating(rock, on: springs, from: trainRockPeriod * Double(index % 3) / 3)
        train.addSublayer(springs)
        for along in wheels {
            let turning = image(wheel, CGRect(x: x + along - radius, y: 0,
                                              width: radius * 2, height: radius * 2))
            // Rolling left turns a wheel anticlockwise, once for every
            // circumference it travels.
            let turn = CABasicAnimation(keyPath: "transform.rotation.z")
            turn.fromValue = 0
            turn.toValue = 2 * CGFloat.pi
            turn.duration = CFTimeInterval(2 * .pi * radius / trainSpeed)
            repeating(turn, on: turning)
            train.addSublayer(turning)
        }
    }

    vehicle(engine, at: 0, size: CGSize(width: engineLength, height: engineHeight),
            wheels: engineWheels,
            radius: engineWheelRadius, wheel: engineWheel, index: 0)
    var x = engineLength
    for (index, (load, carLength)) in zip(loads, carLengths).enumerated() {
        let car = carImage(load, length: carLength,
                           color: carColors[index % carColors.count], scale: scale)
        vehicle(car, at: x,
                size: CGSize(width: carLength + couplingLength,
                             height: carBodyBottom + carBodyHeight + 4),
                wheels: [11, 27, carLength - 27, carLength - 11],
                radius: carWheelRadius, wheel: carWheel, index: index + 1)
        x += carLength + couplingLength
    }

    // The raised pantograph's arms: the lower one turns on its foot, which
    // rises and falls with the engine, and the upper one hangs from the
    // knee up to the head, which stays under the wire. Each is drawn as it
    // is with the engine at rest, from `start` to `end`.
    func angle(_ from: CGPoint, _ to: CGPoint) -> CGFloat { atan2(to.y - from.y, to.x - from.x) }
    func arm(from start: CGPoint, to end: CGPoint) -> CALayer {
        let length = hypot(end.x - start.x, end.y - start.y)
        let pad: CGFloat = 2
        let line = NSBezierPath()
        line.move(to: CGPoint(x: pad, y: pad))
        line.line(to: CGPoint(x: pad + length, y: pad))
        line.lineCapStyle = .round
        let layer = image(trainImage(CGSize(width: length + 2 * pad, height: 2 * pad),
                                     scale: scale) { strokeWire(line, width: 1.4) },
                          CGRect(x: 0, y: 0, width: length + 2 * pad, height: 2 * pad))
        layer.anchorPoint = CGPoint(x: pad / (length + 2 * pad), y: 0.5)
        layer.position = start
        layer.setAffineTransform(CGAffineTransform(rotationAngle: angle(start, end)))
        train.addSublayer(layer)
        return layer
    }
    func flexing(_ layer: CALayer, _ keyPath: String, _ values: [Any]) {
        let animation = CAKeyframeAnimation(keyPath: keyPath)
        animation.values = values
        animation.duration = trainRockPeriod
        repeating(animation, on: layer)
    }
    let feet = trainRockSteps.map { CGPoint(x: pantographFoot.x, y: pantographFoot.y + $0) }
    let knees = feet.map(pantographKnee)
    let lower = arm(from: pantographFoot, to: pantographKneeAtRest)
    flexing(lower, "position", feet.map { NSValue(point: $0) })
    flexing(lower, "transform.rotation.z", zip(feet, knees).map(angle))
    let upper = arm(from: pantographKneeAtRest, to: pantographHead)
    flexing(upper, "position", knees.map { NSValue(point: $0) })
    flexing(upper, "transform.rotation.z", knees.map { angle($0, pantographHead) })
    _ = arm(from: CGPoint(x: pantographHead.x - 9, y: contactWireHeight),
            to: CGPoint(x: pantographHead.x + 9, y: contactWireHeight))

    // Now and then the pantograph sparks where it touches the wire, each
    // spark turned at random so that no two look the same.
    let sparkSize: CGFloat = 16
    let spark = image(sparkImage(size: sparkSize, scale: scale),
                      CGRect(x: pantographContact.x - sparkSize / 2,
                             y: pantographContact.y - sparkSize / 2,
                             width: sparkSize, height: sparkSize))
    spark.opacity = 0
    let sparks = sparkTimes(from: lead.delay, to: duration, using: &random)
    if !sparks.isEmpty {
        let flash = CAKeyframeAnimation(keyPath: "opacity")
        // A spark that comes while the one before is still showing cuts it
        // short, which makes a flicker.
        let shape: [(moment: CFTimeInterval, brightness: Double)] =
            [(0, 0), (0.1, 1), (0.4, 0.7), (1, 0)]
        var times: [CFTimeInterval] = [0]
        var values: [Double] = [0]
        for (index, start) in sparks.enumerated() {
            let next = index + 1 < sparks.count ? sparks[index + 1] : duration
            for point in shape where point.moment == 0 || start + sparkLife * point.moment < next {
                times.append(start + sparkLife * point.moment)
                values.append(point.brightness)
            }
        }
        flash.values = values + [0]
        flash.keyTimes = (times + [duration]).map { NSNumber(value: $0 / duration) }
        flash.duration = duration
        spark.add(flash, forKey: nil)
        let turn = CAKeyframeAnimation(keyPath: "transform.rotation.z")
        turn.calculationMode = .discrete
        turn.values = [0] + sparks.map { _ in CGFloat.random(in: 0..<(.pi / 4), using: &random) }
        turn.keyTimes = ([0] + sparks + [duration]).map { NSNumber(value: $0 / duration) }
        turn.duration = duration
        spark.add(turn, forKey: nil)
    }
    train.addSublayer(spark)

    return (duration, root)
}

// MARK: - The window

final class TrainController {
    private var panel: NSPanel?

    /// Sends a train carrying `text` along the bottom of the main display,
    /// replacing any train already on its way, and returns whether it did.
    /// Text that is only white space sends no train, and leaves any train
    /// already on its way alone.
    @discardableResult
    func run(_ text: String) -> Bool {
        let loads = trainLoads(text)
        guard !loads.isEmpty, let screen = NSScreen.screens.first else { return false }
        dismiss()
        let frame = CGRect(x: screen.frame.minX, y: screen.frame.minY,
                           width: screen.frame.width, height: trainHeight)

        let panel = NSPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel],
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
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle,
                                    .fullScreenAuxiliary, .canJoinAllApplications]

        // The completion block covers the animations added after it is set.
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        CATransaction.setCompletionBlock { [weak self, weak panel] in
            guard let self, let panel, self.panel === panel else { return }
            self.dismiss()
        }
        defer { CATransaction.commit() }
        var random = SystemRandomNumberGenerator()
        let train = trainLayer(loads, width: frame.width, scale: screen.backingScaleFactor,
                               using: &random)
        let view = NSView(frame: CGRect(origin: .zero, size: frame.size))
        view.wantsLayer = true
        view.layer?.addSublayer(train.layer)
        panel.contentView = view
        panel.orderFrontRegardless()
        self.panel = panel
        return true
    }

    func dismiss() {
        panel?.orderOut(nil)
        panel = nil
    }
}
