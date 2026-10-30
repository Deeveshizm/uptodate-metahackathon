import Foundation

struct Account: Codable {
    let id: String
    let alias: String
    let timezone: String
    let token: String?
    var email: String? = nil
    var displayName: String? = nil
    var aliasStatus: String? = nil
    var aliasScope: String? = nil
    var avatarRevision: String? = nil
}

struct NewsletterSource: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let sender: String
    let signupUrl: String
    let muted: Int
    let url: String?
    let feedUrl: String?
    let subscriptionStatus: String?
    let subscriptionDetail: String?
    var sourceKind: String? = nil
    var category: String? = nil
    var managementName: String? = nil
    var unsubscribedAt: String? = nil
    var archivedAt: String? = nil
    var subscriptionConfirmed: Bool? = nil
    var displayName: String { managementName ?? name }
    var isCustom: Bool {
        if let category { return category == "custom" }
        return sourceKind == "email" || ["muse", "claude"].contains(name.lowercased())
    }
    var isUnsubscribed: Bool { subscriptionStatus == "unsubscribed" }
    var canUnsubscribe: Bool {
        if isCustom { return subscriptionStatus == "receiving" }
        guard ["submitted", "email_received", "receiving", "unsubscribe_pending", "unsubscribing"].contains(subscriptionStatus ?? "") else { return false }
        return subscriptionConfirmed ?? ["email_received", "receiving"].contains(subscriptionStatus ?? "")
    }
    var canRemoveSetup: Bool {
        !isCustom && subscriptionConfirmed != true && ["not_requested","queued","submitting","submitted","confirmation_required","manual_action","failed","needs_agent","needs_inbox"].contains(subscriptionStatus ?? "")
    }

    var subscriptionTitle: String {
        switch subscriptionStatus {
        case "queued", "submitting": "Signup in progress"
        case "submitted": subscriptionConfirmed == true ? "Subscribed · waiting for the first edition" : "Signup submitted · waiting for email"
        case "confirmation_required": "Confirm your subscription"
        case "email_received": "Publisher email received · waiting for an edition"
        case "receiving": isCustom ? "Email newsletter connected" : "Newsletter emails connected"
        case "needs_inbox": "Waiting for your newsletter address"
        case "needs_agent", "manual_action", "failed": "Signup needs attention"
        case "unsubscribe_pending", "unsubscribing": "Publisher unsubscribe pending · new posts stopped"
        case "unsubscribed": isCustom ? "Email updates stopped" : "Unsubscribed"
        default: "Newsletter saved"
        }
    }
}

struct IncomingNewsletter: Codable, Identifiable {
    let id: String
    let sender: String
    let senderName: String
    let provider: String?
    let subject: String
    let status: String
    var displayName: String { provider ?? senderName }
}

struct NewsletterConfirmation: Decodable, Identifiable {
    let id: String
    let subject: String
    let senderDomain: String
    let status: String
    let sourceId: String?
    let sourceName: String?
}

struct EmailSourceText: Decodable {
    let title: String
    let text: String
    let author: String
}

struct NewsletterAdded: Decodable {
    let sourceId: String
}

struct UnsubscribeResult: Decodable {
    let status: String
    let detail: String
    let actionUrl: String?
}

struct Evidence: Codable {
    let source: String
    let quote: String
    let url: String
}

struct Post: Codable, Identifiable {
    let id: String
    let title: String
    let hook: String
    let finding: String
    let limitations: String
    let originalUrl: String
    let status: String
    let rights: String
    let generation: String
    let publishedDate: String?
    let isDemo: Bool
    let source: NewsletterSource
    let evidence: [Evidence]
    let bookmarked: Bool
    let read: Bool
    let analysis: PostAnalysis?
    let verification: String?
    let visual: PostVisual?
    let tags: [PostTag]?
    var visuals: [PostVisual]? = nil
    var paper: PaperSource? = nil
    var contentAction: ContentAction? = nil
    var linkActions: [PostLinkAction]? = nil

    var gallery: [PostVisual] {
        var seen = Set<String>()
        return ((visuals?.isEmpty == false ? visuals : nil) ?? visual.map { [$0] } ?? [])
            .filter { item in item.assetId.map { seen.insert($0).inserted } ?? false }
    }
    var actionKind: String {
        if contentAction?.kind == "ingredients", !(contentAction?.recipe?.ingredients.isEmpty ?? true) { return "ingredients" }
        if contentAction?.kind == "places", !(contentAction?.places?.isEmpty ?? true) { return "places" }
        return "ask_ai"
    }
    var actionTitle: String {
        switch actionKind {
        case "ingredients": "Add ingredients"
        case "places": "View places"
        default: paper?.abstract?.isEmpty == false ? "Ask about this paper" : "Ask AI"
        }
    }
    var aiContext: String {
        var parts = ["Help me understand this post. Distinguish source claims from your explanation.", title, hook, finding]
        parts += (analysis?.sections ?? []).map { $0.heading + ": " + $0.text }
        if let caveat = sourceCaveat { parts.append(caveat) }
        if let abstract = paper?.abstract, !abstract.isEmpty { parts.append("Original abstract: " + String(abstract.prefix(6000))) }
        parts += evidence.prefix(6).map { "Source excerpt: " + String($0.quote.prefix(1000)) }
        parts.append(isEmailSource ? "Source: private newsletter email" : "Original source: " + originalUrl)
        return parts.joined(separator: "\n\n")
    }
    var sourceCaveat: String? {
        let text = limitations.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, text != "Automated summary; check the original source for full context." else { return nil }
        return text
    }
    var isEmailSource: Bool { originalUrl.hasPrefix("email:") }
    var isResearchPaper: Bool {
        paper?.doi?.isEmpty == false || paper?.abstract?.isEmpty == false ||
            ["arxiv.org", "www.arxiv.org"].contains(URL(string: originalUrl)?.host ?? "")
    }
}

struct PostLinkAction: Codable, Identifiable {
    let kind: String
    let label: String
    let url: String
    var name: String? = nil
    var address: String? = nil
    var id: String { kind + ":" + url }
    var destination: URL? {
        if kind == "phone" {
            guard url.range(of: #"^tel:\+?[0-9]{7,15}$"#, options: .regularExpression) != nil else { return nil }
            return URL(string: url)
        }
        guard ["website", "github", "map"].contains(kind), let value = URL(string: url),
              ["https", "http"].contains(value.scheme?.lowercased() ?? ""), value.host != nil,
              value.user == nil, value.password == nil else { return nil }
        return value
    }
    var symbol: String {
        switch kind {
        case "phone": "phone"
        case "github": "chevron.left.forwardslash.chevron.right"
        case "map": "mappin.and.ellipse"
        default: "globe"
        }
    }
    var isGitHub: Bool {
        ["website", "github"].contains(kind) && ["github.com", "www.github.com"].contains(destination?.host?.lowercased() ?? "")
    }
    var repositoryURL: URL? {
        guard isGitHub, let destination else { return nil }
        let parts = destination.path.split(separator: "/").map(String.init)
        guard parts.count >= 2,
              !["features", "topics", "settings", "orgs", "users", "login", "signup", "search", "explore", "sponsors", "marketplace"].contains(parts[0].lowercased()) else { return nil }
        let repo = parts[1].lowercased().hasSuffix(".git") ? String(parts[1].dropLast(4)) : parts[1]
        guard !repo.isEmpty else { return nil }
        var components = URLComponents(string: "https://github.com")!
        components.path = "/" + parts[0] + "/" + repo
        return components.url
    }
    private var groupingKey: String {
        if let repositoryURL { return "github:" + repositoryURL.absoluteString.lowercased() }
        return kind + ":" + (destination?.absoluteString ?? url).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
    static func consolidated(_ actions: [PostLinkAction]) -> [PostLinkAction] {
        var seen = Set<String>()
        return actions.compactMap { action in
            guard action.destination != nil, seen.insert(action.groupingKey).inserted else { return nil }
            if action.isGitHub {
                guard let root = action.repositoryURL else { return nil }
                return PostLinkAction(kind: "github", label: String(root.path.dropFirst()), url: root.absoluteString)
            }
            return action
        }
    }
}

struct PaperSource: Codable {
    let url: String?
    let title: String?
    let abstract: String?
    var doi: String? = nil
}

struct RecipeAction: Codable {
    let name: String?
    let ingredients: [String]
    let sourceUrl: String?
    var note: String? = nil
}

struct PlaceAction: Codable, Identifiable {
    let name: String
    let address: String?
    let sourceUrl: String?
    var mapUrl: String? = nil
    var websiteUrl: String? = nil
    var identityKnown: Bool? = nil
    var id: String { name + (address ?? "") }

    var sourceMapURL: URL? {
        guard let value = mapUrl, let url = URL(string: value),
              ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else { return nil }
        return url
    }

    func mapURL(for app: MapsApp) -> URL? {
        if let source = sourceMapURL {
            let host = (source.host ?? "").lowercased().replacingOccurrences(of: "www.", with: "", options: .anchored)
            if app == .apple, host == "maps.apple.com" { return source }
            if app == .google {
                if ["maps.app.goo.gl", "goo.gl"].contains(host) { return source }
                if host == "maps.google.com" || (host == "google.com" && source.path.hasPrefix("/maps")) {
                    var components = URLComponents(url: source, resolvingAgainstBaseURL: false)
                    components?.scheme = "comgooglemapsurl"
                    return components?.url
                }
            }
            if app == .waze, host == "waze.com" { return source }
        }
        guard identityKnown != false, name != "Location from email" else { return nil }
        let query = [name, address ?? ""].map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }.joined(separator: ", ")
        guard !query.isEmpty else { return nil }
        var components = URLComponents(string: app == .apple ? "https://maps.apple.com/" : app.scheme + "://")
        components?.queryItems = [URLQueryItem(name: "q", value: query)]
        return components?.url
    }
}

enum MapsApp: String, CaseIterable, Identifiable {
    case apple, google, waze
    var id: String { rawValue }
    var title: String {
        switch self {
        case .apple: "Apple Maps"
        case .google: "Google Maps"
        case .waze: "Waze"
        }
    }
    var scheme: String {
        switch self {
        case .apple: "maps"
        case .google: "comgooglemaps"
        case .waze: "waze"
        }
    }
}

struct ContentAction: Codable {
    let kind: String
    let recipe: RecipeAction?
    let places: [PlaceAction]?
    enum CodingKeys: String, CodingKey { case kind, data }
    init(from decoder: Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        kind = try box.decode(String.self, forKey: .kind)
        recipe = kind == "ingredients" ? try? box.decode(RecipeAction.self, forKey: .data) : nil
        places = kind == "places" ? try? box.decode([PlaceAction].self, forKey: .data).filter { !$0.name.isEmpty } : nil
    }
    func encode(to encoder: Encoder) throws {
        var box = encoder.container(keyedBy: CodingKeys.self)
        try box.encode(kind, forKey: .kind)
        if let recipe { try box.encode(recipe, forKey: .data) }
        if let places { try box.encode(places, forKey: .data) }
    }
}

struct PostChatReply: Decodable {
    let answer: String
    let evidenceIds: [Int]
    let sourceUrl: String
    let model: String
    var citations: [PostWebCitation]? = nil
    var handoffSuggested: Bool? = nil
    var handoffReason: String? = nil
}

struct PostWebCitation: Decodable, Identifiable {
    let title: String
    let url: String
    var id: String { url }
    var destination: URL? {
        guard let value = URL(string: url), ["https", "http"].contains(value.scheme?.lowercased() ?? ""),
              value.host != nil, value.user == nil, value.password == nil else { return nil }
        return value
    }
}

struct PostChatTurn: Encodable, Identifiable {
    let id = UUID()
    let role: String
    let text: String
    var citations: [PostWebCitation] = []
    enum CodingKeys: CodingKey { case role, text }
}

struct PostTag: Codable, Identifiable {
    let code: String
    let label: String
    let detail: String?
    var id: String { code }
}

struct PostVisual: Codable {
    let kind: String?
    let status: String?
    let assetId: String?
    let caption: String?
    let attribution: String?
    let detail: String?
    let sourceUrl: String?
    let licenseUrl: String?
}

struct PostAnalysis: Codable {
    let contentType: String?
    let problem: String?
    let approach: String?
    let impact: String?
    var sections: [PostSourceSection]? = nil
}

struct PostSourceSection: Codable, Identifiable {
    let heading: String
    let text: String
    var id: String { heading + text }
}

struct DailyFeed: Codable {
    let date: String
    let posts: [Post]
    let total: Int
    let unread: Int
    let finite: Bool
    let nextCursor: String?
}

struct Job: Codable, Identifiable {
    let id: String
    let subject: String
    let status: String
    let error: String?
    let kind: String?
    let stage: String?
    let result: JobResult?
}

struct JobResult: Codable { let items: [JobItem]? }
struct JobItem: Codable {
    let url: String
    let status: String
    let error: String?
}

struct ShareResult: Codable, Identifiable {
    var id: String { url }
    let url: String
    let mode: String
    let localOnly: Bool
}

struct Snapshot: Codable {
    let account: Account
    let feed: DailyFeed?
    let bookmarks: [Post]
    let sources: [NewsletterSource]
    var readFeed: DailyFeed? = nil
}
