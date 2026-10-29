import SwiftUI

// Keep the provisional visual identity here so it is easy to replace later.
enum FeedStyle {
    static let wordmark = "uptodate"
    static let feedWidth: CGFloat = 620
    static let gutter: CGFloat = 20
    static let ring = LinearGradient(colors: [.orange, .pink, .purple], startPoint: .bottomLeading, endPoint: .topTrailing)
    static let covers: [(background: Color, foreground: Color, accent: Color)] = [
        (Color(red: 0.91, green: 0.94, blue: 0.88), Color(red: 0.15, green: 0.25, blue: 0.18), Color(red: 0.54, green: 0.64, blue: 0.43)),
        (Color(red: 0.88, green: 0.90, blue: 0.96), Color(red: 0.18, green: 0.22, blue: 0.39), Color(red: 0.53, green: 0.58, blue: 0.78)),
        (Color(red: 0.97, green: 0.89, blue: 0.82), Color(red: 0.37, green: 0.20, blue: 0.15), Color(red: 0.77, green: 0.52, blue: 0.35)),
    ]
}

// Every screen shares the feed's full-bleed scaffold: edge-to-edge rows on the
// page background, separated by dividers rather than inset grouped cards.
struct PageScroll<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) { content }
                .frame(maxWidth: FeedStyle.feedWidth).frame(maxWidth: .infinity)
        }.background(.background)
    }
}

// Matches the feed's "Today's edition / 4 unread" row.
struct PageHeader: View {
    let title: String
    var trailing: String? = nil
    var body: some View {
        HStack {
            Text(title).font(.system(size: 12, weight: .medium))
            Spacer()
            if let trailing { Text(trailing).font(.system(size: 11)) }
        }.foregroundStyle(.secondary)
            .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 14)
    }
}

struct SectionHeader: View {
    let title: String
    init(_ title: String) { self.title = title }
    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 9, weight: .semibold)).tracking(1.5).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, FeedStyle.gutter).padding(.top, 28).padding(.bottom, 10)
    }
}

struct InfoRow: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 14)
    }
}

struct ActionRow: View {
    let title: String
    var destructive = false
    var disabled = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title).font(.system(size: 15))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 16)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(destructive ? Color.red : Color.primary)
        .disabled(disabled).opacity(disabled ? 0.4 : 1)
    }
}

struct RowDivider: View {
    var body: some View { Divider().padding(.leading, FeedStyle.gutter) }
}

struct EmptyState: View {
    let symbol: String
    let title: String
    let message: String
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: symbol).font(.system(size: 36, weight: .ultraLight))
            Text(title).font(.system(size: 19, weight: .semibold)).tracking(-0.3)
            Text(message).font(.system(size: 13)).foregroundStyle(.secondary)
                .multilineTextAlignment(.center).lineSpacing(3)
        }.frame(maxWidth: .infinity).padding(.horizontal, 40).padding(.vertical, 56)
    }
}

struct SourceAvatar: View {
    let name: String
    let size: CGFloat
    let symbol: String
    private var palette: Int { name.utf8.reduce(0) { ($0 + Int($1)) % FeedStyle.covers.count } }

    var body: some View {
        ZStack {
            Circle().fill(FeedStyle.covers[palette].background)
            Image(systemName: symbol).font(.system(size: size * 0.37, weight: .light))
                .foregroundStyle(FeedStyle.covers[palette].foreground)
        }
        .frame(width: size, height: size).accessibilityHidden(true)
    }
}

struct EditorialCover: View {
    let post: Post
    let index: Int
    private var palette: (background: Color, foreground: Color, accent: Color) {
        FeedStyle.covers[index % FeedStyle.covers.count]
    }
    // Seeded editions have no licensed artwork, so pick a glyph from the item itself.
    private var topicSymbol: String {
        let text = (post.title + " " + post.finding).lowercased()
        if text.contains("snow") || text.contains("tahoe") || text.contains("resort") { return "snowflake" }
        if text.contains("matcha") || text.contains("tea") { return "mug" }
        if text.contains("bakery") || text.contains("sourdough") { return "birthday.cake" }
        return "cup.and.saucer"
    }
    var body: some View {
        ZStack {
            palette.background
            GeometryReader { geometry in
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        HStack(spacing: 6) {
                            Circle().fill(palette.foreground).frame(width: 5, height: 5)
                            Text(post.isDemo ? "WEEKLY ROUNDUP" : "NEWSLETTER EXCERPT").tracking(1.8)
                        }.font(.system(size: 9, weight: .semibold))
                        Spacer()
                        Text(String(format: "%02d", index + 1)).font(.system(size: 10, design: .monospaced))
                    }
                    Spacer(minLength: 8)
                    if post.isDemo {
                        RoundupCover(hook: post.hook, symbol: topicSymbol, color: palette.foreground, accent: palette.accent)
                            .frame(maxWidth: .infinity)
                    } else {
                        Image(systemName: "text.quote").font(.system(size: 34, weight: .ultraLight))
                        Text(post.finding).font(.system(size: 20, weight: .regular, design: .serif))
                            .lineLimit(6)
                    }
                    Spacer(minLength: 8)
                    VStack(alignment: .leading, spacing: 6) {
                        Rectangle().fill(palette.foreground).frame(width: 25, height: 1)
                        Text(post.isDemo ? "Summarized from your Weekly Roundup · source linked" : "No licensed figure available · excerpt preview")
                            .font(.system(size: 10))
                    }
                }.padding(28).frame(width: geometry.size.width, height: geometry.size.height)
            }
        }.aspectRatio(1.06, contentMode: .fit).clipped().foregroundStyle(palette.foreground)
    }
}

struct FeedPostCard: View {
    @EnvironmentObject private var model: AppModel
    let post: Post
    let index: Int
    let openPost: () -> Void
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 9) {
                    HStack {
                        Text(post.isDemo ? "Sample · \(post.source.name)" : post.source.name)
                            .font(.caption.weight(.medium)).lineLimit(1)
                        Spacer()
                        Text(post.publishedDate ?? "Today").font(.caption2)
                    }.foregroundStyle(.secondary)
                    Text(post.title).font(.title2.weight(.semibold)).tracking(-0.4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }.padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 16)
                .contentShape(Rectangle()).onTapGesture(perform: openPost)
                .accessibilityAddTraits(.isButton)
                .accessibilityHint("Open full post")

            if !post.gallery.isEmpty {
                PostVisualView(post: post, inset: 16, onSelect: openPost)
            } else if ["machine_checked", "source_preserved"].contains(post.verification ?? "") {
                Button(action: openPost) {
                    VStack(spacing: 12) {
                        Image(systemName: "photo").font(.system(size: 34, weight: .light))
                        Text(post.visual?.status == "failed" ? "Image unavailable" : "Image is being prepared").font(.callout)
                        Text(post.visual?.status == "failed" ? "Open the post to read it or retry the image." : "You can read the post while its image loads.").font(.caption)
                    }.foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 220)
                        .background(.secondary.opacity(0.05))
                }.buttonStyle(.plain).accessibilityLabel("Read \(post.title)")
            } else {
                Button(action: openPost) {
                    EditorialCover(post: post, index: index)
                }.buttonStyle(.plain).accessibilityLabel("Read \(post.title)")
            }

            VStack(alignment: .leading, spacing: 8) {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(post.tags ?? []) { tag in
                        Label(tag.label, systemImage: "clock")
                            .font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
                            .padding(.horizontal, 8).padding(.vertical, 4)
                            .background(.orange.opacity(0.08), in: Capsule())
                    }
                    Text(post.finding).font(.subheadline).lineSpacing(4).lineLimit(expanded ? nil : 4)
                    if expanded, let caveat = post.sourceCaveat { Text(caveat).font(.footnote).lineSpacing(3) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle()).onTapGesture(perform: openPost)
                .accessibilityAction(named: "Open full post", openPost)
                HStack {
                    Spacer()
                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() }
                    } label: {
                        Text(expanded ? "Read less" : "Read more")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 6).padding(.vertical, 4)
                            .contentShape(Rectangle())
                    }.buttonStyle(.plain).accessibilityIdentifier("post.readMore")
                }
                HStack {
                    PostContextActionView(post: post, isFeed: true)
                    Spacer()
                    Button { Task { await model.bookmark(post) } } label: {
                        Image(systemName: post.bookmarked ? "bookmark.fill" : "bookmark")
                            .font(.system(size: 16)).frame(width: 44, height: 44)
                    }.accessibilityLabel(post.bookmarked ? "Remove bookmark" : "Bookmark post")
                        .disabled(model.busy || model.offline)
                }.buttonStyle(.plain).foregroundStyle(.primary).padding(.top, 2)
            }.padding(.horizontal, 16).padding(.top, 6)
        }
        .background {
            // Catch blank areas without giving controls a parent tap gesture.
            Color.clear.contentShape(Rectangle()).onTapGesture(perform: openPost)
                .accessibilityHidden(true)
        }
    }
}

// The end of the edition gets one slow exhale: a ring expands away from the
// checkmark twice, then rests. Deliberately not a loop — the point is that the
// scrolling has stopped.
struct CaughtUpMark: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var breath = 0

    private struct Exhale { var scale = 0.8; var opacity = 0.0 }

    var body: some View {
        ZStack {
            if !reduceMotion {
                KeyframeAnimator(initialValue: Exhale(), trigger: breath) { exhale in
                    Circle().stroke(.primary.opacity(0.3), lineWidth: 1)
                        .frame(width: 52, height: 52)
                        .scaleEffect(exhale.scale).opacity(exhale.opacity)
                } keyframes: { _ in
                    KeyframeTrack(\.scale) {
                        CubicKeyframe(1.5, duration: 1.4)
                        LinearKeyframe(0.8, duration: 0.5)
                        CubicKeyframe(1.5, duration: 1.4)
                    }
                    KeyframeTrack(\.opacity) {
                        LinearKeyframe(0.85, duration: 0.3)
                        LinearKeyframe(0.0, duration: 1.1)
                        LinearKeyframe(0.0, duration: 0.5)
                        LinearKeyframe(0.85, duration: 0.3)
                        LinearKeyframe(0.0, duration: 1.1)
                    }
                }
            }
            Image(systemName: "checkmark").font(.system(size: 28, weight: .light))
                .foregroundStyle(.primary)
        }
        .frame(width: 78, height: 78)
        .onAppear { breath += 1 }
        .accessibilityHidden(true)
    }
}

struct RoundupCover: View {
    let hook: String
    let symbol: String
    let color: Color
    let accent: Color
    var body: some View {
        VStack(spacing: 18) {
            ZStack {
                Circle().fill(.white.opacity(0.45))
                Circle().stroke(accent.opacity(0.45), lineWidth: 1)
                Image(systemName: symbol).font(.system(size: 22, weight: .ultraLight))
            }.frame(width: 58, height: 58)
            Text(hook)
                .font(.system(size: 26, weight: .medium, design: .serif)).tracking(-0.8)
                .multilineTextAlignment(.center)
                .lineLimit(3).fixedSize(horizontal: false, vertical: true)
        }.foregroundStyle(color)
    }
}
