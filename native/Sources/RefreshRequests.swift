import Foundation

// Coalesce simultaneous reads. Mutations and sign-out invalidate the entire
// generation, so even an uncooperative transport cannot deliver stale data.
@MainActor
final class RefreshRequests {
    private struct Entry {
        let id: UUID
        let task: Task<Data, Error>
    }
    private var entries: [String: Entry] = [:]
    private(set) var generation = 0

    func load(_ key: String, using operation: @escaping @MainActor () async throws -> Data) async throws -> Data {
        try Task.checkCancellation()
        let version = generation
        let entry: Entry
        if let current = entries[key] { entry = current }
        else {
            entry = Entry(id: UUID(), task: Task { try await operation() })
            entries[key] = entry
        }
        defer {
            if entries[key]?.id == entry.id { entries.removeValue(forKey: key) }
        }
        let data = try await entry.task.value
        try Task.checkCancellation()
        guard version == generation else { throw CancellationError() }
        return data
    }

    func invalidate() {
        generation += 1
        for entry in entries.values { entry.task.cancel() }
        entries.removeAll()
    }
}
