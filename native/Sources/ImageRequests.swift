import Foundation

struct ImageResponse: Sendable {
    let data: Data
    let credit: PhotoCredit?
}

// Share a download only while callers are awaiting it. Completed Google
// photos are not retained here or persisted to the phone's image cache.
@MainActor
final class ImageRequests {
    private struct Entry {
        let id: UUID
        let task: Task<ImageResponse, Error>
        var waiters: Set<UUID>
    }
    private var entries: [String: Entry] = [:]

    func load(key: String, using operation: @escaping @MainActor () async throws -> ImageResponse) async throws -> ImageResponse {
        try Task.checkCancellation()
        let waiter = UUID()
        let entry: Entry
        if var existing = entries[key] {
            existing.waiters.insert(waiter)
            entries[key] = existing
            entry = existing
        } else {
            entry = Entry(id: UUID(), task: Task { try await operation() }, waiters: [waiter])
            entries[key] = entry
        }
        defer { release(key: key, request: entry.id, waiter: waiter) }
        return try await withTaskCancellationHandler {
            let response = try await entry.task.value
            try Task.checkCancellation()
            guard entries[key]?.id == entry.id else { throw CancellationError() }
            return response
        } onCancel: {
            Task { @MainActor [weak self] in
                self?.release(key: key, request: entry.id, waiter: waiter)
            }
        }
    }

    private func release(key: String, request: UUID, waiter: UUID) {
        guard var entry = entries[key], entry.id == request,
              entry.waiters.remove(waiter) != nil else { return }
        if entry.waiters.isEmpty {
            entry.task.cancel()
            entries.removeValue(forKey: key)
        } else {
            entries[key] = entry
        }
    }

    func cancelAll() {
        for entry in entries.values { entry.task.cancel() }
        entries.removeAll()
    }
}
