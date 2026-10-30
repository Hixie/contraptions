import Foundation

/// Prefix for the preference domain, the lock file names, and the launchd
/// agent labels. The instance number is appended to it.
let identifierPrefix = "local.ghbar"

let defaultBranchName = "main"
let defaultRefreshSeconds = 60.0
let minimumRefreshSeconds = 15.0

/// Reads a folder as typed, which may start with a tilde. A relative path is
/// taken from the home folder, and empty means the home folder.
func folderURL(_ text: String) -> URL {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty {
        return FileManager.default.homeDirectoryForCurrentUser
    }
    return URL(
        fileURLWithPath: (trimmed as NSString).expandingTildeInPath, isDirectory: true,
        relativeTo: FileManager.default.homeDirectoryForCurrentUser
    ).absoluteURL
}

func isBranchName(_ text: String) -> Bool {
    guard !text.isEmpty, !text.hasPrefix("/"), !text.hasSuffix("/") else { return false }
    return !text.contains(where: { $0.isWhitespace }) && !text.contains("..")
}

// MARK: - Settings

enum SettingsKey {
    static let repository = "repository"
    static let branch = "branch"
    static let menuBarName = "menuBarName"
    static let showsMenuBarName = "showsMenuBarName"
    static let refreshSeconds = "refreshSeconds"
    static let handOff = "handOff"
    static let handOffFolder = "handOffFolder"
    static let handOffPrompt = "handOffPrompt"
    static let codexFollowUp = "codexFollowUp"
    static let relay = "relay"
}

/// One indicator's settings, kept in a preference domain of its own so that
/// indicators with different instance numbers do not overwrite each other.
final class Settings {
    let instance: Int
    private let store: UserDefaults

    init(instance: Int) {
        self.instance = instance
        store = UserDefaults(suiteName: "\(identifierPrefix).\(instance)") ?? .standard
        store.register(defaults: [
            SettingsKey.branch: defaultBranchName,
            SettingsKey.showsMenuBarName: true,
            SettingsKey.refreshSeconds: defaultRefreshSeconds
        ])
    }

    var repository: RepositoryReference? {
        get {
            guard let text = store.string(forKey: SettingsKey.repository) else { return nil }
            return parseRepository(text)?.repository
        }
        set { store.set(newValue?.slug, forKey: SettingsKey.repository) }
    }

    var branch: String {
        get { store.string(forKey: SettingsKey.branch) ?? defaultBranchName }
        set { store.set(newValue, forKey: SettingsKey.branch) }
    }

    var menuBarName: String {
        get { store.string(forKey: SettingsKey.menuBarName) ?? "" }
        set { store.set(newValue, forKey: SettingsKey.menuBarName) }
    }

    var showsMenuBarName: Bool {
        get { store.bool(forKey: SettingsKey.showsMenuBarName) }
        set { store.set(newValue, forKey: SettingsKey.showsMenuBarName) }
    }

    var refreshSeconds: Double {
        get { max(minimumRefreshSeconds, store.double(forKey: SettingsKey.refreshSeconds)) }
        set { store.set(max(minimumRefreshSeconds, newValue), forKey: SettingsKey.refreshSeconds) }
    }

    /// The agent a branch that turns red is handed to, if any.
    var handOff: Agent? {
        get { store.string(forKey: SettingsKey.handOff).flatMap(Agent.init(rawValue:)) }
        set { store.set(newValue?.rawValue, forKey: SettingsKey.handOff) }
    }

    /// The folder an agent's session starts in, as typed. Empty means the
    /// home folder.
    var handOffFolder: String {
        get { store.string(forKey: SettingsKey.handOffFolder) ?? "" }
        set { store.set(newValue, forKey: SettingsKey.handOffFolder) }
    }

    var handOffFolderURL: URL { folderURL(handOffFolder) }

    var handOffPrompt: String {
        get { prompt(forKey: SettingsKey.handOffPrompt, default: defaultHandOffPrompt) }
        set { store.set(newValue, forKey: SettingsKey.handOffPrompt) }
    }

    var codexFollowUp: String {
        get { prompt(forKey: SettingsKey.codexFollowUp, default: defaultCodexFollowUp) }
        set { store.set(newValue, forKey: SettingsKey.codexFollowUp) }
    }

    private func prompt(forKey key: String, default fallback: String) -> String {
        guard let text = store.string(forKey: key),
            !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return fallback }
        return text
    }

    /// The webhook relay to hear about changes from, if any.
    var relay: RelayAddress? {
        get { store.string(forKey: SettingsKey.relay).flatMap(parseRelayAddress) }
        set { store.set(newValue?.description, forKey: SettingsKey.relay) }
    }

    /// The text drawn next to the indicator in the menu bar.
    var displayName: String {
        if !menuBarName.isEmpty { return menuBarName }
        return repository?.name ?? applicationName
    }
}