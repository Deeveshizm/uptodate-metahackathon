import Foundation

enum APIError: LocalizedError {
    case response(Int, String)
    var errorDescription: String? {
        switch self { case .response(let status, let message): return "\(message) (\(status))" }
    }
}
