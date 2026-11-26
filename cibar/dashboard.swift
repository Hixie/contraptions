import Combine
import SwiftUI

/// A count and the noun it counts, such as "1 job" or "2 jobs".
func counted(_ count: Int, _ noun: String) -> String {
    count == 1 ? "1 \(noun)" : noun.hasSuffix("y") ? "\(count) \(noun.dropLast())ies" : "\(count) \(noun)s"
}

/// What the dashboard shows, worked out from what cibar knows.
struct DashboardState: Equatable {
    struct Branch: Equatable, Identifiable {
        var repository: String
        var branch: String
        var red: Bool
        var url: URL?
        var id: String { repository }
    }

    struct Job: Equatable, Identifiable {
        var id: String
        var name: String
        var running: Bool
        var url: URL?
        /// What cibar did about the job, if it failed and cibar did anything.
        var codex: CodexRecord? = nil
        /// Whether, having failed and not been sent to Codex, it can be sent
        /// by hand, and whether it is being sent.
        var sendable = false
        var sending = false
        /// The job in a later run, if, having failed, it is queued or
        /// running again.
        var rerun: Rerun? = nil
    }

    struct Rerun: Equatable {
        var url: URL
        var running: Bool
    }

    struct Failing: Equatable, Identifiable {
        var workflowBranch: WorkflowBranch
        var onDefaultBranch: Bool
        var jobs: [Job]
        var id: String { "\(workflowBranch.repository) \(workflowBranch.path) \(workflowBranch.branch)" }
    }

    struct Run: Equatable, Identifiable {
        var id: Int64
        var workflowBranch: WorkflowBranch
        var onDefaultBranch: Bool
        /// The open pull request of the run's branch, if cibar knows of one.
        var pullRequest: PullRequestInfo? = nil
        var url: URL?
        /// In the order GitHub made them.
        var jobs: [Job]
        /// The run's jobs that have already failed.
        var failed: [Job] = []
        var running: Int { jobs.filter(\.running).count }
        var queued: Int { jobs.count - running }
    }

    enum Connection: Equatable {
        case connected
        case connecting
        case disconnected(reason: String, retryAt: Date?)
        case unusableSettings(String)

        /// How cibar stands with the relay: the settings' problem, if they
        /// cannot be used, or the connection's state.
        init(_ state: ConnectionState, settingsProblem: String?) {
            if let settingsProblem {
                self = .unusableSettings(settingsProblem)
                return
            }
            switch state {
            case .connected:
                self = .connected
            case .connecting:
                self = .connecting
            case .disconnected(let reason, let retryAt):
                self = .disconnected(reason: reason, retryAt: retryAt)
            }
        }
    }

    var connection: Connection
    /// How late the latest deliveries on this connection arrived.
    var lateness = Lateness()
    var deliveryProblem: String?
    /// Why cibar could not last read the runners from GitHub's API.
    var runnersProblem: String?
    /// Whether cibar is reading from GitHub's API, how many of how many
    /// repositories it has read, once it knows, and why it last could not.
    var refreshing = false
    var refreshProgress: RefreshProgress?
    var refreshProblem: String?
    /// Whether Codex sessions start, or nil when the settings have none.
    var sessions: Bool?
    /// Whether what runs on branches other than default ones, such as pull
    /// requests', is shown and counted. What fails on them never is.
    var includesPullRequests = false
    /// Whether the runners board shows, in place of everything above the
    /// bottom row.
    var showsRunners = false
    var board = RunnerBoard()
    var branches: [Branch]
    var failing: [Failing]
    /// The newest first.
    var runs: [Run]
    var runningJobs: Int
    var runningWorkflows: Int
    var queuedJobs: Int
    var failingJobs: Int

    var isConnected: Bool {
        if case .connected = connection { true } else { false }
    }

    /// What is wrong, in a few words for each thing, to be shown as it is.
    var problems: [String] {
        var problems: [String] = []
        switch connection {
        case .unusableSettings(let problem):
            problems.append("The settings cannot be used: \(problem)")
        case .disconnected(let reason, _):
            problems.append("Not connected to the relay: \(reason)")
        case .connected, .connecting:
            break
        }
        if let deliveryProblem {
            problems.append(deliveryProblem)
        }
        if let refreshProblem {
            problems.append(refreshProblem)
        }
        if let runnersProblem {
            problems.append(runnersProblem)
        }
        return problems
    }
}

/// How many seconds after GitHub made them the deliveries of the last few
/// seconds arrived, as far as their delivery IDs say, oldest first.
struct Lateness: Equatable {
    struct Delivery: Equatable {
        var arrived: Date
        var seconds: TimeInterval
    }

    /// How long deliveries are remembered, and how many seconds after GitHub
    /// made it a delivery must arrive to count as late.
    static let remembered: TimeInterval = 10
    static let late: TimeInterval = 5

    private(set) var deliveries: [Delivery] = []

    /// Remembers how late a delivery that arrived at `arrived` was,
    /// forgetting those too long ago. A delivery cannot arrive before it was
    /// made, so one that seems to, by this computer's clock, arrived on time.
    mutating func record(_ seconds: TimeInterval, at arrived: Date) {
        deliveries.removeAll { arrived.timeIntervalSince($0.arrived) > Self.remembered }
        deliveries.append(Delivery(arrived: arrived, seconds: max(seconds, 0)))
    }

    var latest: TimeInterval? { deliveries.last?.seconds }

    var isLate: Bool { (latest ?? 0) > Self.late }

    /// The latest, as a few characters: "0.4s", or "12s".
    var text: String? {
        latest.map { $0 < 10 ? String(format: "%.1fs", $0) : "\(Int($0))s" }
    }
}

extension String {
    /// This, with each full git commit hash in it, forty or sixty-four
    /// hexadecimal digits standing alone, shortened to its first seven, as
    /// git shortens them.
    var abbreviatingHashes: String {
        // Too short to hold a hash, as most are, is left as it is at once,
        // since the expression is slow to try.
        utf8.count < 40 ? self : replacing(#/\b(?:[0-9a-f]{64}|[0-9a-f]{40})\b/#) { String($0.output.prefix(7)) }
    }
}

nonisolated(unsafe) private var textWidths: [String: CGFloat] = [:]

/// How wide `text` is in `font`, remembered for the next time it is asked,
/// until so many are remembered that they are all forgotten.
func textWidth(_ text: String, _ font: NSFont) -> CGFloat {
    let key = "\(font.fontName) \(font.pointSize) \(text)"
    if let width = textWidths[key] {
        return width
    }
    let width = NSAttributedString(string: text, attributes: [.font: font]).size().width
    if textWidths.count >= 5000 {
        textWidths = [:]
    }
    textWidths[key] = width
    return width
}

/// What a run is called: its repository, its branch's pull request if it
/// has one, and its workflow, as `loom PR #1234 · Tests` or `labs · CI`.
func runTitle(repository: String, workflow: String, pullRequest: Int?) -> String {
    pullRequest.map { "\(repository) PR #\($0) · \(workflow)" } ?? "\(repository) · \(workflow)"
}

/// "job" or "jobs", as many as there are.
private func jobs(_ count: Int) -> String {
    count == 1 ? "job" : "jobs"
}

extension DashboardState {
    init(watch: Watch, at now: Date, connection: Connection, lateness: Lateness = Lateness(),
         deliveryProblem: String? = nil,
         refreshing: Bool = false, refreshProgress: RefreshProgress? = nil, refreshProblem: String? = nil,
         sessions: Bool?, codex: CodexSettings? = nil, records: [CodexRecord] = [], sending: Set<JobKey> = [],
         includesPullRequests: Bool,
         showsRunners: Bool = false, runners: [Runner] = [], larger: [LargerRunner] = [],
         runnersProblem: String? = nil) {
        self.connection = connection
        self.lateness = lateness
        self.deliveryProblem = deliveryProblem
        self.refreshing = refreshing
        self.refreshProgress = refreshProgress
        self.refreshProblem = refreshProblem
        self.sessions = sessions
        self.includesPullRequests = includesPullRequests
        self.showsRunners = showsRunners
        self.runnersProblem = runnersProblem
        branches = watch.listedDefaultBranches(at: now).map {
            var runs = URLComponents(string: "https://github.com/\($0.repository)/actions")
            runs?.queryItems = [URLQueryItem(name: "query", value: "branch:\($0.branch)")]
            return Branch(repository: $0.repository, branch: $0.branch, red: $0.red, url: runs?.url)
        }
        failing = watch.failingWorkflowBranches
            .map { workflowBranch in
                Failing(workflowBranch: workflowBranch, onDefaultBranch: watch.isOnDefaultBranch(workflowBranch),
                        jobs: watch.failures(on: workflowBranch).map { failure in
                            let key = JobKey(workflowBranch: workflowBranch, job: failure.name)
                            let record = records.last { $0.job == key }
                            return Job(id: failure.name,
                                       name: failure.name == wholeWorkflow ? "the run" : failure.name.abbreviatingHashes,
                                       running: false,
                                       url: URL(string: failure.url),
                                       codex: record,
                                       sendable: handSentRequest(for: key, in: watch, codex: codex,
                                                                 records: records) != nil,
                                       sending: sending.contains(key),
                                       rerun: watch.rerun(of: failure.name, on: workflowBranch).flatMap { job in
                                           URL(string: job.url).map { Rerun(url: $0, running: job.running) }
                                       })
                        })
            }
        runs = Dictionary(grouping: watch.active, by: \.value.order.run)
            .filter { includesPullRequests || watch.isOnDefaultBranch($0.value[0].value.workflowBranch) }
            .sorted { $0.key > $1.key }
            .map { run, jobs in
                let workflowBranch = jobs[0].value.workflowBranch
                return Run(id: run, workflowBranch: workflowBranch,
                           onDefaultBranch: watch.isOnDefaultBranch(workflowBranch),
                           pullRequest: watch.pullRequest(for: workflowBranch),
                           url: URL(string: jobs[0].value.runURL),
                           jobs: jobs.sorted { $0.key < $1.key }
                               .map { Job(id: "\($0.key)", name: $0.value.name.abbreviatingHashes,
                                          running: $0.value.running,
                                          url: URL(string: $0.value.url)) },
                           failed: watch.failures(on: workflowBranch, in: run).map {
                               Job(id: $0.name, name: $0.name == wholeWorkflow ? "the run" : $0.name.abbreviatingHashes,
                                   running: false, url: URL(string: $0.url))
                           })
            }
        runningJobs = runs.reduce(0) { $0 + $1.running }
        runningWorkflows = Set(runs.filter { $0.running > 0 }.map(\.workflowBranch)).count
        queuedJobs = runs.reduce(0) { $0 + $1.queued }
        failingJobs = failing.reduce(0) { $0 + $1.jobs.count }
        // Every job, whatever its branch, since every job takes a runner;
        // while not connected, which are queued or running is not known.
        if showsRunners {
            board = RunnerBoard(jobs: isConnected ? watch.active : [:], runners: runners, larger: larger,
                                outcomes: watch.outcomes,
                                pullRequests: watch.pullRequest(for:))
            board.problems = problems
        }
    }
}

/// How many of how many repositories a read of GitHub's API has read.
struct RefreshProgress: Equatable {
    var done: Int
    var total: Int
}

/// What the dashboard's controls do.
struct DashboardActions {
    var setSessions: (Bool) -> Void = { _ in }
    var send: (JobKey) -> Void = { _ in }
    var showSettings: () -> Void = {}
    var flyBanners: () -> Void = {}
    var quit: () -> Void = {}
    var refresh: () -> Void = {}
    var setIncludesPullRequests: (Bool) -> Void = { _ in }
    var setShowsRunners: (Bool) -> Void = { _ in }
    var open: (URL) -> Void = { _ in }
    var close: () -> Void = {}
}

/// The dashboard's state, which changes while it shows.
final class DashboardModel: ObservableObject {
    @Published var state: DashboardState
    let actions: DashboardActions
    /// What keeps the views that are not SwiftUI's up to date.
    var subscriptions: [AnyCancellable] = []

    init(state: DashboardState, actions: DashboardActions) {
        self.state = state
        self.actions = actions
    }
}

// MARK: - The view

/// A title bar over the dashboard's content, once torn off: how tall it is,
/// and how far down the middle of its close button is, which what goes
/// beside the button lines up with; or none.
struct TitleBar: Equatable {
    var height: CGFloat = 0
    var middle: CGFloat = 0

    static let none = TitleBar()
}

/// The dashboard's content: told of a title bar over it, it keeps
/// the numbers and the rest below it, and has the runners board put its
/// heading in it.
final class DashboardContent: NSView {
    let board: RunnerBoardView
    /// What puts the numbers' top at the content's.
    var tilesTop: NSLayoutConstraint?

    var titleBar = TitleBar.none {
        didSet {
            tilesTop?.constant = titleBar.height
            board.titleBar = titleBar
        }
    }

    init(board: RunnerBoardView) {
        self.board = board
        super.init(frame: .zero)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { nil }
}

/// The dashboard: its big numbers, the rest, and its bottom row, each drawn
/// by a view of its own, so that what moves in one, many times a second,
/// does not have the others drawn afresh with it. The rest fills what the
/// numbers and the bottom row leave of the view, which is `width` wide. The
/// runners board, when it shows, takes the place of the numbers and the
/// rest.
func makeDashboard(_ model: DashboardModel, width: CGFloat) -> DashboardContent {
    let tiles = NSHostingView(rootView: DashboardTiles(model: model))
    let rest = NSHostingView(rootView: DashboardView(model: model))
    let footer = NSHostingView(rootView: DashboardFooter(model: model))
    let board = RunnerBoardView()
    board.open = model.actions.open
    // Each is as tall as it is at first, and does not say it would be
    // otherwise, since its saying so as it changes, many times a second,
    // would have the whole window laid out again each time.
    tiles.sizingOptions = []
    rest.sizingOptions = []
    footer.sizingOptions = []
    let view = DashboardContent(board: board)
    let tilesTop = tiles.topAnchor.constraint(equalTo: view.topAnchor)
    view.tilesTop = tilesTop
    for part in [tiles, rest, footer, board] {
        part.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(part)
        NSLayoutConstraint.activate([
            part.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            part.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
    }
    NSLayoutConstraint.activate([
        tiles.heightAnchor.constraint(equalToConstant: height(of: DashboardTiles(model: model), width: width)),
        footer.heightAnchor.constraint(equalToConstant: height(of: DashboardFooter(model: model), width: width)),
        tilesTop,
        rest.topAnchor.constraint(equalTo: tiles.bottomAnchor),
        footer.topAnchor.constraint(equalTo: rest.bottomAnchor),
        footer.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        board.topAnchor.constraint(equalTo: view.topAnchor),
        board.bottomAnchor.constraint(equalTo: footer.topAnchor),
    ])
    // The board moves what changed while it shows, and shows how things are
    // when it opens.
    model.subscriptions.append(model.$state.sink { [weak tiles, weak rest, weak board] state in
        tiles?.isHidden = state.showsRunners
        rest?.isHidden = state.showsRunners
        guard let board else { return }
        let wasShowing = !board.isHidden && board.window?.isVisible == true
        board.isHidden = !state.showsRunners
        if state.showsRunners && (state.board != board.board || !wasShowing) {
            board.show(state.board, animated: wasShowing)
        }
    })
    return view
}

/// How tall a view is at `width`.
private func height(of view: some View, width: CGFloat) -> CGFloat {
    NSHostingController(rootView: view).sizeThatFits(in: CGSize(width: width, height: .greatestFiniteMagnitude)).height
}

/// The dashboard's big numbers, which are drawn apart from the rest of it,
/// so that while their digits turn, only they are drawn afresh, frame by
/// frame, and not everything the dashboard shows.
struct DashboardTiles: View {
    @ObservedObject var model: DashboardModel

    private var state: DashboardState { model.state }

    var body: some View {
        tiles
            .padding([.horizontal, .top], 18)
    }

    private var tiles: some View {
        let connected = state.isConnected
        let red = state.branches.filter(\.red).count
        return HStack(spacing: 12) {
            Tile(title: "Default Branches", symbol: "arrow.triangle.branch",
                 value: state.branches.isEmpty ? .unknown : .fraction(state.branches.count - red, state.branches.count),
                 detail: state.branches.isEmpty ? "none to list" : "green",
                 tint: red > 0 ? .red : .green)
            Tile(title: "Failing", symbol: state.failingJobs > 0 ? "xmark.octagon.fill" : "checkmark.circle.fill",
                 value: .count(state.failingJobs),
                 detail: state.failingJobs > 0
                    ? "\(jobs(state.failingJobs)) in \(counted(state.failing.count, "workflow"))"
                    : "nothing failing",
                 tint: state.failingJobs > 0 ? .red : .green)
            Tile(title: "Running", symbol: "play.circle.fill",
                 value: connected ? .count(state.runningJobs) : .unknown,
                 detail: connected
                    ? "\(jobs(state.runningJobs)) in \(counted(state.runningWorkflows, "workflow"))"
                    : "not connected",
                 tint: .blue)
            Tile(title: "Queued", symbol: "clock.fill",
                 value: connected ? .count(state.queuedJobs) : .unknown,
                 detail: "\(jobs(state.queuedJobs)) waiting for a runner", tint: .orange)
        }
    }

}

/// The dashboard's bottom row, which is drawn apart from the rest of it,
/// since its line of how late deliveries arrive moves on several times a
/// second, and drawing the rest afresh with it would cost far more.
struct DashboardFooter: View {
    @ObservedObject var model: DashboardModel

    private var state: DashboardState { model.state }
    private var actions: DashboardActions { model.actions }

    var body: some View {
        VStack(spacing: 16) {
            Divider()
            footer
        }
        // The divider, on which a failed job's ball lands, ends where the
        // runners board's pools do.
        .padding([.horizontal, .bottom], BoardLayout.inset)
    }

    private var footer: some View {
        HStack(spacing: 12) {
            ConnectionLabel(connection: state.connection, lateness: state.lateness)
            Spacer()
            if state.refreshing {
                // A still bar, filled as repositories are read, rather than
                // a spinner, which would have the dashboard drawn afresh
                // many times a second.
                if let progress = state.refreshProgress, progress.total > 0 {
                    ProgressView(value: Double(progress.done), total: Double(progress.total))
                        .progressViewStyle(.linear)
                        .frame(width: 80)
                    Text("\(progress.done) of \(counted(progress.total, "repository"))")
                        .font(.caption)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                } else {
                    Text("Refreshing…").font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Button {
                    actions.refresh()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .help("Read everything afresh from GitHub, forgetting what cibar knew: workflows that will "
                    + "never run again, and jobs whose ends were missed.")
            }
            // The switch between the two views, named for the view it shows,
            // after Refresh, so that the bar Refresh gives way to as cibar
            // reads is not between buttons.
            Button {
                actions.setShowsRunners(!state.showsRunners)
            } label: {
                if state.showsRunners {
                    Label("Show Branches", systemImage: "arrow.triangle.branch")
                } else {
                    Label("Show Runners", systemImage: "server.rack")
                }
            }
            .help(state.showsRunners
                ? "Show the default branches, what fails on them, and what runs, in place of the runners."
                : "Show the runners, and the jobs queued for them and running on them, in place of the lists.")
            Button("Settings…") {
                if NSEvent.modifierFlags.contains(.option) {
                    actions.flyBanners()
                } else {
                    actions.showSettings()
                }
            }
            .help("Hold Option to fly a red and a green banner instead.")
            Button("Quit \(applicationName)", action: actions.quit)
        }
        .controlSize(.small)
    }
}

/// Everything on the dashboard between its big numbers and its bottom row.


struct DashboardView: View {
    @ObservedObject var model: DashboardModel

    private var state: DashboardState { model.state }
    private var actions: DashboardActions { model.actions }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(state.problems, id: \.self) { problem in
                Label(problem, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .symbolRenderingMode(.multicolor)
                    .textSelection(.enabled)
                    .lineLimit(3)
            }
            // Two columns, each half the width: each can be as narrow or as
            // wide as it is given, so that nothing in one, such as the jobs
            // set out in columns as wide as they are given, can take room
            // from the other.
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 12) {
                    Scrolling {
                        failingColumn
                        branchesColumn
                    }
                    sessionsSwitch
                }
                .frame(minWidth: 0, maxWidth: .infinity, alignment: .topLeading)
                VStack(alignment: .leading, spacing: 8) {
                    runningHeading
                    Scrolling {
                        runningColumn
                    }
                }
                .frame(minWidth: 0, maxWidth: .infinity, alignment: .topLeading)
            }
            .frame(maxHeight: .infinity)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .onExitCommand(perform: actions.close)
    }

    // MARK: Columns

    private var failingColumn: some View {
        Column(title: "Failing on Default Branches", count: state.failing.count) {
            if state.failing.isEmpty {
                Placeholder(symbol: "checkmark.seal", text: "Nothing is failing.")
            } else {
                ForEach(state.failing) { failing in
                    FailingCard(failing: failing, actions: actions).equatable()
                }
            }
        }
    }

    private var branchesColumn: some View {
        Column(title: "Default Branches", count: state.branches.count) {
            if state.branches.isEmpty {
                Placeholder(symbol: "moon.zzz", text: "No workflow has finished on a default branch today.")
            } else {
                // Each pill as wide as its name, so that none is cut short,
                // as many to a line as fit.
                Flow(spacing: 8) {
                    let names = state.branches.map { $0.repository.split(separator: "/").last.map(String.init) ?? "" }
                    let ambiguous = Set(names).count < names.count
                    ForEach(state.branches) { branch in
                        Button {
                            branch.url.map(actions.open)
                        } label: {
                            HStack(alignment: .firstTextBaseline, spacing: 6) {
                                Circle().fill(branch.red ? Color.red : Color.green).frame(width: 8, height: 8)
                                    .alignmentGuide(.firstTextBaseline) { $0[.bottom] }
                                Text(ambiguous ? branch.repository
                                     : branch.repository.split(separator: "/").last.map(String.init) ?? "")
                                    .font(.callout.weight(.medium))
                                    .lineLimit(1)
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Capsule().fill(.quaternary.opacity(0.6)))
                            .contentShape(Capsule())
                        }
                        .buttonStyle(.plain)
                        .help("Open the runs on \(branch.repository)'s default branch, \(branch.branch), on GitHub")
                    }
                }
            }
        }
    }

    private var sessionsSwitch: some View {
        Toggle("Automatically send main failures to Codex", isOn: Binding(
            get: { state.sessions ?? false },
            set: { actions.setSessions($0) }))
            .toggleStyle(SwitchFirst())
            .lineLimit(1)
            .disabled(state.sessions == nil)
            .help(state.sessions == nil
                  ? "The configuration file has no [codex] section, which says where sessions start and what "
                    + "they are asked, so cibar starts none."
                  : "Whether each job that fails on a default branch starts a Codex session to fix it as it "
                    + "fails. A failure not sent can be sent by hand from its line.")
    }

    /// The Running column's heading, which stays put as the column scrolls.
    private var runningHeading: some View {
        Column(title: "Running", count: state.runs.count) {
            Toggle("Include PRs", isOn: Binding(
                get: { state.includesPullRequests },
                set: { actions.setIncludesPullRequests($0) }))
                .toggleStyle(.checkbox)
                .controlSize(.small)
                .help("Whether what runs on branches other than default ones, such as pull requests', is shown "
                    + "and counted, here and in the menu bar.")
        } content: {
            EmptyView()
        }
    }

    @ViewBuilder private var runningColumn: some View {
        Group {
            if !state.isConnected {
                Placeholder(symbol: "wifi.slash", text: "Not connected, so what is running is not known.")
            } else if state.runs.isEmpty {
                Placeholder(symbol: "pause.circle", text: "Nothing is running.")
            } else {
                ForEach(state.runs) { run in
                    RunRow(run: run, actions: actions).equatable()
                }
            }
        }
    }
}

/// A switch, with its label after it rather than before, which flips the
/// switch when clicked, as the label of a switch does.
private struct SwitchFirst: ToggleStyle {
    // SwiftUI's, not cibar's settings of the same name.
    func makeBody(configuration: ToggleStyleConfiguration) -> some View {
        SwitchThenLabel(configuration: configuration)
    }

    private struct SwitchThenLabel: View {
        var configuration: ToggleStyleConfiguration
        @Environment(\.isEnabled) private var isEnabled

        var body: some View {
            HStack(spacing: 8) {
                Toggle(isOn: configuration.$isOn) { configuration.label }
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .labelsHidden()
                configuration.label
                    .foregroundStyle(isEnabled ? .primary : .secondary)
                    .onTapGesture {
                        if isEnabled {
                            configuration.isOn.toggle()
                        }
                    }
            }
        }
    }
}

/// A big number, with what it counts.
private struct Tile: View {
    enum Value {
        case count(Int)
        case fraction(Int, Int)
        case unknown
    }

    var title: String
    var symbol: String
    var value: Value
    var detail: String
    var tint: Color

    var body: some View {
        VStack(alignment: .trailing, spacing: 2) {
            Label(title, systemImage: symbol)
                .font(.caption.weight(.semibold))
                .foregroundStyle(tint)
            Group {
                switch value {
                case .count(let count):
                    Odometer(value: Double(count))
                        .animation(.easeInOut(duration: 0.6), value: count)
                case .fraction(let part, let whole):
                    HStack(spacing: 0) {
                        Odometer(value: Double(part))
                            .animation(.easeInOut(duration: 0.6), value: part)
                        Text("/")
                        Odometer(value: Double(whole))
                            .animation(.easeInOut(duration: 0.6), value: whole)
                    }
                case .unknown:
                    Text("–")
                }
            }
            .font(.system(size: 34, weight: .semibold, design: .rounded))
            .monospacedDigit()
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2, reservesSpace: true)
                .multilineTextAlignment(.trailing)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .trailing)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(tint.opacity(0.12)))
    }
}

/// A column of the dashboard, which takes the height the dashboard has to
/// spare, and scrolls when it has more than fits.
private struct Scrolling<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                content
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .scrollBounceBehavior(.basedOnSize)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// A section of a column, with a heading, and anything to go at the end of
/// the heading's line.
private struct Column<Accessory: View, Content: View>: View {
    var title: String
    var count: Int
    @ViewBuilder var accessory: Accessory
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(.headline)
                if count > 0 {
                    Text("\(count)").font(.caption.weight(.semibold)).monospacedDigit()
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Capsule().fill(.quaternary))
                }
                Spacer()
                accessory
            }
            // The whole width of the column, so that anything at the end of
            // the heading's line is at the column's end.
            .frame(maxWidth: .infinity)
            content
        }
    }
}

extension Column where Accessory == EmptyView {
    init(title: String, count: Int, @ViewBuilder content: () -> Content) {
        self.init(title: title, count: count, accessory: { EmptyView() }, content: content)
    }
}

private struct Placeholder: View {
    var symbol: String
    var text: String

    var body: some View {
        Label(text, systemImage: symbol)
            .font(.callout)
            .foregroundStyle(.secondary)
            .padding(.vertical, 6)
    }
}

/// A workflow failing on a default branch, with links to its failing jobs.
/// The branch goes without saying.
private struct FailingCard: View, Equatable {
    var failing: DashboardState.Failing
    var actions: DashboardActions

    static func == (a: FailingCard, b: FailingCard) -> Bool {
        a.failing == b.failing
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                let workflowBranch = failing.workflowBranch
                Text("\(workflowBranch.repositoryName) · \(workflowBranch.workflow.abbreviatingHashes)")
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                    .help("On \(failing.workflowBranch.repository)'s default branch, \(failing.workflowBranch.branch)")
                Spacer(minLength: 0)
            }
            ForEach(failing.jobs) { job in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Button {
                        job.url.map(actions.open)
                    } label: {
                        Label {
                            Text(job.name).lineLimit(1)
                        } icon: {
                            Image(systemName: "xmark.circle.fill")
                                .symbolRenderingMode(.palette)
                                .foregroundStyle(.white, .red)
                        }
                        .font(.caption)
                    }
                    .buttonStyle(.plain)
                    .help("Open the job on GitHub")
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        CodexLine(job: job, open: actions.open) {
                            actions.send(JobKey(workflowBranch: failing.workflowBranch, job: job.id))
                        }
                        if let rerun = job.rerun {
                            Button {
                                actions.open(rerun.url)
                            } label: {
                                JobSymbol(running: rerun.running)
                            }
                            .buttonStyle(.plain)
                            .help((rerun.running ? "Running again" : "Queued to run again")
                                  + ". Open the later run of this job, which will replace this failure.")
                        }
                    }
                    .font(.caption)
                    .fixedSize()
                    .layoutPriority(1)
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.red.opacity(0.08)))
        .overlay(alignment: .leading) {
            UnevenRoundedRectangle(topLeadingRadius: 10, bottomLeadingRadius: 10, style: .continuous)
                .fill(Color.red)
                .frame(width: 3)
        }
    }
}

/// What cibar did about a job that failed: that it is sending it to Codex;
/// when it sent it, in words the system keeps up to date without the
/// dashboard being drawn again, opening the session in the Codex app; or a
/// button that sends it, if it can be sent; or why it was not sent.
private struct CodexLine: View {
    var job: DashboardState.Job
    var open: (URL) -> Void
    var send: () -> Void

    private var record: CodexRecord? { job.codex }

    var body: some View {
        if job.sending {
            Text("sending to Codex…").font(.caption).foregroundStyle(.secondary)
        } else if let record, let url = record.url {
            let ago = Text(.currentDate, format: .reference(to: record.at, allowedFields: [.minute, .hour, .day, .week],
                                                            maxFieldCount: 1, thresholdField: .week))
            Button {
                open(url)
            } label: {
                Text("sent to Codex \(ago)").font(.caption).foregroundStyle(.link)
            }
            .buttonStyle(.plain)
            .help("Open the session in Codex")
        } else if job.sendable {
            Button(action: send) {
                Text("send to Codex").font(.caption).foregroundStyle(.link)
            }
            .buttonStyle(.plain)
            .help("Start a Codex session to fix this job, which was not sent: \(reason).")
        } else {
            Text("(not sent to Codex; \(reason))").font(.caption).foregroundStyle(.secondary)
        }
    }

    /// Why the job was not sent; with nothing recorded, it failed while
    /// cibar was not listening.
    private var reason: String {
        guard case .notSent(let reason)? = record?.outcome else { return "cibar missed it" }
        return reason
    }
}

/// The jobs of a run in progress that have already failed, in red, each
/// opening its page.
private struct FailedLine: View {
    var jobs: [DashboardState.Job]
    var open: (URL) -> Void

    private var names: AttributedString {
        var line = AttributedString(jobs.count == 1 ? "Failed: " : "\(jobs.count) failed: ")
        for (index, job) in jobs.enumerated() {
            var name = AttributedString(job.name)
            name.link = job.url
            line += (index == 0 ? "" : ", ") + name
        }
        return line
    }

    /// Set out as the jobs beneath it are, so that its symbol and name line
    /// up with theirs.
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: "xmark.circle.fill")
                .symbolRenderingMode(.palette)
                .foregroundStyle(.white, .red)
                .frame(width: 12, height: 12)
            Text(names)
                .lineLimit(2)
                .tint(.red)
                .foregroundStyle(.red)
                .environment(\.openURL, OpenURLAction { url in
                    open(url)
                    return .handled
                })
        }
        .font(.caption)
        .padding(.leading, 12)
        .help(jobs.map(\.name).joined(separator: "\n"))
    }
}

/// A branch's name, at the end of a line.
private struct BranchName: View {
    var branch: String

    var body: some View {
        Text(branch.abbreviatingHashes)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .truncationMode(.middle)
    }
}

/// How a run's named jobs are set out: in as many columns as fit across
/// `width`, each as wide as the longest name needs, and as many rows of
/// them as are named at most; the rest are shown by their symbols alone.
struct JobColumns: Equatable {
    static let rows = 8
    /// How wide a job's symbol is with the space after it, and the space
    /// between columns.
    static let symbol: CGFloat = 18
    static let gap: CGFloat = 12

    var count: Int
    var width: CGFloat

    init(names: [String], width available: CGFloat) {
        width = Self.symbol + ceil(names.map(Self.width).max() ?? 0)
        count = max(1, Int((available + Self.gap) / (width + Self.gap)))
        // A single column no wider than there is room for, its names cut
        // short if they must be.
        if count == 1 && available > 0 {
            width = min(width, available)
        }
    }

    /// How many of `jobs` are named: as many as fill the rows, or all of
    /// them when a single job more would take no more room named than not.
    func named(of jobs: Int) -> Int {
        jobs > Self.rows * count + 1 ? Self.rows * count : jobs
    }

    /// How wide a job's name is in the caption it is written in.
    static func width(_ name: String) -> CGFloat {
        textWidth(name, .systemFont(ofSize: 10))
    }
}

/// A run in progress, and beneath it its jobs, in the order GitHub made
/// them, each opening its page: those running with a play sign, those
/// queued with a clock. The first few are named, in as many columns as fit;
/// the rest follow as their symbols alone, wrapped, so that every job is
/// there.
private struct RunRow: View, Equatable {
    var run: DashboardState.Run
    var actions: DashboardActions
    @State private var width: CGFloat = 0

    /// Rows are the same when their runs are, whatever their actions, which
    /// cannot be compared, so that a row whose run has not changed is left
    /// as it is when the dashboard changes.
    static func == (a: RunRow, b: RunRow) -> Bool {
        a.run == b.run
    }

    /// Who opened a run's pull request, which opens it.
    private func pullRequestLine(_ pullRequest: PullRequestInfo) -> AttributedString {
        var author = AttributedString(pullRequest.author)
        author.link = URL(string: "https://github.com/\(run.workflowBranch.repository)/pull/\(pullRequest.number)")
        author.foregroundColor = .secondary
        return author
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                // Text that opens the run, rather than a button, which would
                // not say where its words' baseline is, for the branch beside
                // it to stand on.
                Text(runTitle(repository: run.workflowBranch.repositoryName,
                              workflow: run.workflowBranch.workflow.abbreviatingHashes,
                              pullRequest: run.pullRequest?.number))
                    .font(.callout.weight(.semibold))
                    .lineLimit(1)
                    .onTapGesture { run.url.map(actions.open) }
                    .accessibilityAddTraits(.isButton)
                    .help("Open the run on GitHub")
                Spacer(minLength: 4)
                if let pullRequest = run.pullRequest {
                    Text(pullRequestLine(pullRequest))
                        .font(.caption)
                        .lineLimit(1)
                        .environment(\.openURL, OpenURLAction { url in
                            actions.open(url)
                            return .handled
                        })
                        .help(run.workflowBranch.branch)
                } else {
                    BranchName(branch: run.workflowBranch.branch)
                }
            }
            // The heading's line is as wide as the row is given, whatever
            // the jobs beneath it take, so the jobs are set out to fit it;
            // were the row itself measured, jobs that overflowed it would
            // widen it, and be set out wider still.
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
            if !run.failed.isEmpty {
                FailedLine(jobs: run.failed, open: actions.open)
            }
            let columns = JobColumns(names: run.jobs.map(\.name), width: width - 12)
            let named = columns.named(of: run.jobs.count)
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(columns.width), spacing: JobColumns.gap,
                                                         alignment: .leading), count: columns.count),
                      alignment: .leading, spacing: 3) {
                ForEach(run.jobs.prefix(named)) { job in
                    Button {
                        job.url.map(actions.open)
                    } label: {
                        HStack(spacing: 6) {
                            JobSymbol(running: job.running)
                            Text(job.name)
                                .lineLimit(1)
                                .foregroundStyle(job.running ? .primary : .secondary)
                        }
                        .font(.caption)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.leading, 12)
            if run.jobs.count > named {
                JobGrid(jobs: Array(run.jobs.dropFirst(named)), open: actions.open)
                    .padding(.leading, 12)
            }
        }
        .padding(.vertical, 4)
    }
}

/// A play sign for a job running, a clock for one queued. Still, rather
/// than spinning, since anything that moves has the whole dashboard drawn
/// afresh many times a second for as long as it shows.
private struct JobSymbol: View {
    var running: Bool

    var body: some View {
        Image(systemName: running ? "play.circle.fill" : "clock")
            .foregroundStyle(running ? Color.blue : Color.secondary)
            .frame(width: 12, height: 12)
    }
}

/// Jobs' symbols, wrapped onto as many lines as they need, each opening its
/// job's page, and naming it while pointed at. They are drawn as one, since
/// a view for each would cost much more to lay out each time the dashboard
/// changes.
private struct JobGrid: View {
    var jobs: [DashboardState.Job]
    var open: (URL) -> Void
    @State private var width: CGFloat = 0
    @State private var pointedAt: Int?

    /// How far apart the symbols are, and how big each is.
    static let cell: CGFloat = 15
    static let symbol: CGFloat = 12

    /// How many symbols fit on a line `width` wide: all of them when there is
    /// no end to it.
    static func perLine(_ width: CGFloat, of count: Int = .max) -> Int {
        width.isFinite ? max(1, Int((width + cell - symbol) / cell)) : max(1, count)
    }

    /// Where the symbol of the job at `index` is.
    static func place(_ index: Int, width: CGFloat) -> CGRect {
        let perLine = perLine(width)
        return CGRect(x: CGFloat(index % perLine) * cell, y: CGFloat(index / perLine) * cell,
                      width: symbol, height: symbol)
    }

    private func job(at point: CGPoint) -> Int? {
        guard point.x >= 0, point.y >= 0 else { return nil }
        let index = Int(point.y / Self.cell) * Self.perLine(width) + Int(point.x / Self.cell)
        return index < jobs.count && Self.place(index, width: width).contains(point) ? index : nil
    }

    var body: some View {
        GridLayout(count: jobs.count) {
            Canvas { context, size in
                // As big as the symbols beside the jobs named above them,
                // which are as big as the caption they go with.
                let running = context.resolve(Text(Image(systemName: "play.circle.fill")).font(.caption)
                    .foregroundStyle(Color.blue))
                let queued = context.resolve(Text(Image(systemName: "clock")).font(.caption)
                    .foregroundStyle(Color.secondary))
                for (index, job) in jobs.enumerated() {
                    let place = Self.place(index, width: size.width)
                    context.draw(job.running ? running : queued, at: CGPoint(x: place.midX, y: place.midY))
                }
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .contentShape(Rectangle())
        .onContinuousHover { phase in
            if case .active(let point) = phase {
                pointedAt = job(at: point)
            } else {
                pointedAt = nil
            }
        }
        .onTapGesture { point in
            job(at: point).flatMap { jobs[$0].url }.map(open)
        }
        .overlay(alignment: .topLeading) {
            if let pointedAt, pointedAt < jobs.count {
                let place = Self.place(pointedAt, width: width)
                Text("\(jobs[pointedAt].name): \(jobs[pointedAt].running ? "running" : "queued")")
                    .font(.caption2)
                    .padding(.horizontal, 4)
                    .padding(.vertical, 1)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 4))
                    .fixedSize()
                    .offset(x: place.minX, y: place.maxY + 2)
                    .allowsHitTesting(false)
            }
        }
    }
}

/// Views, each as big as it would be, one after another along a line, onto
/// as many lines as they need.
private struct Flow: Layout {
    var spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let places = arrange(subviews, width: proposal.width ?? .infinity)
        return CGSize(width: places.map(\.maxX).max() ?? 0, height: places.map(\.maxY).max() ?? 0)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for (subview, place) in zip(subviews, arrange(subviews, width: bounds.width)) {
            subview.place(at: CGPoint(x: bounds.minX + place.minX, y: bounds.minY + place.minY),
                          proposal: ProposedViewSize(place.size))
        }
    }

    /// Where each view goes: after the one before it, or at the start of the
    /// next line when it would not fit after it on this one.
    private func arrange(_ subviews: Subviews, width: CGFloat) -> [CGRect] {
        var places: [CGRect] = []
        var origin = CGPoint.zero
        var lineHeight: CGFloat = 0
        for subview in subviews {
            var size = subview.sizeThatFits(.unspecified)
            size.width = min(size.width, width)
            if origin.x > 0 && origin.x + size.width > width {
                origin = CGPoint(x: 0, y: origin.y + lineHeight + spacing)
                lineHeight = 0
            }
            places.append(CGRect(origin: origin, size: size))
            origin.x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
        return places
    }
}

/// Lays out a `JobGrid`'s drawing as tall as its symbols need at the width
/// it is given.
private struct GridLayout: Layout {
    var count: Int

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let all = CGFloat(count) * JobGrid.cell
        let width = proposal.width.map { $0.isFinite ? $0 : all } ?? all
        let perLine = JobGrid.perLine(width, of: count)
        return CGSize(width: width, height: CGFloat((count + perLine - 1) / perLine) * JobGrid.cell)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(bounds.size))
    }
}

/// A number whose digits turn like an odometer's wheels when it changes,
/// each through every step from where it was to where it is to be, over the
/// same time, so that the last turns fastest and each before it a tenth as
/// fast, up as the number rises and down as it falls. Before the number's
/// first digit, a wheel shows a blank, which a digit that comes or goes
/// turns from or to as it would from or to any other.
struct Odometer: View {
    var value: Double

    /// How many places an odometer has room for, of which those before the
    /// number's first digit take no room.
    static let places = 7

    /// How big each digit is.
    static let digit: CGSize = {
        let font = NSFont.monospacedDigitSystemFont(ofSize: 34, weight: .semibold)
        let rounded = font.fontDescriptor.withDesign(.rounded).flatMap { NSFont(descriptor: $0, size: 34) } ?? font
        let width = NSAttributedString(string: "0", attributes: [.font: rounded]).size().width
        return CGSize(width: ceil(width), height: ceil(NSLayoutManager().defaultLineHeight(for: rounded)))
    }()

    var body: some View {
        HStack(spacing: 0) {
            ForEach((0..<Self.places).reversed(), id: \.self) { place in
                OdometerWheel(position: wheel(place), leading: place > 0)
            }
        }
    }

    /// Where the wheel for a place rests when the number is `value`: the
    /// number the digits from that place on make.
    func wheel(_ place: Int) -> Double {
        (max(value, 0) / pow(10, Double(place))).rounded(.down)
    }
}

/// One wheel of an odometer, turned to `position`, showing the digit it has
/// reached and the next, each in part. A wheel before the number's first
/// digit, at nothing, shows a blank, and takes no room once it rests there.
private struct OdometerWheel: View, Animatable {
    var position: Double
    var leading: Bool

    var animatableData: Double {
        get { position }
        set { position = newValue }
    }

    var body: some View {
        let whole = position.rounded(.down)
        let fraction = position - whole
        let size = Odometer.digit
        VStack(spacing: 0) {
            face(Int(whole), away: fraction)
            face(Int(whole) + 1, away: 1 - fraction)
        }
        .offset(y: (0.5 - fraction) * size.height)
        .frame(width: size.width, height: size.height)
        .frame(width: leading && position <= 0 ? 0 : size.width, height: size.height, alignment: .trailing)
        .clipped()
    }

    /// The face for where the wheel has turned to, `number` being the
    /// number the digits from its place on make there: its last digit, or a
    /// blank before the number's first, faded, blurred and shrunk the
    /// further it has turned from where it shows whole.
    private func face(_ number: Int, away: Double) -> some View {
        Text(verbatim: leading && number <= 0 ? " " : "\((number % 10 + 10) % 10)")
            .frame(width: Odometer.digit.width, height: Odometer.digit.height)
            .scaleEffect(1 - away * 0.3)
            .blur(radius: away * 5)
            .opacity(1 - away)
    }
}

/// The window the dashboard shows in: a panel with rounded corners, just
/// below the indicator, which takes the keyboard without cibar becoming the
/// active app, as panels such as Spotlight's do, so that it can be used at
/// once while the app in use stays so. macOS does not make an app the
/// active one just because it asks.
final class DashboardPanel: NSPanel {
    /// What Escape does to the popup, and what tearing the panel off does.
    var onEscape: () -> Void = {}
    var onTearOff: () -> Void = {}
    /// Whether the panel has been dragged away from the indicator, which
    /// makes it a window like any other, which stays until it is closed.
    private(set) var isTornOff = false
    private let handle = TearOffHandle()
    /// What the content is told of the title bar a torn-off panel has over
    /// it, or that it has none once the panel is a popup again.
    var onTitleBar: (TitleBar) -> Void = { _ in }
    private static let popupBehavior: NSWindow.CollectionBehavior =
        [.canJoinAllSpaces, .fullScreenAuxiliary, .transient, .ignoresCycle]

    init(showing content: NSView) {
        super.init(contentRect: .zero, styleMask: [.nonactivatingPanel, .borderless], backing: .buffered,
                   defer: true)
        isFloatingPanel = true
        level = .popUpMenu
        becomesKeyOnlyIfNeeded = false
        hidesOnDeactivate = false
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        isReleasedWhenClosed = false
        collectionBehavior = Self.popupBehavior
        let background = NSVisualEffectView()
        background.material = .popover
        background.blendingMode = .behindWindow
        background.state = .active
        background.maskImage = Self.roundedCorners(radius: 12)
        for view in [content, handle] {
            view.translatesAutoresizingMaskIntoConstraints = false
            background.addSubview(view)
            NSLayoutConstraint.activate([
                view.leadingAnchor.constraint(equalTo: background.leadingAnchor),
                view.trailingAnchor.constraint(equalTo: background.trailingAnchor),
            ])
        }
        NSLayoutConstraint.activate([
            content.topAnchor.constraint(equalTo: background.topAnchor),
            content.bottomAnchor.constraint(equalTo: background.bottomAnchor),
            handle.topAnchor.constraint(equalTo: background.topAnchor),
            handle.heightAnchor.constraint(equalToConstant: TearOffHandle.height),
        ])
        contentView = background
    }

    override var canBecomeKey: Bool { true }

    /// Escape closes the dashboard, torn off or not.
    override func cancelOperation(_ sender: Any?) {
        if isTornOff {
            close()
        } else {
            onEscape()
        }
    }

    /// Drags the panel with the mouse until its button is let go, as its
    /// handle and the runners board ask, tearing it off once the mouse has
    /// moved a few points.
    override func performDrag(with event: NSEvent) {
        let start = NSEvent.mouseLocation, origin = frame.origin
        while let next = nextEvent(matching: [.leftMouseDragged, .leftMouseUp]), next.type == .leftMouseDragged {
            let mouse = NSEvent.mouseLocation
            let offset = CGSize(width: mouse.x - start.x, height: mouse.y - start.y)
            guard isTornOff || hypot(offset.width, offset.height) >= 3 else { continue }
            tearOff()
            setFrameOrigin(NSPoint(x: origin.x + offset.width, y: origin.y + offset.height))
        }
    }

    /// What a torn-off panel has, as torn-off palettes do: a slim title bar,
    /// which shows nothing of its own but a small button that closes it, over
    /// its content, which is told how tall it is, so as to keep clear of it
    /// or put things in it. It stays the size it was, as the runners board
    /// is laid out for that size.
    private static let windowStyle: NSWindow.StyleMask = [.titled, .closable, .utilityWindow, .fullSizeContentView]

    /// Makes the panel a window like any other: it stays where it is put,
    /// behind other windows or in front of them, until it is closed, which
    /// its title bar's small button does.
    func tearOff() {
        guard !isTornOff else { return }
        isTornOff = true
        let kept = frame
        title = applicationName
        titleVisibility = .hidden
        titlebarAppearsTransparent = true
        styleMask.formUnion(Self.windowStyle)
        // Not floating over other apps' windows, as a palette would.
        level = .normal
        collectionBehavior = [.managed, .participatesInCycle, .fullScreenAuxiliary]
        setFrame(kept, display: true)
        for button in [NSWindow.ButtonType.miniaturizeButton, .zoomButton] {
            standardWindowButton(button)?.isHidden = true
        }
        handle.isHidden = true
        contentView?.superview?.layoutSubtreeIfNeeded()
        let height = frame.height - contentLayoutRect.height
        let middle = standardWindowButton(.closeButton).map { frame.height - $0.convert($0.bounds, to: nil).midY }
        onTitleBar(TitleBar(height: height, middle: middle ?? height / 2))
        onTearOff()
    }

    /// Closes the panel, which, if it was torn off, is a popup again the next
    /// time it opens.
    override func close() {
        super.close()
        guard isTornOff else { return }
        isTornOff = false
        level = .popUpMenu
        collectionBehavior = Self.popupBehavior
        styleMask.subtract(Self.windowStyle)
        handle.isHidden = false
        onTitleBar(.none)
    }

    /// A mask with rounded corners, which stretches to any size.
    private static func roundedCorners(radius: CGFloat) -> NSImage {
        let edge = radius * 2 + 1
        let image = NSImage(size: NSSize(width: edge, height: edge), flipped: false) { rect in
            NSColor.black.setFill()
            NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
            return true
        }
        image.capInsets = NSEdgeInsets(top: radius, left: radius, bottom: radius, right: radius)
        image.resizingMode = .stretch
        return image
    }
}

/// A strip along the top of the dashboard, with a grip, that drags it,
/// which tears it off into a window of its own; once torn off, the panel
/// hides it, as the window has a title bar of its own.
final class TearOffHandle: NSView {
    static let height: CGFloat = 14

    private let grip = CALayer()

    init() {
        super.init(frame: .zero)
        wantsLayer = true
        grip.cornerRadius = 2
        layer?.addSublayer(grip)
        toolTip = "Drag to tear the dashboard off into a window of its own."
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { nil }

    override func layout() {
        super.layout()
        grip.frame = CGRect(x: bounds.midX - 18, y: bounds.maxY - 8, width: 36, height: 4)
    }

    override func updateLayer() {
        grip.backgroundColor = NSColor.tertiaryLabelColor.cgColor
    }

    override var wantsUpdateLayer: Bool { true }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with event: NSEvent) {
        window?.performDrag(with: event)
    }
}

/// Where the dashboard goes, `size` big, below the indicator, which is at
/// `indicator` on the screen: centred under it, a little below, and moved
/// in from the screen's edges, `visible` being the part of the screen
/// windows may cover.
func dashboardFrame(below indicator: CGRect, size: CGSize, within visible: CGRect) -> CGRect {
    let margin: CGFloat = 8
    let x = min(max(indicator.midX - size.width / 2, visible.minX + margin), visible.maxX - margin - size.width)
    return CGRect(x: x, y: indicator.minY - 6 - size.height, width: size.width, height: size.height)
}

/// How big the dashboard is on a screen whose visible part is
/// `screenHeight` points high: always as big, so that it does not jump about
/// as what it shows changes, but never taller than the screen.
func dashboardSize(screenHeight: CGFloat) -> NSSize {
    NSSize(width: 780, height: min(640, screenHeight - 60))
}

/// Whether cibar is connected to the relay, as a coloured dot and a few
/// words.
private struct ConnectionLabel: View {
    var connection: DashboardState.Connection
    var lateness: Lateness

    var body: some View {
        let (color, text): (Color, String) = switch connection {
        case .connected:
            (.green, "Connected")
        case .connecting:
            (.orange, "Connecting…")
        case .disconnected(_, let retryAt):
            (.red, retryAt.map {
                "Trying again at \(DateFormatter.localizedString(from: $0, dateStyle: .none, timeStyle: .medium))"
            } ?? "Not connected")
        case .unusableSettings:
            (.red, "Not connected")
        }
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Circle().fill(color).frame(width: 7, height: 7)
                .alignmentGuide(.firstTextBaseline) { $0[.bottom] }
            Text(text).font(.caption).foregroundStyle(.secondary).lineLimit(1).fixedSize()
            if connection == .connected, lateness.latest != nil {
                // Its bars stand, and its latency sits, on the line the
                // words beside it sit on.
                LatencyChart(lateness: lateness)
                    .alignmentGuide(.firstTextBaseline) { $0[.bottom] }
            }
        }
        .help(connection == .connected && lateness.latest != nil
              ? "How late each delivery of the last three minutes arrived after GitHub made it, and the latest. "
                + "GitHub or the relay may be delivering slowly, a delivery may have waited while the computer "
                + "slept, or this computer's clock may be wrong."
              : "")
    }
}

/// How late each delivery of the last few seconds arrived, as a bar where
/// it arrived, now at the right, from no latency at the bottom to eight
/// seconds or more at the top, coloured from grey through orange to red by
/// how tall it is; and the latest delivery's latency, fading as it grows
/// old. Core Animation scrolls the bars and fades the latency, as smoothly
/// as the screen can show, at no cost to cibar between deliveries.
struct LatencyChart: NSViewRepresentable {
    var lateness: Lateness

    func makeNSView(context: Context) -> LatencyChartView {
        LatencyChartView()
    }

    func updateNSView(_ view: LatencyChartView, context: Context) {
        view.show(lateness)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView view: LatencyChartView, context: Context) -> CGSize? {
        LatencyChartView.size
    }
}

final class LatencyChartView: NSView {
    static let chartWidth: CGFloat = 90
    static let gap: CGFloat = 6
    static let labelWidth: CGFloat = 80
    static let height: CGFloat = 16
    static var size: CGSize { CGSize(width: chartWidth + gap + labelWidth, height: height) }
    /// How wide each bar is, how short the shortest, and the latency at the
    /// top, beyond which bars are no taller.
    static let barWidth: CGFloat = 2
    static let shortest: CGFloat = 1.5
    static let top: TimeInterval = 8
    /// How long after a delivery its latency starts to fade, and how long it
    /// takes to fade away, so that it is gone once the delivery's bar has
    /// scrolled off the chart.
    static let fresh: CFTimeInterval = 2
    static let fading: CFTimeInterval = Lateness.remembered - fresh

    /// The gradient the bars show, which stays put while they, as its mask,
    /// scroll across it.
    private let colours = CAGradientLayer()
    private let bars = CALayer()
    private let label = CATextLayer()
    private var shown: [(arrived: CFTimeInterval, bar: CALayer)] = []
    private var latest: Date?
    /// When, by Core Animation's clock, the bars' left edge was at the
    /// chart's right edge, and until when they have been set scrolling.
    private let origin = CACurrentMediaTime()
    private var scrollingUntil: CFTimeInterval = 0

    private static let pointsPerSecond = chartWidth / CGFloat(Lateness.remembered)
    private static let font = NSFont.monospacedDigitSystemFont(ofSize: 10, weight: .regular)

    init() {
        super.init(frame: NSRect(origin: .zero, size: Self.size))
        wantsLayer = true
        guard let layer else { return }
        colours.frame = CGRect(x: 0, y: 0, width: Self.chartWidth, height: Self.height)
        colours.startPoint = CGPoint(x: 0.5, y: 0)
        colours.endPoint = CGPoint(x: 0.5, y: 1)
        colours.locations = [0, NSNumber(value: 2 / Self.top), NSNumber(value: Lateness.late / Self.top), 1]
        bars.anchorPoint = .zero
        bars.bounds = CGRect(x: 0, y: 0, width: 1, height: Self.height)
        colours.mask = bars
        layer.addSublayer(colours)
        // The latency's baseline is the bars' foot, the view's bottom edge.
        label.frame = CGRect(x: Self.chartWidth + Self.gap, y: Self.font.descender,
                             width: Self.labelWidth, height: Self.font.ascender - Self.font.descender)
        label.contentsScale = 2
        layer.addSublayer(label)
        recolour()
        scroll()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { nil }

    override var intrinsicContentSize: NSSize { Self.size }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        recolour()
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        label.contentsScale = window?.backingScaleFactor ?? 2
    }

    /// How many bars there are, which tests ask.
    var barCount: Int { shown.count }

    /// When the latency shown will have faded away, if one is shown.
    private(set) var latencyGoneAt: CFTimeInterval?

    /// Adds a bar for each delivery not yet shown, forgets those that have
    /// scrolled away, and tells the latest's latency afresh.
    func show(_ lateness: Lateness) {
        let now = CACurrentMediaTime(), date = Date()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        if now > scrollingUntil - 1800 {
            scroll()
        }
        for delivery in lateness.deliveries where latest.map({ delivery.arrived > $0 }) ?? true {
            let arrived = now - date.timeIntervalSince(delivery.arrived)
            let bar = CALayer()
            bar.backgroundColor = NSColor.black.cgColor
            let height = max(Self.shortest, Self.height * CGFloat(min(delivery.seconds, Self.top) / Self.top))
            bar.frame = CGRect(x: CGFloat(arrived - origin) * Self.pointsPerSecond - Self.barWidth, y: 0,
                               width: Self.barWidth, height: height)
            bars.addSublayer(bar)
            shown.append((arrived, bar))
        }
        let gone = now - Lateness.remembered - 1
        shown.filter { $0.arrived < gone }.forEach { $0.bar.removeFromSuperlayer() }
        shown.removeAll { $0.arrived < gone }
        if let last = lateness.deliveries.last, last.arrived != latest {
            latest = last.arrived
            tell(lateness, at: now - date.timeIntervalSince(last.arrived))
        }
        CATransaction.commit()
    }

    /// Writes the latest latency, and fades it as it grows old.
    private func tell(_ lateness: Lateness, at arrived: CFTimeInterval) {
        guard let text = lateness.text else { return }
        var colour = NSColor.secondaryLabelColor.cgColor
        effectiveAppearance.performAsCurrentDrawingAppearance {
            colour = (lateness.isLate ? NSColor.systemOrange : NSColor.secondaryLabelColor).cgColor
        }
        label.string = NSAttributedString(string: "latency: \(text)",
                                          attributes: [.font: Self.font, .foregroundColor: colour])
        label.removeAnimation(forKey: "fade")
        label.opacity = 0
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = Float(1)
        fade.toValue = Float(0)
        fade.beginTime = arrived + Self.fresh
        fade.duration = Self.fading
        fade.fillMode = .backwards
        label.add(fade, forKey: "fade")
        latencyGoneAt = fade.beginTime + fade.duration
    }

    /// Sets the bars scrolling left, at a steady pace, for an hour.
    private func scroll() {
        let now = CACurrentMediaTime(), span: CFTimeInterval = 3600
        func x(_ time: CFTimeInterval) -> CGFloat {
            Self.chartWidth - CGFloat(time - origin) * Self.pointsPerSecond
        }
        let move = CABasicAnimation(keyPath: "position.x")
        move.fromValue = x(now)
        move.toValue = x(now + span)
        move.duration = span
        move.beginTime = now
        bars.position = CGPoint(x: x(now + span), y: 0)
        bars.add(move, forKey: "scroll")
        scrollingUntil = now + span
    }

    private func recolour() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            colours.colors = [NSColor.secondaryLabelColor, .secondaryLabelColor, .systemOrange, .systemRed]
                .map(\.cgColor)
        }
    }
}

