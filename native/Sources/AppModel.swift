import Foundation
import Combine

enum APIError: LocalizedError {
    case response(Int, String)
    var errorDescription: String? {
        switch self { case .response(let status, let message): return "\(message) (\(status))" }
    }
}

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
    private var token = Session.read()
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
        if token != nil, let data = try? Data(contentsOf: cache),
           let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) {
            account = snapshot.account
            feed = snapshot.feed
            bookmarks = snapshot.bookmarks
            sources = snapshot.sources
            offline = true
        }
    }

    private func request(_ path: String, method: String = "GET", body: Data? = nil, contentType: String = "application/json") async throws -> Data {
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 15
        request.httpBody = body
        request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard 200..<300 ~= response.statusCode else {
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            if response.statusCode == 401 { clearLocalSession() }
            throw APIError.response(response.statusCode, object?["detail"] as? String ?? "The request failed")
        }
        return data
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try decoder.decode(T.self, from: await request(path))
    }

    func restore() async {
        guard token != nil else { return }
        await perform {
            self.account = try await self.get("v1/me")
            try await self.reload()
        }
    }

    func start() async {
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

    func refresh() async { await perform { try await self.reload() } }

    private func reload() async throws {
        async let feed: DailyFeed = get("v1/feed")
        async let bookmarks: [Post] = get("v1/bookmarks")
        async let sources: [NewsletterSource] = get("v1/me/sources")
        async let review: [Post] = get("v1/review")
        async let jobs: [Job] = get("v1/jobs")
        let values = try await (feed, bookmarks, sources, review, jobs)
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
        defer { busy = false }
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
            let body = try JSONSerialization.data(withJSONObject: ["url": url, "auto_subscribe": autoSubscribe])
            _ = try await self.request("v1/newsletters", method: "POST", body: body)
            try await self.reload()
            self.notice = "Newsletter saved. Posts appear automatically in Home; progress is in Manage."
            added = true
        }
        return added
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
        guard !busy, account != nil, jobs.contains(where: { ["queued", "processing"].contains($0.status) }) else { return }
        do {
            let updated: [Job] = try await get("v1/jobs")
            let finished = updated.contains { job in
                !["queued", "processing"].contains(job.status) && jobs.contains(where: { $0.id == job.id && ["queued", "processing"].contains($0.status) })
            }
            jobs = updated
            if finished { try await reload() }
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
        Session.clear()
        token = nil; account = nil; feed = nil; bookmarks = []; sources = []; review = []; jobs = []
        imageCache.removeAll()
        let images = cache.deletingLastPathComponent().appendingPathComponent("images")
        try? FileManager.default.removeItem(at: images)
        try? FileManager.default.removeItem(at: cache)
    }
}
