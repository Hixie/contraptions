import AppKit
import Metal
import MetalKit
import simd

// The banner that drops out of the menu bar when a branch turns red: a piece
// of velour hanging from the status item, simulated as cloth and lit as
// velvet, carrying the names of the checks that failed. It unrolls, hangs and
// stirs for long enough to be read, then rolls back up and is gone.
//
// The cloth is a grid of point masses joined by distance constraints and
// advanced with Verlet integration. The part of it that has not been let out
// yet is not simulated: it is wound onto a spiral at the bottom of whatever
// has been, which is what a rolled banner does.

// MARK: - Shape and timing

/// The banner's size as a fraction of the screen it hangs on.
let bannerScreenWidthFraction: CGFloat = 0.5
let bannerScreenHeightFraction: CGFloat = 1.0 / 3.0

/// The window is larger than the banner so that a swinging corner has
/// somewhere to go.
/// How much room the window leaves around the banner, as a fraction of the
/// banner's own size. Wider than it is tall, because what needs the room is
/// the sideways billow after the cloth snaps taut, and because the banner is
/// two and a half times wider than it is tall so the same fraction of its
/// width is a much larger distance.
let bannerWindowMargin: CGFloat = 0.20
let bannerWindowSideMargin: CGFloat = 0.30

let clothColumns = 120
let clothRows = 54

/// How much wider the cloth is than the rod it hangs from. Cloth cut to the
/// exact width of its support hangs flat, and flat velvet is just red card:
/// the whole look of the fabric is in what the light does along a fold. Real
/// banners are gathered for the same reason, and this is that gather.
let clothGather: Float = 1.13

/// The solver runs on a coarse grid because that is what it can afford, but
/// its points land about eighteen pixels apart, which turns the hem into a
/// polyline that changes direction by ten degrees at every corner. The drawn
/// mesh is a smooth surface fitted through the solved points instead, using
/// Catmull-Rom, which has the property of passing through every one of them
/// rather than merely near them, evaluated first across the cloth and then
/// down it.
let renderSubdivision = 3
/// How many times the drawn surface's normals are averaged with their
/// neighbours before they are shaded.
let clothNormalRelaxation = 2
/// How many rows at the top are never wound onto the roll, so that there is
/// always some cloth the solver is free to move.
let clothHeldRows = 4
/// How hard the air holds the cloth back, against the square of its speed.
let clothDrag: Float = 1.3
/// How far the direction the cloth runs at the take-up line is allowed to
/// depart from straight down, as a fraction of the way toward whatever the
/// cloth is actually doing there.
let rollFrameGive: Float = 0.34

/// How thick the cloth is. Velour is a heavy pile fabric and its hems are
/// doubled over besides, so its edge is a surface of its own rather than the
/// zero-width cut of a sheet of paper. Drawing that edge is what shows the
/// turns of the spiral when the banner is rolled, and what stops the hem
/// reading as a ruled line when it is not.
let clothThickness: Float = 0.0042

/// The smallest the roll's spiral is allowed to get. Cloth wound onto nothing
/// disappears into a point, and the fringe, which is the first thing rolled,
/// then has no core to wrap onto and simply shrivels.
let rollCoreRadius: Float = 0.0072

/// Eight samples rather than four. Four leaves the cords' edges quantised to
/// five levels of coverage, and thin bright things that step like that crawl
/// as they move.
let bannerSampleCount = 8

/// How much heavier the hem is than a square of the cloth above it. A banner
/// with nothing along its bottom edge flutters; one with a bar in the hem
/// hangs.
let hemBarMass: Float = 7

/// How much of a wound row's weight is felt by the cloth still hanging above
/// the roll. The roll is placed rather than solved, which leaves it weightless
/// unless its load is put back by hand, and cloth carrying a weightless roll
/// is cloth under no tension: the free part gathers into a heap instead of
/// being drawn straight by the heavy thing on the end of it, and the heap then
/// tears the join and splits the roll along its length.
let rollLoadPerRow: Float = 0.55

/// The bullion fringe: the twisted gold cords along the bottom of a
/// ceremonial banner. Without them a red rectangle is a rectangle; with them
/// it is a banner, and they are the part that moves last and settles last,
/// which is what tells the eye how heavy everything above them is.
/// How many points each cord is drawn through. The solver works with nine
/// knots, which is enough to say where a cord is; drawing it through nine is
/// not enough to say that it is round.
let fringeRenderKnots = 32
/// How hard the air holds a cord back, against the square of its speed.
let fringeDrag: Float = 3.4
/// How strongly a cord is drawn toward the one beside it, in world units per
/// relaxation pass.
let fringeGather: Float = 0.000007
/// How many points of spiral the fringe is wound along. More than the cord
/// has knots, because the corner tassels are over twice as long as the fringe
/// between them.
let fringeWoundKnots = 24
let fringeStrands = 152
let fringeKnots = 9
let fringeLength: Float = 0.052
let fringeCordRadius: Float = 0.0021
/// Turns of the twist over the length of a cord.
let fringeTwistTurns: Float = 7.5

/// The fold pattern is seeded rather than left to rounding error, so that the
/// cloth buckles into a drape instead of wherever the arithmetic happens to
/// lean. Three waves that do not share a period keep the folds from arriving
/// at even spacing, which is what makes cloth read as cloth and not corduroy.
let clothSeedDepth: Float = 0.055

/// How far down the drop the seeded pleat reaches full depth. The pleats of a
/// gathered heading are formed at the rod, not somewhere down the cloth.
let clothSeedReach: Float = 0.25

/// How hard the cloth resists being bent. This is what sets the width of a
/// fold, and having none is what makes a sheet crimp into narrow creases at
/// its heading and hang flat everywhere below.
let clothBendStiffness: Float = 0.082

/// The longest a solver step is allowed to be. A frame is cut into as many
/// steps as it takes to stay under this, and each step is corrected for its
/// own length, so the cloth behaves the same however long a frame is and
/// however slowly the animation is being played.
let clothStep: Float = 1.0 / 240.0

/// Passes over the distance constraints per step. More passes make the cloth
/// less stretchy.
let clothRelaxationPasses = 14
/// The most any link between two points may be pulled out beyond its own
/// length, as a fraction of it.
let clothMostStretch: Float = 0.045

/// Holding Shift slows the whole thing down, the way holding it has always
/// slowed a window on its way into the Dock.
let bannerSlowMotionFactor: Double = 10

/// Most steps the cloth will take to catch up in one frame. Past this the
/// banner runs slow rather than trying to cross a long stall in one go.
/// How many of the solver's sub-steps are drawn and averaged to make one
/// frame. Four is exactly the number the solver takes in a sixtieth of a
/// second, so the whole of the shutter's opening is covered and no part of it
/// is counted twice. Only the fastest moments need all four: below about a
/// dozen pixels of travel in a frame there is nothing for a shutter to
/// record, and drawing the scene four times to find that out is the most
/// expensive way of learning it.
let shutterTaps = 4
/// How far the hem must travel between one frame and the next, as a fraction
/// of the banner's width, before a frame is worth building from two sub-steps
/// and then from four.
let shutterSecondTap: Float = 0.0022
let shutterFullTaps: Float = 0.0055
/// How far the shadow's shape is pushed off the banner, as a fraction of the
/// window, and how dark it lands. The direction follows the key light, which
/// sits low and to the left, so the throw is mostly sideways and only a
/// little down.
let castThrow = SIMD2<Float>(0.0180, 0.0058)
let castStrength: Float = 0.34
/// The width of the blur, in pixels of the half-size image the shape is drawn
/// into. The kernel reaches six steps either side of this.
let castSoftness = 2.0
let clothMaximumCatchUpSteps = 12

/// The banner is kept above the top of its own window until it is wanted,
/// which puts it behind the menu bar, and it goes back there at the end. An
/// object that slides out from behind an edge has arrived from somewhere; an
/// object that simply appears has not arrived at all.
/// Where the roll rests before it is wanted: far enough up that the whole of
/// it is behind the menu bar. Two or three frames at the start are therefore
/// empty, and no setting of this avoids that — the roll only clears the bar
/// once it has been lowered to within about a third of this height, so
/// starting there would leave the emerge almost no distance to travel. What
/// was worth fixing, and is fixed, is that the shadow used to reach the
/// screen before the banner did; it is now drawn from a depth-tested picture
/// of the banner, so when there is no banner there is no shadow either.
let bannerHiddenLift: Float = 0.084
/// The drop, as a speed rather than as a curve. The cloth leaves the roll
/// already moving at the roll's surface speed, gains on that under gravity,
/// and is held back by the air it has to push out of the way. Cloth is let
/// off the roll at about the rate the cloth below is actually falling: run
/// faster than that, the surplus has nowhere to go and gathers in the lower
/// half, so the leading edge stalls while the slack builds and then drops
/// thirty pixels back up the screen as it is taken up.
let payoutOpeningSpeed: Float = 0.36
let payoutGravity: Float = 1.35
let payoutDrag: Float = 0.42
/// The withdrawal has to leave at the speed the furl handed it, so it needs
/// more room than the height the banner rests at while hidden.
let withdrawLift: Float = 0.21
/// The speed the flourish hands over at, which is the rate its own curve is
/// travelling when it ends. Opening faster than that puts a step in the
/// motion: measured across the handover the lowest point of the cloth went
/// from eight hundred and fifty pixels a second to two thousand two hundred
/// in a single step of the solver.
let withdrawSpeed: Float = 0.51
let emergeDuration = 0.17
let poiseDuration = 0.13
let furlDuration = 0.46
let flourishDuration = 0.14
/// How much further round the roll is left after the flourish has sprung
/// back, in radians.
let rollSettle: Float = 0.34
let withdrawDuration = 0.13
/// The banner hangs at least this long, and longer when there is more to read.
let minimumReadingTime = 3.6
let readingTimePerCharacter = 0.035

// MARK: - What the banner says

struct BannerMessage {
    /// Which repository and branch, in small letters along the top.
    let subject: String
    /// The one line that says what happened.
    let headline: String
    /// The names of the checks that failed.
    let details: [String]

    var readingTime: TimeInterval {
        let characters = subject.count + headline.count
            + details.reduce(0) { $0 + $1.count }
        return max(minimumReadingTime, Double(characters) * readingTimePerCharacter)
    }
}

// MARK: - Matrices

func perspectiveMatrix(verticalFieldOfView: Float, aspect: Float,
                       near: Float, far: Float) -> simd_float4x4 {
    let y = 1 / tan(verticalFieldOfView * 0.5)
    let x = y / aspect
    let z = far / (near - far)
    return simd_float4x4(columns: (SIMD4<Float>(x, 0, 0, 0),
                                   SIMD4<Float>(0, y, 0, 0),
                                   SIMD4<Float>(0, 0, z, -1),
                                   SIMD4<Float>(0, 0, z * near, 0)))
}

func orthographicMatrix(width: Float, height: Float,
                        near: Float, far: Float) -> simd_float4x4 {
    let z = 1 / (near - far)
    return simd_float4x4(columns: (SIMD4<Float>(2 / width, 0, 0, 0),
                                   SIMD4<Float>(0, 2 / height, 0, 0),
                                   SIMD4<Float>(0, 0, z, 0),
                                   SIMD4<Float>(0, 0, z * near, 1)))
}

func lookAtMatrix(eye: SIMD3<Float>, centre: SIMD3<Float>,
                  up: SIMD3<Float>) -> simd_float4x4 {
    let forward = normalize(centre - eye)
    let side = normalize(cross(forward, up))
    let trueUp = cross(side, forward)
    return simd_float4x4(columns: (
        SIMD4<Float>(side.x, trueUp.x, -forward.x, 0),
        SIMD4<Float>(side.y, trueUp.y, -forward.y, 0),
        SIMD4<Float>(side.z, trueUp.z, -forward.z, 0),
        SIMD4<Float>(-dot(side, eye), -dot(trueUp, eye), dot(forward, eye), 1)))
}

// MARK: - Cloth

/// One vertex handed to the renderer. The layout matches the shader's Vertex
/// structure: SIMD3<Float> and Metal's float3 are both sixteen bytes.
struct ClothVertex {
    var position = SIMD3<Float>(repeating: 0)
    var normal = SIMD3<Float>(repeating: 0)
    var tangent = SIMD3<Float>(repeating: 0)
    var uv = SIMD2<Float>(0, 0)
    /// How enclosed this part of the cloth is by its own folds, which darkens
    /// the inside of a crease.
    var occlusion: Float = 1
}

/// A rectangle of cloth pinned along its top edge, with the part that has not
/// been let out yet wound onto a roll.
final class Cloth {
    let columns: Int
    let rows: Int
    let width: Float
    let height: Float
    let spacing: Float

    private var positions: [SIMD3<Float>]
    private var previous: [SIMD3<Float>]
    // Held as three parallel arrays rather than one array of triples: the
    // solver walks them millions of times a second and wants them contiguous.
    private var constraintA: [Int32] = []
    private var constraintB: [Int32] = []
    private var constraintRest: [Float] = []
    // Curvature constraints, held as triples: the middle point is pulled
    // towards the line between its neighbours.
    private var bendA: [Int32] = []
    private var bendB: [Int32] = []
    private var bendC: [Int32] = []
    /// One over the mass of each point. Nought means the point is held: the
    /// rod along the top. The hem is given a heavier bar than the cloth, as
    /// banners are, so that the bottom edge hangs straight and swings with
    /// some authority instead of fluttering.
    private var inverseMass: [Float] = []
    /// What each point weighs with no roll hanging on it.
    private var restingMass: [Float] = []
    private var loadedRow = -1
    private let spacingX: Float
    private let spacingY: Float
    fileprivate var fitted: [SIMD3<Float>] = []
    fileprivate var surface: [SIMD3<Float>] = []
    /// Where the fringe lies while the hem is wound inside the roll. The
    /// cords are the first thing rolled and so end up at its core; left to
    /// hang on their own they would stand straight down out of the middle of
    /// the cylinder they are supposed to be inside.
    private var woundCord: [SIMD3<Float>] = []
    private var hemArc: [Float] = []
    private var smoothed: [SIMD3<Float>] = []
    private var alongBar: [SIMD3<Float>] = []
    private(set) var cordIsWound = false
    /// An extra turn given to the roll beyond the one the payout accounts
    /// for. The flourish at the end is this and nothing else: cloth that is
    /// already fully wound has no length left to change, so the only thing
    /// left that can move is the angle it is wound at.
    var rollSpin: Float = 0
    /// How long the fringe takes to be gathered onto the roll, or let off it.
    private let cordWindRate: Float = 1 / 0.26
    private let cordUnwindRate: Float = 1 / 0.05
    /// Which side of the cloth the roll sits on. Kept from one placement to
    /// the next so that it can never be decided differently.
    private var rollSide = SIMD3<Float>(0, 0, 1)

    /// How strongly the fringe is held onto the roll. Measured in cloth
    /// rather than in time, this crossed its whole range in about a tenth of
    /// a second at the speed the furl runs at, which snatched the fringe off
    /// the hem in three frames. It now moves at a fixed rate, so the cords
    /// take the same quarter of a second to be gathered in however fast the
    /// cloth above them is moving.
    private(set) var cordWindBlend: Float = 1

    /// How much of the cloth, measured down from the top edge, hangs free.
    /// The rest is on the roll.
    var releasedLength: Float = 0

    /// How far the rod the cloth hangs from has been lifted above its resting
    /// place at the menu bar.
    private(set) var lift: Float = 0
    /// How long the previous step was. Verlet reads the gap between a point's
    /// last two places as its speed, which is only true while every step is
    /// the same length. Keeping the previous length lets the gap be rescaled
    /// when it is not, which is what lets a frame of any length behave, and a
    /// tenth-speed frame in particular.
    private var lastStep: Float = clothStep
    /// Constraint passes owed but not yet run. Relaxing a constraint is not an
    /// integration: a pass moves the cloth a fixed fraction of the way to
    /// satisfying itself whatever the step was, so the cloth's stiffness
    /// depends on how many passes are run per second of cloth time and not on
    /// how long a step is. Running the same number every step means a
    /// tenth-speed frame, which covers a tenth of the time, gets ten times the
    /// passes for the time it covers, and the cloth turns to board. So the
    /// count is owed by the length of the step and paid in whole passes.
    private var passesOwed: Float = 0
    /// Raised while the banner is falling. Cloth in free fall folds far more
    /// tightly than cloth hanging still, and a fold tighter than the height of
    /// a letter tears the lettering apart as it is uncovered.
    var stiffening: Float = 1
    private var elapsed: Float = 0
    private var pending: Float = 0
    private var hanging: Float = 1

    init(columns: Int, rows: Int, width: Float, height: Float) {
        self.columns = columns
        self.rows = rows
        self.width = width
        self.height = height
        spacing = height / Float(rows - 1)
        spacingX = width / Float(columns - 1)
        spacingY = height / Float(rows - 1)

        let count = columns * rows
        positions = Array(repeating: SIMD3<Float>(repeating: 0), count: count)
        previous = positions
        inverseMass = Array(repeating: 1, count: count)
        for column in 0..<columns {
            inverseMass[column] = 0
            inverseMass[(rows - 1) * columns + column] = 1 / hemBarMass
            inverseMass[(rows - 2) * columns + column] = 1 / ((hemBarMass + 1) / 2)
        }
        restingMass = inverseMass

        for row in 0..<rows {
            for column in 0..<columns {
                let across = Float(column) / Float(columns - 1)
                let x = -width / 2 + width * across
                // The seed grows with the distance from the rod, because the
                // rod is what holds the top of the cloth flat.
                // The pleat pattern is set at the heading and reaches full
                // depth a quarter of the way down, the way a gathered header
                // holds its pleats from the rod rather than growing them.
                let reach = min(1, Float(row) / Float(rows - 1) / clothSeedReach)
                let depth = clothSeedDepth * (0.35 + 0.65 * reach)
                let turn = across * 2 * Float.pi
                let z = (sin(turn * 3.5) * 0.62
                         + sin(turn * 6.5 + 1.31) * 0.29
                         + sin(turn * 9.5 + 2.74) * 0.17) * depth
                let point = SIMD3<Float>(x, -Float(row) * spacingY, z)
                positions[index(column, row)] = point
                previous[index(column, row)] = point
            }
        }

        // Structural constraints hold the weave together, shear constraints
        // stop it collapsing into a rhombus, and the longer bend constraints
        // give it the stiffness that decides how wide its folds are.
        for row in 0..<rows {
            for column in 0..<columns {
                if column + 1 < columns { link(index(column, row), index(column + 1, row)) }
                if row + 1 < rows { link(index(column, row), index(column, row + 1)) }
                if column + 1 < columns, row + 1 < rows {
                    link(index(column, row), index(column + 1, row + 1))
                    link(index(column + 1, row), index(column, row + 1))
                }
                // Curvature, not a long distance link. A distance constraint
                // from one column to the one two over hardly changes length
                // when the column between them pops out of plane, so it does
                // nothing to stop exactly the buckling it was meant to stop.
                if column + 2 < columns {
                    bend(index(column, row), index(column + 1, row), index(column + 2, row))
                }
                if row + 2 < rows {
                    bend(index(column, row), index(column, row + 1), index(column, row + 2))
                }
            }
        }
        shuffleConstraints()
    }

    private func index(_ column: Int, _ row: Int) -> Int {
        row * columns + column
    }

    func point(_ column: Int, _ row: Int) -> SIMD3<Float> {
        positions[row * columns + column]
    }

    /// Moves the held top row and lets the constraints drag everything else
    /// after it. The cloth below finds out that the rod moved the same way it
    /// finds out about anything else.
    /// Moves the cloth's held edge. Every row that is never paid out is
    /// carried with it: moving only the topmost one leaves the rest to follow
    /// on their springs, and the roll hanging below them lags behind the rod
    /// that is supposed to be carrying it.
    func setLift(_ height: Float) {
        let delta = height - lift
        guard delta != 0 else { return }
        lift = height
        // The previous positions move with them. Verlet reads the gap
        // between a point's last two places as its speed, so moving only the
        // current one hands every row a free step of velocity that then
        // compounds: measured over the first two steps of the withdrawal the
        // rod rose by one amount and the row the roll hangs from rose by two
        // and a half times as much, and the four held rows concertinaed to a
        // eighth of their rest length.
        let held = min(rows, clothHeldRows)
        for row in 0..<held {
            for column in 0..<columns {
                let i = index(column, row)
                positions[i].y += delta
                previous[i].y += delta
            }
        }
    }

    /// Rest lengths are worked out from the weave rather than measured off
    /// the starting positions, so that the extra width of the gather is built
    /// into the cloth instead of being pressed out of it by the first pass of
    /// the solver.
    private func link(_ a: Int, _ b: Int) {
        let columnSpan = Float(a % columns - b % columns) * spacingX * clothGather
        let rowSpan = Float(a / columns - b / columns) * spacingY
        constraintA.append(Int32(a))
        constraintB.append(Int32(b))
        constraintRest.append(sqrt(columnSpan * columnSpan + rowSpan * rowSpan))
    }

    private func bend(_ a: Int, _ b: Int, _ c: Int) {
        bendA.append(Int32(a))
        bendB.append(Int32(b))
        bendC.append(Int32(c))
    }

    /// Shuffles the constraints once, with a fixed sequence so that every run
    /// of the banner is the same. Relaxing them in the order they were built
    /// walks the grid the same way every pass, and the error it leaves behind
    /// leans the same way every pass with it: the cloth then buckles into
    /// diagonal corduroy rather than hanging in folds. Taking them in an
    /// order that has nothing to do with the grid removes the lean.
    private func shuffleConstraints() {
        var state: UInt64 = 0x9E3779B97F4A7C15
        func next() -> UInt64 {
            state ^= state << 13
            state ^= state >> 7
            state ^= state << 17
            return state
        }
        for i in stride(from: constraintA.count - 1, to: 0, by: -1) {
            let j = Int(next() % UInt64(i + 1))
            constraintA.swapAt(i, j)
            constraintB.swapAt(i, j)
            constraintRest.swapAt(i, j)
        }
    }

    /// The row above which the cloth hangs free. Rows below it are on the
    /// roll. Never fewer than a few: a roll hung straight off the rod has its
    /// place decided entirely by where the rod is, so it cannot swing, cannot
    /// lag behind the rod when the rod moves, and holds exactly the same shape
    /// from one frame to the next. Leaving a short stub of solved cloth above
    /// it gives it something to hang from and something to rock on.
    /// How much cloth is out before any is paid out at all: the rows that are
    /// never wound.
    var heldLength: Float { Float(clothHeldRows - 1) * spacing }

    private var freeRows: Int {
        // Fully paid out is fully paid out. The released length and the row
        // spacing are both worked out from the height, so their quotient lands
        // a hair either side of a whole number, and on the low side the last
        // row is treated as still wound: the hem curls onto a roll while the
        // banner hangs, and the fringe flickers in and out of being wound with
        // it, several times a second.
        if releasedLength >= height - spacing * 0.001 { return rows }
        return max(clothHeldRows, min(rows, Int(releasedLength / spacing) + 1))
    }

    /// Steps of one fixed length, always, however much time has gone by.
    /// Verlet integration keeps no velocity of its own: what it keeps is where
    /// each point was one step ago, and it reads that as a speed by assuming
    /// the step it is about to take is the length of the step it just took.
    /// Hand it a shorter step and it reads its own memory as a faster cloth.
    /// So time is banked instead, spent a whole step at a time, and whatever
    /// is left over waits for the next frame.
    func advance() {
        // One step, of the one length the solver is tuned for. Everything
        // that drives the cloth is put on this same clock by the scene above,
        // so the rod can never move while the cloth it holds up does not.
        integrate(clothStep)
        windRemainderOntoRoll(over: clothStep)
        // Gathering the fringe in is a quarter of a second; letting it go is
        // not the same thing backwards. Once the roll has gone there is no
        // spiral left to be part-way onto, so what a slow release blends
        // toward is the last pose the roll had before it vanished, and the
        // cords spend a quarter of a second lying across the face of the
        // cloth above the hem as two rows of identical gold studs. A cord let
        // off a roll simply falls.
        let wanted: Float = cordIsWound ? 1 : 0
        let move = (cordIsWound ? cordWindRate : cordUnwindRate) * clothStep
        cordWindBlend += simd_clamp(wanted - cordWindBlend, -move, move)
    }

    /// A rough normal for one point, taken straight from the solver's own
    /// grid, for deciding how much of the draught it catches.
    private func normalAt(_ column: Int, _ row: Int) -> SIMD3<Float> {
        let left = positions[index(max(0, column - 1), row)]
        let right = positions[index(min(columns - 1, column + 1), row)]
        let up = positions[index(column, max(0, row - 1))]
        let down = positions[index(column, min(rows - 1, row + 1))]
        let normal = cross(down - up, right - left)
        let length = simd_length(normal)
        return length > 1e-8 ? normal / length : SIMD3<Float>(0, 0, 1)
    }

    /// Hangs the weight of the wound cloth on the lowest row still being
    /// solved, so the free part is under the tension it would really be under
    /// and falls straight rather than gathering.
    private func loadTheRoll() {
        let limit = freeRows
        let carrying = limit - 1
        if loadedRow >= 0 && loadedRow != carrying {
            for column in 0..<columns {
                let i = loadedRow * columns + column
                inverseMass[i] = restingMass[i]
            }
        }
        loadedRow = carrying
        guard carrying > 0 else { return }
        // Held to a few rows' worth. The roll really does weigh forty times
        // what a row does, but a mass ratio that steep is more than the
        // constraints can hold in the passes they are given: the cloth above
        // stretches, and where it cannot stretch it tears.
        let wound = min(Float(max(0, rows - limit)), 3)
        let weight = 1 + wound * rollLoadPerRow
        for column in 0..<columns {
            let i = carrying * columns + column
            inverseMass[i] = restingMass[i] / weight
        }
    }

    private func integrate(_ step: Float) {
        elapsed += step
        loadTheRoll()
        let gravity = SIMD3<Float>(0, -9.81, 0)
        // Both of these are quoted for a step of a two-hundred-and-fortieth of
        // a second, and corrected for the step actually being taken.
        let carry = step / max(lastStep, 1e-9)
        let damping = pow(Float(0.9973), step * 240)
        let limit = freeRows

        for row in 1..<limit {
            for column in 0..<columns {
                let i = index(column, row)
                let current = positions[i]
                var velocity = (current - previous[i]) * (carry * damping)
                // Air that moves in slow, uneven gusts rather than a steady
                // push, so the cloth never settles into a shape.
                // A draught that travels across and down the cloth, so that
                // a fold can move rather than the whole sheet swinging as one,
                // and that arrives in gusts with lulls between them.
                let travelX = current.x * 3.1 - elapsed * 0.62
                let travelY = current.y * 2.7 - elapsed * 0.41
                let gust = sin(travelX) * cos(travelY * 0.83)
                    + 0.55 * sin(travelX * 2.3 + 1.7) * cos(travelY * 1.9 + 0.6)
                let envelope = 0.30 + 0.55 * (0.5 + 0.5 * sin(elapsed * 0.83 + 1.1))
                // The draught falls away towards the rod, which barely moves.
                let reach = simd_clamp(-current.y / max(height, 1e-4), 0, 1)
                // A fold turned edge on to the draught catches less of it than
                // one facing into it, and that difference is the whole of why
                // cloth flutters rather than merely swinging.
                let facing = 0.35 + 0.65 * abs(normalAt(column, row).z)
                let strength = envelope * (0.25 + reach) * facing
                let wind = SIMD3<Float>(gust * 0.20, gust * 0.05, 0.30 + gust * 0.42) * strength
                // Air, against the square of the speed. A sheet this size
                // moving through a room is held back hard, and a damping that
                // is merely proportional to speed cannot tell the difference
                // between the great swing the cloth arrives with and the
                // small stir it should keep afterwards. Arriving, the hem
                // swung a third of the banner's width toward the camera and
                // back, which at this focal length is a third again in
                // apparent size: the whole banner appeared to pump.
                let speed = velocity / step
                let rushing = simd_length(speed)
                let held = rushing > 1e-6 ? speed * (-clothDrag * rushing) : SIMD3<Float>()
                velocity += (gravity + wind + held) * (step * step)
                previous[i] = current
                positions[i] = current + velocity
            }
        }

        passesOwed += Float(clothRelaxationPasses) * (step / clothStep)
        let passes = min(Int(passesOwed), clothRelaxationPasses * 3)
        passesOwed -= Float(passes)
        for _ in 0..<passes {
            satisfyConstraints(limit: limit)
            satisfyBending(limit: limit)
        }
        if passes > 0 {
            for _ in 0..<3 { limitStrain(limit: limit) }
        }
        lastStep = step
    }

    private func satisfyConstraints(limit: Int) {
        let lastFree = Int32(limit * columns)
        let count = constraintA.count
        positions.withUnsafeMutableBufferPointer { point in
            inverseMass.withUnsafeBufferPointer { weight in
                constraintA.withUnsafeBufferPointer { first in
                    constraintB.withUnsafeBufferPointer { second in
                        constraintRest.withUnsafeBufferPointer { rest in
                            for k in 0..<count {
                                let a = first[k]
                                let b = second[k]
                                if a >= lastFree || b >= lastFree { continue }
                                let ia = Int(a)
                                let ib = Int(b)
                                let wa = weight[ia]
                                let wb = weight[ib]
                                let share = wa + wb
                                if share <= 0 { continue }
                                let delta = point[ib] - point[ia]
                                let length = simd_length(delta)
                                if length <= 1e-6 { continue }
                                // The heavier of the two points gives way
                                // less, and a held point does not give way at
                                // all.
                                let pull = (length - rest[k]) / (length * share)
                                point[ia] += delta * (pull * wa)
                                point[ib] -= delta * (pull * wb)
                            }
                        }
                    }
                }
            }
        }
    }

    /// No thread in a woven cloth gives more than a few percent before it
    /// simply refuses. Relaxation approaches each rest length without ever
    /// reaching it, and under the weight of the hem arriving at the end of
    /// the drop it fell far enough short that the bottom of the banner
    /// measured a quarter wider than the top and reached past the edges of
    /// its own window. These passes are a ceiling rather than a spring: any
    /// link longer than its limit is brought back to exactly the limit.
    private func limitStrain(limit: Int) {
        let lastFree = Int32(limit * columns)
        let count = constraintA.count
        let most = 1 + clothMostStretch
        positions.withUnsafeMutableBufferPointer { point in
            inverseMass.withUnsafeBufferPointer { weight in
                constraintA.withUnsafeBufferPointer { first in
                    constraintB.withUnsafeBufferPointer { second in
                        constraintRest.withUnsafeBufferPointer { rest in
                            for k in 0..<count {
                                let a = first[k]
                                let b = second[k]
                                if a >= lastFree || b >= lastFree { continue }
                                let ia = Int(a)
                                let ib = Int(b)
                                let wa = weight[ia]
                                let wb = weight[ib]
                                let share = wa + wb
                                if share <= 0 { continue }
                                let ceiling = rest[k] * most
                                let delta = point[ib] - point[ia]
                                let length = simd_length(delta)
                                if length <= ceiling { continue }
                                let pull = (length - ceiling) / (length * share)
                                point[ia] += delta * (pull * wa)
                                point[ib] -= delta * (pull * wb)
                            }
                        }
                    }
                }
            }
        }
    }

    /// Pulls each point back towards the line between its two neighbours,
    /// which is what a stiff cloth does and what decides how wide its folds
    /// come out.
    private func satisfyBending(limit: Int) {
        let lastFree = Int32(limit * columns)
        let count = bendA.count
        positions.withUnsafeMutableBufferPointer { point in
            inverseMass.withUnsafeBufferPointer { weight in
                bendA.withUnsafeBufferPointer { first in
                    bendB.withUnsafeBufferPointer { middle in
                        bendC.withUnsafeBufferPointer { last in
                            for k in 0..<count {
                                let a = first[k], b = middle[k], c = last[k]
                                if a >= lastFree || b >= lastFree || c >= lastFree { continue }
                                let ia = Int(a), ib = Int(b), ic = Int(c)
                                let wa = weight[ia], wb = weight[ib], wc = weight[ic]
                                let share = wa + wb + wc
                                if share <= 0 { continue }
                                let middlePoint = point[ib]
                                let chord = (point[ia] + point[ic]) * 0.5
                                let pull = (chord - middlePoint)
                                    * (clothBendStiffness * stiffening)
                                point[ib] += pull * (wb * 2 / share)
                                point[ia] -= pull * (wa / share)
                                point[ic] -= pull * (wc / share)
                            }
                        }
                    }
                }
            }
        }
    }

    /// Places every row that has not been let out on a spiral beneath the
    /// lowest free row, so the unreleased cloth reads as a roll that unwinds
    /// as it descends and thickens as it takes cloth back up.
    private func windRemainderOntoRoll(over seconds: Float) {
        let limit = freeRows
        guard limit < rows else {
            cordIsWound = false
            return
        }
        cordIsWound = true
        if woundCord.count != columns * fringeWoundKnots {
            woundCord = Array(repeating: SIMD3<Float>(repeating: 0),
                              count: columns * fringeWoundKnots)
        }
        let cordSegment = fringeLength / Float(fringeKnots - 1)
        // Thicker than the cloth actually is. A banner wound as tightly as
        // its own thickness allows makes a roll about a fiftieth of its width
        // across, which at this size is a rod rather than a roll of cloth,
        // and it does not visibly thicken as it takes the banner up. Cloth
        // with a pile on it and a fringe sewn to one end does not wind that
        // tightly in any case.
        let thickness = clothThickness * 2.6

        // The roll is one bar of cloth, not a row of independent spirals, so
        // every column winds about the same axis. Only where each column
        // meets that bar is its own.
        //
        // Which way the cloth is running is measured over four rows rather
        // than one. The last row before the roll is the one bent hardest
        // around it, so over a single row the direction read back was not the
        // way the cloth falls but the way it curls, and it swung from
        // pointing a little down to pointing a little up as the roll turned.
        // Since the roll is placed from that direction and the cloth then
        // bends around wherever it is placed, the two chased each other.
        let baseline = max(0, limit - 5)
        var travel = SIMD3<Float>(repeating: 0)
        var axis = SIMD3<Float>(repeating: 0)
        for column in 0..<columns {
            travel += positions[index(column, limit - 1)] - positions[index(column, baseline)]
            if column + 1 < columns {
                axis += positions[index(column + 1, limit - 1)]
                    - positions[index(column, limit - 1)]
            }
        }
        if simd_length(travel) < 1e-5 { travel = SIMD3<Float>(0, -1, 0) }
        travel = normalize(travel)
        if simd_length(axis) < 1e-5 { axis = SIMD3<Float>(1, 0, 0) }
        axis = normalize(axis)
        // Which side of the cloth the roll is on cannot be decided afresh
        // each time from whichever way the two happen to point: as the cloth
        // ran level for one frame the test tipped over and the roll turned
        // inside out between one frame and the next, taking a twentieth of
        // the banner off the screen for two frames and putting it back. It is
        // decided once and then kept.
        // Anchored to gravity. Keeping the side the roll is on from turning
        // over is not enough on its own: forbidding a half turn does nothing
        // against a slow one, and because the direction the cloth runs is
        // then taken back out of that same frame, the two turned each other
        // steadily round. Over the drop the frame went through a complete
        // revolution, which swung the roll a quarter of a banner's width
        // toward the camera, made the cloth a third wider than its own
        // window, carried the hem up above the rod it hangs from, and ran the
        // artwork backwards across the face of the roll. A hanging cloth runs
        // downward; it may lean, but it may not point sideways or upward, so
        // the measured direction is only allowed to bend the vertical rather
        // than to replace it.
        travel = normalize(SIMD3<Float>(0, -1, 0) + (travel - SIMD3<Float>(0, -1, 0))
                           * rollFrameGive)
        axis -= travel * simd_dot(axis, travel)
        let span = simd_length(axis)
        axis = span > 1e-5 ? axis / span : SIMD3<Float>(1, 0, 0)
        var outward = cross(axis, travel)
        let girth = simd_length(outward)
        outward = girth > 1e-5 ? outward / girth : rollSide
        if simd_dot(outward, rollSide) < 0 { outward = -outward }
        rollSide = outward
        // And the cloth runs square to the pair, downward.
        travel = normalize(cross(axis, outward))
        if travel.y > 0 { travel = -travel }

        let wound = Float(rows - limit) * spacing
        // How thick a roll of this much cloth is. The floor that used to sit
        // inside the square root held the radius at a fiftieth of the
        // banner's width however little was wound, so the roll did not grow
        // out of the hem, it appeared at nearly full size on the first frame
        // of the furl and threw the bottom of the banner two hundred pixels
        // up the screen. Wound to the core it is now as thin as the core, and
        // it thickens from there as it takes up cloth.
        let startRadius = min(max(rollCoreRadius, sqrt(wound * thickness / Float.pi)),
                              height * 0.34)

        // A roll is a straight bar. Letting every column keep its own place on
        // a wavy hem makes its centre line follow the folds, and the bar
        // wanders and undulates along its length.
        var meanAnchor = SIMD3<Float>(repeating: 0)
        for column in 0..<columns {
            meanAnchor += positions[index(column, limit - 1)]
        }
        meanAnchor /= Float(columns)
        meanAnchor += travel * max(0, releasedLength - Float(limit - 1) * spacing)

        // Where the cloth actually leaves the free part and meets the roll.
        // It lies between the last free row and the first wound one, and it
        // slides between them as the cloth pays out. Pinning it to the last
        // free row instead makes it jump a whole row every time one is
        // released, which is a sawtooth in the drop: the hem advances for a
        // few frames and then hops back up.
        // How far past the last free row the cloth has been let out, as a
        // real distance rather than a nominal one. The rows carrying the roll
        // are stretched by its weight, so a row's actual length is not the
        // length it was cut to, and stepping the anchor by the nominal figure
        // left the roll a little short each time and then made it up in one
        // jump whenever the count of free rows skipped a value. Measured
        // through the furl the roll travelled about ten pixels a frame and
        // then thirty-eight, six times over.
        var measured: Float = 0
        for column in 0..<columns {
            measured += simd_length(positions[index(column, limit - 1)]
                                    - positions[index(column, max(0, limit - 2))])
        }
        measured /= Float(columns)
        let fraction = simd_clamp((releasedLength - Float(limit - 1) * spacing) / spacing, 0, 1)
        let overhang = measured * fraction
        // Along the bar, each column keeps its own place, because collapsing
        // them toward the mean would collapse the roll to a point rather than
        // straightening it. But the free cloth is cut wider than the bar it
        // hangs from, so it arrives in folds, and inside a fold the columns
        // run forward, back and forward again. Placed straight onto the bar
        // that reads as a hard vertical step with a crease running the height
        // of the roll. Smoothing the run over a few columns takes the wobble
        // out without shortening the bar.
        if alongBar.count != columns {
            alongBar = Array(repeating: SIMD3<Float>(repeating: 0), count: columns)
        }
        for column in 0..<columns {
            var total = SIMD3<Float>(repeating: 0)
            var count: Float = 0
            for near in max(0, column - 3)...min(columns - 1, column + 3) {
                total += positions[index(near, limit - 1)]
                count += 1
            }
            alongBar[column] = total / count
        }
        for column in 0..<columns {
            let own = alongBar[column] + travel * overhang
            // Not quite a third of each column's own departure from the straight bar is
            // kept. Keeping only an eighth of it, as this did, throws away
            // everything that made the hem read as cloth the instant it is
            // wound: measured, the roll came out ten to thirty times
            // straighter than the cloth it is made of, dead straight to one
            // pixel over its whole length, and with nothing on its surface
            // whose movement the eye could follow it read as a rod sliding up
            // the screen rather than as a roll turning. The high-frequency
            // wobble that made the bar wander is taken out separately, by
            // smoothing each column's place along the bar against its
            // neighbours, so this no longer has to do that job as well.
            let anchor = SIMD3<Float>(own.x,
                                      meanAnchor.y + (own.y - meanAnchor.y) * 0.30,
                                      meanAnchor.z + (own.z - meanAnchor.z) * 0.30)
            var radius = startRadius
            // Where the first wound row sits on the spiral, measured from the
            // tangent. As the cloth pays out this shrinks smoothly to nothing,
            // and at the moment it reaches nothing the next row down takes
            // over at a full step again. Starting every row at a whole step
            // instead made the cloth leave the roll a row at a time, which is
            // a stair in the speed of the drop and a straight cut across the
            // lettering as it is uncovered.
            var angle = min(spacing, Float(limit) * spacing - releasedLength)
                / max(radius, rollCoreRadius) + rollSpin
            let centre = anchor + outward * radius
            for row in limit..<rows {
                let towardCloth = -outward
                let point = centre
                    + towardCloth * (radius * cos(angle))
                    + travel * (radius * sin(angle))
                angle += spacing / max(radius, rollCoreRadius)
                radius = max(rollCoreRadius,
                             radius - thickness / (2 * .pi)
                                 * (spacing / max(radius, rollCoreRadius)))
                let i = index(column, row)
                // Cloth coming off the roll should arrive carrying the speed
                // the roll had, which is where the overshoot and the ring
                // afterwards come from. But the roll is placed once a frame
                // while the solver reads the gap between a point's last two
                // places as one step's worth of travel, so the gap has to be
                // cut to a step's size or the cloth is handed several times
                // the speed it should have and thrown off the screen.
                // The roll is placed once a frame, but the gap between a
                // point's last two places is read as one step's worth of
                // travel, so the frame's movement is scaled to a step's and
                // then held to a sane size.
                let travelled = (point - positions[i]) * (lastStep / max(seconds, 1e-6))
                let reach = simd_length(travelled)
                let mostPerStep = spacing * 0.6
                previous[i] = reach > mostPerStep
                    ? point - travelled * (mostPerStep / reach)
                    : point - travelled
                positions[i] = point
            }

            // The fringe is simply more length beyond the hem, so it keeps
            // going round the same spiral rather than being left to dangle.
            let cordBase = column * fringeWoundKnots
            woundCord[cordBase] = positions[index(column, rows - 1)]
            // Bullion will not wind down to a point, so the cord keeps a
            // turn of its own rather than being drawn into the very centre.
            // It is not held outside the roll, though. The hem is the last
            // thing to come off and the first thing to go back on, so it
            // belongs at the core, and putting it outside made its position
            // round the roll a function of how many turns were wound: over
            // the eight frames of the drop the fringe swung right around the
            // roll and left the screen twice.
            var cordRadius = max(radius, rollCoreRadius * 1.5)
            for knot in 1..<fringeWoundKnots {
                woundCord[cordBase + knot] = centre
                    + (-outward) * (cordRadius * cos(angle))
                    + travel * (cordRadius * sin(angle))
                angle += cordSegment / max(cordRadius, rollCoreRadius)
                cordRadius = max(cordRadius - thickness / (2 * .pi)
                                     * (cordSegment / max(cordRadius, rollCoreRadius)),
                                 rollCoreRadius * 1.5)
            }
        }
    }

    /// Where one knot of one cord lies while the fringe is wound in.
    /// A point on the spiral the fringe is wound along. `knot` may be
    /// fractional, because the tassels at the corners are more than twice the
    /// length of the fringe between them and so need more than twice as much
    /// spiral to lie along. Given the same amount as everything else they had
    /// to pack their extra length into the same turn, and they knotted.
    func woundCordPoint(at u: Float, knot: Float) -> SIMD3<Float> {
        guard woundCord.count == columns * fringeWoundKnots else {
            return hemPoint(at: u)
        }
        let place = simd_clamp(u, 0, 1) * Float(columns - 1)
        let left = Int(place)
        let right = min(columns - 1, left + 1)
        let t = place - Float(left)
        let step = simd_clamp(knot, 0, Float(fringeWoundKnots - 1))
        let low = Int(step)
        let high = min(fringeWoundKnots - 1, low + 1)
        let f = step - Float(low)
        let a = woundCord[left * fringeWoundKnots + low] * (1 - f)
            + woundCord[left * fringeWoundKnots + high] * f
        let b = woundCord[right * fringeWoundKnots + low] * (1 - f)
            + woundCord[right * fringeWoundKnots + high] * f
        return a * (1 - t) + b * t
    }
}

// MARK: - Turning the cloth into a mesh

func catmullRom(_ p0: SIMD3<Float>, _ p1: SIMD3<Float>, _ p2: SIMD3<Float>,
                _ p3: SIMD3<Float>, _ t: Float) -> SIMD3<Float> {
    let t2: Float = t * t
    let t3: Float = t2 * t
    let a: SIMD3<Float> = p1 * 2
    let b: SIMD3<Float> = (p2 - p0) * t
    let c: SIMD3<Float> = (p0 * 2 - p1 * 5 + p2 * 4 - p3) * t2
    let d: SIMD3<Float> = (p1 * 3 - p0 - p2 * 3 + p3) * t3
    return (a + b + c + d) * 0.5
}

extension Cloth {
    /// Where to tie the cord that is `fraction` of the way along the fringe,
    /// measured by distance travelled along the hem rather than by width.
    /// Cloth gathered by a sixth crowds a great deal more hem into the trough
    /// of a fold than across its crest, so cords spaced evenly by width pile
    /// up in the troughs and leave the crests bald.
    func hemFraction(_ fraction: Float) -> Float {
        let across = drawnColumns
        guard hemArc.count == across, across > 1 else { return fraction }
        let total = hemArc[across - 1]
        guard total > 1e-6 else { return fraction }
        let wanted = simd_clamp(fraction, 0, 1) * total
        var low = 0
        var high = across - 1
        while low + 1 < high {
            let middle = (low + high) / 2
            if hemArc[middle] <= wanted { low = middle } else { high = middle }
        }
        let span = hemArc[high] - hemArc[low]
        let within = span > 1e-9 ? (wanted - hemArc[low]) / span : 0
        return (Float(low) + within) / Float(across - 1)
    }

    /// A point along the bottom edge of the drawn surface, which is where a
    /// cord of the fringe is tied.
    func hemPoint(at u: Float) -> SIMD3<Float> {
        let across = drawnColumns
        guard surface.count == across * drawnRows else {
            return SIMD3<Float>(-width / 2 + width * u, -height, 0)
        }
        let place = simd_clamp(u, 0, 1) * Float(across - 1)
        let left = Int(place)
        let right = min(across - 1, left + 1)
        let t = place - Float(left)
        let row = (drawnRows - 1) * across
        return surface[row + left] * (1 - t) + surface[row + right] * t
    }

    /// The point at the bottom edge together with the two directions that
    /// matter to a cord tied there: the way the cloth falls, and the way it
    /// faces. A cord may not climb back up the first, and may not sink into
    /// the second.
    func hemFrame(at u: Float) -> (point: SIMD3<Float>, down: SIMD3<Float>,
                                   facing: SIMD3<Float>) {
        let across = drawnColumns
        let fallback = (SIMD3<Float>(-width / 2 + width * u, -height, 0),
                        SIMD3<Float>(0, -1, 0), SIMD3<Float>(0, 0, 1))
        guard surface.count == across * drawnRows, drawnRows > 2 else { return fallback }
        let place = simd_clamp(u, 0, 1) * Float(across - 1)
        let left = Int(place)
        let right = min(across - 1, left + 1)
        let t = place - Float(left)
        let hem = (drawnRows - 1) * across
        // Three solved rows up, not three drawn ones. The last rows curl onto
        // the roll as soon as the furl begins, so a direction read across
        // them is the way the cloth is bending rather than the way it hangs,
        // and it turns over entirely within one row. A cord told to stay
        // below the hem along that direction is told to stand up through the
        // face of the banner.
        let above = max(0, drawnRows - 1 - 3 * renderSubdivision) * across
        let point = surface[hem + left] * (1 - t) + surface[hem + right] * t
        let over = surface[above + left] * (1 - t) + surface[above + right] * t
        var down = point - over
        let drop = simd_length(down)
        down = drop > 1e-7 ? down / drop : SIMD3<Float>(0, -1, 0)
        var sideways = surface[hem + right] - surface[hem + left]
        let reach = simd_length(sideways)
        sideways = reach > 1e-7 ? sideways / reach : SIMD3<Float>(1, 0, 0)
        var facing = cross(down, sideways)
        let out = simd_length(facing)
        facing = out > 1e-7 ? facing / out : SIMD3<Float>(0, 0, 1)
        return (point, down, facing)
    }

    var rowSpacing: Float { spacing }

    /// The lowest point of the drawn cloth. What decides whether the banner
    /// has actually left, as against having run out of the time allotted for
    /// leaving.
    var lowestPoint: Float {
        guard !surface.isEmpty else { return 0 }
        var least = Float.greatestFiniteMagnitude
        for point in surface where point.y < least { least = point.y }
        return least
    }

    var drawnColumns: Int { (columns - 1) * renderSubdivision + 1 }
    var drawnRows: Int { (rows - 1) * renderSubdivision + 1 }

    /// Walks the outside of the drawn mesh once, giving for each step the
    /// point on the boundary and the point just inside it, which is what says
    /// which way is out.
    var perimeter: [(edge: Int, inward: Int)] {
        let across = drawnColumns
        let down = drawnRows
        var walk: [(Int, Int)] = []
        for column in 0..<across { walk.append((column, across + column)) }
        for row in 1..<down { walk.append((row * across + across - 1, row * across + across - 2)) }
        for column in stride(from: across - 2, through: 0, by: -1) {
            walk.append(((down - 1) * across + column, (down - 2) * across + column))
        }
        for row in stride(from: down - 2, through: 1, by: -1) {
            walk.append((row * across, row * across + 1))
        }
        return walk
    }

    var drawnVertexCount: Int { drawnColumns * drawnRows + perimeter.count * 2 }

    var triangleIndices: [UInt32] {
        var indices: [UInt32] = []
        let across = drawnColumns
        indices.reserveCapacity((across - 1) * (drawnRows - 1) * 6)
        for row in 0..<(drawnRows - 1) {
            for column in 0..<(across - 1) {
                let a = UInt32(row * across + column)
                let b = a + 1
                let c = a + UInt32(across)
                let d = c + 1
                indices += [a, c, b, b, c, d]
            }
        }
        // The band around the edge, two vertices per step of the walk: one on
        // the face the eye sees and one on the face behind it.
        let rimStart = UInt32(across * drawnRows)
        let steps = perimeter.count
        for step in 0..<steps {
            let next = (step + 1) % steps
            let a = rimStart + UInt32(step * 2)
            let b = a + 1
            let c = rimStart + UInt32(next * 2)
            let d = c + 1
            indices += [a, c, b, b, c, d]
        }
        return indices
    }

    /// Fits a smooth surface through the solved points and builds the drawn
    /// mesh from that: a normal and a tangent at every point, the texture
    /// coordinates, and a measure of how far the cloth curls around each one.
    /// Runs the curve through the solved points and works out where the hem
    /// has got to. This is all the fringe needs in order to hang, and it is a
    /// small fraction of the work of shading the result, so it is what the
    /// solver calls on each of its four steps within a frame.
    func buildSurface() {
        let across = drawnColumns
        let down = drawnRows
        if fitted.count != across * rows {
            fitted = Array(repeating: SIMD3<Float>(repeating: 0), count: across * rows)
        }
        if surface.count != across * down {
            surface = Array(repeating: SIMD3<Float>(repeating: 0), count: across * down)
        }

        // Across each solved row.
        for row in 0..<rows {
            for column in 0..<across {
                let cell = min(column / renderSubdivision, columns - 2)
                let t = Float(column - cell * renderSubdivision) / Float(renderSubdivision)
                fitted[row * across + column] = catmullRom(
                    point(max(0, cell - 1), row), point(cell, row),
                    point(cell + 1, row), point(min(columns - 1, cell + 2), row), t)
            }
        }
        // Then down each fitted column.
        for row in 0..<down {
            let cell = min(row / renderSubdivision, rows - 2)
            let t = Float(row - cell * renderSubdivision) / Float(renderSubdivision)
            let above = max(0, cell - 1) * across
            let first = cell * across
            let second = (cell + 1) * across
            let below = min(rows - 1, cell + 2) * across
            for column in 0..<across {
                surface[row * across + column] = catmullRom(
                    fitted[above + column], fitted[first + column],
                    fitted[second + column], fitted[below + column], t)
            }
        }

        // The distance travelled along the hem, measured once here rather
        // than again for each of a hundred and fifty cords.
        if hemArc.count != across {
            hemArc = Array(repeating: 0, count: across)
        }
        let hemRow = (down - 1) * across
        var walked: Float = 0
        hemArc[0] = 0
        for column in 1..<across {
            walked += simd_length(surface[hemRow + column] - surface[hemRow + column - 1])
            hemArc[column] = walked
        }

    }

    /// Turns the drawn surface into vertices to be shaded. Wanted once for
    /// each image that is actually drawn, which is once a frame, or four
    /// times when a frame is built from four sub-steps.
    /// `smoothing` is how many times the normals are averaged with their
    /// neighbours. The sub-steps that are averaged together to make one
    /// blurred frame do not need it: what it removes is a fine crease along
    /// the boundaries of the solver's grid, and a fine crease in an image
    /// that is about to be smeared across a dozen pixels and mixed with three
    /// others is not going to be seen.
    func buildVertices(into vertices: inout [ClothVertex], smoothing: Int) {
        let across = drawnColumns
        let down = drawnRows
        if vertices.count != drawnVertexCount {
            vertices = Array(repeating: ClothVertex(), count: drawnVertexCount)
        }
        guard surface.count == across * down else { return }
        let step = spacing / Float(renderSubdivision)
        for row in 0..<down {
            for column in 0..<across {
                let i = row * across + column
                let here = surface[i]
                // Reflecting the missing neighbour keeps the difference
                // centred and the same width as everywhere else. Clamping it
                // instead would average the point into its own neighbourhood
                // and quietly light the outer ring differently.
                let inner = surface[row * across + min(across - 1, column + 1)]
                let outer = surface[row * across + max(0, column - 1)]
                let left = column > 0 ? outer : here * 2 - inner
                let right = column < across - 1 ? inner : here * 2 - outer
                let lower = surface[min(down - 1, row + 1) * across + column]
                let higher = surface[max(0, row - 1) * across + column]
                let up = row > 0 ? higher : here * 2 - lower
                let below = row < down - 1 ? lower : here * 2 - higher

                let acrossVector = right - left
                let downVector = below - up
                // Towards the eye: rows run down the cloth, so the cross
                // product has to be taken the other way round to face front.
                var normal = cross(downVector, acrossVector)
                let length = simd_length(normal)
                normal = length > 1e-8 ? normal / length : SIMD3<Float>(0, 0, 1)

                var tangent = acrossVector - normal * dot(acrossVector, normal)
                let tangentLength = simd_length(tangent)
                tangent = tangentLength > 1e-8 ? tangent / tangentLength : SIMD3<Float>(1, 0, 0)

                // A point that its neighbours lean over is inside a fold, and
                // less of the sky reaches it.
                let neighbourMean = (left + right + up + below) * 0.25
                let dip = dot(neighbourMean - here, normal) / step
                // Floored well above black. Where the cloth turns onto the
                // roll the dip is as sharp as it gets anywhere, and taken to
                // the old floor it drew a hard dark line the full width of
                // the banner along the join, so the cloth read as
                // disappearing behind something rather than as curving onto
                // it. Cloth in a fold is shaded, not unlit.
                let occlusion = simd_clamp(1 - max(0, dip) * 1.6, 0.30, 1)

                vertices[i].position = here
                vertices[i].normal = normal
                vertices[i].tangent = tangent
                vertices[i].uv = SIMD2<Float>(Float(column) / Float(across - 1),
                                              Float(row) / Float(down - 1))
                vertices[i].occlusion = occlusion
            }
        }

        // The drawn surface runs a curve through the solved points, and a
        // curve of that kind matches its neighbours in value and in slope but
        // not in curvature. Shading reads curvature, so every boundary
        // between two cells of the solver's grid showed as a crease: the
        // lettering in particular broke into hard-edged panels about the size
        // of one letter, each a different tone, with knife edges between
        // them. Averaging each normal with the four around it a couple of
        // times carries the slope across those boundaries without moving the
        // surface at all.
        if smoothed.count != across * down {
            smoothed = Array(repeating: SIMD3<Float>(repeating: 0), count: across * down)
        }
        for _ in 0..<smoothing {
            for row in 0..<down {
                let here = row * across
                let over = max(0, row - 1) * across
                let under = min(down - 1, row + 1) * across
                for column in 0..<across {
                    let left = max(0, column - 1)
                    let right = min(across - 1, column + 1)
                    var sum = vertices[here + column].normal * 2
                    sum += vertices[here + left].normal
                    sum += vertices[here + right].normal
                    sum += vertices[over + column].normal
                    sum += vertices[under + column].normal
                    let length = simd_length(sum)
                    smoothed[here + column] = length > 1e-8
                        ? sum / length : vertices[here + column].normal
                }
            }
            for i in 0..<(across * down) {
                vertices[i].normal = smoothed[i]
                var tangent = vertices[i].tangent
                tangent -= smoothed[i] * dot(tangent, smoothed[i])
                let length = simd_length(tangent)
                vertices[i].tangent = length > 1e-8 ? tangent / length : tangent
            }
        }

        // The edge band. Its normal points away from the cloth rather than
        // out of its face, so the rim catches light along the silhouette the
        // way a cut edge of thick fabric does.
        let rimStart = across * down
        let walk = perimeter
        for (step, place) in walk.enumerated() {
            let front = vertices[place.edge]
            var outward = front.position - surface[place.inward]
            let reach = simd_length(outward)
            outward = reach > 1e-8 ? outward / reach : front.tangent
            let back = front.position - front.normal * clothThickness
            let out = rimStart + step * 2
            for side in 0..<2 {
                vertices[out + side].position = side == 0 ? front.position : back
                vertices[out + side].normal = outward
                vertices[out + side].tangent = front.normal
                vertices[out + side].uv = front.uv
                vertices[out + side].occlusion = front.occlusion * (side == 0 ? 0.7 : 0.45)
            }
        }
    }
}


// MARK: - The fringe

struct FringeVertex {
    var position = SIMD3<Float>(repeating: 0)
    var tangent = SIMD3<Float>(repeating: 0)
    /// Across the cord, from one edge to the other.
    var side: Float = 0
    /// Along the cord, nought where it leaves the hem and one at its tip.
    var along: Float = 0
    /// Where this cord's twist begins, so that no two lie in step.
    var twist: Float = 0
    /// How stout the cord is: the corners carry a tassel rather than fringe.
    var thickness: Float = 1
    /// How far down this cord the red light bouncing off the cloth reaches.
    var bounce: Float = 1
}

/// Cords hanging from the hem. Each is a little chain of knots run through
/// the same Verlet solver as the cloth, held at the top by the hem it hangs
/// from and left to swing everywhere else.
final class Fringe {
    let strands: Int
    let knots: Int
    private var positions: [SIMD3<Float>]
    private var previous: [SIMD3<Float>]
    private var phase: [Float]
    private var beat: [Float] = []
    private var bounce: [Float] = []
    private var wobble: [Float] = []
    /// Where the hem was when the cords were last hung from it, kept so that
    /// a cord can be stopped from climbing back through the cloth it hangs
    /// from.
    private var roots: [(point: SIMD3<Float>, down: SIMD3<Float>, facing: SIMD3<Float>)] = []
    private let segment: Float
    /// How long each cord is. A banner carries a heavier tassel at each
    /// bottom corner than along its length, and the corners are where the eye
    /// goes when the cloth swings.
    private var reach: [Float]
    private var lastStep: Float = clothStep
    private var passesOwed: Float = 0
    private var elapsed: Float = 0
    private var pending: Float = 0
    private var hanging: Float = 1

    init(strands: Int, knots: Int, length: Float) {
        self.strands = strands
        self.knots = knots
        segment = length / Float(knots - 1)
        // Cut bullion is never cut evenly. A few percent either way on each
        // cord is the difference between a hem that reads as trimming and a
        // hem that reads as a comb.
        func scatter(_ n: Int, _ salt: UInt32) -> Float {
            var h = UInt32(truncatingIfNeeded: n) &* 0x9E3779B9 &+ salt
            h ^= h >> 15; h = h &* 0x85EBCA6B; h ^= h >> 13
            return Float(h % 100_003) / 100_003
        }
        reach = (0..<strands).map { strand in
            let u = Float(strand) / Float(max(1, strands - 1))
            let corner = max(simd_smoothstep(0.075, 0, u), simd_smoothstep(0.925, 1, u))
            return (1 + corner * 1.35) * (0.93 + scatter(strand, 0x51ED) * 0.14)
        }
        positions = Array(repeating: SIMD3<Float>(repeating: 0), count: strands * knots)
        previous = positions
        phase = (0..<strands).map { scatter($0, 0x2F1B) * 2 * .pi }
        // Cords of slightly different length and stiffness do not swing
        // together, and a rank of cords swinging together is the strongest
        // tell there is that they are not real.
        beat = (0..<strands).map { 1.35 + scatter($0, 0x7A3D) * 0.62 }
        // Where the light bouncing off the cloth stops reaching the cord.
        // Level across every strand it draws one hard stripe down the hem.
        bounce = (0..<strands).map { 0.78 + scatter($0, 0x1C77) * 0.40 }
        // Bullion is sewn to a tape by hand and the tape is sewn to the hem.
        // Spaced to the thousandth of an inch it reads as a machined comb, so
        // each cord is set a little either side of where it would fall.
        wobble = (0..<strands).map { (scatter($0, 0x6B29) - 0.5) * 0.72 }
    }

    /// Hangs the cords from wherever the hem has got to. The top knot is
    /// placed rather than solved, and its old position is left behind so that
    /// it carries the hem's own speed into the cords below it. While the hem
    /// is wound inside the roll the whole cord is placed, because a cord that
    /// is inside a roll of cloth is not hanging from anything.
    func hang(from cloth: Cloth) {
        let blend = cloth.cordWindBlend
        if roots.count != strands {
            roots = Array(repeating: (SIMD3<Float>(repeating: 0), SIMD3<Float>(0, -1, 0),
                                      SIMD3<Float>(0, 0, 1)), count: strands)
        }
        for strand in 0..<strands {
            let scatter = wobble.isEmpty ? 0 : wobble[strand]
            let u = cloth.hemFraction(
                simd_clamp((Float(strand) + scatter) / Float(strands - 1), 0, 1))
            let base = strand * knots
            let frame = cloth.hemFrame(at: u)
            roots[strand] = frame
            let root = frame.point
            previous[base] = positions[base]
            positions[base] = root
            guard blend > 0 else { continue }
            // The root is read off the smooth drawn hem and the rest of the
            // cord off the solver's own coarser grid, so the whole cord is
            // shifted onto the root rather than left with a kink at the top.
            let shift = root - cloth.woundCordPoint(at: u, knot: 0)
            let stretch = reach[strand]
            for knot in 1..<knots {
                let target = cloth.woundCordPoint(at: u, knot: Float(knot) * stretch) + shift
                let here = positions[base + knot]
                // Root first, tip last. Moved at one rate the whole cord
                // swings through its neighbours on its way to the roll, and
                // the long tassels at the corners sweep through each other
                // and arrive as a knot. Cloth takes a cord up from the end it
                // is tied by.
                let along = Float(knot) / Float(knots - 1)
                let own = simd_clamp(blend * 1.7 - along * 0.7, 0, 1)
                // The step onto the roll is a placement, not a movement. Left
                // in the gap between a knot's last two places, the solver
                // reads it as speed and fires the whole fringe off the cloth.
                let speed = here - previous[base + knot]
                let placed = here + (target - here) * own
                positions[base + knot] = placed
                previous[base + knot] = placed - speed * (1 - own)
            }
        }
    }

    /// Puts every knot straight below its top, for the moment a cord first
    /// appears and has no history to fall from.
    func settle() {
        for strand in 0..<strands {
            let i = strand * knots
            for knot in 1..<knots {
                let point = positions[i]
                    - SIMD3<Float>(0, Float(knot) * segment * reach[strand], 0)
                positions[i + knot] = point
                previous[i + knot] = point
            }
        }
    }

    /// `hanging` is nought while the cords are being wound onto the roll and
    /// one while they hang free. The rules that keep a hanging cord out of
    /// the cloth are the same rules that would fight the winding.
    func advance(hanging: Float) {
        self.hanging = hanging
        integrate(clothStep)
    }

    private func integrate(_ step: Float) {
        elapsed += step
        let gravity = SIMD3<Float>(0, -9.81, 0)
        let carry = step / max(lastStep, 1e-9)
        let damping = pow(Float(0.992), step * 240)
        for strand in 0..<strands {
            let base = strand * knots
            let rate = beat.isEmpty ? 1.6 : beat[strand]
            for knot in 1..<knots {
                let i = base + knot
                let current = positions[i]
                var velocity = (current - previous[i]) * (carry * damping)
                // Air moving past the cords, and nothing else. A steady push
                // out of the screen held the whole fringe permanently forward
                // of the cloth it hangs from, which the cloth never felt.
                let sway = sin(elapsed * rate + current.x * 7.3 + phase[strand])
                let wind = SIMD3<Float>(sway * 0.10, 0, sway * 0.12)
                // Air. Without it the snap at the end of the drop straightens
                // every cord into a radial spike and throws the corner
                // tassels clear of the banner; a cord this light is slowed by
                // the air far more than by anything else acting on it.
                let speed = velocity / step
                let rushing = simd_length(speed)
                let drag = rushing > 1e-6 ? speed * (-fringeDrag * rushing) : SIMD3<Float>()
                velocity += (gravity + wind + drag) * (step * step)
                previous[i] = current
                positions[i] = current + velocity
            }
        }
        passesOwed += 4 * (step / clothStep)
        let passes = min(Int(passesOwed), 12)
        passesOwed -= Float(passes)
        for _ in 0..<passes {
            for strand in 0..<strands {
                let base = strand * knots
                let span = segment * reach[strand]
                // No correction may move a knot more than a third of a
                // segment in one pass. When the hem is snatched away the
                // uncapped correction was large enough to fire the second
                // knot straight up through the face of the cloth, and once a
                // cord is inside the cloth nothing brings it back out.
                let bound = span * 0.34
                func capped(_ move: SIMD3<Float>) -> SIMD3<Float> {
                    let far = simd_length(move)
                    return far > bound ? move * (bound / far) : move
                }
                for knot in 0..<(knots - 1) {
                    let a = base + knot
                    let b = a + 1
                    let delta = positions[b] - positions[a]
                    let length = simd_length(delta)
                    if length <= 1e-7 { continue }
                    let correction = delta * ((length - span) / length)
                    // The top knot is held by the hem, so the one below it
                    // takes the whole correction.
                    if knot == 0 {
                        positions[b] -= capped(correction)
                    } else {
                        positions[a] += capped(correction * 0.5)
                        positions[b] -= capped(correction * 0.5)
                    }
                }
                // Bullion is stiff: it hangs in a curve, not a chain.
                for knot in 0..<(knots - 2) {
                    let a = base + knot
                    let c = a + 2
                    let delta = positions[c] - positions[a]
                    let length = simd_length(delta)
                    if length <= 1e-7 { continue }
                    let rest = span * 1.94
                    let correction = delta * ((length - rest) / length) * 0.22
                    if knot == 0 {
                        positions[c] -= capped(correction * 2)
                    } else {
                        positions[a] += capped(correction)
                        positions[c] -= capped(correction)
                    }
                }
            }
            keepOutOfTheCloth(hanging)
            partNeighbours()
        }
        lastStep = step
    }

    /// Cords hang from the cloth; they do not pass through it. Two rules do
    /// almost all of the work. A knot may not climb back up past the point it
    /// is tied to, which is what a cord physically cannot do while it hangs,
    /// and it may not sink behind the face of the cloth by more than its own
    /// thickness. Between them these stop the spikes that used to stand up
    /// through the banner whenever the hem was snatched away.
    private func keepOutOfTheCloth(_ hanging: Float) {
        guard roots.count == strands, hanging > 0.01 else { return }
        // Straight down the world, not down the cloth. Which way the cloth
        // runs at its bottom edge turns right over within a single row as
        // soon as the hem starts curling onto the roll, and a cord told to
        // stay below its root along a direction that has turned over is told
        // to stand up through the face of the banner. Gravity does not turn
        // over, and a cord hanging from a point cannot be above that point.
        let down = SIMD3<Float>(0, -1, 0)
        for strand in 0..<strands {
            let (point, _, facing) = roots[strand]
            let base = strand * knots
            for knot in 1..<knots {
                let i = base + knot
                var here = positions[i]
                let offset = here - point
                let fallen = simd_dot(offset, down)
                // A little slack so a cord can be flicked outward without
                // being pinned flat against the hem.
                let least = Float(knot) * segment * 0.28 * hanging
                if fallen < least { here += down * (least - fallen) }
                let front = simd_dot(here - point, facing)
                if front < fringeCordRadius {
                    here += facing * ((fringeCordRadius - front) * hanging)
                }
                positions[i] = here
            }
        }
    }

    /// Cords next to one another share the same air and the same hem, so they
    /// touch. Left to themselves they pass straight through each other and
    /// mat into a bird's nest; pushed apart by their own thickness they
    /// gather into the twos and threes real bullion falls into.
    private func partNeighbours() {
        guard strands > 1 else { return }
        let apart = fringeCordRadius * 2.1
        // Beyond touching, cords do not ignore each other either. Metal
        // thread carries enough of a charge, and enough of a burr, that a
        // hem of it falls into twos and threes rather than into a rank of
        // perfectly parallel lines. The pull is weak and only reaches a few
        // cord widths, so it gathers neighbours without collapsing the fringe
        // into clumps.
        let noticing = fringeCordRadius * 7.0
        for strand in 0..<(strands - 1) {
            let a = strand * knots
            let b = a + knots
            let friendly = fringeGather * (0.4 + 1.2 * bounce[strand])
            for knot in 1..<knots {
                let delta = positions[b + knot] - positions[a + knot]
                let gap = simd_length(delta)
                if gap < 1e-7 { continue }
                if gap < apart {
                    let push = delta * ((apart - gap) / gap * 0.5)
                    positions[a + knot] -= push
                    positions[b + knot] += push
                } else if gap < noticing {
                    let reach = Float(knot) / Float(knots - 1)
                    let pull = delta * (friendly * reach / gap)
                    positions[a + knot] += pull
                    positions[b + knot] -= pull
                }
            }
        }
    }

    var vertexCount: Int { strands * fringeRenderKnots * 2 }

    var triangleIndices: [UInt32] {
        var indices: [UInt32] = []
        indices.reserveCapacity(strands * (fringeRenderKnots - 1) * 6)
        for strand in 0..<strands {
            let base = UInt32(strand * fringeRenderKnots * 2)
            for knot in 0..<(fringeRenderKnots - 1) {
                let a = base + UInt32(knot * 2)
                indices += [a, a + 2, a + 1, a + 1, a + 2, a + 3]
            }
        }
        return indices
    }

    /// The curve the nine solved knots describe, read at whatever point along
    /// it is wanted. Eight straight facets per cord read as a bicycle chain
    /// at this size; the same nine knots drawn as a curve read as cord.
    private func along(_ strand: Int, _ t: Float) -> SIMD3<Float> {
        let base = strand * knots
        let place = simd_clamp(t, 0, 1) * Float(knots - 1)
        let knot = min(knots - 2, Int(place))
        let f = place - Float(knot)
        let p0 = positions[base + max(0, knot - 1)]
        let p1 = positions[base + knot]
        let p2 = positions[base + knot + 1]
        let p3 = positions[base + min(knots - 1, knot + 2)]
        let f2: Float = f * f
        let f3: Float = f2 * f
        let a: SIMD3<Float> = p1 * 2
        let b: SIMD3<Float> = (p2 - p0) * f
        let c: SIMD3<Float> = (p0 * 2 - p1 * 5 + p2 * 4 - p3) * f2
        let d: SIMD3<Float> = (p1 * 3 - p0 - p2 * 3 + p3) * f3
        return (a + b + c + d) * 0.5
    }

    func buildVertices(into vertices: inout [FringeVertex]) {
        if vertices.count != vertexCount {
            vertices = Array(repeating: FringeVertex(), count: vertexCount)
        }
        let last = Float(fringeRenderKnots - 1)
        let nudge = 0.5 / last
        for strand in 0..<strands {
            let stout = 0.85 + (reach[strand] - 1) * 0.75
            for knot in 0..<fringeRenderKnots {
                let t = Float(knot) / last
                let here = along(strand, t)
                var tangent = along(strand, min(1, t + nudge)) - along(strand, max(0, t - nudge))
                if simd_length(tangent) < 1e-7 { tangent = SIMD3<Float>(0, -1, 0) }
                tangent = normalize(tangent)
                let out = strand * fringeRenderKnots * 2 + knot * 2
                for side in 0..<2 {
                    vertices[out + side].position = here
                    vertices[out + side].tangent = tangent
                    vertices[out + side].side = side == 0 ? -1 : 1
                    vertices[out + side].along = t
                    vertices[out + side].twist = phase[strand]
                    vertices[out + side].thickness = stout
                    vertices[out + side].bounce = bounce.isEmpty ? 1 : bounce[strand]
                }
            }
        }
    }
}

// MARK: - The look of velour

/// Velvet and velour are pile fabrics: a dense forest of short fibres standing
/// off the backing. Light that meets them head on is swallowed between the
/// fibres, which is why the flat of a velvet banner reads almost black, and
/// light that grazes them is caught by their sides, which is why every fold
/// and every edge lights up. A Lambert surface does the opposite, so the
/// shading here is built the other way round: a dark diffuse base carrying
/// most of the colour, and over it a sheen lobe, the Charlie distribution
/// with Ashikhmin's visibility term, that rises towards grazing angles.
let bannerShaderSource = """
#include <metal_stdlib>
using namespace metal;

struct Vertex {
    float3 position;
    float3 normal;
    float3 tangent;
    float2 uv;
    float occlusion;
};

struct Uniforms {
    float4x4 viewProjection;
    float4x4 lightViewProjection;
    float4 cameraPosition;
    float4 keyDirection;
    float4 keyColour;
    float4 fillDirection;
    float4 fillColour;
    float4 rimDirection;
    float4 rimColour;
    float4 skyColour;
    float4 groundColour;
    float4 clothColour;
    float4 sheenColour;
    float4 inkColour;
    float4 inkSheenColour;
    float4 scatterColour;
    float4 fringeColour;
    /// Where the cast shadow falls, with how dark it is in its alpha.
    float sheenRoughness;
    float napScale;
    float stitchScale;
    float time;
    float exposure;
    float cordRadius;
    float twistTurns;
};

struct Fragment {
    float4 position [[position]];
    float3 world;
    float3 normal;
    float3 tangent;
    float2 uv;
    float occlusion;
};

struct Cord {
    float3 position;
    float3 tangent;
    float side;
    float along;
    float twist;
    float thickness;
    float bounce;
};

struct CordFragment {
    float4 position [[position]];
    float3 world;
    float3 sideways;
    float3 facing;
    float3 tangent;
    float across;
    float along;
    float twist;
    float bounce;
};

vertex Fragment bannerVertex(uint id [[vertex_id]],
                             const device Vertex *vertices [[buffer(0)]],
                             constant Uniforms &uniforms [[buffer(1)]]) {
    Vertex v = vertices[id];
    Fragment out;
    out.position = uniforms.viewProjection * float4(v.position, 1.0);
    out.world = v.position;
    out.normal = v.normal;
    out.tangent = v.tangent;
    out.uv = v.uv;
    out.occlusion = v.occlusion;
    return out;
}

/// The banner throws a shadow onto whatever is behind the window. Without one
/// it is a picture stuck on the screen rather than a thing hanging in front of
/// it, and no amount of work on the cloth itself answers that.
///
/// The shape of that shadow is drawn once, into a small single-channel image,
/// with the depth test on so that a cord wound away inside the roll leaves no
/// mark. Drawing it once is what keeps it an even tone: a shape drawn with
/// blending darkens itself wherever two folds of the same cloth overlap, and
/// an opaque thing does not throw a darker shadow where it happens to be
/// doubled over.
vertex float4 castVertex(uint id [[vertex_id]],
                         const device Vertex *vertices [[buffer(0)]],
                         constant Uniforms &uniforms [[buffer(1)]]) {
    return uniforms.viewProjection * float4(vertices[id].position, 1.0);
}

vertex float4 castCordVertex(uint id [[vertex_id]],
                             const device Cord *cords [[buffer(0)]],
                             constant Uniforms &uniforms [[buffer(1)]]) {
    Cord v = cords[id];
    float3 toEye = normalize(uniforms.cameraPosition.xyz - v.position);
    float3 sideways = cross(normalize(v.tangent), toEye);
    float reach = length(sideways);
    sideways = reach > 1e-5 ? sideways / reach : float3(1.0, 0.0, 0.0);
    float3 world = v.position + sideways * (v.side * uniforms.cordRadius);
    return uniforms.viewProjection * float4(world, 1.0);
}

fragment float castFragment() {
    return 1.0;
}

struct Screen {
    float4 position [[position]];
    float2 uv;
};

/// Softens the shadow's shape. Run once across and once down, which together
/// give the same result as a round kernel for a fraction of the taps. The
/// edge a real shadow has at this distance is a good deal wider than the one
/// pixel a hard silhouette gives it.
fragment float blurFragment(Screen in [[stage_in]],
                            texture2d<float> shape [[texture(0)]],
                            constant float2 &step [[buffer(0)]]) {
    // Clamped to the edge for the same reason the pass that lays the shadow
    // down is: the shape is cut off where the banner runs up behind the menu
    // bar, and blurring against nothing there fades the cut into a line.
    constexpr sampler flat(coord::normalized, filter::linear, address::clamp_to_edge);
    const float weights[7] = { 0.1963, 0.1747, 0.1235, 0.0695, 0.0311, 0.0111, 0.0031 };
    float sum = shape.sample(flat, in.uv).r * weights[0];
    for (int i = 1; i < 7; ++i) {
        float2 away = step * float(i);
        sum += shape.sample(flat, in.uv + away).r * weights[i];
        sum += shape.sample(flat, in.uv - away).r * weights[i];
    }
    return sum;
}

/// Lays the softened shape down behind the banner, displaced along the
/// direction the key light comes from.
fragment float4 shadowCompositeFragment(Screen in [[stage_in]],
                                        texture2d<float> shape [[texture(0)]],
                                        constant float4 &placing [[buffer(0)]]) {
    // Clamped to the edge rather than to nothing. The banner runs up behind
    // the menu bar, so its shape is cut off at the top of the image it is
    // drawn into; returning nothing above that and then displacing the whole
    // thing down by sixteen pixels left the cut exposed as a ruled horizontal
    // line across the top of the shadow, a third of its depth deep and
    // pinned to the frame rather than to the banner. The caster carries on
    // above the image, so the shape should too.
    constexpr sampler flat(coord::normalized, filter::linear, address::clamp_to_edge);
    float mask = shape.sample(flat, in.uv - placing.xy).r;
    return float4(0.0, 0.0, 0.0, mask * placing.z);
}


/// Half a code value of triangular noise, added just before the eight-bit
/// write. Across the wide soft gradient of a fold, eight bits cannot hold the
/// steps apart and they read as contour lines drawn on the cloth: measured on
/// a held frame, flat plateaus up to fourteen pixels wide. Dither costs one
/// hash and turns the step into grain the eye does not resolve.
static inline float3 dither(float2 at) {
    float a = fract(sin(dot(at, float2(12.9898, 78.233))) * 43758.5453);
    float b = fract(sin(dot(at + 17.31, float2(12.9898, 78.233))) * 43758.5453);
    return float3((a + b - 1.0) / 255.0);
}

/// One triangle covering the whole target, for the pass that adds each
/// sub-step's image into the frame being built.
vertex Screen screenVertex(uint id [[vertex_id]]) {
    float2 corner = float2((id << 1) & 2, id & 2);
    Screen out;
    out.position = float4(corner * 2.0 - 1.0, 0.0, 1.0);
    out.uv = float2(corner.x, 1.0 - corner.y);
    return out;
}

/// A camera open for a sixtieth of a second records everything the cloth did
/// during that sixtieth, not where it happened to be at the end. The solver
/// runs four times for every frame the screen shows, so the four positions it
/// passes through are already known; this pass adds each of their images into
/// the frame at a quarter weight, and the sum is what the shutter would have
/// caught. The colours are already multiplied by their coverage, so adding
/// them and adding their coverage keeps the two in step.
fragment float4 accumulateFragment(Screen in [[stage_in]],
                                   texture2d<float> tap [[texture(0)]],
                                   constant float &weight [[buffer(0)]]) {
    constexpr sampler flat(coord::normalized, filter::linear, address::clamp_to_edge);
    return tap.sample(flat, in.uv) * weight;
}

vertex float4 shadowCordVertex(uint id [[vertex_id]],
                               const device Cord *cords [[buffer(0)]],
                               constant Uniforms &uniforms [[buffer(1)]]) {
    Cord v = cords[id];
    float3 toEye = normalize(uniforms.cameraPosition.xyz - v.position);
    float3 sideways = cross(normalize(v.tangent), toEye);
    float reach = length(sideways);
    sideways = reach > 1e-5 ? sideways / reach : float3(1.0, 0.0, 0.0);
    float3 world = v.position + sideways * (v.side * uniforms.cordRadius);
    return uniforms.lightViewProjection * float4(world, 1.0);
}

vertex float4 shadowVertex(uint id [[vertex_id]],
                           const device Vertex *vertices [[buffer(0)]],
                           constant Uniforms &uniforms [[buffer(1)]]) {
    Vertex v = vertices[id];
    // Pushing the cloth along its own normal before the depth is recorded
    // keeps a single thin sheet from shadowing itself everywhere at once.
    return uniforms.lightViewProjection * float4(v.position, 1.0);
}

/// Hashed on the whole-numbered cell rather than on a float that has grown
/// large. A hash that multiplies a coordinate of several hundred by a large
/// constant and keeps the fraction has only a few dozen distinct values left
/// to give, and neighbouring cells start repeating each other, which is how a
/// nap turns into a lattice of dashes.
static float hashCell(int2 cell) {
    uint2 c = uint2(cell + 32768);
    uint h = c.x * 1597334677u ^ c.y * 3812015801u;
    h ^= h >> 15;
    h *= 2246822519u;
    h ^= h >> 13;
    h *= 3266489917u;
    h ^= h >> 16;
    return float(h) * (1.0 / 4294967296.0);
}

static float valueNoise(float2 p) {
    float2 base = floor(p);
    float2 f = p - base;
    f = f * f * (3.0 - 2.0 * f);
    int2 cell = int2(base);
    float a = hashCell(cell);
    float b = hashCell(cell + int2(1, 0));
    float c = hashCell(cell + int2(0, 1));
    float d = hashCell(cell + int2(1, 1));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

/// The Charlie sheen distribution: an inverted lobe that is at its brightest
/// where the half vector lies along the surface rather than along the normal.
static float sheenDistribution(float roughness, float NdotH) {
    float inverse = 1.0 / max(roughness, 0.07);
    float cos2 = NdotH * NdotH;
    float sin2 = max(1.0 - cos2, 0.0078125);
    return (2.0 + inverse) * pow(sin2, inverse * 0.5) / (2.0 * M_PI_F);
}

static float sheenVisibility(float NdotV, float NdotL) {
    return 1.0 / (4.0 * max(NdotL + NdotV - NdotL * NdotV, 1e-4));
}

/// The same curve applied to brightness alone, with the colour carried
/// through unchanged. Run on each channel separately, a filmic curve pulls a
/// saturated colour toward white as it brightens, because the channel that is
/// already high is compressed hardest. On red cloth that is what a
/// photograph does and it is wanted. On gold it is not: the fringe measured
/// two thirds of the way to grey, and gold that is two thirds of the way to
/// grey is brass.
static float3 acesByBrightness(float3 colour) {
    float bright = dot(colour, float3(0.2126, 0.7152, 0.0722));
    if (bright < 1e-5) { return colour; }
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    float toned = saturate((bright * (a * bright + b)) / (bright * (c * bright + d) + e));
    return min(colour * (toned / bright), 1.0);
}

static float3 acesFilmic(float3 colour) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return saturate((colour * (a * colour + b)) / (colour * (c * colour + d) + e));
}

/// A cord is drawn as a flat ribbon turned to face the eye and then shaded as
/// though it were round: the normal is swung across the ribbon so that the
/// light runs down the middle and falls away to both edges.
vertex CordFragment cordVertex(uint id [[vertex_id]],
                               const device Cord *cords [[buffer(0)]],
                               constant Uniforms &uniforms [[buffer(1)]]) {
    Cord v = cords[id];
    float3 toEye = normalize(uniforms.cameraPosition.xyz - v.position);
    float3 tangent = normalize(v.tangent);
    float3 sideways = cross(tangent, toEye);
    float reach = length(sideways);
    sideways = reach > 1e-5 ? sideways / reach : float3(1.0, 0.0, 0.0);
    // Drawn to a point at the tip. A ribbon that simply stops leaves an open
    // square end facing the eye, and a hem of those reads as a row of cut
    // tubes rather than as the ends of cord.
    float taper = 1.0 - 0.85 * smoothstep(0.90, 1.0, v.along);
    float radius = uniforms.cordRadius * (1.0 - 0.24 * v.along) * v.thickness * taper;
    float3 world = v.position + sideways * (v.side * radius);

    float3 facing = normalize(cross(sideways, tangent));
    if (dot(facing, toEye) < 0.0) { facing = -facing; }

    // The frame the cord is round in is handed on and the surface is worked
    // out for each pixel. Worked out at the two edges and interpolated
    // between them, a cord is a flat ribbon with a lit rim, and a twist of
    // seven and a half turns spread over the same two points is noise.
    CordFragment out;
    out.position = uniforms.viewProjection * float4(world, 1.0);
    out.world = world;
    out.sideways = sideways;
    out.facing = facing;
    out.tangent = tangent;
    out.across = clamp(v.side, -1.0, 1.0);
    out.along = v.along;
    out.twist = v.twist;
    out.bounce = v.bounce;
    return out;
}

fragment float4 cordFragment(CordFragment in [[stage_in]],
                             constant Uniforms &uniforms [[buffer(0)]]) {
    float3 tangent = normalize(in.tangent);
    float across = clamp(in.across, -1.0, 1.0);
    float3 round = normalize(in.sideways * across
                             + in.facing * sqrt(max(1.0 - across * across, 0.0)));
    // No two cords are coiled at quite the same pitch, and a rank of them
    // all banded on the same interval reads as a machined part.
    float pitch = uniforms.twistTurns * (0.86 + 0.28 * fract(in.twist * 0.15915494));
    float turn = in.along * pitch * 6.2831853 + in.twist;
    float3 normal = normalize(round + tangent * sin(turn) * 0.40);
    float3 view = normalize(uniforms.cameraPosition.xyz - in.world);
    float3 gold = uniforms.fringeColour.rgb;

    float3 directions[3] = { uniforms.keyDirection.xyz,
                             uniforms.fillDirection.xyz,
                             uniforms.rimDirection.xyz };
    float3 colours[3] = { uniforms.keyColour.rgb,
                          uniforms.fillColour.rgb,
                          uniforms.rimColour.rgb };
    float3 colour = float3(0.0);
    const float roughness = 0.24;
    const float alpha = roughness * roughness;
    const float alpha2 = alpha * alpha;
    for (int i = 0; i < 3; ++i) {
        float3 toLight = normalize(directions[i]);
        float3 halfway = normalize(toLight + view);
        float NdotL = saturate(dot(normal, toLight));
        float NdotH = saturate(dot(normal, halfway));
        float denominator = NdotH * NdotH * (alpha2 - 1.0) + 1.0;
        float ggx = alpha2 / max(M_PI_F * denominator * denominator, 1e-5);
        // A metal tints the light it reflects, so the highlight stays gold
        // instead of blowing out to white.
        float3 fresnel = gold + (1.0 - gold)
            * pow(1.0 - saturate(dot(halfway, view)), 5.0);
        colour += colours[i] * NdotL * (gold * 0.10 + fresnel * ggx * 0.62);
    }
    float hemisphere = normal.y * 0.5 + 0.5;
    float3 ambient = mix(uniforms.groundColour.rgb, uniforms.skyColour.rgb, hemisphere);
    // Half a square metre of red cloth hangs immediately above these cords,
    // and everything that bounces off it arrives red. The cords nearest the
    // hem take the most of it, and each cord takes it for a different part of
    // its length: level across every strand it draws one hard orange stripe
    // straight across the hem.
    float nearCloth = saturate(1.0 - in.along * (1.7 / in.bounce));
    // Light arriving from somewhere other than a lamp cannot on its own take
    // gold to white. Held together under one ceiling it keeps its colour, and
    // the highlight above is still free to clip.
    float3 gathered = gold * (ambient * 0.48 + uniforms.clothColour.rgb * nearCloth * 0.45);
    colour += min(gathered, gold * 0.92);
    // Darker in the trough of the twist than on its crest, and every so often
    // a turn comes round square to the light and throws a point of it. Wire
    // wound onto a core leaves a shallow groove, not a gap: taken deeper the
    // cord comes apart into a row of beads.
    // Faded as a turn of the wire shrinks toward the size of a pixel. Seven
    // pixels to a turn on a cord seven pixels wide, sampled once a pixel, is
    // past what can be drawn: measured along one strand it swung ninety
    // levels with single-pixel spikes, so the cord read as a dashed ladder
    // and would have crawled as the banner moved. The nap and the satin
    // stitch are both guarded this way; the wire was not.
    float turnCell = fwidth(turn);
    float twistFade = saturate(1.5 - turnCell * 1.1);
    colour *= 1.0 - (0.14 - 0.14 * (0.5 + 0.5 * cos(turn))) * twistFade;
    // Broad. A narrow spike on a cord nine pixels wide, repeating every
    // twenty-two, separates it into a row of links: measured, an eighty-eight
    // level swing along a strand whose whole width is nine pixels. Wire wound
    // onto a core gives fine diagonal ribbing, not beading.
    float glint = pow(saturate(0.5 + 0.5 * cos(turn - 0.7)), 3.5);
    colour += gold * glint * (0.09 * twistFade);
    // The cut end of a bullion cord is duller than its length.
    colour *= mix(1.0, 0.74, smoothstep(0.84, 1.0, in.along));
    colour *= uniforms.exposure;
    float3 shown = acesByBrightness(colour);
    return float4(shown + dither(in.position.xy), 1.0);
}

fragment float4 bannerFragment(Fragment in [[stage_in]],
                               bool frontFacing [[front_facing]],
                               constant Uniforms &uniforms [[buffer(0)]],
                               texture2d<float> inkTexture [[texture(0)]],
                               depth2d<float> shadowMap [[texture(1)]]) {
    constexpr sampler inkSampler(coord::normalized, filter::linear,
                                 mip_filter::linear, max_anisotropy(8),
                                 address::clamp_to_edge);
    constexpr sampler shadowSampler(coord::normalized, filter::linear,
                                    address::clamp_to_edge,
                                    compare_func::less_equal);

    float3 geometric = normalize(in.normal) * (frontFacing ? 1.0 : -1.0);
    float3 tangent = normalize(in.tangent - geometric * dot(in.tangent, geometric));
    float3 bitangent = cross(geometric, tangent);

    // The pile: a dense fine grain with a coarser drift through it, tilting
    // the normal by a fraction of a degree at a time. It never reads as bumps,
    // only as the softness that keeps velvet from looking like plastic.
    float2 napCoordinate = in.uv * uniforms.napScale;
    // A noise cell narrower than a pixel or two cannot be seen, only aliased,
    // and it crawls from frame to frame while it does so. Where the cloth is
    // turned away or far off, the grain is faded out rather than sampled.
    float napCell = max(fwidth(napCoordinate.x), fwidth(napCoordinate.y));
    float napFade = saturate(1.7 - napCell);
    // The finer of the two octaves has its own fade. It is more than three
    // times the frequency of the coarse one, so on this screen its cells are
    // under three pixels across while the coarse one's are twenty, and faded
    // by the coarse one's measure it was never faded at all. What it gave
    // instead of pile was a pepper of single-pixel speckle through the dark
    // of every fold, which reads as staining on the cloth and crawls as the
    // banner moves.
    float closeFade = saturate(1.7 - napCell * 3.3);
    // A step of one whole cell samples the next cell at the same phase, which
    // is a difference of two unrelated numbers rather than a slope. The step
    // has to stay well inside a cell for the difference to mean anything.
    const float napStep = 0.06;
    float fine = valueNoise(napCoordinate);
    float fineX = valueNoise(napCoordinate + float2(napStep, 0.0));
    float fineY = valueNoise(napCoordinate + float2(0.0, napStep));
    // A second, finer octave. One octave on its own is a field of blobs the
    // size of its own cells, and at any strength worth having it reads as
    // quilting rather than as pile.
    float2 closeCoordinate = napCoordinate * 3.3;
    float close = valueNoise(closeCoordinate);
    float closeX = valueNoise(closeCoordinate + float2(napStep, 0.0));
    float closeY = valueNoise(closeCoordinate + float2(0.0, napStep));
    // Finer and weaker than it was. At thirty-seven cells across the banner
    // and a tenth of the strength, this was five times the fine grain at a
    // scale of about fifty pixels, which is exactly the size that turns the
    // boundary between a lit fold and an unlit one into a blotchy watercolour
    // bleed rather than a roll-off.
    float2 drift = float2(valueNoise(in.uv * 58.0) - 0.5,
                          valueNoise(in.uv * 58.0 + 19.0) - 0.5);
    // Velvet seen from across a room shows no fibre at all. The pile is here
    // to break up the sheen, not to be seen: strong enough and it stops being
    // cloth and becomes a hammered surface.
    float2 slope = float2(fineX - fine, fineY - fine) / napStep;
    float2 closeSlope = float2(closeX - close, closeY - close) / napStep;
    float2 grain = (slope * 0.020 + closeSlope * (0.009 * closeFade) + drift * 0.10)
        * napFade;
    if (!frontFacing) { grain = float2(-grain.y, grain.x) * 0.62; }
    float3 normal = normalize(geometric + tangent * grain.x + bitangent * grain.y);

    // The message is worked into one face of the cloth. Where the banner has
    // turned its back on the eye, as it does all the way round the roll, what
    // shows is plain velour.
    float faceUp = frontFacing ? 1.0 : 0.0;
    float4 ink = inkTexture.sample(inkSampler, in.uv);
    ink.a *= faceUp;
    // Thread sits proud of the cloth it is worked into. The slope is measured
    // across texels rather than across pixels: a screen-space derivative at a
    // glyph edge runs to nearly one over a pixel, and multiplying that by any
    // useful amount swings the normal through most of a right angle and puts
    // a white rim round every letter. It would also change strength as the
    // banner came nearer.
    // The red channel carries how high the thread stands, which is the
    // coverage blurred by about half a stroke, so its slope is felt across the
    // whole width of a stroke rather than only at its outline.
    float2 texel = 4.0 / float2(inkTexture.get_width(), inkTexture.get_height());
    float rightInk = inkTexture.sample(inkSampler, in.uv + float2(texel.x, 0.0)).r;
    float leftInk = inkTexture.sample(inkSampler, in.uv - float2(texel.x, 0.0)).r;
    float downInk = inkTexture.sample(inkSampler, in.uv + float2(0.0, texel.y)).r;
    float upInk = inkTexture.sample(inkSampler, in.uv - float2(0.0, texel.y)).r;
    float2 inkFall = float2(rightInk - leftInk, downInk - upInk);
    // Kept shallow. Taken high, the blurred coverage becomes a sharp ridge
    // running down the skeleton of each letterform, so every bowl carries an
    // X where its strokes meet and the whole word reads as inflated foil
    // rather than as thread lying on cloth.
    float2 inkSlope = inkFall * (0.48 * faceUp);
    normal = normalize(normal + tangent * inkSlope.x + bitangent * inkSlope.y);

    // Satin stitch runs across a stroke, not along it, and the direction it
    // runs in is the way the height field falls: straight out of the middle
    // of the stroke towards its edge. That direction is also the axis its
    // sheen is drawn out along, which is what tells thread from paint. The
    // stitches themselves are laid in a row up the stroke, close enough
    // together to read as a ribbed sheen rather than as separate stitches.
    // Down the middle of every stroke the height field stops falling either
    // way, so its slope goes to nothing and the direction taken from it turns
    // to noise. Read straight, that put a hard crease along the spine of each
    // letter, an X across every bowl, and a scatter of red speckle through
    // the flat of the strokes. The stitch is now only asserted where there is
    // a slope to take it from, and fades out along the spine, which is where
    // real satin stitch meets itself anyway.
    float bite = length(inkFall);
    float2 stitchWay = bite > 1e-5 ? inkFall / bite : float2(0.0, 1.0);
    float certain = smoothstep(0.0060, 0.030, bite);
    float3 stitchAxis = normalize(tangent * stitchWay.x + bitangent * stitchWay.y);
    float alongStroke = dot(in.uv * float2(uniforms.stitchScale,
                                           uniforms.stitchScale * 0.42),
                            float2(-stitchWay.y, stitchWay.x));
    // And faded out wherever a stitch has shrunk toward the size of a pixel,
    // which is what happens along a fold and around the roll. A pattern at
    // the pixel scale cannot be seen, only aliased, and it crawls while it
    // does so.
    float ribCell = fwidth(alongStroke);
    float ribs = sin(alongStroke * 6.2831853) * certain * saturate(1.25 - ribCell * 2.4);
    // Weighted by how thick the stroke is. Satin stitch does run across a
    // stroke rather than along it, so rungs are right, but a rung across a
    // rule seven pixels wide at the strength a headline letter wants turns
    // the rule into a row of beads. The blurred height stands high in the
    // middle of a thick stroke and never gets there in a thin one, so it is
    // already a measure of thickness.
    float thick = saturate(ink.r * 1.35);
    normal = normalize(normal + stitchAxis * (ribs * 0.30 * ink.a * thick * faceUp));

    float3 view = normalize(uniforms.cameraPosition.xyz - in.world);
    float NdotV = saturate(dot(normal, view));

    // Thread laid on pile sits proud of it and shades the cloth immediately
    // around itself. Without that the letters float rather than sit in.
    float skirt = saturate(ink.r * 1.5) * (1.0 - ink.a) * faceUp;
    float3 cloth = uniforms.clothColour.rgb * mix(1.0, 0.58, skirt);
    // The back of a pile fabric is not the front without the printing on it.
    // The pile stands on one side only; the other is the woven ground that
    // carries it, which is flatter, greyer and a good deal duller, and the
    // dye that soaks through from the face leaves a faint ghost of the
    // artwork on it. Given the same colour, nap and sheen as the face, the
    // roll came out looking like a painted dowel rather than like cloth
    // wound the printed side inward.
    float behind = 1.0 - faceUp;
    float4 through = inkTexture.sample(inkSampler, in.uv);
    float3 backing = mix(uniforms.clothColour.rgb * 0.80,
                         float3(dot(uniforms.clothColour.rgb, float3(0.31, 0.55, 0.14))),
                         0.30);
    backing *= mix(1.0, 0.80, saturate(through.a * 0.55 + through.r * 0.20));
    cloth = mix(cloth, backing, behind);
    float3 albedo = mix(cloth, uniforms.inkColour.rgb, ink.a);
    float couching = smoothstep(0.30, 0.46, ink.r) * (1.0 - smoothstep(0.58, 0.74, ink.r));
    albedo *= mix(1.0, 0.74, couching * faceUp);
    float3 sheenTint = mix(uniforms.sheenColour.rgb, uniforms.inkSheenColour.rgb, ink.a);
    // A woven ground has no pile to catch the light on, so it keeps far less
    // of the sheen and scatters what it does keep over a wider angle.
    sheenTint *= mix(1.0, 0.46, behind);
    float roughness = mix(uniforms.sheenRoughness, uniforms.sheenRoughness * 0.62, ink.a);
    roughness *= mix(1.0, 1.22, behind);
    // The pile lies at slightly different angles across the bolt, and catches
    // the light unevenly because of it.
    roughness *= 0.86 + 0.28 * fine;
    // Where the pile has been faded out because its cells have shrunk toward
    // the size of a pixel, the surface would otherwise turn glassy: around
    // the roll the cloth measured five to ten times smoother than the same
    // cloth hanging flat, and read as red tubing. Detail that can no longer
    // be drawn as shape is added as roughness instead, which is what it does
    // to the light anyway.
    roughness = min(1.0, roughness + (1.0 - napFade) * 0.30);

    // A banner that lives rolled up comes out with the pile crushed in bands
    // across it, one to each turn of the roll, and they never quite hang out.
    // They are faint, and they are the difference between cloth that has been
    // somewhere and cloth that was made this morning.
    // Spaced by the circumference of the turn that made them, which is not
    // one spacing. The hem is wound first and so lies at the core, where a
    // turn takes up very little cloth and the bands come close together; the
    // heading is wound last, around the outside, where a turn takes up a
    // great deal and they are far apart. The pressure at the core is higher
    // too, so the bands near the hem are the ones that never hang out.
    float wound = in.uv.y * (0.42 + 0.58 * in.uv.y);
    float turns = wound * 6.2831853 * 4.6;
    float crush = sin(turns + 1.27) * 0.5 + 0.5;
    crush *= 0.62 + 0.38 * valueNoise(float2(in.uv.x * 2.6, in.uv.y * 9.0));
    crush *= 0.68 + 0.32 * in.uv.y;
    roughness *= 1.0 - 0.16 * crush;
    normal = normalize(normal + bitangent * (cos(turns + 1.27) * 0.012));

    // A hem: the cloth is doubled over at its edges, so it is darker and
    // stiffer there.
    // The fringe is sewn to the bottom of the cloth and hangs against it, so
    // the last of the cloth is in the cords' shade.
    albedo *= mix(1.0, 0.55, smoothstep(0.955, 1.0, in.uv.y) * faceUp);

    float2 fromEdge = min(in.uv, 1.0 - in.uv);
    float hem = smoothstep(0.0, 0.022, min(fromEdge.x * 2.4, fromEdge.y));
    // A doubled hem is darker than the cloth, but not by so much that it
    // beats the light coming off the cut. Measured, the selvedge was thirteen
    // to nineteen percent darker than the cloth an eighth of an inch inboard
    // of it, on every sample down the right-hand edge.
    albedo *= mix(0.84, 1.0, hem);
    // A cut edge of pile is the brightest thing on a velvet banner: the
    // fibres are seen from the side along the whole of it. Darkening the
    // sheen here as well as the colour turned the one clearest velvet cue on
    // the banner upside down, and the selvedge measured a third darker than
    // the flat panel beside it where it should have been the lightest thing
    // in frame.
    sheenTint *= mix(1.45, 1.0, hem);

    float bend = in.occlusion;
    float4 lightClip = uniforms.lightViewProjection
        * float4(in.world + geometric * 0.0016, 1.0);
    float3 lightNDC = lightClip.xyz / lightClip.w;
    float2 shadowUV = lightNDC.xy * float2(0.5, -0.5) + 0.5;
    float shadow = 1.0;
    if (all(shadowUV > 0.0) && all(shadowUV < 1.0)) {
        // A wider, softer spread of taps. Three by three at one texel leaves
        // the edge of a fold's shadow following the map's own grid, which
        // reads as a staircase climbing across the cloth.
        float total = 0.0;
        float texel = 1.5 / float(shadowMap.get_width());
        for (int y = -2; y <= 2; ++y) {
            for (int x = -2; x <= 2; ++x) {
                float2 at = shadowUV + float2(x, y) * texel;
                total += shadowMap.sample_compare(shadowSampler, at, lightNDC.z);
            }
        }
        shadow = total / 25.0;
    }

    float3 colour = float3(0.0);
    float3 directions[3] = { uniforms.keyDirection.xyz,
                             uniforms.fillDirection.xyz,
                             uniforms.rimDirection.xyz };
    float3 colours[3] = { uniforms.keyColour.rgb,
                          uniforms.fillColour.rgb,
                          uniforms.rimColour.rgb };
    // The fill and the rim reach into a fold no better than the key does.
    float occlusion[3] = { shadow * bend, bend, mix(1.0, bend, 0.7) };

    for (int i = 0; i < 3; ++i) {
        float3 toLight = normalize(directions[i]);
        float3 halfway = normalize(toLight + view);
        float NdotL = dot(normal, toLight);
        float NdotH = saturate(dot(normal, halfway));
        // Light entering the pile scatters sideways before it leaves, so the
        // terminator is soft and reaches round past ninety degrees.
        float wrapped = saturate((NdotL + 0.12) / 1.12);
        float diffuse = wrapped * wrapped * 0.62;
        float sheen = sheenDistribution(roughness, NdotH)
                    * sheenVisibility(NdotV, saturate(NdotL))
                    * saturate(NdotL);
        // Light that went into the pile, turned over once and came back out
        // has passed through the dye twice, so just off the crown of a fold
        // is the most saturated red anywhere on the cloth.
        float shoulder = saturate(1.0 - abs(NdotL - 0.34) * 5.5);
        // Thread is a little cylinder lying on the cloth, so its highlight is
        // a band drawn out along the stitch rather than a spot.
        float alongLight = dot(stitchAxis, toLight);
        float alongView = dot(stitchAxis, view);
        float spread = sqrt(saturate(1.0 - alongLight * alongLight))
                     * sqrt(saturate(1.0 - alongView * alongView))
                     - alongLight * alongView;
        float floss = pow(saturate(spread), 30.0) * ink.a * certain;
        colour += colours[i] * occlusion[i]
                * (albedo * diffuse
                   + sheenTint * sheen
                   + uniforms.inkSheenColour.rgb * floss * 0.34
                   + uniforms.scatterColour.rgb * shoulder * 0.42);
    }

    // Sky above, floor below, and the rim glow that a pile fabric shows even
    // where no lamp reaches it.
    float hemisphere = normal.y * 0.5 + 0.5;
    // The back of the cloth faces the desk, which is not the underside of a
    // room: it is a lit surface a little way behind the banner and it throws
    // a good deal back. Given the same darkness overhead as underfoot, a
    // downward-facing patch of the roll's underside received almost nothing.
    float sees = mix(hemisphere, max(hemisphere, 0.42), behind);
    float3 ambient = mix(uniforms.groundColour.rgb, uniforms.skyColour.rgb, sees);
    // The woven ground takes more of its light from the room than the pile
    // does, because it has no pile to swallow it, and it is never black. With
    // the same share as the face, and with the sheen cut as well, the roll's
    // underside fell below one code value over an eighth of the banner as
    // soon as the flourish turned it away from the key light: a hole cut in
    // the cylinder rather than a shaded side.
    colour += albedo * ambient * mix(bend, max(bend, 0.45), behind)
        * mix(0.35, 0.62, behind);
    // Under the same ceiling as the cloth, and in the same folds. Given a
    // flat unoccluded term at nearly two and a half times the cloth's, the
    // letters brightened by half across a fold where the cloth beneath them
    // brightened threefold, so they read as floating rather than as worked
    // into it.
    colour += uniforms.inkColour.rgb * ambient * ink.a * bend * 0.46;
    // Grazing angles brighten because of where the light is, not only because
    // of where the eye is, so the rim is held down where no light reaches.
    float reached = saturate(dot(normal, normalize(uniforms.keyDirection.xyz)) + 0.35);
    // The one thing that tells velvet from satin at a glance. A pile fabric
    // is dark where it faces you and lights up wherever it turns away, so
    // every fold is outlined and the selvedge glows. A tight lobe on smooth
    // cloth does the opposite: it puts a hard bright band along each crest
    // and leaves the edges dark, which is what this was doing.
    // Held to what an edge can return. On flat cloth this term fires only
    // along the silhouette, which is what is wanted; on a cylinder the whole
    // range of angles is present at once, so a broad band of the roll took
    // the term at full strength and washed out to a pale salmon, measured at
    // half the saturation of the cloth it was made of one second earlier.
    float rim = min(pow(1.0 - NdotV, 1.55), 0.42);
    colour += sheenTint * ambient * rim * bend * reached * 0.86;
    // And the cut itself, which does not care where the lamp is. Along a
    // selvedge every fibre is seen from the side, so it catches light from
    // the whole room; gated behind the key light, as the grazing term above
    // is, the edge away from the lamp stayed dead.
    colour += sheenTint * ambient * ((1.0 - hem) * 0.85);

    colour *= uniforms.exposure;
    float3 shown = mix(acesFilmic(colour), acesByBrightness(colour), ink.a);
    return float4(shown + dither(in.position.xy), 1.0);
}
"""

// MARK: - Renderer

struct BannerUniforms {
    var viewProjection = matrix_identity_float4x4
    var lightViewProjection = matrix_identity_float4x4
    var cameraPosition = SIMD4<Float>(0, 0, 1, 0)
    var keyDirection = SIMD4<Float>(0, 0, 1, 0)
    var keyColour = SIMD4<Float>(1, 1, 1, 0)
    var fillDirection = SIMD4<Float>(0, 0, 1, 0)
    var fillColour = SIMD4<Float>(0, 0, 0, 0)
    var rimDirection = SIMD4<Float>(0, 0, -1, 0)
    var rimColour = SIMD4<Float>(0, 0, 0, 0)
    var skyColour = SIMD4<Float>(0, 0, 0, 0)
    var groundColour = SIMD4<Float>(0, 0, 0, 0)
    var clothColour = SIMD4<Float>(0, 0, 0, 0)
    var sheenColour = SIMD4<Float>(0, 0, 0, 0)
    var inkColour = SIMD4<Float>(0, 0, 0, 0)
    var inkSheenColour = SIMD4<Float>(0, 0, 0, 0)
    var scatterColour = SIMD4<Float>(0, 0, 0, 0)
    var fringeColour = SIMD4<Float>(0, 0, 0, 0)
    var sheenRoughness: Float = 0.55
    var napScale: Float = 220
    /// How many stitches run up a stroke across the whole ink. This has to be
    /// read against the size the banner is drawn at rather than against
    /// itself: at four hundred and thirty the pitch works out at four pixels
    /// both ways, which is close enough to the pixel grid to beat against it,
    /// and an earlier attempt to widen the stitch moved it from just under
    /// three pixels to just over four and fixed nothing. On a vertical rule
    /// seven pixels wide that put a rung every four pixels, a forty percent
    /// swing, so the rule read as a beaded line rather than as gold. At a
    /// hundred and ninety it is nine pixels, which is about what satin stitch
    /// measures on a banner of this size.
    var stitchScale: Float = 265
    var time: Float = 0
    var exposure: Float = 1
    var cordRadius: Float = fringeCordRadius
    var twistTurns: Float = fringeTwistTurns
}

/// The lighting the banner is shown under. A key light off to one side to rake
/// across the folds, a cool fill to keep the shadow side from going black, and
/// a light behind it, which is what a pile fabric most wants: the sheen it is
/// known for is a back-scattered effect.
func bannerLighting(into uniforms: inout BannerUniforms) {
    uniforms.keyDirection = SIMD4<Float>(normalize(SIMD3<Float>(-0.93, 0.30, 0.16)), 0)
    uniforms.keyColour = SIMD4<Float>(1.95, 1.66, 1.40, 0)
    uniforms.fillDirection = SIMD4<Float>(normalize(SIMD3<Float>(0.74, 0.13, 0.66)), 0)
    uniforms.fillColour = SIMD4<Float>(0.26, 0.31, 0.44, 0)
    uniforms.rimDirection = SIMD4<Float>(normalize(SIMD3<Float>(0.18, 0.72, -0.67)), 0)
    uniforms.rimColour = SIMD4<Float>(1.30, 0.80, 0.66, 0)
    uniforms.skyColour = SIMD4<Float>(0.32, 0.35, 0.44, 0)
    uniforms.groundColour = SIMD4<Float>(0.09, 0.06, 0.06, 0)
    // Velour reads far darker than its dye: most of the light that meets it
    // head on never comes back out.
    uniforms.clothColour = SIMD4<Float>(0.315, 0.0190, 0.0295, 0)
    // Pale, not red: the sheen is light off the tips of the fibres, which has
    // hardly been through the dye at all.
    // Pile catches the light on the sides of its fibres, and those fibres
    // are dyed, so what comes back off them is not white. Left near white and
    // spread over the wider lobe the sheen now uses, the crest of every fold
    // washed out to pink.
    uniforms.sheenColour = SIMD4<Float>(0.86, 0.45, 0.41, 0)
    uniforms.scatterColour = SIMD4<Float>(0.55, 0.03, 0.05, 0)
    uniforms.inkColour = SIMD4<Float>(0.55, 0.36, 0.10, 0)
    // Gold keeps its own colour in its highlight, which is the difference
    // between metal and anything painted to look like it.
    uniforms.fringeColour = SIMD4<Float>(0.88, 0.57, 0.14, 0)
    uniforms.inkSheenColour = SIMD4<Float>(1.0, 0.80, 0.38, 0)
    uniforms.exposure = 1.34
}

final class BannerRenderer {
    let device: MTLDevice
    private let queue: MTLCommandQueue
    private let mainPipeline: MTLRenderPipelineState
    private let shadowPipeline: MTLRenderPipelineState
    private let cordPipeline: MTLRenderPipelineState
    private let castPipeline: MTLRenderPipelineState
    private let castCordPipeline: MTLRenderPipelineState
    private let shadowCordPipeline: MTLRenderPipelineState
    private let accumulatePipeline: MTLRenderPipelineState
    private let blurPipeline: MTLRenderPipelineState
    private let shadowCompositePipeline: MTLRenderPipelineState
    /// One set of vertices for each sub-step a frame can be built from. They
    /// cannot share one buffer: the passes for all the sub-steps are put into
    /// a single command buffer that is not handed to the hardware until they
    /// have all been written, so a shared buffer would hold whichever
    /// sub-step was copied in last by the time any of them were drawn, and
    /// every sub-step would draw the same picture. Which is exactly what it
    /// did: the cloth moves as much as a hundred pixels between the first
    /// sub-step and the last, and the four-sub-step frame came out identical
    /// to the one-sub-step frame on all but a thousandth of its pixels.
    private var tapVertexBuffers: [MTLBuffer] = []
    private var tapCordBuffers: [MTLBuffer] = []
    private var castMask: MTLTexture?
    private var castBlurred: MTLTexture?
    private var castDepth: MTLTexture?
    private var sceneTexture: MTLTexture?
    private var cordVertexBuffer: MTLBuffer
    private let cordIndexBuffer: MTLBuffer
    private let cordIndexCount: Int
    private let depthState: MTLDepthStencilState
    /// For the shadow, which must never keep anything else from being drawn.
    private let flatDepthState: MTLDepthStencilState
    private let indexBuffer: MTLBuffer
    private let indexCount: Int
    private var vertexBuffer: MTLBuffer
    /// A second set of everything the processor writes into each frame, and a
    /// count of how many frames may be in the hardware's hands at once. The
    /// processor runs ahead of the hardware by design, and with one set of
    /// buffers it overwrites the vertices of a frame that is still being
    /// drawn. Nothing tracks that: a texture written by one pass and read by
    /// another is ordered for you, but a buffer written by the processor
    /// between one frame and the next is not. What it produces when it goes
    /// wrong is a frame drawn from half of one set of positions and half of
    /// another, which is a tear or a flicker, and it happens only when the
    /// timing lines up.
    private var spareVertexBuffer: MTLBuffer?
    private var spareCordBuffer: MTLBuffer?
    private var ring = 0
    private let framesInFlight = DispatchSemaphore(value: 2)
    private let shadowMap: MTLTexture
    private var multisampleTexture: MTLTexture?
    private var depthTexture: MTLTexture?
    private var inkTexture: MTLTexture?

    private let shadowSize = 2048

    init?(cloth: Cloth, fringe: Fringe, pixelFormat: MTLPixelFormat) {
        guard let device = MTLCreateSystemDefaultDevice(),
              let queue = device.makeCommandQueue() else {
            return nil
        }
        self.device = device
        self.queue = queue

        let library: MTLLibrary
        do {
            library = try device.makeLibrary(source: bannerShaderSource, options: nil)
        } catch {
            return nil
        }
        guard let vertexFunction = library.makeFunction(name: "bannerVertex"),
              let fragmentFunction = library.makeFunction(name: "bannerFragment"),
              let shadowFunction = library.makeFunction(name: "shadowVertex"),
              let cordVertexFunction = library.makeFunction(name: "cordVertex"),
              let cordFragmentFunction = library.makeFunction(name: "cordFragment"),
              let castVertexFunction = library.makeFunction(name: "castVertex"),
              let castCordVertexFunction = library.makeFunction(name: "castCordVertex"),
              let castFragmentFunction = library.makeFunction(name: "castFragment"),
              let shadowCordFunction = library.makeFunction(name: "shadowCordVertex"),
              let screenVertexFunction = library.makeFunction(name: "screenVertex"),
              let blurFunction = library.makeFunction(name: "blurFragment"),
              let shadowCompositeFunction = library.makeFunction(name: "shadowCompositeFragment"),
              let accumulateFunction = library.makeFunction(name: "accumulateFragment") else {
            return nil
        }

        let main = MTLRenderPipelineDescriptor()
        main.vertexFunction = vertexFunction
        main.fragmentFunction = fragmentFunction
        main.colorAttachments[0].pixelFormat = pixelFormat
        main.depthAttachmentPixelFormat = .depth32Float
        main.rasterSampleCount = bannerSampleCount

        let shadow = MTLRenderPipelineDescriptor()
        shadow.vertexFunction = shadowFunction
        shadow.depthAttachmentPixelFormat = .depth32Float
        shadow.rasterSampleCount = 1

        let accumulate = MTLRenderPipelineDescriptor()
        accumulate.vertexFunction = screenVertexFunction
        accumulate.fragmentFunction = accumulateFunction
        accumulate.colorAttachments[0].pixelFormat = pixelFormat
        accumulate.colorAttachments[0].isBlendingEnabled = true
        accumulate.colorAttachments[0].rgbBlendOperation = .add
        accumulate.colorAttachments[0].alphaBlendOperation = .add
        accumulate.colorAttachments[0].sourceRGBBlendFactor = .one
        accumulate.colorAttachments[0].sourceAlphaBlendFactor = .one
        accumulate.colorAttachments[0].destinationRGBBlendFactor = .one
        accumulate.colorAttachments[0].destinationAlphaBlendFactor = .one
        accumulate.rasterSampleCount = 1

        let shadowCord = MTLRenderPipelineDescriptor()
        shadowCord.vertexFunction = shadowCordFunction
        shadowCord.depthAttachmentPixelFormat = .depth32Float
        shadowCord.rasterSampleCount = 1

        // The shape is written straight into a single-channel image, with no
        // blending at all, so that a pixel covered by twenty folds holds the
        // same value as a pixel covered by one.
        func castDescriptor(_ vertexFunction: MTLFunction) -> MTLRenderPipelineDescriptor {
            let cast = MTLRenderPipelineDescriptor()
            cast.vertexFunction = vertexFunction
            cast.fragmentFunction = castFragmentFunction
            cast.colorAttachments[0].pixelFormat = .r8Unorm
            cast.depthAttachmentPixelFormat = .depth32Float
            cast.rasterSampleCount = 1
            return cast
        }

        let blur = MTLRenderPipelineDescriptor()
        blur.vertexFunction = screenVertexFunction
        blur.fragmentFunction = blurFunction
        blur.colorAttachments[0].pixelFormat = .r8Unorm
        blur.rasterSampleCount = 1

        let shadowComposite = MTLRenderPipelineDescriptor()
        shadowComposite.vertexFunction = screenVertexFunction
        shadowComposite.fragmentFunction = shadowCompositeFunction
        shadowComposite.colorAttachments[0].pixelFormat = pixelFormat
        shadowComposite.colorAttachments[0].isBlendingEnabled = true
        shadowComposite.colorAttachments[0].sourceRGBBlendFactor = .sourceAlpha
        shadowComposite.colorAttachments[0].destinationRGBBlendFactor = .oneMinusSourceAlpha
        shadowComposite.colorAttachments[0].sourceAlphaBlendFactor = .one
        shadowComposite.colorAttachments[0].destinationAlphaBlendFactor = .oneMinusSourceAlpha
        shadowComposite.depthAttachmentPixelFormat = .depth32Float
        shadowComposite.rasterSampleCount = bannerSampleCount

        let cord = MTLRenderPipelineDescriptor()
        cord.vertexFunction = cordVertexFunction
        cord.fragmentFunction = cordFragmentFunction
        cord.colorAttachments[0].pixelFormat = pixelFormat
        cord.depthAttachmentPixelFormat = .depth32Float
        cord.rasterSampleCount = bannerSampleCount

        let depth = MTLDepthStencilDescriptor()
        depth.depthCompareFunction = .lessEqual
        depth.isDepthWriteEnabled = true

        // The cast shadow is a flat stand-in for something falling on a
        // surface behind the window, not an object in the scene. Letting it
        // write depth punches holes clean through the cloth: the banner
        // billows towards the eye by more than the shadow is pushed away from
        // it, so a shadow thrown by a fold near the front can end up in front
        // of cloth lying further back, and the cloth is then rejected.
        let flat = MTLDepthStencilDescriptor()
        flat.depthCompareFunction = .always
        flat.isDepthWriteEnabled = false

        let indices = cloth.triangleIndices
        let cordIndices = fringe.triangleIndices
        guard let mainPipeline = try? device.makeRenderPipelineState(descriptor: main),
              let shadowPipeline = try? device.makeRenderPipelineState(descriptor: shadow),
              let cordPipeline = try? device.makeRenderPipelineState(descriptor: cord),
              let castPipeline = try? device.makeRenderPipelineState(
                descriptor: castDescriptor(castVertexFunction)),
              let castCordPipeline = try? device.makeRenderPipelineState(
                descriptor: castDescriptor(castCordVertexFunction)),
              let shadowCordPipeline = try? device.makeRenderPipelineState(
                descriptor: shadowCord),
              let accumulatePipeline = try? device.makeRenderPipelineState(descriptor: accumulate),
              let blurPipeline = try? device.makeRenderPipelineState(descriptor: blur),
              let shadowCompositePipeline
                  = try? device.makeRenderPipelineState(descriptor: shadowComposite),
              let cordIndexBuffer = device.makeBuffer(bytes: cordIndices,
                                                      length: cordIndices.count * 4,
                                                      options: .storageModeShared),
              let cordVertexBuffer = device.makeBuffer(
                length: fringe.vertexCount * MemoryLayout<FringeVertex>.stride,
                options: .storageModeShared),
              let depthState = device.makeDepthStencilState(descriptor: depth),
              let flatDepthState = device.makeDepthStencilState(descriptor: flat),
              let indexBuffer = device.makeBuffer(bytes: indices,
                                                  length: indices.count * 4,
                                                  options: .storageModeShared),
              let vertexBuffer = device.makeBuffer(
                length: cloth.drawnVertexCount * MemoryLayout<ClothVertex>.stride,
                options: .storageModeShared) else {
            return nil
        }
        self.mainPipeline = mainPipeline
        self.shadowPipeline = shadowPipeline
        self.cordPipeline = cordPipeline
        self.castPipeline = castPipeline
        self.castCordPipeline = castCordPipeline
        self.shadowCordPipeline = shadowCordPipeline
        self.accumulatePipeline = accumulatePipeline
        self.blurPipeline = blurPipeline
        self.shadowCompositePipeline = shadowCompositePipeline
        self.cordIndexBuffer = cordIndexBuffer
        self.cordIndexCount = cordIndices.count
        self.cordVertexBuffer = cordVertexBuffer
        self.depthState = depthState
        self.flatDepthState = flatDepthState
        self.indexBuffer = indexBuffer
        self.indexCount = indices.count
        self.vertexBuffer = vertexBuffer
        self.spareVertexBuffer = device.makeBuffer(length: vertexBuffer.length,
                                                   options: .storageModeShared)
        self.spareCordBuffer = device.makeBuffer(length: cordVertexBuffer.length,
                                                 options: .storageModeShared)

        let shadowDescriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: .depth32Float, width: shadowSize, height: shadowSize, mipmapped: false)
        shadowDescriptor.usage = [.renderTarget, .shaderRead]
        shadowDescriptor.storageMode = .private
        guard let shadowMap = device.makeTexture(descriptor: shadowDescriptor) else { return nil }
        self.shadowMap = shadowMap
    }

    /// Puts the message onto the cloth. The image is the alpha of the thread;
    /// where it is opaque the shading swaps the cloth's colour for the ink's.
    func setInk(_ ink: BannerInk) {
        let width = ink.width
        let height = ink.height
        let bytes = ink.bytes
        let descriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: .rgba8Unorm, width: width, height: height, mipmapped: true)
        descriptor.usage = [.shaderRead]
        descriptor.storageMode = .managed
        guard let texture = device.makeTexture(descriptor: descriptor) else { return }
        texture.replace(region: MTLRegionMake2D(0, 0, width, height), mipmapLevel: 0,
                        withBytes: bytes, bytesPerRow: width * 4)
        if let buffer = queue.makeCommandBuffer(), let blit = buffer.makeBlitCommandEncoder() {
            blit.generateMipmaps(for: texture)
            blit.endEncoding()
            buffer.commit()
            buffer.waitUntilCompleted()
        }
        inkTexture = texture
    }

    /// The three small images the shadow is made in: the shape, the shape
    /// blurred across, and the depth the shape is tested against. Half the
    /// width and height of the frame, which is ample for something that is
    /// about to be softened by six pixels anyway.
    private func shadowScratch(for target: MTLTexture) -> (MTLTexture, MTLTexture, MTLTexture)? {
        let width = max(1, target.width / 2), height = max(1, target.height / 2)
        if let mask = castMask, let blurred = castBlurred, let depth = castDepth,
           mask.width == width, mask.height == height {
            return (mask, blurred, depth)
        }
        func make(_ format: MTLPixelFormat) -> MTLTexture? {
            let descriptor = MTLTextureDescriptor.texture2DDescriptor(
                pixelFormat: format, width: width, height: height, mipmapped: false)
            descriptor.usage = [.renderTarget, .shaderRead]
            descriptor.storageMode = .private
            return device.makeTexture(descriptor: descriptor)
        }
        guard let mask = make(.r8Unorm), let blurred = make(.r8Unorm),
              let depth = make(.depth32Float) else { return nil }
        castMask = mask
        castBlurred = blurred
        castDepth = depth
        return (mask, blurred, depth)
    }

    /// Where one sub-step's image lands before it is added into the frame.
    /// Only wanted when a frame is built from more than one of them.
    private func working(for target: MTLTexture) -> MTLTexture? {
        if let scene = sceneTexture,
           scene.width == target.width, scene.height == target.height {
            return scene
        }
        let descriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: target.pixelFormat, width: target.width, height: target.height,
            mipmapped: false)
        descriptor.usage = [.renderTarget, .shaderRead]
        descriptor.storageMode = .private
        guard let scene = device.makeTexture(descriptor: descriptor) else { return nil }
        sceneTexture = scene
        return scene
    }

    private func attachments(for target: MTLTexture) -> (MTLTexture, MTLTexture)? {
        if let colour = multisampleTexture, let depth = depthTexture,
           colour.width == target.width, colour.height == target.height {
            return (colour, depth)
        }
        let colourDescriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: target.pixelFormat, width: target.width, height: target.height,
            mipmapped: false)
        colourDescriptor.textureType = .type2DMultisample
        colourDescriptor.sampleCount = bannerSampleCount
        colourDescriptor.usage = [.renderTarget]
        colourDescriptor.storageMode = .private

        let depthDescriptor = MTLTextureDescriptor.texture2DDescriptor(
            pixelFormat: .depth32Float, width: target.width, height: target.height,
            mipmapped: false)
        depthDescriptor.textureType = .type2DMultisample
        depthDescriptor.sampleCount = bannerSampleCount
        depthDescriptor.usage = [.renderTarget]
        depthDescriptor.storageMode = .private

        guard let colour = device.makeTexture(descriptor: colourDescriptor),
              let depth = device.makeTexture(descriptor: depthDescriptor) else {
            return nil
        }
        multisampleTexture = colour
        depthTexture = depth
        return (colour, depth)
    }

    /// Draws one frame. Each entry in `taps` is the cloth as it stood at one
    /// of the solver's sub-steps within this frame; they are drawn in turn and
    /// averaged, which is the streak a shutter open for the whole frame would
    /// have recorded. One tap is the ordinary case and costs nothing extra:
    /// it is resolved straight into the target.
    func render(taps: [(vertices: [ClothVertex], cords: [FringeVertex])],
                uniforms: BannerUniforms, into target: MTLTexture, present: MTLDrawable?) {
        guard !taps.isEmpty,
              let (colour, depth) = attachments(for: target),
              let ink = inkTexture,
              let buffer = queue.makeCommandBuffer() else {
            return
        }
        // Wait until the hardware has finished with the set of buffers this
        // frame is about to write into.
        framesInFlight.wait()
        buffer.addCompletedHandler { [framesInFlight] _ in framesInFlight.signal() }
        ring = 1 - ring
        let vertexBuffer = ring == 0 ? self.vertexBuffer : (spareVertexBuffer ?? self.vertexBuffer)
        let cordVertexBuffer = ring == 0
            ? self.cordVertexBuffer : (spareCordBuffer ?? self.cordVertexBuffer)

        let averaging = taps.count > 1
        guard let scene = averaging ? working(for: target) : target as MTLTexture? else {
            buffer.commit()
            return
        }
        if averaging && tapVertexBuffers.count < shutterTaps * 2 {
            tapVertexBuffers = []
            tapCordBuffers = []
            for _ in 0..<(shutterTaps * 2) {
                guard let cloth = device.makeBuffer(length: vertexBuffer.length,
                                                    options: .storageModeShared),
                      let cords = device.makeBuffer(length: cordVertexBuffer.length,
                                                    options: .storageModeShared) else { break }
                tapVertexBuffers.append(cloth)
                tapCordBuffers.append(cords)
            }
            if tapVertexBuffers.count < shutterTaps * 2 {
                buffer.commit()
                return
            }
        }

        // Where the cloth ends up, which is what the shadow is worked out
        // from. A shadow softened over twenty-odd pixels does not need to be
        // averaged over the shutter as well, and doing it once rather than
        // once per sub-step is most of what the sub-steps used to cost.
        if let last = taps.last {
            last.vertices.withUnsafeBytes { source in
                vertexBuffer.contents().copyMemory(from: source.baseAddress!,
                                                   byteCount: source.count)
            }
            last.cords.withUnsafeBytes { source in
                cordVertexBuffer.contents().copyMemory(from: source.baseAddress!,
                                                       byteCount: source.count)
            }
        }
        var settings = uniforms

        let shadowPass = MTLRenderPassDescriptor()
        shadowPass.depthAttachment.texture = shadowMap
        shadowPass.depthAttachment.loadAction = .clear
        shadowPass.depthAttachment.storeAction = .store
        shadowPass.depthAttachment.clearDepth = 1
        if let encoder = buffer.makeRenderCommandEncoder(descriptor: shadowPass) {
            encoder.setRenderPipelineState(shadowPipeline)
            encoder.setDepthStencilState(depthState)
            encoder.setCullMode(.none)
            encoder.setFrontFacing(.counterClockwise)
            encoder.setDepthBias(0.00035, slopeScale: 1.6, clamp: 0.004)
            encoder.setVertexBuffer(vertexBuffer, offset: 0, index: 0)
            encoder.setVertexBytes(&settings, length: MemoryLayout<BannerUniforms>.stride, index: 1)
            encoder.drawIndexedPrimitives(type: .triangle, indexCount: indexCount,
                                          indexType: .uint32, indexBuffer: indexBuffer,
                                          indexBufferOffset: 0)
            encoder.setRenderPipelineState(shadowCordPipeline)
            encoder.setVertexBuffer(cordVertexBuffer, offset: 0, index: 0)
            encoder.setVertexBytes(&settings, length: MemoryLayout<BannerUniforms>.stride, index: 1)
            encoder.drawIndexedPrimitives(type: .triangle, indexCount: cordIndexCount,
                                          indexType: .uint32, indexBuffer: cordIndexBuffer,
                                          indexBufferOffset: 0)
            encoder.endEncoding()
        }

        // The shape of the shadow, drawn once, depth tested so that nothing
        // hidden inside the roll leaves a mark on the wall.
        if let (mask, blurred, maskDepth) = shadowScratch(for: target) {
            let shape = MTLRenderPassDescriptor()
            shape.colorAttachments[0].texture = mask
            shape.colorAttachments[0].loadAction = .clear
            shape.colorAttachments[0].storeAction = .store
            shape.colorAttachments[0].clearColor = MTLClearColor(red: 0, green: 0,
                                                                 blue: 0, alpha: 0)
            shape.depthAttachment.texture = maskDepth
            shape.depthAttachment.loadAction = .clear
            shape.depthAttachment.storeAction = .dontCare
            shape.depthAttachment.clearDepth = 1
            if let encoder = buffer.makeRenderCommandEncoder(descriptor: shape) {
                encoder.setDepthStencilState(depthState)
                encoder.setCullMode(.none)
                encoder.setFrontFacing(.counterClockwise)
                encoder.setRenderPipelineState(castPipeline)
                encoder.setVertexBuffer(vertexBuffer, offset: 0, index: 0)
                encoder.setVertexBytes(&settings, length: MemoryLayout<BannerUniforms>.stride,
                                       index: 1)
                encoder.drawIndexedPrimitives(type: .triangle, indexCount: indexCount,
                                              indexType: .uint32, indexBuffer: indexBuffer,
                                              indexBufferOffset: 0)
                encoder.setRenderPipelineState(castCordPipeline)
                encoder.setVertexBuffer(cordVertexBuffer, offset: 0, index: 0)
                encoder.setVertexBytes(&settings, length: MemoryLayout<BannerUniforms>.stride,
                                       index: 1)
                encoder.drawIndexedPrimitives(type: .triangle, indexCount: cordIndexCount,
                                              indexType: .uint32, indexBuffer: cordIndexBuffer,
                                              indexBufferOffset: 0)
                encoder.endEncoding()
            }
            // Across into one image, then back down into the other.
            let spread = Float(castSoftness)
            for (source, into, step) in [
                (mask, blurred, SIMD2<Float>(spread / Float(mask.width), 0)),
                (blurred, mask, SIMD2<Float>(0, spread / Float(mask.height))),
            ] {
                var along = step
                let smooth = MTLRenderPassDescriptor()
                smooth.colorAttachments[0].texture = into
                smooth.colorAttachments[0].loadAction = .dontCare
                smooth.colorAttachments[0].storeAction = .store
                if let encoder = buffer.makeRenderCommandEncoder(descriptor: smooth) {
                    encoder.setRenderPipelineState(blurPipeline)
                    encoder.setFragmentTexture(source, index: 0)
                    encoder.setFragmentBytes(&along, length: MemoryLayout<SIMD2<Float>>.stride,
                                             index: 0)
                    encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
                    encoder.endEncoding()
                }
            }
        }

        for (tap, state) in taps.enumerated() {
        // Each sub-step draws from its own copy of the vertices. Sharing one
        // buffer means the hardware sees only whichever was written last.
        let slot = ring * shutterTaps + tap
        let clothTap = averaging ? tapVertexBuffers[slot] : vertexBuffer
        let cordTap = averaging ? tapCordBuffers[slot] : cordVertexBuffer
        if averaging {
            state.vertices.withUnsafeBytes { source in
                clothTap.contents().copyMemory(from: source.baseAddress!,
                                               byteCount: source.count)
            }
            state.cords.withUnsafeBytes { source in
                cordTap.contents().copyMemory(from: source.baseAddress!,
                                              byteCount: source.count)
            }
        }

        let pass = MTLRenderPassDescriptor()
        pass.colorAttachments[0].texture = colour
        pass.colorAttachments[0].resolveTexture = scene
        pass.colorAttachments[0].loadAction = .clear
        pass.colorAttachments[0].storeAction = .multisampleResolve
        pass.colorAttachments[0].clearColor = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 0)
        pass.depthAttachment.texture = depth
        pass.depthAttachment.loadAction = .clear
        pass.depthAttachment.storeAction = .dontCare
        pass.depthAttachment.clearDepth = 1
        if let encoder = buffer.makeRenderCommandEncoder(descriptor: pass) {
            // The shadow first, behind everything: the softened shape laid
            // down once, displaced the way the key light pushes it.
            encoder.setDepthStencilState(flatDepthState)
            encoder.setCullMode(.none)
            encoder.setFrontFacing(.counterClockwise)
            if let mask = castMask {
                // Both components are fractions of the frame's width, so the
                // downward one has to be turned into a fraction of its height
                // before it can be used as a texture offset. Used directly it
                // put the shadow eight degrees below the horizontal on a frame
                // more than twice as wide as it is tall, where the key light
                // says eighteen.
                let tall = Float(target.width) / Float(max(1, target.height))
                var placing = SIMD4<Float>(castThrow.x, castThrow.y * tall,
                                           castStrength, 0)
                encoder.setRenderPipelineState(shadowCompositePipeline)
                encoder.setFragmentTexture(mask, index: 0)
                encoder.setFragmentBytes(&placing, length: MemoryLayout<SIMD4<Float>>.stride,
                                         index: 0)
                encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
            }

            encoder.setDepthStencilState(depthState)
            encoder.setRenderPipelineState(mainPipeline)
            encoder.setVertexBuffer(clothTap, offset: 0, index: 0)
            encoder.setVertexBytes(&settings, length: MemoryLayout<BannerUniforms>.stride, index: 1)
            encoder.setFragmentBytes(&settings, length: MemoryLayout<BannerUniforms>.stride, index: 0)
            encoder.setFragmentTexture(ink, index: 0)
            encoder.setFragmentTexture(shadowMap, index: 1)
            encoder.drawIndexedPrimitives(type: .triangle, indexCount: indexCount,
                                          indexType: .uint32, indexBuffer: indexBuffer,
                                          indexBufferOffset: 0)

            encoder.setRenderPipelineState(cordPipeline)
            encoder.setVertexBuffer(cordTap, offset: 0, index: 0)
            encoder.setVertexBytes(&settings, length: MemoryLayout<BannerUniforms>.stride, index: 1)
            encoder.setFragmentBytes(&settings, length: MemoryLayout<BannerUniforms>.stride,
                                     index: 0)
            encoder.drawIndexedPrimitives(type: .triangle, indexCount: cordIndexCount,
                                          indexType: .uint32, indexBuffer: cordIndexBuffer,
                                          indexBufferOffset: 0)
            encoder.endEncoding()
        }

        // Add this sub-step's image into the frame at its share of the
        // shutter. The first one clears what was there before.
        if averaging {
            var weight = 1 / Float(taps.count)
            let add = MTLRenderPassDescriptor()
            add.colorAttachments[0].texture = target
            add.colorAttachments[0].loadAction = tap == 0 ? .clear : .load
            add.colorAttachments[0].storeAction = .store
            add.colorAttachments[0].clearColor = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 0)
            if let encoder = buffer.makeRenderCommandEncoder(descriptor: add) {
                encoder.setRenderPipelineState(accumulatePipeline)
                encoder.setFragmentTexture(scene, index: 0)
                encoder.setFragmentBytes(&weight, length: MemoryLayout<Float>.stride, index: 0)
                encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
                encoder.endEncoding()
            }
        }
        }

        if let present {
            buffer.present(present)
        }
        buffer.commit()
        if present == nil {
            buffer.waitUntilCompleted()
        }
    }
}

// MARK: - The message, worked into the cloth

/// A face has to be one thread can make. The system serif is drawn for a
/// display: it has hairlines a tenth the width of its stems, and a hairline
/// cannot exist in thread, because the thinnest thing there is is one stitch.
/// Every hairline is therefore a place where the relief has nowhere to
/// happen, and half of every letter goes flat. What is wanted instead is low
/// contrast and blunt slabs, which is the showcard and banner idiom anyway.
func bannerFont(_ names: [String], size: CGFloat, weight: NSFont.Weight) -> NSFont {
    for name in names {
        if let found = NSFont(name: name, size: size) {
            return found
        }
    }
    let base = NSFont.systemFont(ofSize: size, weight: weight)
    if let descriptor = base.fontDescriptor.withDesign(.serif),
       let serif = NSFont(descriptor: descriptor, size: size) {
        return serif
    }
    return base
}

/// An Egyptian for the verdict: heavy bracketed slabs, near enough one width
/// throughout, which is what can be worked in thread.
let bannerHeadlineFaces = ["Rockwell-Bold", "Superclarendon-Bold", "Charter-Black",
                           "AmericanTypewriter-Bold"]
/// Inscriptional capitals for the label, which is what banners and memorials
/// have always used for a line of small letterspaced caps.
let bannerSubjectFaces = ["Copperplate-Bold", "Optima-Bold", "Charter-Bold"]
let bannerDetailFaces = ["Rockwell", "Charter-Roman", "AmericanTypewriter"]

/// One texel of the thread: how much of it covers this point, and how high it
/// stands. The height is the coverage blurred by about half a stroke, because
/// the slope of a hard-edged mask is nothing at all across the middle of a
/// stroke and everything at its outline, which gives a two-pixel rim of relief
/// round a flat interior: a picture frame instead of a rope. Blurred, the
/// slope runs the whole width of the stroke and the whole stroke turns.
struct BannerInk {
    let bytes: [UInt8]
    let width: Int
    let height: Int
}

/// Blurs one channel in place with three box passes, which is close enough to
/// a Gaussian for a height field and costs a few sums per pixel.
private func blurChannel(_ field: inout [Float], width: Int, height: Int, radius: Int) {
    guard radius > 0 else { return }
    var scratch = field
    for _ in 0..<3 {
        for y in 0..<height {
            let row = y * width
            var total: Float = 0
            for x in 0...min(radius, width - 1) { total += field[row + x] }
            for x in 0..<width {
                let count = Float(min(x + radius, width - 1) - max(x - radius, 0) + 1)
                scratch[row + x] = total / count
                let leaving = x - radius
                let arriving = x + radius + 1
                if leaving >= 0 { total -= field[row + leaving] }
                if arriving < width { total += field[row + arriving] }
            }
        }
        for x in 0..<width {
            var total: Float = 0
            for y in 0...min(radius, height - 1) { total += scratch[y * width + x] }
            for y in 0..<height {
                let count = Float(min(y + radius, height - 1) - max(y - radius, 0) + 1)
                field[y * width + x] = total / count
                let leaving = y - radius
                let arriving = y + radius + 1
                if leaving >= 0 { total -= scratch[leaving * width + x] }
                if arriving < height { total += scratch[arriving * width + x] }
            }
        }
    }
}

func bannerInkImage(_ message: BannerMessage, width: Int, height: Int) -> CGImage? {
    guard let context = CGContext(data: nil, width: width, height: height,
                                  bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        return nil
    }
    context.clear(CGRect(x: 0, y: 0, width: width, height: height))
    let previous = NSGraphicsContext.current
    NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: true)
    context.translateBy(x: 0, y: CGFloat(height))
    context.scaleBy(x: 1, y: -1)
    defer { NSGraphicsContext.current = previous }

    let side = CGFloat(width)
    let tall = CGFloat(height)
    let margin = side * 0.085
    let usable = side - margin * 2

    // A banner two and a half times wider than it is tall, with the writing
    // in a column down the middle, leaves most of its width unspoken for.
    // Every real one answers that the same way, and none of them answer it by
    // setting the text wider: a braid set in from the edge, and something
    // deliberate in the corners. Woven braid is a thick line and a thin one
    // together, never a single stroke.
    let inset = tall * 0.072
    let heavy = max(1, tall * 0.0115)
    // Wide enough to survive being squeezed along a fold. A rule a couple of
    // texels across is below what the sampler can hold on to where the cloth
    // turns away, and it breaks into a dotted ladder.
    let light = max(1, tall * 0.0062)
    let apart = tall * 0.0125
    let frame = CGRect(x: inset, y: inset, width: side - inset * 2, height: tall - inset * 2)
    context.setFillColor(NSColor(white: 1, alpha: 0.62).cgColor)

    func band(_ rect: CGRect) { context.fill(rect) }
    for (thickness, offset) in [(heavy, CGFloat(0)), (light, heavy + apart)] {
        let outer = frame.insetBy(dx: -offset, dy: -offset)
        band(CGRect(x: outer.minX, y: outer.minY, width: outer.width, height: thickness))
        band(CGRect(x: outer.minX, y: outer.maxY - thickness,
                    width: outer.width, height: thickness))
        band(CGRect(x: outer.minX, y: outer.minY, width: thickness, height: outer.height))
        band(CGRect(x: outer.maxX - thickness, y: outer.minY,
                    width: thickness, height: outer.height))
    }

    /// A lozenge, which is the one ornament that survives being small and
    /// being seen through the folds of a moving cloth.
    func lozenge(at centre: CGPoint, radius: CGFloat, alpha: CGFloat) {
        func diamond(_ reach: CGFloat, _ opacity: CGFloat) {
            context.setFillColor(NSColor(white: 1, alpha: opacity).cgColor)
            context.beginPath()
            context.move(to: CGPoint(x: centre.x, y: centre.y - reach))
            context.addLine(to: CGPoint(x: centre.x + reach * 0.62, y: centre.y))
            context.addLine(to: CGPoint(x: centre.x, y: centre.y + reach))
            context.addLine(to: CGPoint(x: centre.x - reach * 0.62, y: centre.y))
            context.closePath()
            context.fillPath()
        }
        // Two diamonds, the inner one worked heavier than the outer. A single
        // flat one reads as a plastic stud pressed into the cloth; the pair
        // reads as a boss with a raised centre, which is what the ornament at
        // the corner of a real galloon is.
        diamond(radius, alpha)
        diamond(radius * 0.50, min(1, alpha * 1.4))
    }
    let corner = tall * 0.030
    let reach = heavy + apart + light
    for x in [frame.minX - reach / 2, frame.maxX + reach / 2] {
        for y in [frame.minY - reach / 2, frame.maxY + reach / 2] {
            lozenge(at: CGPoint(x: x, y: y), radius: corner, alpha: 0.74)
        }
    }

    let centred = NSMutableParagraphStyle()
    centred.alignment = .center
    centred.lineBreakMode = .byTruncatingTail

    func draw(_ text: String, font: NSFont, alpha: CGFloat, kern: CGFloat,
              at top: CGFloat) -> CGFloat {
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: NSColor(white: 1, alpha: alpha),
            .kern: kern,
            .paragraphStyle: centred,
        ]
        let line = NSMutableAttributedString(string: text, attributes: attributes)
        // Letterspacing puts a gap after the last letter as well as between
        // the others, and a centred line is centred on its width including
        // that gap, so every letterspaced line sat half a gap left of the
        // middle. Measured against the braid frame the label was eighteen
        // pixels out and the two small lines about fifteen.
        if !text.isEmpty {
            line.removeAttribute(.kern, range: NSRange(location: text.utf16.count - 1, length: 1))
        }
        let bounds = line.boundingRect(with: CGSize(width: usable, height: .greatestFiniteMagnitude),
                                       options: [.usesLineFragmentOrigin])
        line.draw(with: CGRect(x: margin, y: top, width: usable, height: bounds.height),
                  options: [.usesLineFragmentOrigin])
        return bounds.height
    }

    let subjectFont = bannerFont(bannerSubjectFaces, size: tall * 0.050, weight: .medium)
    let detailFont = bannerFont(bannerDetailFaces, size: tall * 0.076, weight: .regular)
    // The verdict is fitted to the cloth rather than set at a fixed size, the
    // way a sign painter sizes letters to the board, so that "1 check failed"
    // and "12 checks failed" arrive with the same weight on the eye.
    var headlineSize = tall * 0.205
    let measured = NSAttributedString(
        string: message.headline,
        attributes: [.font: bannerFont(bannerHeadlineFaces, size: headlineSize, weight: .bold)])
        .size().width
    if measured > 1 {
        headlineSize *= min(max(usable * 0.72 / measured, 0.62), 1.35)
        headlineSize = min(max(headlineSize, tall * 0.135), tall * 0.235)
    }
    let headlineFont = bannerFont(bannerHeadlineFaces, size: headlineSize, weight: .bold)

    // Measured, not estimated. The block was being placed against nominal
    // line heights that all ran larger than the heights the layout actually
    // advances by, against a gap of one figure where the code steps by
    // another, and without the leading inserted between the small lines at
    // all. What came out was a hundred and nine pixels of empty cloth above
    // the writing and a hundred and seventy-six below it.
    func measure(_ text: String, font: NSFont, kern: CGFloat) -> CGFloat {
        let line = NSMutableAttributedString(string: text, attributes: [
            .font: font, .kern: kern, .paragraphStyle: centred,
        ])
        if !text.isEmpty {
            line.removeAttribute(.kern, range: NSRange(location: text.utf16.count - 1, length: 1))
        }
        return line.boundingRect(with: CGSize(width: usable,
                                              height: .greatestFiniteMagnitude),
                                 options: [.usesLineFragmentOrigin]).height
    }
    let visible = Array(message.details.prefix(3))
    let overflow = message.details.count - visible.count
    let subject = message.subject.uppercased().replacingOccurrences(of: "/", with: " / ")
    var total = measure(subject, font: subjectFont, kern: tall * 0.007)
    total += tall * 0.050
    total += measure(message.headline, font: headlineFont, kern: 0)
    total += tall * 0.030
    for (index, detail) in visible.enumerated() {
        if index > 0 { total += tall * 0.014 }
        total += measure(detail, font: detailFont, kern: tall * 0.002)
    }
    if overflow > 0 {
        if !visible.isEmpty { total += tall * 0.014 }
        total += measure("and \(overflow) more", font: detailFont, kern: tall * 0.002)
    }

    // Just below the middle. Above the middle the block left two and a half
    // times as much empty cloth below it as above, and the top of the banner
    // is partly behind the menu bar in any case, so what reads as centred
    // sits a little lower than the arithmetic centre.
    var cursor = (tall - total) * 0.52
    cursor += draw(subject, font: subjectFont, alpha: 0.60,
                   kern: tall * 0.007, at: cursor)

    // Braid again under the label: a heavy line, a light one, and a lozenge
    // sitting on the join, rather than the stub of a rule it was.
    let ruleWidth = usable * 0.34
    let ruleTop = cursor + tall * 0.016
    context.setFillColor(NSColor(white: 1, alpha: 0.46).cgColor)
    context.fill(CGRect(x: (side - ruleWidth) / 2, y: ruleTop,
                        width: ruleWidth, height: max(1, tall * 0.0055)))
    let thinWidth = ruleWidth * 0.66
    context.setFillColor(NSColor(white: 1, alpha: 0.30).cgColor)
    context.fill(CGRect(x: (side - thinWidth) / 2, y: ruleTop + tall * 0.0105,
                        width: thinWidth, height: max(1, tall * 0.0042)))
    lozenge(at: CGPoint(x: side / 2, y: ruleTop + tall * 0.0035),
            radius: tall * 0.017, alpha: 0.62)
    cursor += tall * 0.050

    cursor += draw(message.headline, font: headlineFont, alpha: 0.96, kern: 0, at: cursor)
    cursor += tall * 0.030

    // Set solid, the descender of the g in one line lands in the ascenders
    // of the next and the pair reads as one clotted block.
    for (index, detail) in visible.enumerated() {
        if index > 0 { cursor += tall * 0.014 }
        cursor += draw(detail, font: detailFont, alpha: 0.80, kern: tall * 0.002, at: cursor)
    }
    if overflow > 0 {
        if !visible.isEmpty { cursor += tall * 0.014 }
        _ = draw("and \(overflow) more", font: detailFont, alpha: 0.58,
                 kern: tall * 0.002, at: cursor)
    }

    return context.makeImage()
}

/// Draws the message and works out the height of the thread from it.
func bannerInk(_ message: BannerMessage, width: Int, height: Int) -> BannerInk? {
    guard let drawn = bannerInkImage(message, width: width, height: height) else { return nil }
    var flat = [UInt8](repeating: 0, count: width * height * 4)
    guard let reader = CGContext(data: &flat, width: width, height: height,
                                 bitsPerComponent: 8, bytesPerRow: width * 4,
                                 space: CGColorSpaceCreateDeviceRGB(),
                                 bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        return nil
    }
    reader.draw(drawn, in: CGRect(x: 0, y: 0, width: width, height: height))

    var coverage = [Float](repeating: 0, count: width * height)
    for i in 0..<(width * height) {
        coverage[i] = Float(flat[i * 4 + 3]) / 255
    }
    var raised = coverage
    blurChannel(&raised, width: width, height: height,
                radius: max(2, Int(Double(height) * 0.013)))

    var bytes = [UInt8](repeating: 0, count: width * height * 4)
    for i in 0..<(width * height) {
        bytes[i * 4] = UInt8(max(0, min(255, raised[i] * 255)))
        bytes[i * 4 + 3] = flat[i * 4 + 3]
    }
    return BannerInk(bytes: bytes, width: width, height: height)
}

// MARK: - The scene, and how it plays out

enum BannerPhase {
    /// Sliding out from behind the menu bar, still rolled.
    case emerging
    /// Held for a beat, then kicked upwards: the breath before the drop.
    case poised
    /// Paying out under gravity.
    case dropping
    /// Hanging: ringing itself out at first, stirring afterwards.
    case hanging
    /// Rolling back up, fastest at the end.
    case furling
    /// The free end whipping round the roll after it lands.
    case flourishing
    /// Going back behind the menu bar.
    case withdrawing
    case finished
}

/// Owns the cloth, the camera and the clock. The world is set up so that the
/// banner is one unit wide and its top edge sits at the origin, which is where
/// the menu bar is.
final class BannerScene {
    let cloth: Cloth
    let fringe: Fringe
    let renderer: BannerRenderer
    private var uniforms = BannerUniforms()
    private var vertices: [ClothVertex] = []
    private var cords: [FringeVertex] = []

    private let bannerHeight: Float
    private let viewHeight: Float
    private let topInset: Float
    private let readingTime: TimeInterval

    private(set) var phase: BannerPhase = .emerging
    private var phaseTime: TimeInterval = 0
    /// Time handed to the scene but not yet spent. Everything that moves is
    /// moved in whole steps of the solver's own length: the choreography, the
    /// cloth, and the fringe together. Letting the choreography run on the
    /// display's clock while the cloth runs on its own leaves the rod moving
    /// during frames when the cloth it holds up cannot, and the cloth then
    /// catches up in a lurch. At a tenth speed that is two or three frames of
    /// lag followed by a jump, over and over.
    private var unspent: TimeInterval = 0
    /// How much cloth has been let off the roll, and how fast it is coming
    /// off. Carried rather than worked out from the phase's clock.
    private var paidOut: Float = 0
    private var payoutSpeed: Float = payoutOpeningSpeed
    /// The cloth as it stood at each of the solver's sub-steps inside the
    /// frame now being drawn. Averaging their images is the streak a camera
    /// would have recorded over the time its shutter was open.
    private var shutter: [(vertices: [ClothVertex], cords: [FringeVertex])] = []
    private var lastHem: Float = 0

    init?(message: BannerMessage, bannerSize: CGSize, viewSize: CGSize,
          pixelFormat: MTLPixelFormat) {
        bannerHeight = Float(bannerSize.height / bannerSize.width)
        viewHeight = Float(viewSize.height / bannerSize.width)
        // Negative on purpose. The top edge of the cloth belongs behind the
        // menu bar, not a little way below it, so it is run up past the top of
        // the window and clipped there. Anything else leaves a band of desktop
        // between the bar and the banner and the banner reads as hovering.
        topInset = Float(bannerSize.height / bannerSize.width) * -0.030
        readingTime = message.readingTime

        cloth = Cloth(columns: clothColumns, rows: clothRows, width: 1, height: bannerHeight)
        fringe = Fringe(strands: fringeStrands, knots: fringeKnots, length: fringeLength)
        guard let renderer = BannerRenderer(cloth: cloth, fringe: fringe,
                                            pixelFormat: pixelFormat) else {
            return nil
        }
        // The pose the animation begins from, set here rather than left to
        // the first step of the solver. A newly built cloth is a flat sheet
        // hanging at its full height from a rod that has not been lifted, and
        // that is what was drawn on any frame that arrived before the solver
        // had taken a step. At full speed the first frame carries a
        // sixtieth of a second, which is four steps, so the choreography had
        // always run by the time anything was drawn and the fault never
        // showed. Holding Shift divides the time by ten, and a six-hundredth
        // of a second is less than the solver's own step, so the first two
        // frames took none at all: the whole banner appeared, fully dropped,
        // for two frames before rolling up out of sight and starting
        // properly.
        cloth.setLift(bannerHiddenLift)
        cloth.releasedLength = cloth.heldLength
        cloth.advance()
        cloth.buildSurface()
        cloth.buildVertices(into: &vertices, smoothing: clothNormalRelaxation)
        fringe.settle()
        fringe.hang(from: cloth)
        fringe.buildVertices(into: &cords)
        self.renderer = renderer

        // Half again as many texels across as the banner has pixels. At one
        // for one every hairline in the braid is a single texel wide before
        // it is minified along a fold, and the mip chain has nothing to work
        // from. Drawing it costs no more than the smaller size did, and it is
        // drawn once.
        let inkWidth = 3072
        let inkHeight = max(64, Int((Double(inkWidth) * bannerSize.height
                                     / bannerSize.width).rounded()))
        if let ink = bannerInk(message, width: inkWidth, height: inkHeight) {
            renderer.setInk(ink)
        }

        bannerLighting(into: &uniforms)
        let aspect = Float(viewSize.width / viewSize.height)
        let fieldOfView: Float = 26 * .pi / 180
        let centre = SIMD3<Float>(0, topInset - viewHeight / 2, 0)
        let distance = (viewHeight / 2) / tan(fieldOfView / 2)
        let eye = centre + SIMD3<Float>(0, 0, distance)
        uniforms.cameraPosition = SIMD4<Float>(eye, 0)
        uniforms.viewProjection = perspectiveMatrix(verticalFieldOfView: fieldOfView,
                                                    aspect: aspect, near: 0.05,
                                                    far: distance + 4)
            * lookAtMatrix(eye: eye, centre: centre, up: SIMD3<Float>(0, 1, 0))

        let toLight = normalize(SIMD3<Float>(-0.93, 0.30, 0.16))
        let lightTarget = SIMD3<Float>(0, -bannerHeight * 0.5, 0)
        let lightEye = lightTarget + toLight * 2.2
        // Fitted to the cloth. A two-by-two frustum over a banner one unit
        // wide and less than half a unit tall spends nine tenths of the map
        // on nothing.
        uniforms.lightViewProjection = orthographicMatrix(width: 1.30, height: 0.86,
                                                          near: 0.1, far: 4.6)
            * lookAtMatrix(eye: lightEye, centre: lightTarget, up: SIMD3<Float>(0, 1, 0))
    }

    private func step(_ to: BannerPhase) {
        phase = to
        phaseTime = 0
        if to == .dropping {
            // Starting from nothing, the first four frames of the drop went
            // into paying out cloth that was already free, so the banner
            // hung still while the phase had supposedly begun.
            paidOut = cloth.heldLength
            payoutSpeed = payoutOpeningSpeed
        }
    }

    func advance(by seconds: TimeInterval) {
        // Only the phases that travel are worth the extra passes. While the
        // banner hangs it should be perfectly sharp, and in slow motion the
        // shutter is short in the same proportion as everything else, so the
        // sub-steps stop arriving on their own.
        let racing: Bool
        switch phase {
        case .emerging, .dropping, .furling, .flourishing, .withdrawing: racing = true
        case .poised, .hanging, .finished: racing = false
        }
        // How far the banner moved over the previous frame, which is a good
        // enough guide to how far it is about to move: nothing on screen
        // changes speed appreciably in a sixtieth of a second.
        let hem = cloth.hemPoint(at: 0.5).y
        let travelled = abs(hem - lastHem)
        lastHem = hem
        let wanted = !racing || travelled < shutterSecondTap ? 1
            : (travelled < shutterFullTaps ? 2 : shutterTaps)
        unspent += seconds
        let step = TimeInterval(clothStep)
        var taken = 0
        shutter.removeAll(keepingCapacity: true)
        while unspent >= step && taken < clothMaximumCatchUpSteps {
            unspent -= step
            taken += 1
            advanceOneStep(step)
            if wanted > 1 && shutter.count < wanted {
                // The first, which always exists. Choosing the last meant
                // that whenever fewer of the solver's steps ran than the
                // frame asked for, which is every fast frame on a display
                // faster than sixty hertz, the test never fired and no
                // sub-step got relaxed normals at all.
                cloth.buildVertices(into: &vertices,
                                    smoothing: shutter.isEmpty ? clothNormalRelaxation : 0)
                fringe.buildVertices(into: &cords)
                shutter.append((vertices, cords))
            }
        }
        // Only when the frame is not already made of sub-steps: the last of
        // those is the current state, and shading a hundred thousand vertices
        // again to arrive at the same place is the single most expensive
        // thing this loop can do.
        if taken > 0 && shutter.count < 2 {
            fringe.hang(from: cloth)
            cloth.buildVertices(into: &vertices, smoothing: clothNormalRelaxation)
            fringe.buildVertices(into: &cords)
        }
    }

    private func advanceOneStep(_ seconds: TimeInterval) {
        phaseTime += seconds
        switch phase {
        case .emerging:
            let t = min(1, phaseTime / emergeDuration)
            // Arriving a shade below where it settles, so that it comes out
            // with weight rather than easing to a halt and waiting.
            let eased = 1 - pow(1 - t, 2.0)
            cloth.setLift(bannerHiddenLift * Float(1 - eased) - Float(eased) * 0.0045)
            cloth.releasedLength = cloth.heldLength
            if phaseTime >= emergeDuration { step(.poised) }
        case .poised:
            // The wind-up. It has to be big enough to read as one or not be
            // there at all: at a fifth of its present size it was twelve
            // pixels of travel spread over a fifth of a second, which after
            // the emerge had already come to a halt read as a hiccup rather
            // than as an intake of breath. It now begins the moment the
            // emerge ends, at the speed the emerge was still carrying, lifts
            // the roll clear, and hands it back moving downward.
            let t = Float(min(1, phaseTime / poiseDuration))
            let rise = Float(sin(Double(t) * Double.pi))
            cloth.setLift(rise * 0.017 - 0.0045 * (1 - t))
            cloth.releasedLength = cloth.heldLength
            if phaseTime >= poiseDuration { step(.dropping) }
        case .dropping:
            // Cloth coming off a roll is not a number being eased between two
            // other numbers, and it does not start from rest: it is already
            // travelling at the speed of the roll's own surface at the moment
            // it is let go. Shaping the phase's time by squaring it gave the
            // payout no rate at all to begin with, so the banner hung
            // trembling for the best part of two tenths of a second, released
            // three percent of itself, and then lurched two hundred pixels in
            // the next eighty milliseconds. A speed is carried instead, and
            // gravity is added to it, which is both simpler and what cloth
            // does.
            cloth.setLift(0)
            cloth.stiffening = 2.6
            paidOut += payoutSpeed * Float(seconds)
            payoutSpeed += (payoutGravity - payoutDrag * payoutSpeed * payoutSpeed)
                * Float(seconds)
            cloth.releasedLength = min(cloth.height, paidOut)
            // The phase ends when the cloth runs out, not when the clock says
            // so. What happens at that moment is the whole point: the hem
            // keeps the speed it had, the cloth above it goes taut, and the
            // banner rings.
            if paidOut >= cloth.height { step(.hanging) }
        case .hanging:
            // Softening back over the first moments of the hang, so the cloth
            // settles into its own drape instead of staying board-stiff.
            cloth.stiffening = max(1.25, 2.6 - Float(phaseTime) * 3.2)
            cloth.releasedLength = cloth.height
            if phaseTime >= readingTime { step(.furling) }
        case .furling:
            // Winding up is not the drop in reverse. A roller takes up cloth
            // faster and faster as its own girth grows, and ends with a snap.
            let t = Float(min(1, phaseTime / furlDuration))
            cloth.releasedLength = cloth.height * (1 - pow(t, 1.5))
            if phaseTime >= furlDuration { step(.flourishing) }
        case .flourishing:
            // A roller taking up the last of its cloth does not stop dead on
            // the last turn; it carries past and settles back. Asking for a
            // little more released length could not express that, because
            // four rows are always held free and the amount being asked for
            // was a fifth of that, so every quantity the roll is built from
            // came out identical to zero and the climax of the animation was
            // ninety milliseconds of frozen frames. What moves here is the
            // angle the cloth is wound at, which is the only thing left that
            // can move once the length has run out, and a couple of rows are
            // let go so that there is a loose end for the whip to travel
            // down.
            let t = Float(min(1, phaseTime / flourishDuration))
            // Out and back, on a curve that flattens far less at the turn
            // than a sine does. It is not the corner an earlier note here
            // claimed: a power of one and a third still has no rate at its
            // peak. What it does have is a much shorter flat, and measured
            // frame by frame the roll covers half as much ground either side
            // of the turn as it does at the ends of the phase rather than
            // none at all.
            let swing = 1 - pow(abs(2 * t - 1), 1.35)
            // The overshoot springs back, but not all the way to where it
            // started. A gesture that returns exactly to its own beginning is
            // a see-saw: measured across the phase the roll travelled thirty
            // pixels down and thirty back for a net three, and finished in a
            // pose that could not be told from the one it began in. A roller
            // that has been carried past its stop settles a little way past
            // where it was.
            cloth.rollSpin = swing * (1 - t * 0.55) * 0.62 + t * rollSettle
            // The roll bobs down out from under the bar as it swallows the
            // last of the cloth, and is drawn back up before it leaves.
            // Letting cloth back out instead did not work: rows wound at the
            // core have to travel out through the roll to reach the air, and
            // for the two or three frames that takes, half the roll is inside
            // itself and the silhouette collapses.
            cloth.setLift(-swing * 0.017)
            cloth.releasedLength = 0
            if phaseTime >= flourishDuration {
                cloth.rollSpin = rollSettle
                step(.withdrawing)
            }
        case .withdrawing:
            // Back behind the menu bar, and already moving when it starts.
            // Coming to a halt and then setting off again reads as the roll
            // running out of energy, sitting down, and thinking better of it,
            // and that is what it did: the furl ended at about 1.4 of a
            // banner width a second and this phase opened at 0.30, so the
            // roll stopped for a frame and then crawled. It now leaves at the
            // speed the flourish hands over at and gains on that the whole
            // way, reaching about five times that speed by the time it is
            // gone. It needs more room than the height the banner is hidden
            // at, so it goes further up than it strictly has to.
            let span = Float(withdrawDuration)
            let rest = (withdrawLift - withdrawSpeed * span) / (span * span)
            let t = Float(phaseTime)
            // The curve is not stopped at the height it was aimed at. The
            // rod is what this moves; the cloth below it follows on its own
            // springs and through the air, and it lags. Stopping the rod at
            // a fixed height and calling the animation over left cloth on
            // the screen at the moment the window was taken away.
            cloth.setLift(withdrawSpeed * t + rest * t * t)
            cloth.releasedLength = 0
            // Over when it has gone, not when the clock says it should have.
            if phaseTime >= withdrawDuration && cloth.lowestPoint >= topInset {
                phase = .finished
            }
        case .finished:
            cloth.releasedLength = 0
        }
        uniforms.time += Float(seconds)
        cloth.advance()
        cloth.buildSurface()
        fringe.hang(from: cloth)
        fringe.advance(hanging: 1 - cloth.cordWindBlend)
    }

    func draw(into target: MTLTexture, present: MTLDrawable?) {
        let taps = shutter.count > 1 ? shutter : [(vertices, cords)]
        renderer.render(taps: taps, uniforms: uniforms, into: target, present: present)
    }
}

// MARK: - The window the banner hangs in

/// A view whose backing layer is the one Metal draws into.
private final class BannerView: NSView {
    let metalLayer = CAMetalLayer()

    override func makeBackingLayer() -> CALayer { metalLayer }
    override var isOpaque: Bool { false }
}

/// Hangs a banner from a status item and takes it away again when it has
/// finished. Nothing about it can be clicked: it is a thing that happens on
/// the screen, not a window to be dealt with.
final class BannerWindowController {
    private var panel: NSPanel?
    private var view: BannerView?
    private var scene: BannerScene?
    private var link: CADisplayLink?
    private var previousTimestamp: CFTimeInterval = 0
    private var whenFinished: (() -> Void)?

    var isShowing: Bool { panel != nil }

    func show(_ message: BannerMessage, from button: NSStatusBarButton?,
              whenFinished: (() -> Void)? = nil) {
        dismiss()
        self.whenFinished = whenFinished

        guard let hostWindow = button?.window,
              let screen = hostWindow.screen ?? NSScreen.main else {
            whenFinished?()
            return
        }
        let anchor = hostWindow.convertToScreen(button!.convert(button!.bounds, to: nil))
        let bounds = screen.frame

        let bannerSize = CGSize(width: (bounds.width * bannerScreenWidthFraction).rounded(),
                                height: (bounds.height * bannerScreenHeightFraction).rounded())
        let windowSize = CGSize(
            width: (bannerSize.width * (1 + bannerWindowSideMargin * 2)).rounded(),
            height: (bannerSize.height * (1 + bannerWindowMargin * 2.4)).rounded())

        // The status item says where along the bar to hang from. How far
        // down the bar reaches is asked of the screen instead: a status item
        // that has only just been made has not been given its place yet, and
        // its own idea of where it is would put the banner off the bottom of
        // the screen.
        let menuBarDepth = max(bounds.maxY - screen.visibleFrame.maxY,
                               NSStatusBar.system.thickness)
        var x = anchor.midX - windowSize.width / 2
        x = min(max(x, bounds.minX), bounds.maxX - windowSize.width)
        let frame = CGRect(x: x, y: bounds.maxY - menuBarDepth - windowSize.height,
                           width: windowSize.width, height: windowSize.height)

        guard let scene = BannerScene(message: message, bannerSize: bannerSize,
                                      viewSize: windowSize,
                                      pixelFormat: .bgra8Unorm_srgb) else {
            whenFinished?()
            return
        }
        self.scene = scene

        let view = BannerView(frame: CGRect(origin: .zero, size: windowSize))
        view.wantsLayer = true
        let scale = screen.backingScaleFactor
        view.metalLayer.device = scene.renderer.device
        view.metalLayer.pixelFormat = .bgra8Unorm_srgb
        view.metalLayer.framebufferOnly = true
        view.metalLayer.isOpaque = false
        view.metalLayer.contentsScale = scale
        view.metalLayer.drawableSize = CGSize(width: windowSize.width * scale,
                                              height: windowSize.height * scale)
        self.view = view

        let panel = NSPanel(contentRect: frame,
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.contentView = view
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
        panel.orderFrontRegardless()
        self.panel = panel

        previousTimestamp = 0
        let link = view.displayLink(target: self, selector: #selector(step(_:)))
        link.add(to: .main, forMode: .common)
        self.link = link
    }

    @objc private func step(_ link: CADisplayLink) {
        guard let scene, let view else { return }
        let now = link.targetTimestamp
        // The first frame has nothing to measure from, and a frame that
        // arrives after a long gap is clamped so the cloth is never asked to
        // cross a large stretch of time in one go.
        let elapsed = previousTimestamp == 0
            ? 1.0 / 60.0
            : min(now - previousTimestamp, 1.0 / 20.0)
        previousTimestamp = now
        let slowly = NSEvent.modifierFlags
            .intersection(.deviceIndependentFlagsMask).contains(.shift)
        scene.advance(by: slowly ? elapsed / bannerSlowMotionFactor : elapsed)
        if let drawable = view.metalLayer.nextDrawable() {
            scene.draw(into: drawable.texture, present: drawable)
        }
        if scene.phase == .finished {
            let finished = whenFinished
            dismiss()
            finished?()
        }
    }

    func dismiss() {
        link?.invalidate()
        link = nil
        panel?.orderOut(nil)
        panel = nil
        view = nil
        scene = nil
        whenFinished = nil
    }
}

// MARK: - What to put on it

/// The banner says which branch, how bad it is, and what broke.
func bannerMessage(subject: String, failing: [String], unlisted: Int) -> BannerMessage {
    let headline: String
    var details = failing
    if failing.isEmpty {
        headline = "The branch is red"
        if unlisted > 0 {
            details = ["\(unlisted) checks, not listed by this token"]
        }
    } else if failing.count == 1 {
        headline = "1 check failed"
    } else {
        headline = "\(failing.count) checks failed"
    }
    return BannerMessage(subject: subject, headline: headline, details: details)
}
