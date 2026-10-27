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
}

struct NewsletterSource: Codable, Identifiable {
    let id: String
    let name: String
    let sender: String
    let signupUrl: String
    let muted: Int
    let url: String?
    let feedUrl: String?
    let subscriptionStatus: String?
    let subscriptionDetail: String?

    var subscriptionTitle: String {
        switch subscriptionStatus {
        case "queued", "submitting": "Signup in progress"
        case "submitted": "Signup submitted · waiting for email"
        case "confirmation_required": "Confirm your subscription"
        case "email_received": "Publisher email received · waiting for an edition"
        case "receiving": "Newsletter emails connected"
        case "needs_inbox": "Waiting for your newsletter address"
        case "needs_agent", "manual_action", "failed": "Signup needs attention"
        case "unsubscribe_pending", "unsubscribing": "Publisher unsubscribe pending · new posts stopped"
        case "unsubscribed": "Unsubscribed"
        default: "Newsletter saved"
        }
    }
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
}

struct DailyFeed: Codable {
    let date: String
    let posts: [Post]
    let total: Int
    let unread: Int
    let finite: Bool
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
}
