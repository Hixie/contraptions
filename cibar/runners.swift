import AppKit
import SwiftUI

// The runners board: the dashboard's other face, which shows the runners
// that jobs run on, grouped as GitHub would hand jobs to them, each job a
// circle in its workflow's colour, waiting in its pool's queue until a runner
// takes it, then running there until it finishes.

/// A self-hosted runner of an organization, as GitHub's API lists it.
struct Runner: Equatable {
    /// The organization, or the user, whose runner it is.
    var owner: String
    var name: String
    var labels: [String]
    var online: Bool

    /// What tells it apart from every other runner.
    var id: String { "\(owner)/\(name)" }
}

/// One of an organization's larger runners: a kind of runner that GitHub
/// starts for each job, up to `maximum` at once, as GitHub's API lists them.
struct LargerRunner: Equatable {
    var owner: String
    var name: String
    var maximum: Int?
    var cores: Int?
    var memory: Int?
}

/// The labels GitHub gives every self-hosted runner, which say what system it
/// runs, but not which of the organization's jobs are for it.
let systemLabels: Set<String> = ["self-hosted", "linux", "macos", "windows", "x64", "arm64", "arm"]

/// The runner group of the runners GitHub starts for each job.
let gitHubHostedGroup = "GitHub Actions"

/// A number for `text` that is the same each time cibar runs, unlike
/// Swift's hashes: FNV-1a's.
func stableHash(_ text: String) -> UInt64 {
    text.utf8.reduce(0xcbf2_9ce4_8422_2325) { ($0 ^ UInt64($1)) &* 0x100_0000_01b3 }
}

/// What the runners board shows: pools of runners, each a set of runners
/// that the same jobs can run on, with the jobs queued for it, and the
/// runners in boxes, a box for each machine with runners of its own, each
/// runner a slot, or a box for each kind of runner that comes and goes with
/// the jobs it runs.
struct RunnerBoard: Equatable {
    struct Token: Equatable, Identifiable {
        var id: Int64
        var job: String
        /// The repository's name and the workflow's.
        var repositoryName: String
        var workflow: String
        var branch: String
        /// The number of the branch's open pull request, if cibar knows of
        /// one.
        var pullRequest: Int? = nil
        /// The first digits of the commit its run runs.
        var commit = ""
        /// The run's ID, which GitHub gives no other run.
        var runID: String
        var running: Bool
        var since: Date?
        var labels: [String]
        var runner: String?
        var url: URL?

        /// The workflow and which of its branches, as `labs · CI · #12`, or
        /// `labs · CI on main`.
        var workflowTitle: String {
            runTitle(repository: repositoryName, workflow: workflow, pullRequest: pullRequest)
                + (pullRequest == nil ? " on \(branch.abbreviatingHashes)" : "")
        }
    }

    struct Slot: Equatable, Identifiable {
        /// The runner's `Runner.id`, and its name.
        var id: String
        var name: String
        /// The runner's name without its machine's.
        var label: String
        var online: Bool
        var job: Token?
        /// Whether it is a place a job keeps on runners known only by their
        /// number, or that come and go, rather than a runner of its own.
        var place = false
    }

    struct Box: Equatable, Identifiable {
        var id: String
        /// Empty for the only box of a pool, which the pool's title names.
        var title: String
        /// A slot for each runner of a machine, or nil for runners that come
        /// and go with the jobs they run.
        var slots: [Slot]?
        /// The jobs running on runners that come and go, or, beside a
        /// machine's slots, those that say they run on a runner whose slot
        /// another holds; and how many runners that come and go are online
        /// and idle.
        var jobs: [Token] = []
        var idle = 0
        /// How many jobs the box's runners can run at once, when that is
        /// known though the runners are not, as for a larger runner.
        var capacity: Int? = nil

        var tokens: [Token] { (slots ?? []).compactMap(\.job) + jobs }
        /// The box with no jobs in it.
        var emptied: Box {
            var box = self
            box.jobs = []
            box.slots = slots?.map { slot in
                var slot = slot
                slot.job = nil
                return slot
            }
            return box
        }
        var running: Int { tokens.count }
        var offline: Int { slots?.filter { !$0.online }.count ?? 0 }
    }

    struct Pool: Equatable, Identifiable {
        /// In the order the board lists them.
        enum Kind: Int, Comparable {
            case machines, comeAndGo, larger, gitHubHosted
            static func < (a: Self, b: Self) -> Bool { a.rawValue < b.rawValue }
        }

        var id: String
        var kind: Kind
        var title: String
        var detail: String
        var boxes: [Box]
        /// The oldest first.
        var queue: [Token] = []

        var running: Int { boxes.reduce(0) { $0 + $1.running } }
        var waiting: Int { queue.count }
    }

    /// A run with jobs on the board, and how many.
    struct Run: Equatable, Identifiable {
        var id: String
        var jobs: Int
    }

    var pools: [Pool] = []
    /// How the latest jobs to finish ended, so that each leaves the board
    /// showing how.
    var outcomes: [Int64: Outcome] = [:]
    /// The runs of the jobs on the board, those with the most first.
    var runs: [Run] = []
    /// What is wrong, as the dashboard says it.
    var problems: [String] = []

    var tokens: [Token] { pools.flatMap { $0.queue + $0.boxes.flatMap(\.tokens) } }
    var running: Int { pools.reduce(0) { $0 + $1.running } }
    var queued: Int { pools.reduce(0) { $0 + $1.waiting } }
    /// How many runners with slots of their own there are, how many of those
    /// are online and idle, and how many offline, leaving out the places
    /// jobs keep on other runners.
    var slots: Int { runnerSlots.count }
    var idleSlots: Int { runnerSlots.filter { $0.online && $0.job == nil }.count }
    private var runnerSlots: [Slot] { pools.flatMap(\.boxes).flatMap { $0.slots ?? [] }.filter { !$0.place } }
    var offlineSlots: Int { pools.flatMap(\.boxes).reduce(0) { $0 + $1.offline } }
}

extension RunnerBoard {
    /// The board for the jobs queued and running, on `runners`, the
    /// self-hosted runners GitHub lists, and on those the jobs say they run
    /// on, which it may not have listed.
    ///
    /// A runner whose name is one of its labels, or one of its labels, a
    /// dash and a word, as `ci-mac-01-3` is, is on the machine that label
    /// names, and has a slot in its box. Machines that share a label other
    /// than their names are one pool, with every machine that shares one
    /// with them. Other runners not in GitHub's own group are pooled by their
    /// labels, with a box that grows with the jobs on them, as runners made
    /// for each job do. GitHub's own runners are one pool, with a box for
    /// each set of labels jobs ask for. A queued job waits in the pool of the
    /// first online runner that has every label it asks for, or, with none
    /// online, of the first that has; failing that, in the pool of runners
    /// with the labels it asks for, made for it if it asks for a self-hosted
    /// runner; and failing that, for one of GitHub's.
    init(jobs: [Int64: ActiveJob], runners listed: [Runner], larger: [LargerRunner] = [],
         outcomes: [Int64: Outcome], pullRequests: (WorkflowBranch) -> PullRequestInfo? = { _ in nil }) {
        self.outcomes = outcomes
        func token(_ id: Int64, _ job: ActiveJob) -> Token {
            let workflowBranch = job.workflowBranch
            return Token(id: id, job: job.name.abbreviatingHashes,
                         repositoryName: workflowBranch.repositoryName,
                         workflow: workflowBranch.workflow.abbreviatingHashes,
                         branch: workflowBranch.branch, pullRequest: pullRequests(workflowBranch)?.number,
                         commit: String(job.sha.prefix(7)),
                         runID: "\(job.order.run)",
                         running: job.running, since: job.since, labels: job.labels, runner: job.runner,
                         url: URL(string: job.url))
        }
        func isSystem(_ label: String) -> Bool { systemLabels.contains(label.lowercased()) }
        /// The labels that say which jobs a runner takes, rather than what
        /// system it runs, each once however it is spelt, in order.
        func distinctive(_ labels: [String]) -> [String] {
            var seen = Set<String>()
            return labels.filter { !isSystem($0) && seen.insert($0.lowercased()).inserted }
                .sorted { $0.lowercased() < $1.lowercased() }
        }
        /// What tells apart runners by their labels, as GitHub does, whatever
        /// their case.
        func key(_ labels: [String]) -> String {
            distinctive(labels).map { $0.lowercased() }.joined(separator: ", ")
        }
        /// The system labels of runners, as GitHub spells them.
        func system(_ labels: [String]) -> String {
            var seen = Set<String>()
            return labels.filter { isSystem($0) && seen.insert($0.lowercased()).inserted }.joined(separator: " ")
        }
        func joined(_ parts: [String]) -> String {
            parts.filter { !$0.isEmpty }.joined(separator: " · ")
        }
        func selfHosted(_ labels: [String]) -> Bool { labels.contains { $0.lowercased() == "self-hosted" } }
        /// The labels a running job asked for, or, for one that asked for a
        /// runner group instead, as `runs-on: group:` does, that group.
        func asked(_ job: ActiveJob) -> [String] {
            job.labels.isEmpty ? job.runnerGroup.map { ["runner group \($0)"] } ?? [] : job.labels
        }
        /// Whether a running job runs on one of GitHub's own runners, as its
        /// runner's group or name says, or, for a job that does not yet say
        /// where it runs, as GitHub does not for a moment after it starts,
        /// as its labels do.
        func isGitHubHosted(_ job: ActiveJob) -> Bool {
            job.runnerGroup == gitHubHostedGroup || job.runner?.hasPrefix("\(gitHubHostedGroup) ") == true
                || (job.runnerGroup == nil && job.runner == nil && !selfHosted(job.labels))
        }

        // Every runner GitHub lists, and every other a job runs on, each
        // told apart by its owner and its name.
        let active = jobs.sorted { $0.key < $1.key }
        var runners = listed
        var known = Set(listed.map(\.id))
        /// The larger runner a job runs on, or, queued, asks for: the one
        /// its runner is named after, or that it names, the longest such.
        func largerRunner(_ job: ActiveJob) -> LargerRunner? {
            let runner = job.runner?.lowercased() ?? ""
            let labels = Set(job.labels.map { $0.lowercased() })
            guard !selfHosted(job.labels) else { return nil }
            return larger.filter { candidate in
                let name = candidate.name.lowercased()
                return candidate.owner == job.workflowBranch.owner
                    && (labels.contains(name) || runner == name || runner.hasPrefix(name + "-"))
            }.max { $0.name.count < $1.name.count }
        }
        /// The pool of a larger runner, made if there is none yet: one box,
        /// with a place for each job it can run at once.
        var pools: [String: Pool] = [:]
        func largerPool(_ runner: LargerRunner) -> String {
            let id = "larger \(runner.owner)/\(runner.name.lowercased())"
            if pools[id] == nil {
                let size = [runner.cores.map { "\($0) cores" }, runner.memory.map { "\($0) GB" }]
                    .compactMap { $0 }.joined(separator: ", ")
                pools[id] = Pool(id: id, kind: .larger, title: runner.name,
                                 detail: joined(["GitHub-hosted", size]),
                                 boxes: [Box(id: runner.name.lowercased(), title: "", slots: nil,
                                             capacity: runner.maximum)])
            }
            return id
        }
        for (_, job) in active where job.running && !isGitHubHosted(job) && largerRunner(job) == nil {
            if let name = job.runner, known.insert("\(job.workflowBranch.owner)/\(name)").inserted {
                runners.append(Runner(owner: job.workflowBranch.owner, name: name, labels: asked(job), online: true))
            }
        }

        // Where each runner is: its pool, its box, and whether it has a slot.
        var places: [String: (pool: String, box: String, slot: Bool)] = [:]
        var machines: [String: (name: String, runners: [Runner])] = [:]
        var comeAndGo: [String: [Runner]] = [:]
        for runner in runners {
            let labels = distinctive(runner.labels)
            let name = runner.name.lowercased()
            // A machine's label says where a runner is, so another must say
            // what it is for.
            let machine = labels.count < 2 ? nil : labels.filter { label in
                let label = label.lowercased()
                return name == label || (name.hasPrefix(label + "-") && !name.dropFirst(label.count + 1).contains("-"))
            }.max { $0.count < $1.count }
            if let machine {
                machines[machine.lowercased(), default: (machine, [])].runners.append(runner)
            } else {
                comeAndGo[key(runner.labels), default: []].append(runner)
            }
        }
        // Machines that share a label other than their names take the same
        // jobs, so they are one pool, with every machine that shares a label
        // with any of them.
        var machinePools: [(roles: Set<String>, members: [(name: String, runners: [Runner])])] = []
        for (machine, members) in machines.sorted(by: { $0.key < $1.key }) {
            let roles = Set(distinctive(members.runners.flatMap(\.labels)).map { $0.lowercased() }).subtracting([machine])
            var pool = (roles: roles, members: [members])
            for index in machinePools.indices.reversed() where !machinePools[index].roles.isDisjoint(with: roles) {
                pool.roles.formUnion(machinePools[index].roles)
                pool.members += machinePools.remove(at: index).members
            }
            machinePools.append(pool)
        }
        for (_, members) in machinePools {
            let members = members.sorted { $0.name < $1.name }
            let names = Set(members.map { $0.name.lowercased() })
            let roles = distinctive(members.flatMap(\.runners).flatMap(\.labels))
                .filter { !names.contains($0.lowercased()) }.joined(separator: ", ")
            let id = "machines \(members.map(\.name).joined(separator: ", "))"
            pools[id] = Pool(
                id: id, kind: .machines, title: Self.title(members.map(\.name)),
                detail: joined([system(members.flatMap(\.runners).flatMap(\.labels)), roles]),
                boxes: members.map { machine, runners in
                    let slots = runners.map { runner in
                        places[runner.id] = (id, machine, true)
                        let label = String(runner.name.dropFirst(machine.count + 1))
                        return Slot(id: runner.id, name: runner.name, label: label.isEmpty ? runner.name : label,
                                    online: runner.online)
                    }
                    return Box(id: machine, title: machine, slots: slots.sorted {
                        (Int($0.label) ?? .max, $0.label, $0.id) < (Int($1.label) ?? .max, $1.label, $1.id)
                    })
                })
        }
        /// The pool of runners with `labels`, made if there is none yet.
        func comeAndGoPool(_ labels: [String]) -> String {
            let key = key(labels)
            let id = "labelled \(key)"
            if pools[id] == nil {
                let title = distinctive(labels).joined(separator: ", ")
                pools[id] = Pool(id: id, kind: .comeAndGo, title: title.isEmpty ? "unlabelled" : title,
                                 detail: system(labels), boxes: [Box(id: key, title: "", slots: nil)])
            }
            return id
        }
        for (_, runners) in comeAndGo {
            let id = comeAndGoPool(runners.flatMap(\.labels))
            for runner in runners {
                places[runner.id] = (id, pools[id]!.boxes[0].id, false)
            }
        }
        let hosted = "github-hosted"
        /// The box of GitHub's runners with a job's labels, made if there is
        /// none yet.
        func hostedBox(_ job: ActiveJob) -> String {
            let title = job.labels.isEmpty ? "any runner" : job.labels.joined(separator: ", ")
            if pools[hosted] == nil {
                pools[hosted] = Pool(id: hosted, kind: .gitHubHosted, title: "GitHub-hosted",
                                     detail: "a fresh runner for each job", boxes: [])
            }
            if !pools[hosted]!.boxes.contains(where: { $0.id == title.lowercased() }) {
                pools[hosted]!.boxes.append(Box(id: title.lowercased(), title: title, slots: nil))
            }
            return title.lowercased()
        }

        // The jobs running, each where it runs: in its runner's slot, or,
        // should two jobs say they run on one runner, as deliveries out of
        // order can, beside the slots.
        func add(_ token: Token, pool: String, box: String) {
            guard let index = pools[pool]?.boxes.firstIndex(where: { $0.id == box }) else { return }
            pools[pool]!.boxes[index].jobs.append(token)
        }
        for (id, job) in active where job.running {
            if isGitHubHosted(job) {
                add(token(id, job), pool: hosted, box: hostedBox(job))
            } else if let name = job.runner, let place = places["\(job.workflowBranch.owner)/\(name)"] {
                if place.slot, let box = pools[place.pool]?.boxes.firstIndex(where: { $0.id == place.box }),
                   let slot = pools[place.pool]!.boxes[box].slots?.firstIndex(where: {
                       $0.id == "\(job.workflowBranch.owner)/\(name)" && $0.job == nil
                   }) {
                    pools[place.pool]!.boxes[box].slots![slot].job = token(id, job)
                } else {
                    add(token(id, job), pool: place.pool, box: place.box)
                }
            } else if let runner = largerRunner(job) {
                let pool = largerPool(runner)
                add(token(id, job), pool: pool, box: pools[pool]!.boxes[0].id)
            } else {
                add(token(id, job), pool: comeAndGoPool(asked(job)), box: key(asked(job)))
            }
        }
        for (id, pool) in pools where pool.kind == .comeAndGo {
            let busy = Set(pool.boxes[0].jobs.compactMap(\.runner))
            pools[id]!.boxes[0].idle = (comeAndGo[pool.boxes[0].id] ?? [])
                .filter { $0.online && !busy.contains($0.name) }.count
        }

        // The jobs queued, each in the queue of the pool that will take it.
        // Online runners come first, as offline ones take nothing.
        let order = pools.values.sorted { ($0.kind, $0.title, $0.id) < ($1.kind, $1.title, $1.id) }.map(\.id)
        let candidates = runners.compactMap { runner in
            places[runner.id].map { (pool: $0.pool, offline: runner.online ? 0 : 1,
                                     rank: order.firstIndex(of: $0.pool) ?? .max, id: runner.id,
                                     labels: Set(runner.labels.map { $0.lowercased() })) }
        }
        .sorted { ($0.offline, $0.rank, $0.id) < ($1.offline, $1.rank, $1.id) }
        for (id, job) in active where !job.running {
            let wanted = Set(job.labels.map { $0.lowercased() })
            let pool: String
            if !wanted.isEmpty, let runner = candidates.first(where: { wanted.isSubset(of: $0.labels) }) {
                pool = runner.pool
            } else if let runner = largerRunner(job) {
                pool = largerPool(runner)
            } else if selfHosted(job.labels) || (!key(job.labels).isEmpty && pools["labelled \(key(job.labels))"] != nil) {
                pool = comeAndGoPool(job.labels)
            } else {
                _ = hostedBox(job)
                pool = hosted
            }
            pools[pool]!.queue.append(token(id, job))
        }

        self.pools = Array(pools.values)
        arrange()
        runs = Dictionary(grouping: tokens, by: \.runID).map { Run(id: $0.key, jobs: $0.value.count) }
            .sorted { ($1.jobs, $0.id) < ($0.jobs, $1.id) }
    }

    /// Puts everything in the order the board shows it: pools by kind and
    /// name, GitHub's boxes by name, and jobs the oldest first.
    private mutating func arrange() {
        func older(_ a: Token, _ b: Token) -> Bool {
            (a.since ?? .distantFuture, a.id) < (b.since ?? .distantFuture, b.id)
        }
        for index in pools.indices {
            pools[index].queue.sort(by: older)
            if pools[index].kind == .gitHubHosted {
                pools[index].boxes.sort { $0.title < $1.title }
            }
            for box in pools[index].boxes.indices {
                pools[index].boxes[box].jobs.sort(by: older)
            }
        }
        pools.sort { ($0.kind, $0.title, $0.id) < ($1.kind, $1.title, $1.id) }
    }

    /// The most places a box is given for runners known only by their
    /// number; one that can run more jobs at once grows with its jobs.
    static let mostSeats = 64

    /// What the board remembers while it shows, so that nothing on it moves
    /// that need not: every pool and box it has shown, in the order it first
    /// showed them, which place each running job has, and how many places
    /// each box has had at most.
    struct Steadiness {
        var pools: [Pool] = []
        /// Each job's place, by the key of its queue or box and its ID.
        var places: [String: Int] = [:]
        var counts: [String: Int] = [:]

        /// Whether a pool or box is remembered that `board` does not have.
        func remembersMore(than board: RunnerBoard) -> Bool {
            pools.contains { pool in
                guard let current = board.pools.first(where: { $0.id == pool.id }) else { return true }
                return pool.boxes.contains { box in !current.boxes.contains { $0.id == box.id } }
            }
        }

        /// Forgets the pools and boxes that `board` does not have.
        mutating func forget(beyond board: RunnerBoard) {
            pools = pools.compactMap { pool in
                guard let current = board.pools.first(where: { $0.id == pool.id }) else { return nil }
                var pool = pool
                pool.boxes = pool.boxes.filter { box in current.boxes.contains { $0.id == box.id } }
                return pool
            }
        }
    }

    /// This board as it was last shown, as far as it can be: pools and boxes
    /// where they were, empty if their jobs have gone, those that are new
    /// after them; and each box of runners that are not its own, a machine's,
    /// given numbered places, as many as a larger runner can run jobs at
    /// once, up to `mostSeats`, or else as many as it has needed, each job
    /// in the place it had, or else the first free one, or else a new one at
    /// the end. A job that finishes leaves its place empty.
    func steadied(_ memory: inout Steadiness) -> RunnerBoard {
        func emptied(_ pool: Pool) -> Pool {
            var pool = pool
            pool.queue = []
            pool.boxes = pool.boxes.map(\.emptied)
            return pool
        }
        // Each pool and box where it was, as it is now if it is still here.
        var remembered = memory.pools
        for pool in pools {
            guard let index = remembered.firstIndex(where: { $0.id == pool.id }) else {
                remembered.append(emptied(pool))
                continue
            }
            var boxes = remembered[index].boxes
            for box in pool.boxes where !boxes.contains(where: { $0.id == box.id }) {
                boxes.append(box.emptied)
            }
            remembered[index] = emptied(pool)
            remembered[index].boxes = boxes
        }
        memory.pools = remembered
        var board = self
        board.pools = remembered.map { past in
            guard var pool = pools.first(where: { $0.id == past.id }) else { return past }
            pool.boxes = past.boxes.map { box in pool.boxes.first { $0.id == box.id } ?? box }
            return pool
        }
        // A place for each job in its queue or box, kept while it is there.
        var places: [String: Int] = [:]
        /// Each of `jobs` in the place it had in the queue or box `key`
        /// names, or else the first free one, the oldest first, or else a
        /// new one at the end, unless there are `fixed` places; and those
        /// that do not fit, the oldest first.
        func place(_ jobs: [Token], in key: String, among count: Int,
                   fixed: Bool) -> (places: [Token?], overflow: [Token]) {
            var taken: [Token?] = Array(repeating: nil, count: count)
            var unplaced: [Token] = []
            for job in jobs {
                if let place = memory.places["\(key)\n\(job.id)"], place < count, taken[place] == nil {
                    taken[place] = job
                } else {
                    unplaced.append(job)
                }
            }
            var overflow: [Token] = []
            for job in unplaced {
                if let place = taken.firstIndex(where: { $0 == nil }) {
                    taken[place] = job
                } else if !fixed {
                    taken.append(job)
                } else {
                    overflow.append(job)
                }
            }
            return (taken, overflow)
        }
        func record(_ taken: [Token?], in key: String) {
            for (place, job) in taken.enumerated() {
                if let job {
                    places["\(key)\n\(job.id)"] = place
                }
            }
        }
        for pool in board.pools.indices {
            for index in board.pools[pool].boxes.indices {
                let box = board.pools[pool].boxes[index]
                guard box.slots == nil else { continue }
                let key = "\(board.pools[pool].id)\n\(box.id)"
                let fixed = box.capacity.flatMap { $0 <= Self.mostSeats ? $0 : nil }
                let count = fixed ?? memory.counts[key] ?? 0
                guard count > 0 || !box.jobs.isEmpty else { continue }
                let (taken, overflow) = place(box.jobs, in: key, among: count, fixed: fixed != nil)
                record(taken, in: key)
                if fixed == nil {
                    memory.counts[key] = taken.count
                }
                let title = board.pools[pool].title
                board.pools[pool].boxes[index].slots = taken.enumerated().map { place, job in
                    Slot(id: "\(key)#\(place)", name: "\(title) place \(place + 1)", label: "", online: true, job: job,
                         place: true)
                }
                board.pools[pool].boxes[index].jobs = overflow
            }
        }
        memory.places = places
        return board
    }

    /// What a pool of machines is called: its machine's name, or what the
    /// names of its machines start with, without the numbers that tell them
    /// apart, or, failing that, all their names.
    static func title(_ machines: [String]) -> String {
        guard let first = machines.first, machines.count > 1 else { return machines.first ?? "" }
        var prefix = Substring(first)
        for machine in machines.dropFirst() {
            prefix = prefix.prefix(zip(prefix, machine).prefix { $0 == $1 }.count)
        }
        let trimmed = prefix.reversed().drop { "-_ 0123456789".contains($0) }
        return trimmed.isEmpty ? machines.joined(separator: ", ") : String(trimmed.reversed())
    }
}

// MARK: - Where everything goes

/// Where everything on the runners board goes, in a view `size` big, with
/// its origin at the top left: every job's circle, every runner's slot,
/// every box and pool, and every line of text. The circles are as big as
/// lets everything fit, up to a size that reads easily.
struct BoardLayout {
    enum Style {
        case heading, detail, count, slot, warning
    }

    struct Text {
        var string: String
        var frame: CGRect
        var style: Style
    }

    /// A pool's queue: the pool's ID, where its jobs heap up, and which
    /// they are, the oldest first.
    struct HeapArea {
        var pool: String
        var frame: CGRect
        var jobs: [Int64]
    }

    static let sizes: [CGFloat] = [16, 14, 12, 10, 8, 6]
    static let inset: CGFloat = 18
    /// Where a heading in a title bar starts, past the close button.
    static let besideClose: CGFloat = 28
    static let poolPadding: CGFloat = 8
    static let boxPadding: CGFloat = 6
    static let boxHeading: CGFloat = 14
    static let poolHeading: CGFloat = 16
    static let spacing: CGFloat = 8
    /// How many places across a box of places is that shares its pool.
    static let placesAcross = 8

    /// The space between places for circles `size` across, and between a
    /// slot's outline and its circle: a quarter and an eighth of the circle,
    /// so that a place is half as big again as its circle at every size.
    static func gap(_ size: CGFloat) -> CGFloat { size / 4 }
    static func rim(_ size: CGFloat) -> CGFloat { size / 8 }

    var size: CGFloat = sizes[0]
    var tokens: [Int64: CGRect] = [:]
    var slots: [(slot: RunnerBoard.Slot, frame: CGRect)] = []
    var boxes: [CGRect] = []
    var pools: [CGRect] = []
    var heaps: [HeapArea] = []
    var texts: [Text] = []
    /// How many pools there was no room for.
    var hidden = 0

    init() {}

    /// Lays out `board`, `size` big, with circles no bigger than `largest`;
    /// with its heading, across the top, in `titleBar`, if it has one, past
    /// and level with the title bar's close button at its left.
    init(board: RunnerBoard, size: CGSize, largest: CGFloat = sizes[0], titleBar: TitleBar = .none) {
        let inset = Self.inset
        let inTitleBar = titleBar.height > 0
        var top: CGFloat = inTitleBar ? titleBar.middle - 8 : 14
        let headingX = inTitleBar ? Self.besideClose : inset
        var summary = "\(board.running) running · \(board.queued) queued"
        if board.slots > 0 {
            summary += " · \(board.idleSlots) of \(counted(board.slots, "machine runner")) idle"
        }
        // Short of the legend's button, at the top right.
        texts.append(Text(string: summary, frame: CGRect(x: headingX, y: top, width: size.width - headingX - inset - 28,
                                                         height: 16),
                          style: .heading))
        if board.offlineSlots > 0 {
            let width = Self.width(summary, .heading)
            texts.append(Text(string: "\(board.offlineSlots) offline",
                              frame: CGRect(x: headingX + width + 8, y: top + 2, width: 120, height: 14), style: .warning))
        }
        top = inTitleBar ? titleBar.height + 6 : top + 22
        for problem in board.problems {
            texts.append(Text(string: problem, frame: CGRect(x: inset, y: top, width: size.width - 2 * inset, height: 14),
                              style: .warning))
            top += 16
        }
        let bottom = size.height - 12
        if board.pools.isEmpty {
            texts.append(Text(string: "Nothing is queued or running, and no runners are known.",
                              frame: CGRect(x: inset, y: top, width: size.width - 2 * inset, height: 14), style: .detail))
        }
        // The biggest circles that let every pool fit, or, when even the
        // smallest do not, as many pools as fit, and how many more there are.
        var fitting: BoardLayout?
        for candidate in Self.sizes where fitting == nil && candidate <= largest {
            var trial = self
            trial.size = candidate
            if trial.place(board.pools, from: top, width: size.width) <= bottom {
                fitting = trial
            }
        }
        if let fitting {
            self = fitting
        } else {
            self.size = Self.sizes.last!
            let room = bottom - 18
            for shown in (0..<board.pools.count).reversed() {
                var trial = self
                if trial.place(Array(board.pools.prefix(shown)), from: top, width: size.width) <= room || shown == 0 {
                    let more = board.pools.count - shown
                    trial.hidden = more
                    trial.texts.append(Text(string: "\(counted(more, "more pool")) not shown",
                                            frame: CGRect(x: inset, y: room + 4, width: size.width - 2 * inset,
                                                          height: 14), style: .warning))
                    self = trial
                    break
                }
            }
        }
    }

    /// Places the pools one under another from `top`, and says where the
    /// last ends.
    private mutating func place(_ pools: [RunnerBoard.Pool], from top: CGFloat, width: CGFloat) -> CGFloat {
        let inset = Self.inset, padding = Self.poolPadding
        let poolWidth = width - 2 * inset
        let laneX = inset + padding
        let laneWidth = floor((poolWidth - 2 * padding) * 0.24)
        let boxX = laneX + laneWidth + padding
        let boxRight = inset + poolWidth - padding
        // Spaces that grow and shrink with the circles: between places, and
        // between a slot's outline and its circle.
        let gap = Self.gap(size), rim = Self.rim(size)
        let cell = size + gap
        let slotCell = size + 2 * rim + gap
        var y = top
        for pool in pools {
            // The heading: the pool's name, what its runners are, and how
            // busy they are.
            let headingY = y + padding
            let capacity = pool.boxes.count == 1 ? pool.boxes[0].capacity : nil
            let busy = "\(pool.waiting) queued · "
                + (capacity.map { "\(pool.running) of \($0) running" } ?? "\(pool.running) running")
            let busyWidth = Self.width(busy, .count)
            let titleWidth = max(0, min(Self.width(pool.title, .heading) + 2, boxRight - busyWidth - 8 - laneX))
            texts.append(Text(string: pool.title, frame: CGRect(x: laneX, y: headingY, width: titleWidth, height: 16),
                              style: .heading))
            texts.append(Text(string: busy, frame: CGRect(x: boxRight - busyWidth - 2, y: headingY + 2,
                                                          width: busyWidth + 2, height: 14), style: .count))
            let detailX = laneX + titleWidth + 6
            texts.append(Text(string: pool.detail, frame: CGRect(x: detailX, y: headingY + 2,
                                                                 width: max(0, boxRight - busyWidth - 8 - detailX),
                                                                 height: 14), style: .detail))

            // The boxes: a machine's as wide as its slots, along a line, onto
            // as many lines as they need; boxes of places that share a pool
            // in columns as many as fit, each taking the next column in
            // turn, one under another, so that one growing moves only those
            // under it.
            let boxesTop = headingY + Self.poolHeading + 4
            let width = boxRight - boxX
            let inner = Self.boxPadding * 2
            let columnar = pool.boxes.count > 1
                && pool.boxes.allSatisfy { ($0.slots?.allSatisfy(\.place) ?? true) && $0.capacity == nil }
            let widest = pool.boxes.map { box in
                box.title.isEmpty ? 0 : Self.width(box.title, .detail) + Self.width(Self.count(box), .count) + 16 + inner
            }.max() ?? 0
            // As many columns as fit at the biggest circles, whatever size
            // they are, so that smaller circles fit more to a line rather
            // than more columns.
            let biggest = Self.sizes[0]
            let columns = columnar
                ? max(1, Int((width + Self.spacing)
                                / (max(CGFloat(Self.placesAcross) * (biggest + 2 * Self.rim(biggest) + Self.gap(biggest))
                                        - Self.gap(biggest) + inner,
                                       widest) + Self.spacing)))
                : 1
            let columnWidth = floor((width - CGFloat(columns - 1) * Self.spacing) / CGFloat(columns))
            var columnBottoms = Array(repeating: boxesTop, count: columns)
            var x = boxX, lineY = boxesTop, lineHeight: CGFloat = 0
            var firstContent: CGFloat?
            for (index, box) in pool.boxes.enumerated() {
                let heading = box.title.isEmpty ? 0 : Self.boxHeading
                let count = Self.count(box)
                let headingWidth = box.title.isEmpty ? 0
                    : Self.width(box.title, .detail) + Self.width(count, .count) + 16
                var boxWidth: CGFloat
                let perLine: Int, items: Int, itemCell: CGFloat
                if let slots = box.slots {
                    itemCell = slotCell
                    items = slots.count + box.jobs.count
                    // A box of places is as wide as its pool or its column,
                    // and grows only by lines, so that nothing beside it
                    // moves.
                    let places = slots.allSatisfy(\.place) && box.capacity == nil
                    boxWidth = columnar ? columnWidth : places ? width
                        : min(width, max(CGFloat(items) * slotCell - gap + inner, headingWidth + inner))
                } else {
                    itemCell = cell
                    items = box.jobs.count
                    boxWidth = columnar ? columnWidth : width
                }
                perLine = max(1, Int((boxWidth - inner + gap) / itemCell))
                let lines = max(1, (items + perLine - 1) / perLine)
                let boxHeight = heading + inner + CGFloat(lines) * itemCell - gap
                let frame: CGRect
                if columnar {
                    let column = index % columns
                    frame = CGRect(x: boxX + CGFloat(column) * (columnWidth + Self.spacing), y: columnBottoms[column],
                                   width: boxWidth, height: boxHeight)
                    columnBottoms[column] = frame.maxY + Self.spacing
                } else {
                    if x > boxX && x + boxWidth > boxRight {
                        x = boxX
                        lineY += lineHeight + Self.spacing
                        lineHeight = 0
                    }
                    frame = CGRect(x: x, y: lineY, width: boxWidth, height: boxHeight)
                    x += boxWidth + Self.spacing
                    lineHeight = max(lineHeight, boxHeight)
                }
                boxes.append(frame)
                if !box.title.isEmpty {
                    let countWidth = Self.width(count, .count)
                    texts.append(Text(string: box.title,
                                      frame: CGRect(x: frame.minX + Self.boxPadding, y: frame.minY + 3,
                                                    width: max(0, frame.width - inner - countWidth - 4), height: 12),
                                      style: .detail))
                    texts.append(Text(string: count,
                                      frame: CGRect(x: frame.maxX - Self.boxPadding - countWidth - 2, y: frame.minY + 3,
                                                    width: countWidth + 2, height: 12),
                                      style: box.offline > 0 ? .warning : .count))
                }
                let contentX = frame.minX + Self.boxPadding, contentY = frame.minY + heading + Self.boxPadding
                firstContent = firstContent ?? contentY
                func origin(_ index: Int) -> CGPoint {
                    CGPoint(x: contentX + CGFloat(index % perLine) * itemCell,
                            y: contentY + CGFloat(index / perLine) * itemCell)
                }
                if let slots = box.slots {
                    for (index, slot) in slots.enumerated() {
                        let slotFrame = CGRect(origin: origin(index), size: CGSize(width: size + 2 * rim,
                                                                                   height: size + 2 * rim))
                        self.slots.append((slot, slotFrame))
                        if let job = slot.job {
                            tokens[job.id] = slotFrame.insetBy(dx: rim, dy: rim)
                        }
                    }
                    for (index, job) in box.jobs.enumerated() {
                        tokens[job.id] = CGRect(origin: origin(slots.count + index),
                                                size: CGSize(width: size + 2 * rim, height: size + 2 * rim))
                            .insetBy(dx: rim, dy: rim)
                    }
                } else {
                    for (index, job) in box.jobs.enumerated() {
                        tokens[job.id] = CGRect(origin: origin(index), size: CGSize(width: size, height: size))
                    }
                    if box.jobs.isEmpty {
                        let empty = box.idle > 0 ? "\(box.idle) idle" : "none running"
                        texts.append(Text(string: empty,
                                          frame: CGRect(x: contentX, y: contentY + (size - 12) / 2,
                                                        width: max(0, frame.width - inner), height: 12),
                                          style: .detail))
                    }
                }
            }
            let boxesBottom = columnar ? (columnBottoms.max() ?? boxesTop) - Self.spacing : lineY + lineHeight

            // The queue: a heap of its jobs, as tall as the runners it feeds
            // and the pool's heading above them, up to the top of the pool,
            // so that however long it grows, nothing below it moves.
            let laneBottom = max(boxesBottom, (firstContent ?? boxesTop) + size)
            heaps.append(HeapArea(pool: pool.id,
                                  frame: CGRect(x: laneX, y: y, width: laneWidth, height: laneBottom - y),
                                  jobs: pool.queue.map(\.id)))
            let bottom = laneBottom + padding
            self.pools.append(CGRect(x: inset, y: y, width: poolWidth, height: bottom - y))
            y = bottom + Self.spacing
        }
        return y - Self.spacing
    }

    /// How busy a box is, in a few characters.
    static func count(_ box: RunnerBoard.Box) -> String {
        guard let slots = box.slots, box.capacity != nil || slots.contains(where: { !$0.place }) else {
            return "\(box.running)"
        }
        return box.offline > 0 ? "\(box.running)/\(slots.count), \(box.offline) offline"
            : "\(box.running)/\(slots.count)"
    }

    static func font(_ style: Style) -> NSFont {
        switch style {
        case .heading: .systemFont(ofSize: 12, weight: .semibold)
        case .detail, .warning: .systemFont(ofSize: 10)
        case .count: .monospacedDigitSystemFont(ofSize: 10, weight: .medium)
        case .slot: .systemFont(ofSize: 7, weight: .medium)
        }
    }

    static func width(_ text: String, _ style: Style) -> CGFloat {
        ceil(textWidth(text, font(style)))
    }

    /// The circle or the slot at `point`, if there is one.
    func token(at point: CGPoint) -> Int64? {
        tokens.first { $0.value.insetBy(dx: -1, dy: -1).contains(point) }?.key
    }

    func slot(at point: CGPoint) -> RunnerBoard.Slot? {
        slots.first { $0.frame.contains(point) }?.slot
    }
}

// MARK: - The view

/// The colours runs are told apart by: the strong ones, then a paler shade
/// of each.
let runColours: [NSColor] = strongRunColours + [
    0xaec7e8, 0xffbb78, 0x98df8a, 0xff9896, 0xc5b0d5, 0xc49c94,
    0xf7b6d2, 0xdbdb8d, 0x9edae5, 0x9c9ede, 0xe7cb94, 0xde9ed6,
].map(colour)

/// The colours a run's ID picks from, before it settles for a paler one that
/// is free: blue, orange, green, red, purple, brown, pink, olive, cyan, navy,
/// gold and plum, each far from the others.
let strongRunColours: [NSColor] = [
    0x1f77b4, 0xff7f0e, 0x2ca02c, 0xd62728, 0x9467bd, 0x8c564b,
    0xe377c2, 0xbcbd22, 0x17becf, 0x393b79, 0xe7ba52, 0x7b4173,
].map(colour)

/// A colour given as its red, green and blue, two hexadecimal digits each.
private func colour(_ rgb: Int) -> NSColor {
    NSColor(srgbRed: CGFloat(rgb >> 16 & 0xff) / 255, green: CGFloat(rgb >> 8 & 0xff) / 255,
            blue: CGFloat(rgb & 0xff) / 255, alpha: 1)
}

/// Which of the `runColours` each run on the board is shown in, by its ID.
/// A run keeps its colour while it is on the board. One that comes onto it
/// takes the strong colour its ID hashes to, or, when another run on the
/// board has that one, the next that none has, while there is one, strong
/// ones first, those with the most jobs choosing first.
struct RunPalette {
    private(set) var colours: [String: Int] = [:]

    mutating func update(for runs: [RunnerBoard.Run]) {
        let present = Set(runs.map(\.id))
        colours = colours.filter { present.contains($0.key) }
        for run in runs where colours[run.id] == nil {
            let taken = Set(colours.values)
            let strong = strongRunColours.count
            let preferred = Int(stableHash(run.id) % UInt64(strong))
            let order = (0..<strong).map { (preferred + $0) % strong } + Array(strong..<runColours.count)
            colours[run.id] = order.first { !taken.contains($0) } ?? preferred
        }
    }

    func colour(_ run: String) -> NSColor {
        runColours[colours[run] ?? Int(stableHash(run) % UInt64(runColours.count))]
    }
}

/// A queue's heap as last worked out: where it was, how big its balls were,
/// from when, and each ball's path in the heap's own coordinates.
struct HeapMotion {
    var frame: CGRect
    var diameter: CGFloat
    var start: CFTimeInterval
    var paths: [Int64: [CGPoint]]
}

/// The runners board, drawn by Core Animation: each job a layer that falls
/// into its queue's heap, is drawn from there to the runner that takes it,
/// and leaves the board marked green, red, or grey, as it passed, failed, or
/// neither: thrown off to the right, bounced off the right side onto the line
/// along the bottom and off its end, or floating up like smoke. Between
/// deliveries, cibar draws nothing.
final class RunnerBoardView: NSView {
    /// What clicking a job does with its page.
    var open: (URL) -> Void = { _ in }

    private(set) var board = RunnerBoard()
    private(set) var placement = BoardLayout()
    /// Whose origin is at the top left, as the layout's is.
    private let canvas = CALayer()
    private let chrome = CALayer()
    private let tokenLayers = CALayer()
    private var shown: [Int64: (token: RunnerBoard.Token, layer: CALayer)] = [:]
    /// Each pool's rounded box, by the pool's ID, which clips the balls of
    /// its queue, in the board's own coordinates, so that a ball is where it
    /// is in it or out of it.
    private var clips: [String: CALayer] = [:]
    private var tokensByID: [Int64: RunnerBoard.Token] = [:]
    /// The jobs on their way off the board, each with its run, whose colour
    /// no other run takes until it has gone.
    private var leaving: [Int64: (layer: CALayer, ring: CALayer?, run: String)] = [:]
    /// Each queue's heap as last worked out, by its pool: where it was, how
    /// big its balls were, from when, and each ball's path, in the heap's
    /// own coordinates, so that a ball can go on from where it is.
    private var heaps: [String: HeapMotion] = [:]
    private var heapStart: CFTimeInterval = 0
    /// The path, on the board, of each ball moving on its own, as to the
    /// runner that took its job, and from when, so that a ball can go on from
    /// where it is, moving as it moves.
    private var flights: [Int64: (start: CFTimeInterval, path: [CGPoint])] = [:]
    /// What time it is, as Core Animation tells it, for the heaps' paths to
    /// start from.
    var clock: () -> CFTimeInterval = CACurrentMediaTime

    /// What the board remembers while it shows, so that nothing moves that
    /// need not, and the biggest its circles may be, which only shrinks.
    private var steadiness = RunnerBoard.Steadiness()
    private var largest = BoardLayout.sizes[0]
    private(set) var palette = RunPalette()
    /// Where the pointer is while it is over the board, and what names what
    /// it points at.
    private var pointer: CGPoint?
    private let bubble = CALayer()
    private let bubbleText = CATextLayer()

    /// How long a circle takes to change size, and a job to leave.
    static let move: CFTimeInterval = 0.7
    static let leave: CFTimeInterval = 1.3

    init() {
        super.init(frame: .zero)
        wantsLayer = true
        // Nothing clips the board, so that a ball that leaves it can be seen
        // going, over the dashboard's bottom row.
        canvas.isGeometryFlipped = true
        canvas.addSublayer(chrome)
        canvas.addSublayer(tokenLayers)
        bubble.isHidden = true
        bubble.cornerRadius = 5
        bubble.borderWidth = 0.5
        bubble.shadowOpacity = 0.2
        bubble.shadowRadius = 4
        bubble.shadowOffset = CGSize(width: 0, height: 2)
        bubble.addSublayer(bubbleText)
        canvas.addSublayer(bubble)
        layer?.addSublayer(canvas)
        addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseMoved, .mouseEnteredAndExited, .activeAlways,
                                                              .inVisibleRect], owner: self))
        legendButton.image = NSImage(systemSymbolName: "paintpalette", accessibilityDescription: "Colours")
        legendButton.isBordered = false
        legendButton.contentTintColor = .secondaryLabelColor
        legendButton.target = self
        legendButton.action = #selector(toggleLegend)
        legendButton.toolTip = "What each colour is"
        legendButton.translatesAutoresizingMaskIntoConstraints = false
        addSubview(legendButton)
        NSLayoutConstraint.activate([
            legendButton.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -BoardLayout.inset),
            legendMiddle,
        ])
    }

    /// A title bar over the board, if it has one, which its heading and the
    /// legend's button go in, level with its close button.
    var titleBar = TitleBar.none {
        didSet {
            guard titleBar != oldValue else { return }
            legendMiddle.constant = titleBar.height > 0 ? titleBar.middle : 22
            show(board, animated: false)
        }
    }
    /// What puts the legend's button level with the heading.
    private lazy var legendMiddle = legendButton.centerYAnchor.constraint(equalTo: topAnchor, constant: 22)

    // MARK: The legend

    /// What each colour on the board is: a window beside the dashboard, to
    /// its right, or, with no room there, to its left, which can be dragged
    /// anywhere, one line for each run, which goes when the board does, or
    /// when the button at the board's top right is clicked again.
    let legendButton = NSButton()
    private(set) var legend: NSPanel?
    private let legendModel = LegendModel()
    /// What the legend says, which tests ask.
    var legendEntries: [LegendEntry] { legendModel.entries }

    @objc func toggleLegend() {
        if legend?.isVisible == true {
            closeLegend()
            return
        }
        guard let window else { return }
        let panel = legend ?? makeLegend()
        legend = panel
        panel.setFrame(legendFrame(beside: window.frame, size: legendSize(),
                                   within: window.screen?.visibleFrame ?? window.frame),
                       display: true)
        window.addChildWindow(panel, ordered: .above)
        panel.orderFront(nil)
    }

    func closeLegend() {
        guard let legend else { return }
        legend.parent?.removeChildWindow(legend)
        legend.orderOut(nil)
    }

    private func makeLegend() -> NSPanel {
        let panel = NSPanel(contentRect: .zero, styleMask: [.nonactivatingPanel, .borderless], backing: .buffered,
                            defer: true)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.isReleasedWhenClosed = false
        panel.isMovableByWindowBackground = true
        let background = NSVisualEffectView()
        background.material = .popover
        background.state = .active
        background.wantsLayer = true
        background.layer?.cornerRadius = 8
        background.layer?.masksToBounds = true
        let content = NSHostingView(rootView: LegendView(model: legendModel))
        content.translatesAutoresizingMaskIntoConstraints = false
        background.addSubview(content)
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: background.leadingAnchor),
            content.trailingAnchor.constraint(equalTo: background.trailingAnchor),
            content.topAnchor.constraint(equalTo: background.topAnchor),
            content.bottomAnchor.constraint(equalTo: background.bottomAnchor),
        ])
        panel.contentView = background
        return panel
    }

    /// How big the legend is for what it lists, no taller than most screens.
    private func legendSize() -> CGSize {
        let fitting = NSHostingController(rootView: LegendLines(model: legendModel)).sizeThatFits(
            in: CGSize(width: LegendView.width, height: .greatestFiniteMagnitude))
        return CGSize(width: LegendView.width, height: min(fitting.height, 420))
    }

    /// Says what each run on the board is, in its colour, in alphabetical
    /// order, and fits the legend to it, if it shows.
    private func describeRuns(_ board: RunnerBoard) {
        let tokens = Dictionary(grouping: board.tokens, by: \.runID)
        legendModel.entries = board.runs.compactMap { run in
            tokens[run.id]?.first.map { token in
                LegendEntry(id: run.id, colour: palette.colour(run.id),
                            title: token.commit.isEmpty ? token.workflowTitle : "\(token.workflowTitle) @ \(token.commit)",
                            jobs: run.jobs)
            }
        }
        .sorted { a, b in
            let order = a.title.localizedStandardCompare(b.title)
            return order == .orderedSame ? a.id < b.id : order == .orderedAscending
        }
        if let legend, legend.isVisible {
            let size = legendSize()
            var frame = legend.frame
            frame.origin.y += frame.height - size.height
            frame.size.height = size.height
            legend.setFrame(frame, display: true)
        }
    }

    override func viewDidHide() {
        super.viewDidHide()
        closeLegend()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { nil }

    /// How many jobs are on the board, not counting those on their way off
    /// it, and the layer of each, which tests ask.
    var tokenCount: Int { shown.count }

    func tokenLayer(_ id: Int64) -> CALayer? { shown[id]?.layer }

    override func layout() {
        super.layout()
        guard canvas.frame != bounds else { return }
        show(board, animated: false)
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        show(board, animated: false)
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        show(board, animated: false)
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    /// A point in the view, as the layout places things.
    private func flipped(_ point: NSPoint) -> CGPoint {
        CGPoint(x: point.x, y: bounds.height - point.y)
    }

    /// Clicking a job opens it; dragging anything else drags the window.
    override func mouseDown(with event: NSEvent) {
        let point = flipped(convert(event.locationInWindow, from: nil))
        if let id = placement.token(at: point), let url = tokensByID[id]?.url {
            open(url)
        } else {
            window?.performDrag(with: event)
        }
    }

    /// Shows `board`, moving what moved, when `animated`, from where it was.
    func show(_ board: RunnerBoard, animated: Bool) {
        self.board = board
        let reduceMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        let animate = animated && !reduceMotion
        // Shown as things are, the board has none leaving.
        if !animate {
            for gone in leaving.values {
                gone.layer.removeFromSuperlayer()
                gone.ring?.removeFromSuperlayer()
            }
            leaving = [:]
        }
        // Shown afresh, as when the dashboard opens, the board starts again,
        // with no legend.
        if !animated {
            steadiness = RunnerBoard.Steadiness()
            largest = BoardLayout.sizes[0]
            closeLegend()
        }
        var laidOut = board.steadied(&steadiness)
        // The runs of jobs on their way off, or about to be, keep their
        // colours until they have gone.
        let present = Set(board.tokens.map(\.id))
        let ending = animate ? shown.values.map(\.token).filter { !present.contains($0.id) && board.outcomes[$0.id] != nil }
            .map(\.runID) : []
        palette.update(for: laidOut.runs + (ending + leaving.values.map(\.run)).map { RunnerBoard.Run(id: $0, jobs: 0) })
        // A transaction's duration and timing would apply to every animation
        // added in it, so only the changes of size that are left to Core
        // Animation to animate are given them, each in a transaction of its
        // own, and the paths worked out ahead of time play at their own pace.
        CATransaction.begin()
        CATransaction.setDisableActions(!animate)
        canvas.frame = bounds
        placement = BoardLayout(board: laidOut, size: bounds.size, largest: largest, titleBar: titleBar)
        // Pools and boxes kept empty give way once they would make the
        // circles smaller or leave a pool off the board.
        if (placement.size < largest || placement.hidden > 0) && steadiness.remembersMore(than: board) {
            steadiness.forget(beyond: board)
            laidOut = board.steadied(&steadiness)
            placement = BoardLayout(board: laidOut, size: bounds.size, largest: largest, titleBar: titleBar)
        }
        largest = placement.size
        let before = heaps
        let (paths, hidden, settled) = settleHeaps(afresh: !animated, animated: animate)
        let now = heapStart
        if !animate {
            flights = [:]
        }
        // Each pool's queue is clipped to the pool's rounded box.
        var nextClips: [String: CALayer] = [:]
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for (area, box) in zip(placement.heaps, placement.pools) {
            let clip = clips[area.pool] ?? CALayer()
            clip.masksToBounds = true
            clip.cornerRadius = 10
            clip.position = CGPoint(x: box.midX, y: box.midY)
            clip.bounds = box
            if clip.superlayer == nil {
                tokenLayers.addSublayer(clip)
            }
            nextClips[area.pool] = clip
        }
        CATransaction.commit()
        let oldClips = clips
        clips = nextClips
        let queueOf = placement.heaps.reduce(into: [Int64: String]()) { queues, area in
            for id in area.jobs {
                queues[id] = area.pool
            }
        }
        let scale = window?.backingScaleFactor ?? 2
        effectiveAppearance.performAsCurrentDrawingAppearance {
            drawChrome(scale: scale)
            var next: [Int64: (token: RunnerBoard.Token, layer: CALayer)] = [:]
            for token in board.tokens {
                guard let frame = placement.tokens[token.id] else { continue }
                let target = CGPoint(x: frame.midX, y: frame.midY)
                let layer: CALayer
                if settled.contains(token.id), let existing = shown[token.id]?.layer {
                    // A ball in a heap that has not changed goes on as it was.
                    layer = existing
                    style(layer, token, size: frame.width)
                } else if let path = paths[token.id] {
                    // A queued job's ball pours, falls or rolls along its path
                    // in the heap.
                    layer = shown[token.id]?.layer ?? CALayer()
                    // A ball grows or shrinks as the heap's simulation has it
                    // do: steadily, over the same time.
                    CATransaction.begin()
                    CATransaction.setAnimationDuration(Heap.growing)
                    CATransaction.setAnimationTimingFunction(CAMediaTimingFunction(name: .linear))
                    style(layer, token, size: frame.width)
                    layer.bounds = CGRect(origin: .zero, size: frame.size)
                    CATransaction.commit()
                    layer.position = target
                    place(layer, in: queueOf[token.id].flatMap { clips[$0] } ?? tokenLayers)
                    layer.removeAnimation(forKey: "wait")
                    if animate && path.count > 1 {
                        // From the moment its path was worked out from, so
                        // that it is where the heap last said it was.
                        let fall = move(along: path, from: heapStart, in: layer)
                        layer.add(fall, forKey: "position")
                        // Out of sight until it falls into the heap's space.
                        if let delay = hidden[token.id] {
                            let wait = CABasicAnimation(keyPath: "opacity")
                            wait.fromValue = 0
                            wait.toValue = 0
                            wait.duration = delay
                            wait.beginTime = fall.beginTime
                            layer.add(wait, forKey: "wait")
                        }
                    } else {
                        layer.removeAnimation(forKey: "position")
                    }
                } else if let existing = shown[token.id]?.layer {
                    layer = existing
                    // One taken from its queue leaves its pool's clip, so as
                    // not to be clipped on its way to its runner; and one
                    // taken while still waiting to come in comes into sight
                    // as it goes.
                    if !token.running, let pool = queueOf[token.id], let clip = clips[pool] {
                        place(layer, in: clip)
                    } else {
                        place(layer, in: tokenLayers)
                    }
                    layer.removeAnimation(forKey: "wait")
                    // Set only when they change, so that a job on its way
                    // keeps going.
                    if layer.bounds.size != frame.size {
                        CATransaction.begin()
                        CATransaction.setAnimationDuration(Self.move)
                        CATransaction.setAnimationTimingFunction(CAMediaTimingFunction(name: .easeInEaseOut))
                        style(layer, token, size: frame.width)
                        layer.bounds = CGRect(origin: .zero, size: frame.size)
                        CATransaction.commit()
                    }
                    style(layer, token, size: frame.width)
                    if layer.position != target {
                        // From where it is, moving as it moves, drawn to its
                        // new place, against gravity if it is falling.
                        let state = self.state(of: token.id, at: now, heaps: before, layer: layer)
                        layer.position = target
                        if animate {
                            let path = Flight.steer(from: state, to: target,
                                                    supported: hypot(state.velocity.dx, state.velocity.dy) < 5)
                            flights[token.id] = (now, path)
                            layer.add(move(along: path, from: now, in: layer), forKey: "position")
                        }
                    }
                } else {
                    layer = CALayer()
                    style(layer, token, size: frame.width)
                    layer.bounds = CGRect(origin: .zero, size: frame.size)
                    layer.position = target
                    place(layer, in: tokenLayers)
                    if animate {
                        let grow = CABasicAnimation(keyPath: "transform.scale")
                        grow.fromValue = 0.1
                        grow.toValue = 1
                        grow.duration = Self.move
                        grow.timingFunction = CAMediaTimingFunction(name: .easeOut)
                        layer.add(grow, forKey: "arrive")
                    }
                }
                next[token.id] = (token, layer)
            }
            // A job whose end was delivered leaves showing how it ended;
            // one that is gone for another reason, such as cibar losing its
            // connection, simply goes.
            for (id, gone) in shown where next[id] == nil {
                // One still out of sight, above its heap, goes unseen.
                if let outcome = board.outcomes[id], animate, gone.layer.presentation()?.opacity != 0 {
                    leave(gone.layer, id: id, run: gone.token.runID, outcome: outcome,
                          from: state(of: id, at: now, heaps: before, layer: gone.layer))
                } else {
                    gone.layer.removeFromSuperlayer()
                }
            }
            shown = next
            flights = flights.filter { next[$0.key] != nil }
            for (pool, clip) in oldClips where nextClips[pool] == nil {
                clip.removeFromSuperlayer()
            }
        }
        tokensByID = laidOut.tokens.reduce(into: [:]) { $0[$1.id] = $1 }
        describeRuns(laidOut)
        CATransaction.commit()
        name()
    }

    /// Works out where the balls of each queue's heap go, unless the heap
    /// is as it was: those already there from where they are, moving, when
    /// `animated`, as they move, and those just queued poured in from above,
    /// one after another, as small as lets them all fit; puts each where it
    /// comes to rest; and says the path, on the board, of each ball whose
    /// path was worked out, every point of which it passes through when
    /// `animated`, how long each that starts out of sight above its heap
    /// stays out of sight, and which balls are in heaps as they were. Shown
    /// `afresh`, every heap is poured anew.
    private func settleHeaps(afresh: Bool, animated: Bool)
        -> (paths: [Int64: [CGPoint]], hidden: [Int64: Double], settled: Set<Int64>) {
        let now = clock()
        heapStart = now
        var paths: [Int64: [CGPoint]] = [:]
        var hidden: [Int64: Double] = [:]
        var settled = Set<Int64>()
        var next: [String: HeapMotion] = [:]
        for area in placement.heaps where !area.jobs.isEmpty {
            let planned = Heap.diameter(for: area.jobs.count, in: area.frame.size, largest: placement.size)
            let past = afresh ? nil : heaps[area.pool]
            func rest(_ id: Int64, _ point: CGPoint, _ diameter: CGFloat) {
                placement.tokens[id] = CGRect(x: area.frame.minX + point.x - diameter / 2,
                                              y: area.frame.minY + point.y - diameter / 2, width: diameter, height: diameter)
            }
            // A heap as it was, of balls no bigger than they may now be, lies
            // as it did.
            if let past, past.frame == area.frame, past.diameter <= planned, Set(past.paths.keys) == Set(area.jobs) {
                next[area.pool] = past
                for (id, path) in past.paths {
                    rest(id, path[path.count - 1], past.diameter)
                    settled.insert(id)
                }
                continue
            }
            // Those just queued are set out in rows, a pile as wide as the
            // heap, the oldest at the bottom, nearest the heap. Shown afresh,
            // the pile is just above the heap, and drops into it. Otherwise
            // it is centred far off to the left, two heaps' widths, and high
            // up, two heaps' widths and three heights above the heap's floor,
            // or higher if it must be to be clear of the slope that leads into
            // the heap, as for a wide, shallow heap, or of any balls still on
            // their way in beneath it; so that it falls, out of sight, onto
            // the slope, and the balls roll down it into the heap one after
            // another.
            let fresh = area.jobs.filter { past?.paths[$0] == nil }
            let width = area.frame.width, height = area.frame.height
            let across = max(1, Int(width / (planned * 1.1)))
            let pile = CGSize(width: CGFloat(across) * planned * 1.1,
                              height: CGFloat((fresh.count + across - 1) / across) * planned * 1.2)
            let right = animated ? -2 * width + pile.width / 2 : pile.width
            let beneath = area.jobs.compactMap { past?.paths[$0] }
                .map { Flight.state(on: $0, at: now - (past?.start ?? now)).position }
                .filter { $0.x < right + planned && $0.x > right - pile.width - planned }.map(\.y).min()
            let bottom = animated
                ? min(-2 * width - 2 * height + pile.height / 2, right - pile.width - planned,
                      (beneath ?? 0) - 2 * planned)
                : -planned / 2
            let pouring = Dictionary(uniqueKeysWithValues: fresh.enumerated().map { k, id in
                let row = k / across
                return (id, CGPoint(x: right - planned / 2 - CGFloat(k % across) * planned * 1.1
                                        - (row % 2 == 0 ? 0 : planned * 0.55),
                                    y: bottom - planned / 2 - CGFloat(row) * planned * 1.2))
            })
            let balls = area.jobs.map { id -> Heap.Ball in
                if let past, let path = past.paths[id] {
                    let state = Flight.state(on: path, at: now - past.start)
                    return Heap.Ball(id: id, position: state.position, velocity: animated ? state.velocity : .zero)
                }
                return Heap.Ball(id: id, position: pouring[id] ?? .zero)
            }
            // Balls already in the heap grow or shrink as their circles do.
            let fitted = Heap.fit(balls, in: area.frame.size, diameter: planned, grownFrom: animated ? past?.diameter : nil)
            let diameter = fitted.diameter
            var worked = fitted.paths
            if !animated {
                worked = worked.mapValues { [$0[$0.count - 1]] }
            }
            next[area.pool] = HeapMotion(frame: area.frame, diameter: diameter, start: now, paths: worked)
            for (id, path) in worked {
                paths[id] = path.map { CGPoint(x: $0.x + area.frame.minX, y: $0.y + area.frame.minY) }
                rest(id, path[path.count - 1], diameter)
                // A ball on its way in is out of sight until it is half past
                // the heap's left side.
                if animated, path[0].x < 0 {
                    hidden[id] = Double(path.firstIndex { $0.x >= 0 } ?? path.count - 1) / Heap.rate
                }
            }
        }
        heaps = next
        return (paths, hidden, settled)
    }

    /// Puts a job's circle in `parent`, if it is not already there, whole
    /// at once, rather than fading in, which, for many at once, is hard to
    /// follow; it is where it was on the board, as every parent it goes in
    /// has the board's coordinates.
    private func place(_ layer: CALayer, in parent: CALayer) {
        guard layer.superlayer !== parent else { return }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        parent.addSublayer(layer)
        CATransaction.commit()
    }

    /// Where the ball of job `id` is at `now`, and how it is moving, by the
    /// path it is on, on its own, or in a heap as `heaps` had it; or, on
    /// none, where its layer is, at rest.
    private func state(of id: Int64, at now: CFTimeInterval, heaps: [String: HeapMotion], layer: CALayer) -> Flight.State {
        if let flight = flights[id] {
            return Flight.state(on: flight.path, at: now - flight.start)
        }
        for heap in heaps.values {
            if let path = heap.paths[id] {
                // One still on its way in, out of sight, comes out of the
                // heap's top left corner, where it would have come into sight.
                var state = Flight.state(on: path, at: now - heap.start)
                if state.position.x < 0 {
                    state.position = .zero
                }
                state.position.x += heap.frame.minX
                state.position.y = heap.frame.minY + max(0, state.position.y)
                return state
            }
        }
        return Flight.State(position: layer.presentation()?.position ?? layer.position)
    }

    /// Moves `layer` along `path`, `Flight.rate` points a second, from `now`,
    /// at the pace the path says, unhurried at neither end by the ease in
    /// and out that the board's transaction would otherwise give it.
    private func move(along path: [CGPoint], from now: CFTimeInterval, in layer: CALayer) -> CAKeyframeAnimation {
        let move = CAKeyframeAnimation(keyPath: "position")
        move.values = path
        move.duration = Double(path.count - 1) / Flight.rate
        move.calculationMode = .linear
        move.timingFunction = CAMediaTimingFunction(name: .linear)
        move.beginTime = layer.convertTime(now, from: nil)
        return move
    }

    /// Colours a job's circle, in its run's colour, the same queued or
    /// running, as where it is says which.
    private func style(_ layer: CALayer, _ token: RunnerBoard.Token, size: CGFloat) {
        let colour = palette.colour(token.runID).cgColor
        if layer.cornerRadius != size / 2 {
            layer.cornerRadius = size / 2
        }
        if layer.backgroundColor != colour {
            layer.backgroundColor = colour
        }
    }

    /// Takes a job off the board, from `state`: keeping its colour, it gains
    /// an outline in the colour of how it ended, green, red or grey, and a
    /// mark to match, swells for a moment, slowing to a stop, and goes, over
    /// everything else, as `Flight.leaving` says: thrown off to the right if
    /// it passed; thrown against the right side, onto the line along the
    /// board's bottom, and off its left end, leaving a ring behind, if it
    /// failed; and floating up and away, growing and fading, like a puff of
    /// smoke, if neither.
    private func leave(_ layer: CALayer, id: Int64, run: String, outcome: Outcome, from state: Flight.State) {
        let colour = (outcome == .passed ? NSColor.systemGreen : outcome == .failed ? .systemRed : .systemGray).cgColor
        let size = layer.bounds.width
        layer.removeAllAnimations()
        // Out of its pool's clip, so as to be seen going.
        place(layer, in: tokenLayers)
        let ring = outcome == .failed ? CAShapeLayer() : nil
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        leaving[id] = (layer, ring, run)
        CATransaction.setCompletionBlock { [weak self] in
            layer.removeFromSuperlayer()
            ring?.removeFromSuperlayer()
            if self?.leaving[id]?.layer === layer {
                self?.leaving[id] = nil
            }
        }
        layer.zPosition = 1
        layer.opacity = 1
        let pause = Self.leave * 0.35
        let width = canvas.bounds.width
        let path = Flight.leaving(from: state, diameter: size, outcome: outcome, pause: pause,
                                  board: Flight.Board(width: width, floor: canvas.bounds.height,
                                                      line: BoardLayout.inset...max(BoardLayout.inset, width - BoardLayout.inset)))
        layer.position = path[path.count - 1]
        layer.borderColor = colour
        layer.borderWidth = max(1.5, size * 0.16)
        let mark = CATextLayer()
        mark.string = NSAttributedString(string: outcome == .passed ? "✓" : outcome == .failed ? "✕" : "–", attributes: [
            .font: NSFont.systemFont(ofSize: size * 0.62, weight: .heavy),
            .foregroundColor: NSColor.white.cgColor,
        ])
        mark.alignmentMode = .center
        mark.contentsScale = window?.backingScaleFactor ?? 2
        mark.frame = CGRect(x: 0, y: (size - size * 0.62 * 1.25) / 2, width: size, height: size * 0.62 * 1.25)
        layer.addSublayer(mark)
        let fly = CAKeyframeAnimation(keyPath: "position")
        fly.values = path
        fly.duration = Double(path.count - 1) / Flight.rate
        fly.calculationMode = .linear
        let total = fly.duration
        let swelled = NSNumber(value: Self.leave * 0.15 / total), flying = NSNumber(value: pause / total)
        let swell = CAKeyframeAnimation(keyPath: "transform.scale")
        swell.values = outcome == .neither ? [1, 1.3, 1, 2.4] : [1, 1.3, 1]
        swell.keyTimes = outcome == .neither ? [0, swelled, flying, 1] : [0, swelled, flying]
        swell.duration = total
        let group = CAAnimationGroup()
        group.timingFunction = CAMediaTimingFunction(name: .linear)
        group.animations = [fly, swell]
        if outcome == .neither {
            let fade = CAKeyframeAnimation(keyPath: "opacity")
            fade.values = [1, 1, 0]
            fade.keyTimes = [0, flying, 1]
            fade.duration = total
            group.animations?.append(fade)
        }
        group.duration = total
        layer.add(group, forKey: "leave")
        if let ring {
            ring.path = CGPath(ellipseIn: CGRect(x: -size / 2, y: -size / 2, width: size, height: size), transform: nil)
            ring.position = path[min(path.count - 1, Int(pause * Flight.rate))]
            ring.fillColor = nil
            ring.strokeColor = colour
            ring.lineWidth = 1.5
            ring.opacity = 0
            tokenLayers.addSublayer(ring)
            let spread = CABasicAnimation(keyPath: "transform.scale")
            spread.fromValue = 1
            spread.toValue = 3.5
            let vanish = CABasicAnimation(keyPath: "opacity")
            vanish.fromValue = 1
            vanish.toValue = 0
            let burst = CAAnimationGroup()
            burst.animations = [spread, vanish]
            burst.duration = Self.leave
            burst.timingFunction = CAMediaTimingFunction(name: .easeOut)
            ring.add(burst, forKey: "burst")
        }
        CATransaction.commit()
    }

    /// Draws afresh everything but the jobs: the pools, boxes and slots, the
    /// queues' arrows, and the words.
    private func drawChrome(scale: CGFloat) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        chrome.sublayers = nil
        let separator = NSColor.separatorColor.cgColor
        for frame in placement.pools {
            let pool = CALayer()
            pool.frame = frame
            pool.cornerRadius = 10
            pool.backgroundColor = NSColor.labelColor.withAlphaComponent(0.035).cgColor
            chrome.addSublayer(pool)
        }
        for frame in placement.boxes {
            let box = CALayer()
            box.frame = frame
            box.cornerRadius = 7
            box.backgroundColor = NSColor.labelColor.withAlphaComponent(0.05).cgColor
            box.borderColor = separator
            box.borderWidth = 0.5
            chrome.addSublayer(box)
        }
        let size = placement.size
        // A place is outlined only while empty, to show the hole.
        for (slot, frame) in placement.slots where !slot.place || slot.job == nil {
            let outline = CALayer()
            outline.frame = frame
            outline.cornerRadius = frame.width / 2
            outline.borderWidth = 1
            outline.borderColor = slot.online ? NSColor.tertiaryLabelColor.cgColor
                : NSColor.systemRed.withAlphaComponent(0.7).cgColor
            chrome.addSublayer(outline)
            if !slot.online {
                let cross = CAShapeLayer()
                let path = CGMutablePath()
                path.move(to: CGPoint(x: frame.minX + 4, y: frame.minY + 4))
                path.addLine(to: CGPoint(x: frame.maxX - 4, y: frame.maxY - 4))
                cross.path = path
                cross.strokeColor = NSColor.systemRed.withAlphaComponent(0.7).cgColor
                cross.lineWidth = 1
                chrome.addSublayer(cross)
            } else if slot.job == nil && size >= 12 {
                let label = BoardLayout.width(slot.label, .slot) <= size ? slot.label : String(slot.label.prefix(1))
                chrome.addSublayer(text(label, style: .slot, frame: CGRect(x: frame.minX, y: frame.midY - 5,
                                                                         width: frame.width, height: 10),
                                        centred: true, scale: scale))
            }
        }
        for item in placement.texts {
            chrome.addSublayer(text(item.string, style: item.style, frame: item.frame, centred: false, scale: scale))
        }
        CATransaction.commit()
    }

    private func text(_ string: String, style: BoardLayout.Style, frame: CGRect, centred: Bool,
                      scale: CGFloat) -> CATextLayer {
        let colour: NSColor = switch style {
        case .heading: .labelColor
        case .detail, .count: .secondaryLabelColor
        case .warning: .systemRed
        case .slot: .tertiaryLabelColor
        }
        let layer = CATextLayer()
        layer.frame = frame
        layer.contentsScale = scale
        layer.truncationMode = .end
        layer.alignmentMode = centred ? .center : .left
        layer.string = NSAttributedString(string: string, attributes: [
            .font: BoardLayout.font(style), .foregroundColor: colour.cgColor,
        ])
        return layer
    }

    // MARK: Naming what is pointed at

    override func mouseMoved(with event: NSEvent) {
        pointer = flipped(convert(event.locationInWindow, from: nil))
        name()
    }

    override func mouseExited(with event: NSEvent) {
        pointer = nil
        name()
    }

    /// What is at `point`, in words: a job, or a runner's slot.
    func words(at point: CGPoint, now: Date) -> String? {
        if let id = placement.token(at: point), let token = tokensByID[id] {
            return Self.describe(token, at: now)
        }
        return placement.slot(at: point).map {
            "\($0.name): \($0.online ? $0.job == nil ? $0.place ? "free" : "idle" : "running a job" : "offline")"
        }
    }

    /// Names what the pointer points at, in a bubble beside it, at once, as
    /// tooltips would not while cibar is not the active app.
    private func name() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }
        guard let pointer, let words = words(at: pointer, now: Date()) else {
            bubble.isHidden = true
            return
        }
        effectiveAppearance.performAsCurrentDrawingAppearance {
            let text = NSAttributedString(string: words, attributes: [
                .font: NSFont.systemFont(ofSize: 10), .foregroundColor: NSColor.labelColor.cgColor,
            ])
            let measured = text.boundingRect(with: CGSize(width: 360, height: 400), options: .usesLineFragmentOrigin).size
            let size = CGSize(width: ceil(measured.width) + 12, height: ceil(measured.height) + 8)
            let origin = CGPoint(x: min(pointer.x + 12, bounds.width - size.width - 4),
                                 y: pointer.y + 16 + size.height > bounds.height ? pointer.y - size.height - 8
                                    : pointer.y + 16)
            bubble.frame = CGRect(origin: origin, size: size)
            bubble.backgroundColor = NSColor.controlBackgroundColor.cgColor
            bubble.borderColor = NSColor.separatorColor.cgColor
            bubbleText.frame = CGRect(x: 6, y: 4, width: size.width - 12, height: size.height - 8)
            bubbleText.contentsScale = window?.backingScaleFactor ?? 2
            bubbleText.string = text
            // Drawn now, so that the old words do not show for a frame.
            bubbleText.display()
            bubble.isHidden = false
        }
    }

    /// What a job's circle says when pointed at.
    static func describe(_ token: RunnerBoard.Token, at now: Date) -> String {
        let how = token.running ? "Running on \(token.runner ?? "a runner")" : "Queued"
        let since = token.since.map { " for \(duration(now.timeIntervalSince($0)))" } ?? ""
        var lines = [token.commit.isEmpty ? token.workflowTitle : "\(token.workflowTitle) @ \(token.commit)",
                     "\(token.job)", how + since]
        if !token.running {
            lines.append(token.labels.isEmpty ? "for any runner" : "for a runner labelled \(token.labels.joined(separator: ", "))")
        }
        return lines.joined(separator: "\n")
    }

    /// A length of time in its largest unit: "40s", "4m", "2h".
    static func duration(_ seconds: TimeInterval) -> String {
        let seconds = max(0, Int(seconds))
        return seconds < 60 ? "\(seconds)s" : seconds < 3600 ? "\(seconds / 60)m"
            : seconds < 86400 ? "\(seconds / 3600)h" : "\(seconds / 86400)d"
    }
}

// MARK: - The legend

/// One line of the legend: a run, its colour, what it is, and how many of
/// its jobs are on the board.
struct LegendEntry: Identifiable, Equatable {
    var id: String
    var colour: NSColor
    var title: String
    var jobs: Int
}

final class LegendModel: ObservableObject {
    @Published var entries: [LegendEntry] = []
}

/// The legend's lines, scrolling when there are more than fit.
struct LegendView: View {
    static let width: CGFloat = 340

    @ObservedObject var model: LegendModel

    var body: some View {
        ScrollView {
            LegendLines(model: model)
        }
        .scrollBounceBehavior(.basedOnSize)
    }
}

/// A line for each run: its colour, what it is, and how many of its jobs
/// are on the board.
struct LegendLines: View {
    @ObservedObject var model: LegendModel

    var body: some View {
            VStack(alignment: .leading, spacing: 5) {
                if model.entries.isEmpty {
                    Text("No jobs on the board.").foregroundStyle(.secondary)
                }
                ForEach(model.entries) { entry in
                    HStack(spacing: 7) {
                        Circle().fill(Color(nsColor: entry.colour)).frame(width: 10, height: 10)
                        Text(entry.title).lineLimit(1).truncationMode(.middle)
                        Spacer(minLength: 8)
                        Text(counted(entry.jobs, "job")).monospacedDigit().foregroundStyle(.secondary)
                    }
                }
            }
            .font(.caption)
            .padding(10)
            .frame(width: LegendView.width, alignment: .leading)
    }
}

/// Where the legend goes, `size` big, beside a window `beside`: to its
/// right, its tops level, or, with no room there in `visible`, to its left,
/// or, with room on neither side, over its right edge; and moved in from the
/// top and bottom of `visible`.
func legendFrame(beside window: CGRect, size: CGSize, within visible: CGRect) -> CGRect {
    let gap: CGFloat = 8
    let x = window.maxX + gap + size.width <= visible.maxX ? window.maxX + gap
        : window.minX - gap - size.width >= visible.minX ? window.minX - gap - size.width
        : visible.maxX - size.width
    let y = min(max(window.maxY - size.height, visible.minY), visible.maxY - size.height)
    return CGRect(x: x, y: y, width: size.width, height: size.height)
}
