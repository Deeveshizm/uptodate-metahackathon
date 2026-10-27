import Foundation
import Security

enum Session {
    private static let service = "local.uptodate.prototype"
    #if targetEnvironment(simulator)
    // Unsigned simulator builds have no Keychain application entitlement.
    // Keep this fallback inside the simulator's app sandbox only.
    private static var simulatorURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("UpToDate/simulator-session")
    }
    #endif

    static func read() -> String? {
        #if targetEnvironment(simulator)
        guard let data = try? Data(contentsOf: simulatorURL) else { return nil }
        return String(data: data, encoding: .utf8)
        #else
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: "session",
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
        #endif
    }

    static func save(_ token: String) throws {
        #if targetEnvironment(simulator)
        try FileManager.default.createDirectory(at: simulatorURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(token.utf8).write(to: simulatorURL, options: [.atomic, .completeFileProtection])
        #else
        clear()
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: "session",
            kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Cannot save the local session in Keychain (\(status))."])
        }
        #endif
    }

    static func clear() {
        #if targetEnvironment(simulator)
        try? FileManager.default.removeItem(at: simulatorURL)
        #else
        SecItemDelete([kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: "session"] as CFDictionary)
        #endif
    }
}
