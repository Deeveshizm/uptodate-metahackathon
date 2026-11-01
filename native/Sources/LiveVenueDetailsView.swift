import SwiftUI

struct LiveVenueDetailsView: View {
    let details: LiveVenueDetails?
    private func link(_ value: String?) -> URL? {
        guard let value, let url = URL(string: value.hasPrefix("//") ? "https:" + value : value),
              url.scheme == "https", url.host != nil else { return nil }
        return url
    }
    var body: some View {
        if let details, !details.sources.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                if let url = link(details.sourceUrl) {
                    Link("Google Maps", destination: url).font(.system(size: 12))
                } else { Text("Google Maps").font(.system(size: 12)) }
                VenueFactsView(facts: VenueFacts(rating: details.fields.rating, hours: details.fields.hours))
                if let reviews = details.fields.reviews {
                    if let summary = reviews.summary, !summary.isEmpty {
                        Text(summary).font(.subheadline).lineSpacing(3)
                        if let disclosure = reviews.disclosure { Text(disclosure).font(.caption).foregroundStyle(.secondary) }
                        if let url = link(reviews.sourceUrl) { Link("View reviews", destination: url).font(.caption) }
                    }
                    ForEach(Array((reviews.items ?? []).enumerated()), id: \.offset) { _, review in
                        VStack(alignment: .leading, spacing: 7) {
                            HStack(spacing: 6) {
                                if let avatar = link(review.avatarUrl) {
                                    AsyncImage(url: avatar) { image in image.resizable().scaledToFill() }
                                        placeholder: { Color.clear }.frame(width: 20, height: 20).clipShape(Circle())
                                }
                                if let url = link(review.authorUrl) { Link(review.author ?? "Reviewer", destination: url) }
                                else { Text(review.author ?? "Reviewer") }
                            }.font(.caption)
                            Text(review.text).font(.subheadline).lineSpacing(3)
                            if let source = link(review.sourceUrl) { Link("View on Google Maps", destination: source).font(.caption) }
                        }
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
                .padding(14).background(.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 16))
        }
    }
}
