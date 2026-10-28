import AppKit

// When a branch turns red, the indicator can hand it to a coding agent.
//
// Codex is given a session that starts working without anyone pressing
// anything. The Codex command-line tool that ships inside the Codex app
// creates the session by running the request outside the app, then queues
// "Execute.", and the app runs that message when it loads the session.
//
// Claude is given a new session with the request already written in it.
// The Claude app does not send a request that arrives by link, so it waits
// for the user to send it.

enum Agent: String, CaseIterable {
    case codex
    case claude

    var title: String {
        switch self {
        case .codex: return "Start a Codex session to fix it"
        case .claude: return "Open a Claude session, ready to send"
        }
    }
}

let noAgentTitle = "Do nothing more"

/// What happened when a branch was handed off, for the menu.
enum HandOffOutcome {
    /// The session was started. Opening the address, when there is one,
    /// shows the session.
    case started(String, URL?)
    case failed(String, detail: String)

    var summary: String {
        switch self {
        case .started(let summary, _): return summary
        case .failed(let summary, let detail): return "\(summary): \(detail)"
        }
    }
}

/// The request to the agent, followed by `report`, which says what failed.
func handOffPrompt(_ status: BranchStatus, report: String) -> String {
    var prompt = "Select an appropriate worktree and fix the red \(status.branch) on "
        + status.repository.slug
    if let url = status.checksURL {
        prompt += ": \(url.absoluteString)"
    }
    return prompt + "\n\n" + report
}

/// Hands the branch to the agent and calls back on the main thread once the
/// session has been started, or has failed to start.
func handOff(_ status: BranchStatus, report: String, to agent: Agent, in folder: URL,
             completion: @escaping (HandOffOutcome) -> Void) {
    let prompt = handOffPrompt(status, report: report)
    let target = "\(status.repository.name) \(status.branch)"
    switch agent {
    case .claude:
        completion(openClaudeSession(prompt: prompt, folder: folder, target: target))
    case .codex:
        guard let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: codexBundleIdentifier)
        else {
            completion(.failed("Could not start a Codex session",
                               detail: "The Codex app is not installed."))
            return
        }
        let tool = app.appendingPathComponent(codexToolPath)
        DispatchQueue.global(qos: .userInitiated).async {
            let outcome = startCodexSession(tool: tool, prompt: prompt, folder: folder,
                                            target: target)
            DispatchQueue.main.async {
                if case .started(_, let url?) = outcome, !NSWorkspace.shared.open(url) {
                    completion(.failed("Could not open the Codex session",
                                       detail: "It continues once \(url.absoluteString) is opened."))
                    return
                }
                completion(outcome)
            }
        }
    }
}

func openClaudeSession(prompt: String, folder: URL, target: String) -> HandOffOutcome {
    var components = URLComponents()
    components.scheme = "claude"
    components.host = "code"
    components.path = "/new"
    // The app reads the query as a form, where a plus sign is a space.
    let allowed = CharacterSet.urlQueryAllowed.subtracting(CharacterSet(charactersIn: "+&="))
    components.percentEncodedQueryItems = [("q", prompt), ("folder", folder.path)].map {
        URLQueryItem(name: $0, value: $1.addingPercentEncoding(withAllowedCharacters: allowed))
    }
    guard let url = components.url, NSWorkspace.shared.open(url) else {
        return .failed("Could not open a Claude session", detail: "Is the Claude app installed?")
    }
    return .started("Opened a Claude session to send for \(target) at "
                        + timeOfDayFormatter.string(from: Date()), nil)
}

let codexBundleIdentifier = "com.openai.codex"
let codexToolPath = "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"

/// One line of what `codex exec --json` prints.
private struct CodexEvent: Decodable {
    struct Failure: Decodable {
        let message: String
    }

    let type: String
    let threadID: String?
    let message: String?
    let error: Failure?

    enum CodingKeys: String, CodingKey {
        case type
        case threadID = "thread_id"
        case message
        case error
    }
}

/// Creates the session with the request and queues "Execute.". Blocks until
/// the first turn is over, so it runs off the main thread. The returned
/// address loads the session in the app, which runs the queued message.
func startCodexSession(tool: URL, prompt: String, folder: URL, target: String) -> HandOffOutcome {
    let failed = "Could not start a Codex session"
    do {
        let created = try runTool(tool, [
            "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only",
            "--cd", folder.path, prompt,
        ])
        let events = created.output.split(separator: "\n").compactMap { line in
            try? JSONDecoder().decode(CodexEvent.self, from: Data(line.utf8))
        }
        guard created.status == 0,
              let thread = events.first(where: { $0.type == "thread.started" })?.threadID else {
            let reported = events.last { $0.type == "error" || $0.type == "turn.failed" }
            return .failed(failed, detail: reported?.error?.message ?? reported?.message
                ?? created.failure("codex exec"))
        }
        let queued = try runTool(tool, ["queue", "--thread", thread, "--message", "Execute."])
        guard queued.status == 0 else {
            return .failed(failed, detail: queued.failure("codex queue"))
        }
        guard let url = URL(string: "codex://threads/\(thread)") else {
            return .failed(failed, detail: "Codex named the session \(thread).")
        }
        return .started("Started a Codex session for \(target) at "
                            + timeOfDayFormatter.string(from: Date()), url)
    } catch {
        return .failed(failed, detail: error.localizedDescription)
    }
}
