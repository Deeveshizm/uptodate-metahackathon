import Foundation
import CryptoKit
import Security

struct AuthFlow: Codable, Sendable {
    enum Kind: String, Codable, Sendable { case signup, recovery }
    static let redirectURL = URL(string: "local.uptodate.prototype://auth/callback")!
    let email: String
    let kind: Kind
    let verifier: String
    let createdAt: TimeInterval

    init(email: String, kind: Kind) throws {
        var random = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, random.count, &random) == errSecSuccess else {
            throw APIError.response(503, "Cannot start a secure email verification request")
        }
        self.email = email; self.kind = kind
        verifier = Self.base64url(Data(random))
        createdAt = Date().timeIntervalSince1970
    }

    var isCurrent: Bool { (0..<3600).contains(Date().timeIntervalSince1970 - createdAt) }
    var challenge: String { Self.base64url(Data(SHA256.hash(data: Data(verifier.utf8)))) }

    func code(from url: URL) throws -> String {
        guard isCurrent else {
            throw APIError.response(400, "The verification request on this device has expired. Start a new request in the app.")
        }
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme == Self.redirectURL.scheme, components.host == "auth",
              components.path == "/callback", components.user == nil, components.password == nil,
              components.port == nil else {
            throw APIError.response(400, "This email link has the wrong return address. Check the app's redirect URL in Supabase.")
        }
        let fragmentItems = URLComponents(string: "?" + (components.fragment ?? ""))?.queryItems ?? []
        let queryItems = components.queryItems ?? []
        let parameters = queryItems + fragmentItems
        if parameters.contains(where: { $0.name == "error_code" && $0.value == "otp_expired" }) {
            throw APIError.response(400, "Supabase reports this email link was already used or expired. If you already confirmed your email, return to sign in with your password. Otherwise request a new link.")
        }
        if parameters.contains(where: { ["error", "error_code"].contains($0.name) }) {
            throw APIError.response(400, "Supabase could not verify this email link. Try signing in if you already confirmed your email, or request a new link.")
        }
        // An empty trailing '#' is harmless. Tokens in a fragment are not:
        // accept only a PKCE authorization code, never an injected session.
        guard components.fragment?.isEmpty != false else {
            throw APIError.response(400, "This link does not use the current secure verification flow. Try signing in if your email is confirmed, or start a new request in this app.")
        }
        let codes = (components.queryItems ?? []).filter { $0.name == "code" }
        guard !codes.isEmpty else {
            throw APIError.response(400, "The confirmation code is missing. Open the full HTTPS confirmation link from your email, rather than the app's return address.")
        }
        guard codes.count == 1, let code = codes[0].value, !code.isEmpty, code.count <= 4096,
              !queryItems.contains(where: { ["access_token", "refresh_token"].contains($0.name) }) else {
            throw APIError.response(400, "The verification link is malformed. Start a new request in this app.")
        }
        return code
    }

    private static func base64url(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}

struct PasswordRecovery: Codable {
    let email: String
    let session: AuthSession
}
