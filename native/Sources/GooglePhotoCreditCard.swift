import SwiftUI

struct GooglePhotoCreditCard: View {
    let credit: PhotoCredit

    private func webURL(_ value: String?) -> URL? {
        guard let value, let url = URL(string: value.hasPrefix("//") ? "https:" + value : value),
              url.scheme == "https", url.host != nil, url.user == nil, url.password == nil else { return nil }
        return url
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: "Google Maps").font(.system(size: 13, weight: .regular)).foregroundStyle(.primary)
                ForEach(Array(credit.authors.enumerated()), id: \.offset) { _, author in
                    if let profile = webURL(author.uri) {
                        Link("Photo by \(author.displayName)", destination: profile)
                    } else { Text("Photo by \(author.displayName)") }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            if let source = webURL(credit.sourceUrl) {
                Link(destination: source) {
                    Label("View photo", systemImage: "arrow.up.right")
                        .font(.system(size: 12, weight: .medium)).fixedSize()
                        .foregroundStyle(.blue)
                        .padding(.horizontal, 10).padding(.vertical, 7)
                        .background(.primary.opacity(0.06), in: Capsule())
                }.accessibilityLabel("View original photo and full credits on Google Maps")
            }
        }
        .font(.system(size: 12, weight: .regular)).foregroundStyle(.primary.opacity(0.75))
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("post.photoCredit")
    }
}
