import Foundation

enum BackendConfiguration {
    // Only a public origin is bundled. Database, model and admin credentials
    // remain on the server; no .env file is part of the app.
    static func validatedURL(_ value: String) -> URL? {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/",
              url.scheme == "https" || (url.scheme == "http" && ["127.0.0.1", "localhost", "::1"].contains(host))
        else { return nil }
        return url
    }
}
