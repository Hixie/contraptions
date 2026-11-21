import Foundation
import os

let applicationName = "cibar"
let defaultConfigurationPath = "~/.config/cibar/config.ini"

/// The system log keeps every line, however cibar was started;
/// `/usr/bin/log show --predicate 'subsystem == "local.cibar"'` lists them.
let systemLog = Logger(subsystem: "local.cibar", category: "events")

/// Where `log` sends each line: the system log and standard error. The
/// tests keep the lines instead, to check them, so that they add nothing
/// to cibar's own log.
var logLine: (String) -> Void = { message in
    systemLog.notice("\(message, privacy: .public)")
    // Once the terminal cibar was started from has gone, writing fails, and
    // the line is dropped.
    fputs("\(ISO8601DateFormatter().string(from: Date())) \(message)\n", stderr)
}

func log(_ message: String) {
    logLine(message)
}

struct Problem: Error, CustomStringConvertible {
    let description: String
}

let defaultCodexPrompt = """
    Select an appropriate worktree and fix the failing {job} job of the {workflow} workflow \
    on the {branch} branch of {repository}: {job_url}

    {report}
    """

/// The settings as the configuration file holds them, each as the text the
/// settings window edits.
struct Settings: Equatable {
    struct Codex: Equatable {
        var folder = "~"
        var prompt = defaultCodexPrompt
    }

    var address = ""
    var owners = ""
    /// The `[codex]` section's settings, or nothing when the file has none.
    var codex: Codex? = Codex()

    static let keys: [String: Set<String>] = [
        "relay": ["address"],
        "github": ["owners"],
        "codex": ["folder", "prompt"],
    ]

    init() {}

    /// Reads the settings out of a configuration file's text. Lines starting
    /// with `#` or `;` are comments. In values, `\n` stands for a line break
    /// and `\\` for a backslash. A file that sets a key cibar does not know,
    /// or sets one twice, is refused, so that a misspelling is not quietly
    /// ignored.
    init(text: String, name: String = "the configuration") throws {
        var values: [String: [String: String]] = [:]
        var section: String?
        for (index, raw) in text.split(separator: "\n", omittingEmptySubsequences: false).enumerated() {
            let line = raw.trimmingCharacters(in: .whitespaces)
            func refuse(_ why: String) -> Problem {
                Problem(description: "\(name), line \(index + 1): \(why)")
            }
            if line.isEmpty || line.hasPrefix("#") || line.hasPrefix(";") {
                continue
            }
            if line.hasPrefix("["), line.hasSuffix("]") {
                let name = String(line.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
                guard Self.keys[name] != nil else { throw refuse("there is no section [\(name)]") }
                section = name
                values[name] = values[name] ?? [:]
                continue
            }
            guard let equals = line.firstIndex(of: "=") else {
                throw refuse("expected `key = value`")
            }
            let key = line[..<equals].trimmingCharacters(in: .whitespaces)
            guard let section else { throw refuse("\(key) is not in a section") }
            guard Self.keys[section]?.contains(key) == true else {
                throw refuse("[\(section)] has no setting \(key)")
            }
            guard values[section]?[key] == nil else { throw refuse("\(key) is set twice") }
            values[section]?[key] = unescape(line[line.index(after: equals)...]
                .trimmingCharacters(in: .whitespaces))
        }
        address = values["relay"]?["address"] ?? ""
        owners = values["github"]?["owners"] ?? ""
        codex = values["codex"].map {
            Codex(folder: $0["folder"] ?? "~", prompt: $0["prompt"] ?? defaultCodexPrompt)
        }
    }

    /// The configuration file that holds these settings.
    var text: String {
        var text = """
            # cibar's configuration. The settings window writes it, and it can be
            # edited by hand too.

            # The relay of GitHub webhook deliveries, as HOST:PORT.
            [relay]
            address = \(escape(address))

            # The repository owners to follow, separated by commas; empty follows all.
            [github]
            owners = \(escape(owners))

            """
        if let codex {
            text += """

                # What to do when a job fails: the folder each Codex session starts
                # in, and the request it is sent. An empty request starts no
                # session. Without this section, no sessions start. In the request,
                # \\n is a line break, \\\\ is a backslash, and {repository}, {branch},
                # {workflow}, {job}, {job_url}, {event}, {sha}, {run_url},
                # {checks_url} and {report} are filled in.
                [codex]
                folder = \(escape(codex.folder))
                prompt = \(escape(codex.prompt))

                """
        }
        return text
    }
}

/// What to do when something fails: start a Codex session in `folder`, and
/// send it `prompt`, unless it is empty.
struct CodexSettings: Equatable {
    var folder: URL
    var prompt: String
}

/// The settings, as cibar uses them.
struct Configuration: Equatable {
    var host: String
    var port: UInt16
    /// The repository owners whose deliveries count. Empty means every owner.
    var owners: Set<String>
    /// What to do when something fails, or nothing when the file has no
    /// `[codex]` section.
    var codex: CodexSettings?

    /// Whether this connects to the same relay as `other`, and takes the
    /// same owners' deliveries, so that nothing needs reading again.
    func connectsLike(_ other: Configuration) -> Bool {
        host == other.host && port == other.port && owners == other.owners
    }

    init(contentsOf url: URL) throws {
        let text: String
        do {
            text = try String(contentsOf: url, encoding: .utf8)
        } catch {
            throw Problem(description: "cannot read \(url.path): \(error.localizedDescription)")
        }
        try self.init(text: text, name: url.lastPathComponent)
    }

    init(text: String, name: String = "the configuration") throws {
        try self.init(Settings(text: text, name: name), name: name)
    }

    /// Checks `settings`, and takes what they say.
    init(_ settings: Settings, name: String = "the configuration") throws {
        let address = settings.address
        guard let colon = address.lastIndex(of: ":"), let port = UInt16(address[address.index(after: colon)...]),
              colon > address.startIndex else {
            throw Problem(description: "\(name) must set address in [relay] to HOST:PORT")
        }
        host = String(address[..<colon])
        self.port = port
        owners = Set(settings.owners.split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty })
        codex = settings.codex.map { codex in
            CodexSettings(
                folder: URL(fileURLWithPath: NSString(string: codex.folder).expandingTildeInPath,
                            isDirectory: true,
                            relativeTo: URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true))
                    .standardizedFileURL,
                prompt: codex.prompt)
        }
    }
}

/// Writes a line break as `\n` and a backslash as `\\`, so that a value
/// fits on one line of the file.
private func escape(_ text: String) -> String {
    text.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\n", with: "\\n")
}

/// Replaces `\n` with a line break and `\\` with a backslash.
private func unescape(_ text: String) -> String {
    var result = ""
    var escaped = false
    for character in text {
        if escaped {
            result += character == "n" ? "\n" : character == "\\" ? "\\" : "\\\(character)"
            escaped = false
        } else if character == "\\" {
            escaped = true
        } else {
            result.append(character)
        }
    }
    return escaped ? result + "\\" : result
}
