import Foundation
import Security

// Where quotabar keeps its accounts and their tokens: one item in the login
// keychain, so that a rebuilt quotabar asks for access to it at most once.

struct KeychainItem {
    let service: String
    let account: String

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    /// The item's contents, or nil when there is no such item.
    func read() throws -> Data? {
        var query = query
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        try check(status, "read")
        guard let data = result as? Data else {
            throw Problem(description: "the keychain item \(service) holds no data")
        }
        return data
    }

    func write(_ data: Data) throws {
        var status = SecItemUpdate(query as CFDictionary,
                                   [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var query = query
            query[kSecValueData as String] = data
            status = SecItemAdd(query as CFDictionary, nil)
        }
        try check(status, "write")
    }

    func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        if status != errSecItemNotFound {
            try check(status, "delete")
        }
    }

    private func check(_ status: OSStatus, _ action: String) throws {
        guard status == errSecSuccess else {
            let message = SecCopyErrorMessageString(status, nil).map { $0 as String } ?? "\(status)"
            throw Problem(description: "cannot \(action) the keychain item \(service): \(message)")
        }
    }
}

/// Where the watcher reads and saves the accounts.
protocol AccountKeeper {
    func load() throws -> [Account]
    func save(_ accounts: [Account]) throws
}

struct AccountStore: AccountKeeper {
    let item: KeychainItem

    func load() throws -> [Account] {
        guard let data = try item.read() else {
            return []
        }
        let accounts = try decode([Account].self, from: data, what: "keychain item \(item.service)")
        if let twice = Dictionary(grouping: accounts, by: \.id).first(where: { $0.value.count > 1 }) {
            throw Problem(description: "the keychain item \(item.service) lists the account \(twice.key) twice")
        }
        return accounts
    }

    func save(_ accounts: [Account]) throws {
        try item.write(try JSONEncoder().encode(accounts))
    }
}
