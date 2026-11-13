import Foundation

// github-hookbar's configuration file, and what it says.

struct Problem: Error, CustomStringConvertible {
    let description: String
}

/// The settings the configuration file holds, each named by its section and
/// key, with how the settings window shows it.
let settings: [(name: String, label: String, placeholder: String)] = [
    ("relay.address", "Relay:", "host:port"),
    ("github.authors", "Authors:", "everyone"),
    ("github.orgs", "Repository owners:", "anyone"),
    ("github.actions", "Actions:", "opened"),
    ("command.run", "Command:", "open"),
]

/// What one line of INI text is: a "[section]" header, a "key = value"
/// setting, nothing (a blank line, or a comment starting with "#" or ";"),
/// or none of those.
enum INILine {
    case section(String)
    case setting(key: String, value: String)
    case nothing
    case invalid

    init(_ line: String) {
        let line = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if line.isEmpty || line.hasPrefix("#") || line.hasPrefix(";") {
            self = .nothing
        } else if line.hasPrefix("["), line.hasSuffix("]") {
            self = .section(line.dropFirst().dropLast().trimmingCharacters(in: .whitespaces)
                .lowercased())
        } else if let equals = line.firstIndex(of: "=") {
            self = .setting(key: line[..<equals].trimmingCharacters(in: .whitespaces).lowercased(),
                            value: line[line.index(after: equals)...]
                                .trimmingCharacters(in: .whitespaces))
        } else {
            self = .invalid
        }
    }
}

/// INI text, with the line each setting is on. Settings are named
/// "section.key", and each may be set only once. Changing a setting changes
/// only its own line, so the rest of the text stays as it was.
struct INIFile {
    /// The settings, in the order the text sets them.
    private(set) var names: [String] = []
    private(set) var values: [String: String] = [:]
    private var lines: [String]
    private var lineOfSetting: [String: Int] = [:]
    /// Where a new setting in each section goes: after the section's last
    /// setting, or after its header when it has none.
    private var endOfSection: [String: Int] = [:]

    init(_ text: String) throws {
        lines = text.components(separatedBy: "\n")
        if lines.last == "" {
            lines.removeLast()
        }
        var section = ""
        for (index, line) in lines.enumerated() {
            switch INILine(line) {
            case .section(let name):
                section = name
                endOfSection[name] = index + 1
            case .setting(let key, let value):
                let name = "\(section).\(key)"
                guard values[name] == nil else {
                    throw Problem(description: "\(name) is set more than once")
                }
                names.append(name)
                values[name] = value
                lineOfSetting[name] = index
                endOfSection[section] = index + 1
            case .nothing:
                break
            case .invalid:
                throw Problem(description: "a line is not \"key = value\": \(line)")
            }
        }
    }

    var text: String {
        lines.joined(separator: "\n") + "\n"
    }

    /// Sets a setting, adding it after the last setting in its section, or
    /// adding the section at the end, when the text does not set it yet. A
    /// new line ends the way the text's first line does, so CRLF text stays
    /// CRLF text.
    mutating func set(_ name: String, to value: String) throws {
        let parts = name.split(separator: ".", maxSplits: 1).map(String.init)
        let ending = lines.first?.hasSuffix("\r") == true ? "\r" : ""
        let line = (value.isEmpty ? "\(parts[1]) =" : "\(parts[1]) = \(value)") + ending
        if let index = lineOfSetting[name] {
            lines[index] = line
        } else if let index = endOfSection[parts[0]] {
            lines.insert(line, at: index)
        } else {
            lines += (lines.isEmpty ? [] : [ending]) + ["[\(parts[0])]" + ending, line]
        }
        self = try INIFile(text)
    }
}

struct Configuration {
    let host: String
    let port: UInt16
    let authors: [String]
    let orgs: [String]
    let actions: [String]
    let command: String

    var relay: String { "\(host):\(port)" }

    init(contentsOf url: URL) throws {
        let text: String
        do {
            text = try String(contentsOf: url, encoding: .utf8)
        } catch {
            throw Problem(description: "cannot read \(url.path): \(error.localizedDescription)")
        }
        do {
            try self.init(try INIFile(text))
        } catch {
            throw Problem(description: "\(url.path): \(error)")
        }
    }

    init(_ file: INIFile) throws {
        for name in file.names where !settings.contains(where: { $0.name == name }) {
            throw Problem(description: "\(name) is not a setting github-hookbar knows")
        }
        let values = file.values
        func required(_ key: String) throws -> String {
            guard let value = values[key], !value.isEmpty else {
                throw Problem(description: "\(key) is not set")
            }
            return value
        }
        func list(_ key: String) -> [String] {
            (values[key] ?? "").split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
                .filter { !$0.isEmpty }
        }

        // An IPv6 address is written in brackets, as in [::1]:8081.
        let address = try required("relay.address")
        guard let colon = address.lastIndex(of: ":"),
              let port = UInt16(address[address.index(after: colon)...]) else {
            throw Problem(description: "the relay address \(address) is not host:port")
        }
        var host = address[..<colon]
        if host.hasPrefix("["), host.hasSuffix("]") {
            host = host.dropFirst().dropLast()
        } else if host.contains(":") {
            host = ""
        }
        guard !host.isEmpty else {
            throw Problem(description: "the relay address \(address) is not host:port")
        }
        self.host = String(host)
        self.port = port
        authors = list("github.authors")
        orgs = list("github.orgs")
        let actions = list("github.actions")
        self.actions = actions.isEmpty ? ["opened"] : actions
        command = try required("command.run")
    }
}
