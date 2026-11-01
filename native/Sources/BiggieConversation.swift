import Foundation
import Combine

@MainActor
final class BiggieConversation: ObservableObject {
    typealias ReplyProvider = @MainActor (String, [PostChatTurn]) async throws -> PostChatReply

    @Published var question = ""
    @Published private(set) var turns: [PostChatTurn] = []
    @Published private(set) var sending = false
    @Published private(set) var error: String?
    @Published private(set) var suggestOpenIn = false
    private var task: Task<Void, Never>?
    private var requestID: UUID?
    private var failedRequest: PendingRequest?

    private struct PendingRequest {
        let text: String
        let history: [PostChatTurn]
        let evidenceSources: [String]
    }

    private enum ReplyError: LocalizedError {
        case empty
        var errorDescription: String? { "Biggie returned an empty answer. Your conversation is kept; try again." }
    }

    var canRetry: Bool { failedRequest != nil && !sending }

    func send(evidenceSources: [String], using reply: @escaping ReplyProvider) {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !sending, !text.isEmpty, text.count <= 1000 else { return }
        let pending = PendingRequest(text: text, history: Array(turns.suffix(6)), evidenceSources: evidenceSources)
        turns.append(PostChatTurn(role: "user", text: text))
        question = ""
        start(pending, using: reply)
    }

    func retry(using reply: @escaping ReplyProvider) {
        guard !sending, let pending = failedRequest else { return }
        start(pending, using: reply)
    }

    private func start(_ pending: PendingRequest, using reply: @escaping ReplyProvider) {
        let identifier = UUID()
        requestID = identifier
        sending = true
        error = nil
        failedRequest = nil
        task = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.requestID == identifier {
                    self.sending = false
                    self.requestID = nil
                    self.task = nil
                }
            }
            do {
                let result = try await reply(pending.text, pending.history)
                guard !Task.isCancelled, self.requestID == identifier else { return }
                guard !result.answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw ReplyError.empty }
                self.suggestOpenIn = result.handoffSuggested == true
                let references = result.evidenceIds.compactMap { index -> String? in
                    pending.evidenceSources.indices.contains(index) ? pending.evidenceSources[index] : nil
                }
                let suffix = references.isEmpty ? "" : "\n\nSource excerpts: " + references.joined(separator: "; ")
                self.turns.append(PostChatTurn(role: "assistant", text: result.answer + suffix, citations: result.citations ?? []))
            } catch {
                guard !Task.isCancelled, self.requestID == identifier else { return }
                self.error = error.localizedDescription
                self.failedRequest = pending
            }
        }
    }

    func cancel() {
        requestID = nil
        task?.cancel()
        task = nil
        sending = false
        failedRequest = nil
    }

    func clear() {
        cancel()
        turns = []
        question = ""
        error = nil
        suggestOpenIn = false
    }
}

@MainActor
final class BiggieConversationStore {
    private struct Key: Hashable { let owner: String; let post: String }
    private var conversations: [Key: BiggieConversation] = [:]

    func conversation(owner: String, post: String) -> BiggieConversation {
        let key = Key(owner: owner, post: post)
        if let existing = conversations[key] { return existing }
        let value = BiggieConversation()
        conversations[key] = value
        return value
    }

    func clear() {
        for conversation in conversations.values { conversation.clear() }
        conversations.removeAll()
    }
}
