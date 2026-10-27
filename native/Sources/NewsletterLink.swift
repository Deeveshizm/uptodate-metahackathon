import Foundation

enum NewsletterLink {
    enum Invalid: LocalizedError {
        case emailAddress
        case website
        var errorDescription: String? {
            switch self {
            case .emailAddress:
                "Paste the newsletter's website link here. Your newsletter email address is filled in automatically."
            case .website:
                "Enter a newsletter website, such as https://newsletter.example.com."
            }
        }
    }

    static func normalize(_ input: String) throws -> String {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, text.count <= 3000,
              text.rangeOfCharacter(from: .whitespacesAndNewlines) == nil else { throw Invalid.website }
        let explicitWebScheme = text.lowercased().hasPrefix("https://") || text.lowercased().hasPrefix("http://")
        if text.contains("@") && !explicitWebScheme { throw Invalid.emailAddress }
        let candidate = text.contains("://") ? text : "https://" + text
        guard var components = URLComponents(string: candidate),
              let scheme = components.scheme?.lowercased(), ["https", "http"].contains(scheme),
              let host = components.host, host.contains("."),
              components.user == nil, components.password == nil,
              let url = components.url, url.host != nil else { throw Invalid.website }
        components.scheme = scheme
        components.fragment = nil
        guard let normalized = components.url?.absoluteString, normalized.count <= 3000 else { throw Invalid.website }
        return normalized
    }
}
