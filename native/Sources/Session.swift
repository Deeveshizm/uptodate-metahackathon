import Foundation
import Security

enum Session {
    private static let service = "local.uptodate.prototype"
    #if targetEnvironment(simulator)
    // Unsigned simulator builds have no Keychain application entitlement.
    // Keep this fallback inside the simulator's app sandbox only.
    private static func simulatorURL(_ account: String) -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("UpToDate/simulator-\(account)")
    }
    #endif

    static func read(account: String = "session") -> String? {
        #if targetEnvironment(simulator)
        guard let data = try? Data(contentsOf: simulatorURL(account)) else { return nil }
        return String(data: data, encoding: .utf8)
        #else
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: account,
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
        #endif
    }

    static func save(_ token: String, account: String = "session") throws {
        #if targetEnvironment(simulator)
        let url = simulatorURL(account)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(token.utf8).write(to: url, options: [.atomic, .completeFileProtection])
        #else
        clear(account: account)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: account,
            kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Cannot save the local session in Keychain (\(status))."])
        }
        #endif
    }

    static func clear(account: String = "session") {
        #if targetEnvironment(simulator)
        try? FileManager.default.removeItem(at: simulatorURL(account))
        #else
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: account] as CFDictionary)
        #endif
    }
}
