import Foundation

struct AuthConfiguration: Decodable {
    let mode: String
    let url: String?
    let publishableKey: String?
}

enum PasswordRequirement: String, CaseIterable, Identifiable {
    case length, uppercase, lowercase, number, symbol, confirmation

    var id: String { rawValue }
    var title: String {
        switch self {
        case .length: "At least \(PasswordPolicy.minimumLength) characters"
        case .uppercase: "An uppercase letter (A–Z)"
        case .lowercase: "A lowercase letter (a–z)"
        case .number: "A number (0–9)"
        case .symbol: "A symbol, such as !, @, or #"
        case .confirmation: "Passwords match"
        }
    }
    func isMet(password: String, confirmation: String) -> Bool {
        switch self {
        case .length: password.count >= PasswordPolicy.minimumLength
        case .uppercase: password.range(of: "[A-Z]", options: .regularExpression) != nil
        case .lowercase: password.range(of: "[a-z]", options: .regularExpression) != nil
        case .number: password.range(of: "[0-9]", options: .regularExpression) != nil
        case .symbol: password.unicodeScalars.contains {
            (33...47).contains($0.value) || (58...64).contains($0.value) ||
            (91...96).contains($0.value) || (123...126).contains($0.value)
        }
        case .confirmation: !password.isEmpty && !confirmation.isEmpty && password == confirmation
        }
    }
}

enum PasswordPolicy {
    static let minimumLength = 8
    static let maximumUTF8Bytes = 72

    static func accepts(_ password: String, confirmation: String) -> Bool {
        password.utf8.count <= maximumUTF8Bytes && PasswordRequirement.allCases.allSatisfy {
            $0.isMet(password: password, confirmation: confirmation)
        }
    }
}

enum AppGuide: String, Codable, CaseIterable, Sendable {
    case welcome, createNewsletter

    var metadataKey: String {
        switch self {
        case .welcome: "uptodate_welcome_guide_completed"
        case .createNewsletter: "uptodate_newsletter_guide_completed"
        }
    }
}

struct GuideProgress: Codable, Equatable, Sendable {
    var completed: Set<AppGuide> = []

    static func fromUser(_ data: Data, owner: String) throws -> GuideProgress {
        guard let user = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = user["id"] as? String, id.lowercased() == owner.lowercased() else {
            throw APIError.response(401, "Guide progress belongs to a different account.")
        }
        let metadata = user["user_metadata"] as? [String: Any] ?? [:]
        return GuideProgress(completed: Set(AppGuide.allCases.filter { metadata[$0.metadataKey] as? Bool == true }))
    }
}

struct AuthSession: Codable, Sendable {
    let accessToken: String
    let refreshToken: String
    let expiresAt: TimeInterval

    static func decodeResponse(_ data: Data) throws -> AuthSession? {
        struct Tokens: Decodable {
            let accessToken: String?
            let refreshToken: String?
            let expiresIn: Double?
            let expiresAt: Double?
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let tokens = try decoder.decode(Tokens.self, from: data)
        guard let access = tokens.accessToken, let refresh = tokens.refreshToken else { return nil }
        return AuthSession(accessToken: access, refreshToken: refresh,
                           expiresAt: tokens.expiresAt ?? Date().timeIntervalSince1970 + (tokens.expiresIn ?? 3600))
    }
}

struct AuthClient: Sendable {
    let origin: URL
    let publishableKey: String
    let session: URLSession

    init(configuration: AuthConfiguration, session: URLSession = .shared) throws {
        guard let value = configuration.url, let url = URL(string: value),
              url.scheme == "https", url.host != nil, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil, url.path.isEmpty || url.path == "/",
              let key = configuration.publishableKey, !key.isEmpty, !key.hasPrefix("sb_secret_") else {
            throw APIError.response(503, "Account service is not configured")
        }
        origin = url; publishableKey = key
        self.session = session
    }

    private func call(_ path: String, method: String = "POST", fields: [String: String] = [:], token: String? = nil, redirect: URL? = nil, payload: Data? = nil) async throws -> Data {
        guard let initialURL = URL(string: "auth/v1/" + path, relativeTo: origin.appendingPathComponent("/")),
              var components = URLComponents(url: initialURL, resolvingAgainstBaseURL: true) else {
            throw URLError(.badURL)
        }
        if let redirect {
            components.queryItems = (components.queryItems ?? []) + [URLQueryItem(name: "redirect_to", value: redirect.absoluteString)]
        }
        guard let url = components.url else { throw URLError(.badURL) }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.setValue(publishableKey, forHTTPHeaderField: "apikey")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if method != "GET" { request.httpBody = try payload ?? JSONSerialization.data(withJSONObject: fields) }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard 200..<300 ~= response.statusCode else {
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            let code = object?["error_code"] as? String ?? object?["code"] as? String ?? ""
            let message: String
            switch code {
            case "invalid_credentials": message = "The email or password is incorrect."
            case "email_not_confirmed": message = "Verify your email before signing in."
            case "otp_expired": message = "The code is invalid or expired. Request a new code."
            case "weak_password":
                let weakness = object?["weak_password"] as? [String: Any]
                let reasons = weakness?["reasons"] as? [String] ?? object?["reasons"] as? [String] ?? []
                if reasons.contains("pwned") {
                    message = "This password was found in a data breach. Choose a different password."
                } else {
                    message = "Use at least \(PasswordPolicy.minimumLength) characters with an uppercase letter, lowercase letter, number, and symbol."
                }
            case "over_email_send_rate_limit", "over_request_rate_limit": message = "Too many attempts. Wait before trying again."
            default: message = "The account request could not be completed. Check your details and try again."
            }
            throw APIError.response(response.statusCode, message)
        }
        return data
    }

    func signUp(email: String, password: String, flow: AuthFlow) async throws -> AuthSession? {
        try await AuthSession.decodeResponse(call("signup", fields: ["email": email, "password": password,
            "code_challenge": flow.challenge, "code_challenge_method": "s256"], redirect: AuthFlow.redirectURL))
    }

    func signIn(email: String, password: String) async throws -> AuthSession {
        guard let session = try await AuthSession.decodeResponse(call("token?grant_type=password", fields: ["email": email, "password": password])) else {
            throw URLError(.cannotParseResponse)
        }
        return session
    }

    func refresh(_ session: AuthSession) async throws -> AuthSession {
        guard let updated = try await AuthSession.decodeResponse(call("token?grant_type=refresh_token", fields: ["refresh_token": session.refreshToken])) else {
            throw URLError(.cannotParseResponse)
        }
        return updated
    }

    func verify(email: String, code: String, recovery: Bool) async throws -> AuthSession {
        guard let session = try await AuthSession.decodeResponse(call("verify", fields: ["email": email, "token": code, "type": recovery ? "recovery" : "signup"])) else {
            throw URLError(.cannotParseResponse)
        }
        return session
    }

    func recover(email: String, flow: AuthFlow) async throws {
        _ = try await call("recover", fields: ["email": email, "code_challenge": flow.challenge,
                                               "code_challenge_method": "s256"], redirect: AuthFlow.redirectURL)
    }
    func resend(email: String) async throws {
        _ = try await call("resend", fields: ["email": email, "type": "signup"], redirect: AuthFlow.redirectURL)
    }
    func exchange(code: String, flow: AuthFlow) async throws -> AuthSession {
        guard let session = try await AuthSession.decodeResponse(call("token?grant_type=pkce", fields: ["auth_code": code, "code_verifier": flow.verifier])) else {
            throw URLError(.cannotParseResponse)
        }
        return session
    }
    func updatePassword(_ password: String, session: AuthSession) async throws {
        _ = try await call("user", method: "PUT", fields: ["password": password], token: session.accessToken)
    }
    func signOut(_ session: AuthSession) async throws {
        _ = try await call("logout?scope=local", token: session.accessToken)
    }

    func guideProgress(owner: String, session: AuthSession) async throws -> GuideProgress {
        try GuideProgress.fromUser(await call("user", method: "GET", token: session.accessToken), owner: owner)
    }

    func completeGuides(_ guides: Set<AppGuide>, owner: String, session: AuthSession) async throws -> GuideProgress {
        let flags = Dictionary(uniqueKeysWithValues: guides.map { ($0.metadataKey, true) })
        let payload = try JSONSerialization.data(withJSONObject: ["data": flags])
        return try GuideProgress.fromUser(await call("user", method: "PUT", token: session.accessToken, payload: payload), owner: owner)
    }
}
