import SwiftUI

struct WelcomeGuidePresenter: ViewModifier {
    @EnvironmentObject private var model: AppModel
    @State private var presented: GuideAccount?

    func body(content: Content) -> some View {
        presentation(content)
            .task(id: model.account?.id) {
                presented = nil
                guard let owner = model.account?.id else { return }
                while model.busy || model.offline || model.authConfiguration == nil {
                    do { try await Task.sleep(for: .milliseconds(250)) }
                    catch { return }
                    guard model.account?.id == owner else { return }
                }
                await model.prepareGuides()
                guard !Task.isCancelled, model.account?.id == owner,
                      !model.completedGuides.contains(.welcome) else { return }
                presented = GuideAccount(id: owner)
            }
    }

    @ViewBuilder private func presentation(_ content: Content) -> some View {
        #if os(iOS)
        content.fullScreenCover(item: $presented) { account in welcome(for: account) }
        #else
        content.sheet(item: $presented) { account in welcome(for: account) }
        #endif
    }

    private func welcome(for account: GuideAccount) -> some View {
        WelcomeGuideView {
            if model.account?.id == account.id { model.finishGuide(.welcome) }
            presented = nil
        }.interactiveDismissDisabled()
    }
}

private struct GuideAccount: Identifiable { let id: String }

private struct WelcomePage: Identifiable, Sendable {
    let id: Int
    let symbol: String
    let title: String
    let detail: String
    var rows: [(symbol: String, title: String, detail: String)] = []

    static let pages = [
        WelcomePage(id: 0, symbol: "newspaper", title: "Welcome to uptodate",
                    detail: "Staying up to date is now clutter free. Turn the newsletters you care about into a feed you can catch up on in minutes."),
        WelcomePage(id: 1, symbol: "envelope", title: "Your own newsletter address",
                    detail: "Your account gets a separate address for newsletters. Use it for subscriptions so those editions arrive in UpToDate instead of filling your personal inbox.",
                    rows: [("doc.on.doc", "Find it in Profile", "Tap Copy alias whenever you need the address.")]),
        WelcomePage(id: 2, symbol: "rectangle.stack", title: "Read more. Do more.",
                    detail: "Tap a post to read the full story. Useful actions appear when the source includes the details.",
                    rows: [("sparkles", "Ask Biggie", "Ask questions about the post."),
                           ("mappin.and.ellipse", "Explore a place", "Open its location, website, or phone number."),
                           ("checklist", "Cook something new", "Add recipe ingredients to your grocery list.")]),
        WelcomePage(id: 3, symbol: "square.grid.2x2", title: "Find your way around",
                    detail: "The tabs at the bottom keep everything close.",
                    rows: [("house", "Home", "Your newsletter posts, ready to read."),
                           ("bookmark", "Saved", "Posts you have bookmarked."),
                           ("newspaper", "Manage", "Add a newsletter link, import an email, or manage subscriptions."),
                           ("person.crop.circle", "Profile", "Your alias, account settings, and read history.")]),
        WelcomePage(id: 4, symbol: "sparkles", title: "Make a newsletter with Muse",
                    detail: "Open Manage → Create newsletter. Pick a topic, the kinds of posts you want, and a schedule. We prepare a prompt with your newsletter address already included.",
                    rows: [("arrow.up.forward.app", "Finish the setup in Muse", "Share or paste the prompt there and create the recurring task."),
                           ("tray", "Bring it into your feed", "When its first email arrives, choose to add it as a custom newsletter.")]),
    ]
}

struct WelcomeGuideView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var page = 0
    let onFinish: () -> Void

    var body: some View {
        VStack(spacing: 18) {
            ZStack {
                Text(FeedStyle.wordmark).font(.system(size: 30, weight: .semibold))
                HStack {
                Spacer()
                Button("Skip", action: onFinish)
                    .font(.subheadline).buttonStyle(.bordered).buttonBorderShape(.capsule)
                    .accessibilityIdentifier("welcome.skip")
                }
            }.frame(height: 44).padding(.horizontal, 24).padding(.top, 18)

            #if os(iOS)
            TabView(selection: $page) {
                ForEach(WelcomePage.pages) { value in card(value).tag(value.id) }
            }.tabViewStyle(.page(indexDisplayMode: .never))
            #else
            card(WelcomePage.pages[page]).frame(maxHeight: .infinity)
                .gesture(DragGesture().onEnded { value in
                    if abs(value.translation.width) > 60 { move(value.translation.width < 0 ? 1 : -1) }
                })
            #endif

            HStack(spacing: 6) {
                ForEach(WelcomePage.pages) { value in
                    Button { setPage(value.id) } label: {
                        Circle().fill(value.id == page ? Color.primary : Color.primary.opacity(0.2))
                            .frame(width: 5, height: 5).padding(.vertical, 10).padding(.horizontal, 3)
                    }.buttonStyle(.plain)
                        .accessibilityLabel("Page \(value.id + 1) of \(WelcomePage.pages.count)")
                        .accessibilityAddTraits(value.id == page ? .isSelected : [])
                }
            }
            HStack(spacing: 14) {
                if page > 0 {
                    Button { move(-1) } label: {
                        Image(systemName: "chevron.left").font(.body.weight(.semibold)).frame(width: 44, height: 44)
                    }.buttonStyle(.bordered).buttonBorderShape(.circle).accessibilityLabel("Previous page")
                }
                Button {
                    if page == WelcomePage.pages.count - 1 { onFinish() }
                    else { move(1) }
                } label: {
                    Text(page == WelcomePage.pages.count - 1 ? "Get started" : "Next")
                        .modifier(CapsuleButtonLabelSizing(fontSize: 16, weight: .semibold))
                        .frame(maxWidth: .infinity, minHeight: 40)
                }.buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
                    .accessibilityIdentifier("welcome.next")
            }.padding(.horizontal, 24).padding(.bottom, 24)
        }.frame(maxWidth: FeedStyle.feedWidth).frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(.background).foregroundStyle(.primary)
            .frame(minWidth: 340, minHeight: 600)
    }

    private func card(_ value: WelcomePage) -> some View {
        ScrollView {
            VStack(spacing: 22) {
                Image(systemName: value.symbol).font(.system(size: 34, weight: .medium)).foregroundStyle(.blue)
                    .frame(width: 88, height: 88).background(.blue.opacity(0.1), in: Circle())
                    .accessibilityHidden(true)
                Text(value.title).font(.system(size: 27, weight: .semibold)).multilineTextAlignment(.center)
                Text(value.detail).font(.body).lineSpacing(3).foregroundStyle(.secondary).multilineTextAlignment(.center)
                if !value.rows.isEmpty {
                    VStack(alignment: .leading, spacing: 18) {
                        ForEach(Array(value.rows.enumerated()), id: \.offset) { _, row in
                            HStack(alignment: .top, spacing: 13) {
                                Image(systemName: row.symbol).font(.system(size: 18)).frame(width: 24, height: 24).foregroundStyle(.blue)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(row.title).font(.subheadline.weight(.semibold))
                                    Text(row.detail).font(.subheadline).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }.padding(24).frame(maxWidth: .infinity)
                .background(.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 24))
                .padding(.horizontal, 24).padding(.vertical, 12)
        }.accessibilityIdentifier("welcome.page.\(value.id)")
    }

    private func setPage(_ destination: Int) {
        withAnimation(reduceMotion ? nil : .smooth(duration: 0.25)) { page = destination }
    }
    private func move(_ delta: Int) { setPage(min(max(page + delta, 0), WelcomePage.pages.count - 1)) }
}
