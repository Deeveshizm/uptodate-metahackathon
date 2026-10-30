import Foundation
import Combine

@MainActor
final class AppModel: ObservableObject {
    @Published var account: Account?
    @Published private(set) var avatarData: Data?
    private var loadedAvatarRevision: String?
    @Published var feed: DailyFeed?
    @Published var readFeed: DailyFeed?
    @Published var loadingOlder = false
    @Published var loadingReadHistory = false
    @Published var bookmarks: [Post] = []
    @Published var sources: [NewsletterSource] = []
    @Published var review: [Post] = []
    @Published var jobs: [Job] = []
    @Published var inboxCandidates: [IncomingNewsletter] = []
    @Published var newsletterConfirmations: [NewsletterConfirmation] = []
    @Published var error: String?
    @Published var notice: String?
    @Published var busy = false
    @Published private(set) var completedGuides: Set<AppGuide> = []
    @Published private(set) var guideProgressReady = false
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
    private var guideOwner: String?
    private var guideLoadTask: Task<GuideProgress?, Never>?
    private var guideSyncTask: Task<Void, Never>?
    private var lastIdleJobPoll = Date.distantPast
    private var imageCache: [String: Data] = [:]
    private let base: URL = {
        #if targetEnvironment(simulator)
        if let value = ProcessInfo.processInfo.environment["UPTODATE_TEST_API_URL"],
           let url = URL(string: value), url.scheme == "http", url.host == "127.0.0.1" { return url }
        #endif
        if let value = Bundle.main.object(forInfoDictionaryKey: "UpToDateAPIURL") as? String,
           let url = BackendConfiguration.validatedURL(value) { return url }
        return URL(string: "http://127.0.0.1:8000")!
    }()
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
            readFeed = snapshot.readFeed
            offline = true
            avatarData = try? Data(contentsOf: cache.deletingLastPathComponent().appendingPathComponent("avatar.png"))
            loadedAvatarRevision = avatarData == nil ? nil : account?.avatarRevision
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

    private func request(_ path: String, method: String = "GET", body: Data? = nil, contentType: String = "application/json", retryAuth: Bool = true, timeout: TimeInterval = 15, query: [URLQueryItem] = []) async throws -> Data {
        let epoch = sessionEpoch
        if let session = authSession, session.expiresAt < Date().timeIntervalSince1970 + 60 {
            try await refreshSession()
        }
        var endpoint = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { endpoint.queryItems = query }
        var request = URLRequest(url: endpoint.url!)
        request.httpMethod = method
        request.timeoutInterval = timeout
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
                return try await self.request(path, method: method, body: body, contentType: contentType, retryAuth: false, timeout: timeout, query: query)
            }
            if response.statusCode == 401 { clearLocalSession() }
            throw APIError.response(response.statusCode, object?["detail"] as? String ?? "The request failed")
        }
        return data
    }

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try decoder.decode(T.self, from: await request(path, query: query))
    }

    func restore() async {
        await loadAuthConfiguration()
        guard token != nil else { return }
        await perform {
            self.account = try await self.get("v1/me")
            await self.syncTimezone()
            await self.refreshAvatar()
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
            await syncTimezone()
            await refreshAvatar()
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

    func updateProfile(name: String) async -> Bool {
        var saved = false
        await perform {
            let body = try JSONSerialization.data(withJSONObject: ["display_name": name])
            self.account = try self.decoder.decode(Account.self, from: await self.request("v1/me", method: "PATCH", body: body))
            try await self.reload()
            saved = true
        }
        return saved
    }

    private func syncTimezone() async {
        let current = TimeZone.current.identifier
        guard authConfiguration?.mode == "supabase", account?.timezone != current else { return }
        let epoch = sessionEpoch
        do {
            let body = try JSONSerialization.data(withJSONObject: ["timezone": current])
            let updated: Account = try decoder.decode(Account.self, from: await request("v1/me", method: "PATCH", body: body))
            guard epoch == sessionEpoch else { return }
            account = updated
        } catch { /* Keep the saved timezone if connectivity is interrupted. */ }
    }

    private func refreshAvatar() async {
        let file = cache.deletingLastPathComponent().appendingPathComponent("avatar.png")
        guard let revision = account?.avatarRevision else {
            avatarData = nil; loadedAvatarRevision = nil
            try? FileManager.default.removeItem(at: file)
            return
        }
        guard loadedAvatarRevision != revision || avatarData == nil else { return }
        let epoch = sessionEpoch
        do {
            let image = try await request("v1/me/avatar")
            guard epoch == sessionEpoch, account?.avatarRevision == revision else { return }
            avatarData = image; loadedAvatarRevision = revision
            try? image.write(to: file, options: [.atomic, .completeFileProtection])
        } catch { /* Leave the last photo visible while temporarily offline. */ }
    }

    func updateAvatar(_ image: Data) async {
        await perform {
            self.account = try self.decoder.decode(Account.self, from: await self.request("v1/me/avatar", method: "PUT", body: image, contentType: "image/jpeg", timeout: 60))
            await self.refreshAvatar()
            try await self.reload()
        }
    }

    func removeAvatar() async {
        await perform {
            _ = try await self.request("v1/me/avatar", method: "DELETE")
            self.account = try await self.get("v1/me")
            await self.refreshAvatar()
            try await self.reload()
        }
    }

    func resetPasswordFromAccount() async {
        guard let email = account?.email, !email.isEmpty else { return }
        await perform {
            let client = try self.authClient()
            let flow = try self.emailFlow(email: email, kind: .recovery)
            let previousSession = self.authSession
            try await client.recover(email: email, flow: flow)
            self.clearLocalSession()
            try Session.save(String(decoding: JSONEncoder().encode(flow), as: UTF8.self), account: "auth-flow")
            self.pendingEmail = email; self.pendingAuthKind = .recovery
            if let previousSession { try? await client.signOut(previousSession) }
            self.notice = "Recovery link sent. Open it on this device to choose a new password."
        }
    }

    func refresh() async { await perform { try await self.reload() } }

    func refreshAccount() async {
        await perform { self.account = try await self.get("v1/me"); await self.refreshAvatar(); try await self.reload() }
    }

    private func guideCacheKey(_ owner: String) -> String { "guide-progress-" + owner }

    private func cacheGuideProgress(_ owner: String) {
        guard let data = try? JSONEncoder().encode(GuideProgress(completed: completedGuides)) else { return }
        try? Session.save(String(decoding: data, as: UTF8.self), account: guideCacheKey(owner))
    }

    func prepareGuides() async {
        guard let owner = account?.id else { return }
        if guideOwner == owner, guideProgressReady { return }
        let epoch = sessionEpoch
        if guideOwner != owner {
            guideOwner = owner
            guideProgressReady = false
            completedGuides = []
            if let saved = Session.read(account: guideCacheKey(owner)),
               let progress = try? JSONDecoder().decode(GuideProgress.self, from: Data(saved.utf8)) {
                completedGuides = progress.completed
            }
        }
        if guideLoadTask == nil, let session = authSession, let client = try? authClient() {
            guideLoadTask = Task { try? await client.guideProgress(owner: owner, session: session) }
        }
        let remote = await guideLoadTask?.value
        guard epoch == sessionEpoch, account?.id == owner else { return }
        if let remote { completedGuides.formUnion(remote.completed) }
        guideLoadTask = nil
        guideProgressReady = true
        cacheGuideProgress(owner)
        if let remote, !completedGuides.isSubset(of: remote.completed) { syncGuideProgress() }
    }

    func finishGuide(_ guide: AppGuide) {
        guard let owner = account?.id else { return }
        completedGuides.insert(guide)
        cacheGuideProgress(owner)
        syncGuideProgress()
    }

    private func syncGuideProgress() {
        guard guideSyncTask == nil, !completedGuides.isEmpty,
              let owner = account?.id, let session = authSession, let client = try? authClient() else { return }
        let epoch = sessionEpoch
        let completed = completedGuides
        guideSyncTask = Task {
            do {
                let progress = try await client.completeGuides(completed, owner: owner, session: session)
                guard epoch == sessionEpoch, account?.id == owner else { return }
                completedGuides.formUnion(progress.completed)
                cacheGuideProgress(owner)
                guideSyncTask = nil
                if !completedGuides.isSubset(of: progress.completed) { syncGuideProgress() }
            } catch {
                // Local completion still prevents repeats; retry at the next sign-in.
                if epoch == sessionEpoch { guideSyncTask = nil }
            }
        }
    }

    private func reload() async throws {
        let epoch = sessionEpoch
        // Read job states before the feed: a fast background job can otherwise
        // finish between parallel requests, leaving an empty feed paired with
        // an already-completed job that polling will never see transition.
        let jobs: [Job] = try await get("v1/jobs")
        async let feed: DailyFeed = get("v1/feed", query: feedQuery(read: false))
        async let readFeed: DailyFeed = get("v1/feed", query: feedQuery(read: true))
        async let bookmarks: [Post] = get("v1/bookmarks")
        async let sources: [NewsletterSource] = get("v1/me/sources")
        async let review: [Post] = get("v1/review")
        async let candidates: [IncomingNewsletter] = get("v1/inbox/candidates")
        async let confirmations: [NewsletterConfirmation] = get("v1/newsletter-confirmations")
        let values = try await (feed, bookmarks, sources, review, candidates, readFeed, confirmations)
        guard epoch == sessionEpoch else { throw CancellationError() }
        self.feed = values.0; self.bookmarks = values.1; self.sources = values.2
        self.review = values.3; self.jobs = jobs
        self.inboxCandidates = values.4
        self.newsletterConfirmations = values.6
        self.readFeed = values.5
        offline = false
        if let account {
            let snapshot = Snapshot(account: account, feed: self.feed, bookmarks: self.bookmarks, sources: self.sources, readFeed: self.readFeed)
            try JSONEncoder().encode(snapshot).write(to: cache, options: [.atomic, .completeFileProtection])
        }
    }

    private func feedQuery(read: Bool, before: String? = nil) -> [URLQueryItem] {
        var items = [URLQueryItem(name: "history", value: "true"), URLQueryItem(name: "read_state", value: read ? "read" : "unread")]
        if let before { items.append(URLQueryItem(name: "before", value: before)) }
        return items
    }

    func loadOlderPosts(read: Bool = false) async {
        guard !offline, !(read ? loadingReadHistory : loadingOlder),
              let cursor = (read ? readFeed : feed)?.nextCursor else { return }
        if read { loadingReadHistory = true } else { loadingOlder = true }
        defer { if read { loadingReadHistory = false } else { loadingOlder = false } }
        let epoch = sessionEpoch
        do {
            let page: DailyFeed = try await get("v1/feed", query: feedQuery(read: read, before: cursor))
            guard epoch == sessionEpoch, let current = read ? readFeed : feed, current.nextCursor == cursor else { return }
            let known = Set(current.posts.map(\.id))
            let merged = DailyFeed(date: page.date, posts: current.posts + page.posts.filter { !known.contains($0.id) }, total: page.total, unread: page.unread, finite: page.finite, nextCursor: page.nextCursor)
            if read { readFeed = merged } else { feed = merged }
        } catch { if !isCancellation(error) { self.error = error.localizedDescription } }
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
            self.notice = source.isCustom ? "Email processing resumed for \(source.displayName)." : "Signup queued using your account alias. Subscription progress is in Manage."
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

    func confirmUnsubscribe(_ source: NewsletterSource) async -> Bool {
        var confirmed = false
        await perform {
            _ = try await self.request("v1/me/sources/\(source.id)/unsubscribe/confirm", method: "POST")
            try await self.reload()
            self.notice = "Unsubscribed from \(source.displayName). Existing posts are kept."
            confirmed = true
        }
        return confirmed
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
        let epoch = sessionEpoch
        do {
            if account?.aliasStatus != "active" { account = try await get("v1/me") }
            let updated: [Job] = try await get("v1/jobs")
            async let candidates: [IncomingNewsletter] = get("v1/inbox/candidates")
            async let confirmations: [NewsletterConfirmation] = get("v1/newsletter-confirmations")
            async let sourceList: [NewsletterSource] = get("v1/me/sources")
            let mail = try await (candidates, confirmations, sourceList)
            guard epoch == sessionEpoch else { return }
            inboxCandidates = mail.0; newsletterConfirmations = mail.1; sources = mail.2
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

    func visualData(_ post: Post, visual: PostVisual? = nil) async -> Data? {
        guard let assetId = (visual ?? post.visual)?.assetId, let accountID = account?.id else { return nil }
        let cacheKey = accountID + "-" + post.id + "-" + assetId
        if let data = imageCache[cacheKey] { return data }
        guard assetId.allSatisfy({ $0.isHexDigit }) else { return nil }
        let directory = cache.deletingLastPathComponent().appendingPathComponent("images")
        let path = directory.appendingPathComponent(cacheKey + ".png")
        if let data = try? Data(contentsOf: path) { imageCache[cacheKey] = data; return data }
        guard !offline else { return nil }
        do {
            let route = assetId == post.visual?.assetId ? "image" : "images/\(assetId)"
            let data = try await request("v1/posts/\(post.id)/\(route)")
            guard account?.id == accountID else { return nil }
            if imageCache.count >= 20 { imageCache.removeAll() }
            imageCache[cacheKey] = data
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try data.write(to: path, options: [.atomic, .completeFileProtection])
            return data
        } catch { return nil }
    }

    func acceptEmailNewsletter(_ candidate: IncomingNewsletter, name: String) async -> Bool {
        var accepted = false
        await perform {
            let body = try JSONSerialization.data(withJSONObject: ["name": name])
            _ = try await self.request("v1/inbox/candidates/\(candidate.id)/accept", method: "POST", body: body)
            try await self.reload(); accepted = true
        }
        return accepted
    }

    func dismissEmailNewsletter(_ candidate: IncomingNewsletter) async {
        await perform {
            _ = try await self.request("v1/inbox/candidates/\(candidate.id)/dismiss", method: "POST")
            try await self.reload()
        }
    }

    func sourceEmail(_ post: Post) async throws -> EmailSourceText {
        try await get("v1/posts/\(post.id)/source")
    }

    func newsletterConfirmationURL(_ confirmation: NewsletterConfirmation) async -> URL? {
        var destination: URL?
        await perform {
            struct Handoff: Decodable { let url: String }
            let result = try self.decoder.decode(Handoff.self, from: await self.request("v1/newsletter-confirmations/\(confirmation.id)/open", method: "POST"))
            guard let url = URL(string: result.url), url.scheme == "https", url.user == nil, url.password == nil else {
                throw APIError.response(422, "Confirmation link is unavailable")
            }
            destination = url
            try await self.reload()
        }
        return destination
    }

    func beginNewsletterSetup(_ source: NewsletterSource) async -> Bool {
        var ready = false
        await perform {
            _ = try await self.request("v1/me/sources/\(source.id)/setup", method: "POST")
            ready = true
        }
        return ready
    }

    func newsletterCode(_ confirmation: NewsletterConfirmation) async -> String? {
        var code: String?
        await perform {
            struct Result: Decodable { let code: String }
            let result = try self.decoder.decode(Result.self, from: await self.request("v1/newsletter-confirmations/\(confirmation.id)/code", method: "POST"))
            code = result.code
        }
        return code
    }

    func refreshNewsletterConfirmations() async {
        guard account != nil else { return }
        do { newsletterConfirmations = try await get("v1/newsletter-confirmations") }
        catch { /* The normal refresh path reports connection failures. */ }
    }

    func completeNewsletterConfirmation(_ confirmation: NewsletterConfirmation) async -> Bool {
        var completed = false
        await perform {
            _ = try await self.request("v1/newsletter-confirmations/\(confirmation.id)/complete", method: "POST")
            try await self.reload(); completed = true
        }
        return completed
    }

    func removePendingSetup(_ source: NewsletterSource) async {
        await removeSource(source)
    }

    func removeSource(_ source: NewsletterSource) async {
        await perform {
            _ = try await self.request("v1/me/sources/\(source.id)", method: "DELETE")
            try await self.reload()
            self.notice = source.isUnsubscribed ? "Newsletter removed from the list. Existing posts are kept." : "Setup removed. Future emails from this newsletter will be ignored."
        }
    }

    func ask(_ post: Post, question: String, history: [PostChatTurn]) async throws -> PostChatReply {
        struct Input: Encodable { let question: String; let history: [PostChatTurn] }
        let bounded = history.suffix(6).map { PostChatTurn(role: $0.role, text: String($0.text.prefix(2000))) }
        let body = try JSONEncoder().encode(Input(question: question, history: bounded))
        return try decoder.decode(PostChatReply.self, from: await request("v1/posts/\(post.id)/chat", method: "POST", body: body, timeout: 270))
    }

    func retryVisual(_ post: Post) async {
        await perform {
            _ = try await self.request("v1/posts/\(post.id)/image/retry", method: "POST")
            try await self.reload()
        }
    }

    func deleteAccount() async {
        await perform {
            let owner = self.account?.id
            _ = try await self.request("v1/me", method: "DELETE")
            if let owner { Session.clear(account: self.guideCacheKey(owner)) }
            self.clearLocalSession()
        }
    }

    private func clearLocalSession() {
        cancelEmailFlow()
        deferredAuthURL = nil
        sessionEpoch += 1
        guideLoadTask?.cancel(); guideLoadTask = nil
        guideSyncTask?.cancel(); guideSyncTask = nil
        guideOwner = nil; guideProgressReady = false; completedGuides = []
        refreshTask?.cancel(); refreshTask = nil; authSession = nil
        Session.clear()
        token = nil; account = nil; feed = nil; readFeed = nil; bookmarks = []; sources = []; review = []; jobs = []; inboxCandidates = []; newsletterConfirmations = []
        imageCache.removeAll()
        avatarData = nil; loadedAvatarRevision = nil
        try? FileManager.default.removeItem(at: cache.deletingLastPathComponent().appendingPathComponent("avatar.png"))
        let images = cache.deletingLastPathComponent().appendingPathComponent("images")
        try? FileManager.default.removeItem(at: images)
        try? FileManager.default.removeItem(at: cache)
    }
}
