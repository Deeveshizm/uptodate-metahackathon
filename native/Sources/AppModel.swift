import Foundation
import Combine

@MainActor
final class AppModel: ObservableObject {
    @Published var account: Account?
    @Published var feed: DailyFeed?
    @Published var bookmarks: [Post] = []
    @Published var sources: [NewsletterSource] = []
    @Published var review: [Post] = []
    @Published var jobs: [Job] = []
    @Published var error: String?
    @Published var notice: String?
    @Published var busy = false
    @Published var offline = false
    @Published var authConfiguration: AuthConfiguration?
    @Published private(set) var recoveryReady = false
    @Published private(set) var pendingEmail: String?
    @Published private(set) var pendingAuthKind: AuthFlow.Kind?
    private var deferredAuthURL: URL?
    private var token: String?
    private var authSession: AuthSession?
    private var refreshTask: Task<AuthSession, Error>?
    private var sessionEpoch = 0
    private var lastIdleJobPoll = Date.distantPast
    private var imageCache: [String: Data] = [:]
    private let base = URL(string: "http://127.0.0.1:8000")!
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()
    private let cache: URL = {
        let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("UpToDate", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent("snapshot.json")
    }()

    init() {
        if let stored = Session.read() {
            authSession = try? JSONDecoder().decode(AuthSession.self, from: Data(stored.utf8))
            token = authSession?.accessToken ?? stored
        }
        if token != nil, let data = try? Data(contentsOf: cache),
           let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) {
            account = snapshot.account
            feed = snapshot.feed
            bookmarks = snapshot.bookmarks
            sources = snapshot.sources
            offline = true
        }
        if token == nil {
            if let text = Session.read(account: "auth-flow"),
               let flow = try? JSONDecoder().decode(AuthFlow.self, from: Data(text.utf8)), flow.isCurrent {
                pendingEmail = flow.email; pendingAuthKind = flow.kind
            }
            if let recovery = storedRecovery(), recovery.session.expiresAt > Date().timeIntervalSince1970 {
                pendingEmail = recovery.email; recoveryReady = true
            }
        }
    }

    private func request(_ path: String, method: String = "GET", body: Data? = nil, contentType: String = "application/json", retryAuth: Bool = true) async throws -> Data {
        let epoch = sessionEpoch
        if let session = authSession, session.expiresAt < Date().timeIntervalSince1970 + 60 {
            try await refreshSession()
        }
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 15
        request.httpBody = body
        request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let usedToken = token
        let (data, response) = try await URLSession.shared.data(for: request)
        guard epoch == sessionEpoch else { throw CancellationError() }
        guard let response = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard 200..<300 ~= response.statusCode else {
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            if response.statusCode == 401, authSession != nil, retryAuth {
                if usedToken == token { try await refreshSession() }
                return try await self.request(path, method: method, body: body, contentType: contentType, retryAuth: false)
            }
            if response.statusCode == 401 { clearLocalSession() }
            throw APIError.response(response.statusCode, object?["detail"] as? String ?? "The request failed")
        }
        return data
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try decoder.decode(T.self, from: await request(path))
    }

    func restore() async {
        await loadAuthConfiguration()
        guard token != nil else { return }
        await perform {
            self.account = try await self.get("v1/me")
            try await self.reload()
        }
    }

    func start() async {
        guard authConfiguration?.mode == "local" else { return }
        await perform {
            let body = try JSONSerialization.data(withJSONObject: ["timezone": TimeZone.current.identifier])
            let account = try self.decoder.decode(Account.self, from: await self.request("v1/dev/accounts", method: "POST", body: body))
            guard let token = account.token else { throw URLError(.cannotParseResponse) }
            try Session.save(token)
            self.token = token
            // Cache identity without duplicating the session secret.
            self.account = Account(id: account.id, alias: account.alias, timezone: account.timezone, token: nil)
            try await self.reload()
        }
    }

    func loadAuthConfiguration() async {
        do {
            let (data, response) = try await URLSession.shared.data(from: base.appendingPathComponent("v1/auth/config"))
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let configuration = try decoder.decode(AuthConfiguration.self, from: data)
            guard ["local", "supabase"].contains(configuration.mode) else { throw URLError(.cannotParseResponse) }
            authConfiguration = configuration
        } catch { self.error = "Cannot connect to the account service. Start the backend and retry." }
    }

    private func authClient() throws -> AuthClient {
        guard let config = authConfiguration, config.mode == "supabase" else {
            throw APIError.response(503, "Account service is not configured")
        }
        return try AuthClient(configuration: config)
    }

    private func persistSession(_ session: AuthSession) throws {
        try Session.save(String(decoding: JSONEncoder().encode(session), as: UTF8.self))
        authSession = session; token = session.accessToken
    }

    private func completeSignIn(_ session: AuthSession) async throws {
        clearLocalSession()
        do {
            try persistSession(session)
            account = try await get("v1/me")
            try await reload()
        } catch {
            clearLocalSession()
            throw error
        }
    }

    private func refreshSession() async throws {
        let epoch = sessionEpoch
        let task: Task<AuthSession, Error>
        if let existing = refreshTask { task = existing }
        else {
            guard let session = authSession else { throw APIError.response(401, "Sign in again") }
            let client = try authClient()
            task = Task { try await client.refresh(session) }
            refreshTask = task
        }
        do {
            let updated = try await task.value
            guard epoch == sessionEpoch else { throw CancellationError() }
            try persistSession(updated)
            refreshTask = nil
        } catch {
            if epoch == sessionEpoch {
                refreshTask = nil
                if case APIError.response(let status, _) = error, [400, 401, 403].contains(status) {
                    clearLocalSession()
                }
            }
            throw error
        }
    }

    func signIn(email: String, password: String) async {
        await perform { try await self.completeSignIn(self.authClient().signIn(email: email, password: password)) }
    }

    func signUp(email: String, password: String) async -> Bool {
        var confirmationNeeded = false
        await perform {
            let flow = try self.emailFlow(email: email, kind: .signup)
            if let session = try await self.authClient().signUp(email: email, password: password, flow: flow) {
                try await self.completeSignIn(session)
            } else { confirmationNeeded = true }
        }
        return confirmationNeeded
    }

    func resendConfirmation(email: String) async {
        await perform {
            try await self.authClient().resend(email: email)
            self.notice = "If confirmation is available for this address, a new link has been sent."
        }
    }

    func requestRecovery(email: String) async -> Bool {
        var sent = false
        await perform {
            let flow = try self.emailFlow(email: email, kind: .recovery)
            try await self.authClient().recover(email: email, flow: flow)
            sent = true
        }
        return sent
    }

    func recoverAccount(password: String) async {
        await perform {
            guard let recovery = self.storedRecovery(), recovery.session.expiresAt > Date().timeIntervalSince1970 else {
                throw APIError.response(400, "Open a new recovery link from your email before changing your password.")
            }
            let client = try self.authClient()
            try await client.updatePassword(password, session: recovery.session)
            // Obtain a fresh session after the password update.
            try await self.completeSignIn(client.signIn(email: recovery.email, password: password))
        }
    }

    private func emailFlow(email: String, kind: AuthFlow.Kind) throws -> AuthFlow {
        let previous = Session.read(account: "auth-flow").flatMap { try? JSONDecoder().decode(AuthFlow.self, from: Data($0.utf8)) }
        let flow: AuthFlow
        if let previous, previous.email == email, previous.kind == kind, previous.isCurrent { flow = previous }
        else { flow = try AuthFlow(email: email, kind: kind) }
        try Session.save(String(decoding: JSONEncoder().encode(flow), as: UTF8.self), account: "auth-flow")
        Session.clear(account: "password-recovery"); recoveryReady = false
        pendingEmail = email; pendingAuthKind = kind
        return flow
    }

    private func storedRecovery() -> PasswordRecovery? {
        Session.read(account: "password-recovery").flatMap {
            try? JSONDecoder().decode(PasswordRecovery.self, from: Data($0.utf8))
        }
    }

    func cancelEmailFlow() {
        Session.clear(account: "auth-flow"); Session.clear(account: "password-recovery")
        recoveryReady = false; pendingEmail = nil; pendingAuthKind = nil
    }

    func handleAuthURL(_ url: URL) async {
        guard url.scheme == AuthFlow.redirectURL.scheme, account == nil else { return }
        if busy { deferredAuthURL = url; return }
        if authConfiguration == nil { await loadAuthConfiguration() }
        await perform {
            guard let text = Session.read(account: "auth-flow"),
                  let flow = try? JSONDecoder().decode(AuthFlow.self, from: Data(text.utf8)) else {
                throw APIError.response(400, "Start verification or password recovery in this app before opening an email link.")
            }
            let code = try flow.code(from: url)
            // A callback is only an authorization code; exchange requires the
            // random verifier retained on the device that started this flow.
            let session = try await self.authClient().exchange(code: code, flow: flow)
            if flow.kind == .recovery {
                let recovery = PasswordRecovery(email: flow.email, session: session)
                try Session.save(String(decoding: JSONEncoder().encode(recovery), as: UTF8.self), account: "password-recovery")
                Session.clear(account: "auth-flow")
                self.pendingAuthKind = nil; self.pendingEmail = flow.email
                self.recoveryReady = true
            } else { try await self.completeSignIn(session) }
        }
    }

    func signOut() async {
        guard !busy else { return }
        let session = authSession
        let client = try? authClient()
        clearLocalSession()
        if let session, let client {
            do { try await client.signOut(session) }
            catch { notice = "Signed out on this device. Remote session revocation could not be confirmed." }
        }
    }

    func updateProfile(name: String, timezone: String) async -> Bool {
        var saved = false
        await perform {
            let body = try JSONSerialization.data(withJSONObject: ["display_name": name, "timezone": timezone])
            self.account = try self.decoder.decode(Account.self, from: await self.request("v1/me", method: "PATCH", body: body))
            try await self.reload()
            saved = true
        }
        return saved
    }

    func refresh() async { await perform { try await self.reload() } }

    func refreshAccount() async {
        await perform { self.account = try await self.get("v1/me"); try await self.reload() }
    }

    private func reload() async throws {
        let epoch = sessionEpoch
        async let feed: DailyFeed = get("v1/feed")
        async let bookmarks: [Post] = get("v1/bookmarks")
        async let sources: [NewsletterSource] = get("v1/me/sources")
        async let review: [Post] = get("v1/review")
        async let jobs: [Job] = get("v1/jobs")
        let values = try await (feed, bookmarks, sources, review, jobs)
        guard epoch == sessionEpoch else { throw CancellationError() }
        self.feed = values.0; self.bookmarks = values.1; self.sources = values.2
        self.review = values.3; self.jobs = values.4
        offline = false
        if let account {
            let snapshot = Snapshot(account: account, feed: self.feed, bookmarks: self.bookmarks, sources: self.sources)
            try JSONEncoder().encode(snapshot).write(to: cache, options: [.atomic, .completeFileProtection])
        }
    }

    // Pull-to-refresh tears down its task when the gesture ends, which surfaces as
    // a cancellation. That is not a failure: it must not alert, and it must not
    // flip the UI to the cached offline state.
    private func isCancellation(_ error: Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }

    private func perform(_ operation: () async throws -> Void) async {
        guard !busy else { return }
        busy = true
        defer {
            busy = false
            if let url = deferredAuthURL {
                deferredAuthURL = nil
                Task { await self.handleAuthURL(url) }
            }
        }
        do { try await operation() }
        catch {
            if isCancellation(error) { return }
            if error is URLError { offline = true }
            self.error = error.localizedDescription
        }
    }

    func loadDemo() async {
        await perform {
            _ = try await self.request("v1/dev/demo", method: "POST")
            try await self.reload()
            self.notice = "Loaded the sample Weekly Roundup edition. Its signup URL is a placeholder."
        }
    }

    func addNewsletter(url: String, autoSubscribe: Bool) async -> Bool {
        var added = false
        await perform {
            let website = try NewsletterLink.normalize(url)
            let body = try JSONSerialization.data(withJSONObject: ["url": website, "auto_subscribe": autoSubscribe])
            let response = try await self.request("v1/newsletters", method: "POST", body: body)
            let result = try self.decoder.decode(NewsletterAdded.self, from: response)
            try await self.reload()
            if let source = self.sources.first(where: { $0.id == result.sourceId }) {
                self.notice = source.subscriptionTitle + ". See Manage for subscription details."
            } else {
                self.notice = "Signup queued using your account alias. See Manage for confirmation and delivery status."
            }
            added = true
        }
        return added
    }

    func retrySubscription(_ source: NewsletterSource) async {
        await perform {
            _ = try await self.request("v1/me/sources/\(source.id)/subscribe", method: "POST")
            try await self.reload()
            self.notice = "Signup queued using your account alias. Subscription progress is in Manage."
        }
    }

    func unsubscribe(_ source: NewsletterSource) async -> URL? {
        var actionURL: URL?
        await perform {
            let response = try await self.request("v1/me/sources/\(source.id)/unsubscribe", method: "POST")
            let result = try self.decoder.decode(UnsubscribeResult.self, from: response)
            try await self.reload()
            self.notice = result.detail
            if let value = result.actionUrl, let url = URL(string: value), url.scheme == "https", url.host != nil {
                actionURL = url
            }
        }
        return actionURL
    }

    func generateFromLink(url: String, mode: String, sourceId: String? = nil) async -> Bool {
        var added = false
        await perform {
            var fields: [String: Any] = ["url": url, "mode": mode]
            if let sourceId { fields["source_id"] = sourceId }
            _ = try await self.request("v1/generation", method: "POST", body: JSONSerialization.data(withJSONObject: fields))
            try await self.reload()
            self.notice = "Generation queued. Follow progress in Manage; posts appear automatically in Home."
            added = true
        }
        return added
    }

    func retryJob(_ job: Job) async {
        await perform {
            _ = try await self.request("v1/jobs/\(job.id)/retry", method: "POST")
            try await self.reload()
        }
    }

    func pollJobs() async {
        guard !busy, account != nil else { return }
        let pending = jobs.contains(where: { ["queued", "processing"].contains($0.status) })
        guard pending || Date().timeIntervalSince(lastIdleJobPoll) >= 15 else { return }
        lastIdleJobPoll = Date()
        do {
            if account?.aliasStatus != "active" { account = try await get("v1/me") }
            let updated: [Job] = try await get("v1/jobs")
            let discovered = Set(updated.map(\.id)) != Set(jobs.map(\.id))
            let finished = updated.contains { job in
                !["queued", "processing"].contains(job.status) && jobs.contains(where: { $0.id == job.id && ["queued", "processing"].contains($0.status) })
            }
            jobs = updated
            if finished || discovered { try await reload() }
        } catch {
            if error is URLError { offline = true }
        }
    }

    func importEmail(_ url: URL) async {
        await perform {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            if (attributes[.size] as? NSNumber)?.intValue ?? 0 > 2_000_000 {
                throw APIError.response(413, "Email must be under 2 MB")
            }
            let content = try Data(contentsOf: url)
            let boundary = UUID().uuidString
            var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"newsletter.eml\"\r\nContent-Type: message/rfc822\r\n\r\n".utf8)
            body.append(content)
            body.append(Data("\r\n--\(boundary)--\r\n".utf8))
            _ = try await self.request("v1/import/email", method: "POST", body: body, contentType: "multipart/form-data; boundary=\(boundary)")
            try await self.reload()
            self.notice = "Email imported. Posts appear in Home; processing progress is in Manage."
        }
    }

    func bookmark(_ post: Post) async {
        await perform {
            _ = try await self.request("v1/posts/\(post.id)/bookmark", method: post.bookmarked ? "DELETE" : "POST")
            try await self.reload()
        }
    }

    func markRead(_ post: Post) async {
        await perform {
            _ = try await self.request("v1/posts/\(post.id)/read", method: "POST")
            try await self.reload()
        }
    }

    func approve(_ post: Post, isPublic: Bool) async {
        await perform {
            let body = try JSONSerialization.data(withJSONObject: ["reviewed": true, "rights": isPublic ? "public" : "private"])
            _ = try await self.request("v1/review/\(post.id)/approve", method: "POST", body: body)
            try await self.reload()
        }
    }

    func reject(_ post: Post) async {
        await perform {
            _ = try await self.request("v1/review/\(post.id)/reject", method: "POST")
            try await self.reload()
        }
    }

    func updateSource(_ source: NewsletterSource, muted: Bool? = nil, signup: String? = nil) async {
        await perform {
            var fields: [String: Any] = [:]
            if let muted { fields["muted"] = muted }
            if let signup { fields["signup_url"] = signup }
            _ = try await self.request("v1/me/sources/\(source.id)", method: "PATCH", body: JSONSerialization.data(withJSONObject: fields))
            try await self.reload()
        }
    }

    func share(_ post: Post) async -> ShareResult? {
        do {
            return try decoder.decode(ShareResult.self, from: await request("v1/posts/\(post.id)/share", method: "POST"))
        } catch {
            if !isCancellation(error) { self.error = error.localizedDescription }
            return nil
        }
    }

    func visualData(_ post: Post) async -> Data? {
        guard let assetId = post.visual?.assetId else { return nil }
        if let data = imageCache[assetId] { return data }
        guard assetId.allSatisfy({ $0.isHexDigit }) else { return nil }
        let directory = cache.deletingLastPathComponent().appendingPathComponent("images")
        let path = directory.appendingPathComponent(assetId + ".png")
        if let data = try? Data(contentsOf: path) { imageCache[assetId] = data; return data }
        guard !offline else { return nil }
        do {
            let data = try await request("v1/posts/\(post.id)/image")
            if imageCache.count >= 20 { imageCache.removeAll() }
            imageCache[assetId] = data
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try data.write(to: path, options: [.atomic, .completeFileProtection])
            return data
        } catch { return nil }
    }

    func retryVisual(_ post: Post) async {
        await perform {
            _ = try await self.request("v1/posts/\(post.id)/image/retry", method: "POST")
            try await self.reload()
        }
    }

    func deleteAccount() async {
        await perform {
            _ = try await self.request("v1/me", method: "DELETE")
            self.clearLocalSession()
        }
    }

    private func clearLocalSession() {
        cancelEmailFlow()
        deferredAuthURL = nil
        sessionEpoch += 1
        refreshTask?.cancel(); refreshTask = nil; authSession = nil
        Session.clear()
        token = nil; account = nil; feed = nil; bookmarks = []; sources = []; review = []; jobs = []
        imageCache.removeAll()
        let images = cache.deletingLastPathComponent().appendingPathComponent("images")
        try? FileManager.default.removeItem(at: images)
        try? FileManager.default.removeItem(at: cache)
    }
}
