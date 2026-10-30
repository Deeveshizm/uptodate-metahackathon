import SwiftUI
import UniformTypeIdentifiers
#if os(macOS)
import AppKit
#else
import UIKit
#endif

@main
struct UpToDateApp: App {
    @StateObject private var model = AppModel()
    @AppStorage("appAppearance") private var appearance = "system"
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(model)
                .frame(minWidth: 340, minHeight: 600)
                .preferredColorScheme(appearance == "dark" ? .dark : appearance == "light" ? .light : nil)
        }
    }
}

func copyText(_ text: String) {
    #if os(macOS)
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
    #else
    UIPasteboard.general.string = text
    #endif
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var importing = false
    @State private var selection = 0
    @State private var homeNavigationID = UUID()

    var body: some View {
        Group {
            if model.account == nil {
                AuthView()
            } else {
                nativeTabs
            }
        }
        .tint(.primary)
        .onOpenURL { url in Task { await model.handleAuthURL(url) } }
        .task {
            await model.restore()
            if ProcessInfo.processInfo.arguments.contains("--demo"), model.account == nil {
                await model.start()
            }
            if ProcessInfo.processInfo.arguments.contains("--demo"), model.account != nil, model.feed?.posts.isEmpty != false {
                await model.loadDemo()
                model.notice = nil
            }
        }
        .alert("UpToDate", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
            Button("OK") { model.error = nil }
        } message: { Text(model.error ?? "") }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.item]) { result in
            switch result {
            case .success(let url): Task { await model.importEmail(url) }
            case .failure(let error): model.error = error.localizedDescription
            }
        }
        .modifier(NewsletterConfirmationPresenter())
        .task(id: model.account?.id) {
            guard model.account != nil else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(3)) }
                catch { return }
                await model.pollJobs()
            }
        }
    }

    private var tabSelection: Binding<Int> {
        Binding(get: { min(selection, 3) }, set: { destination in
            if destination == 0 { showHome() }
            else { selection = destination }
        })
    }

    private func showHome() {
        selection = 0
        homeNavigationID = UUID()
    }

    @ViewBuilder private var nativeTabs: some View {
        #if os(iOS)
        NativeTabNavigation(selection: $selection, roots: tabRoots,
                            avatarData: model.account?.avatarRevision == nil ? nil : model.avatarData,
                            onHome: showHome)
            .ignoresSafeArea(.container)
        #else
        TabView(selection: tabSelection) {
            tabRoots[0].tabItem { Label("Home", systemImage: "house") }.tag(0)
            tabRoots[1].tabItem { Label("Saved", systemImage: "bookmark") }.tag(1)
            tabRoots[2].tabItem { Label("Manage", systemImage: "newspaper") }.tag(2)
            tabRoots[3].tabItem { Label("Profile", systemImage: "person.crop.circle") }.tag(3)
        }
        #endif
    }

    private var tabRoots: [AnyView] {
        [
            tabRoot(NavigationStack { FeedView() }.id(homeNavigationID)),
            tabRoot(NavigationStack { LibraryView() }),
            tabRoot(NavigationStack { ManageView(importEmail: { importing = true }) }),
            tabRoot(NavigationStack {
                if selection == 4 { ReadPostsView() }
                else { SourcesView(openBookmarks: { selection = 1 }, openRead: { selection = 4 }) }
            })
        ]
    }

    private func tabRoot<Content: View>(_ content: Content) -> AnyView {
        AnyView(content.environmentObject(model)
            .tint(.primary)
            .environment(\.appScrollHeader, AnyView(scrollHeader)))
    }

    private var scrollHeader: some View {
        VStack(spacing: 0) {
            ZStack {
                Button { showHome() } label: {
                    Text(FeedStyle.wordmark).font(.system(size: 30, weight: .semibold))
                }.buttonStyle(.plain).accessibilityLabel("Home")
                HStack {
                    Spacer()
                    if model.busy { ProgressView().controlSize(.small).frame(width: 34, height: 34) }
                }.allowsHitTesting(false)
            }.frame(maxWidth: .infinity).frame(height: 44)
                .padding(.horizontal, 18).padding(.vertical, 5).foregroundStyle(.primary)
            Divider()
            if model.offline {
                HStack {
                    Image(systemName: "wifi.slash")
                    Text("Cached edition · backend unavailable")
                    Spacer()
                    Button("Retry") { Task { await model.refresh() } }
                }.font(.caption).padding(10).background(.orange.opacity(0.1))
            }
            if let notice = model.notice {
                HStack {
                    Text(notice).font(.caption)
                    Spacer()
                    Button { model.notice = nil } label: { Image(systemName: "xmark") }
                        .accessibilityLabel("Dismiss notice")
                }.padding(10).background(.secondary.opacity(0.08))
            }
        }.frame(maxWidth: FeedStyle.feedWidth).frame(maxWidth: .infinity)
    }
}

struct FeedView: View {
    @EnvironmentObject private var model: AppModel
    @State private var sourceFilter: String?
    @State private var selectedPost: Post?
    @State private var showingPost = false
    private var needsNewsletterPrompt: Bool {
        (model.readFeed?.total ?? 0) == 0 && (model.feed?.posts ?? []).allSatisfy { $0.isDemo }
    }
    private var hasNewsletter: Bool {
        let demoSourceIDs = Set((model.feed?.posts ?? []).filter { $0.isDemo }.map { $0.source.id })
        return model.sources.contains { !demoSourceIDs.contains($0.id) }
    }
    var body: some View {
        AppScrollView {
            VStack(spacing: 0) {
                HStack {
                    Menu {
                        Button("All newsletters") { sourceFilter = nil }
                        ForEach(model.sources.filter { $0.muted == 0 }) { source in
                            Button(source.name) { sourceFilter = source.id }
                        }
                    } label: {
                        HStack(spacing: 5) {
                            Text(sourceFilter == nil ? "Unread posts" : "Filtered posts")
                            Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold))
                        }.font(.system(size: 12, weight: .medium))
                    }
                    Spacer()
                    Text("\(model.feed?.unread ?? 0) unread").font(.system(size: 11))
                }.foregroundStyle(.secondary).padding(.horizontal, 20).padding(.vertical, 14)
                ForEach(model.inboxCandidates.filter { $0.status == "pending" }) { candidate in
                    EmailNewsletterPrompt(candidate: candidate)
                }
                if needsNewsletterPrompt {
                    VStack(spacing: 16) {
                        Image(systemName: "text.book.closed").font(.system(size: 40, weight: .ultraLight))
                        Text(hasNewsletter ? "Your newsletters, turned into posts." : "Subscribe to your first newsletter")
                            .font(.system(size: 22, weight: .semibold)).lineLimit(1).minimumScaleFactor(0.7)
                            .frame(maxWidth: .infinity, alignment: .center).multilineTextAlignment(.center)
                        Text(hasNewsletter ? "Follow signup and confirmation progress in Manage. Posts will appear here as editions are processed." : "Paste a newsletter link. We'll use your private newsletter address for signup and create posts from incoming editions.")
                            .multilineTextAlignment(.center).foregroundStyle(.secondary)
                        NewsletterLinkForm()
                        Text("You can also import an .eml file from Manage.").font(.caption).foregroundStyle(.secondary)
                    }.padding(.horizontal, 24).padding(.vertical, 36)
                }
                ForEach(Array(model.sources.filter { ["manual_action", "needs_agent", "failed", "confirmation_required"].contains($0.subscriptionStatus ?? "") }.prefix(3))) { source in
                    InfoRow("\(source.name): \(source.subscriptionTitle). Open Manage to finish the subscription. Available public posts may already appear below.")
                }
                LazyVStack(spacing: 0) {
                    ForEach(Array((model.feed?.posts ?? []).filter { sourceFilter == nil || $0.source.id == sourceFilter }.enumerated()), id: \.element.id) { index, post in
                        FeedPostCard(post: post, index: index) {
                            selectedPost = post
                            showingPost = true
                        }
                        Divider().padding(.top, 14)
                    }
                }
                if let cursor = model.feed?.nextCursor {
                    OlderPostsControl(cursor: cursor)
                } else if model.feed?.posts.isEmpty == false || !needsNewsletterPrompt {
                    VStack(spacing: 10) {
                        CaughtUpMark()
                        Text(model.feed?.unread == 0 ? "You're all caught up" : "You're at the end of your unread posts").font(.subheadline.weight(.semibold))
                        Text("Posts you mark read are available in Read from the menu.").font(.caption).foregroundStyle(.secondary)
                        Text(model.feed?.date ?? "").font(.caption2).foregroundStyle(.tertiary)
                    }.frame(maxWidth: .infinity).padding(.vertical, 40).padding(.horizontal, 20)
                }
            }.frame(maxWidth: FeedStyle.feedWidth).frame(maxWidth: .infinity)
        }.background(.background)
        #if os(iOS)
        .scrollDismissesKeyboard(.interactively)
        #endif
        .toolbar(.hidden)
        .refreshable { await model.refresh() }
        .navigationDestination(isPresented: $showingPost) {
            if let selectedPost { PostDetail(post: selectedPost) }
        }
    }
}

struct PostRow: View {
    let post: Post
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(post.isDemo ? "SAMPLE EDITION" : "FROM YOUR NEWSLETTERS")
                .font(.system(size: 9, weight: .semibold)).tracking(1.5).foregroundStyle(.secondary)
            Text(post.title).font(.system(size: 19, weight: .semibold)).tracking(-0.4)
                .fixedSize(horizontal: false, vertical: true)
            Text(post.hook).font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(3)
                .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                Text(post.source.name).font(.system(size: 11)).foregroundStyle(.secondary)
                Spacer()
                if post.bookmarked {
                    Image(systemName: "bookmark.fill").font(.system(size: 11)).accessibilityLabel("Saved")
                }
                if post.read {
                    Image(systemName: "checkmark").font(.system(size: 11)).accessibilityLabel("Read")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 18)
        .contentShape(Rectangle())
    }
}

struct LibraryView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        PageScroll {
            PageHeader(title: "Saved posts", trailing: "\(model.bookmarks.count) saved")
            if model.bookmarks.isEmpty {
                EmptyState(symbol: "bookmark", title: "Nothing saved yet.",
                           message: "Bookmark a post from today's edition and it will wait for you here.")
            } else {
                ForEach(model.bookmarks) { post in
                    NavigationLink { PostDetail(post: post) } label: { PostRow(post: post) }
                        .buttonStyle(.plain)
                    RowDivider()
                }
            }
        }.toolbar(.hidden)
    }
}

struct OlderPostsControl: View {
    @EnvironmentObject private var model: AppModel
    let cursor: String
    var read = false
    var body: some View {
        Button {
            Task { await model.loadOlderPosts(read: read) }
        } label: {
            HStack {
                if read ? model.loadingReadHistory : model.loadingOlder { ProgressView() }
                Text("Load older posts")
            }.padding(20)
        }.disabled(read ? model.loadingReadHistory : model.loadingOlder)
            .task(id: cursor) { await model.loadOlderPosts(read: read) }
    }
}

struct ReadPostsView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        PageScroll {
            PageHeader(title: "Read posts", trailing: "\(model.readFeed?.total ?? 0) read")
            if model.readFeed?.posts.isEmpty != false {
                EmptyState(symbol: "checkmark.circle", title: "Nothing marked read yet.",
                           message: "Choose Mark read on a post to move it from Home to this section.")
            } else {
                LazyVStack(spacing: 0) {
                    ForEach(model.readFeed?.posts ?? []) { post in
                        NavigationLink { PostDetail(post: post) } label: { PostRow(post: post) }.buttonStyle(.plain)
                        RowDivider()
                    }
                    if let cursor = model.readFeed?.nextCursor { OlderPostsControl(cursor: cursor, read: true) }
                }
            }
        }.toolbar(.hidden).refreshable { await model.refresh() }
    }
}

struct PostDetail: View {
    @EnvironmentObject private var model: AppModel
    let post: Post
    @State private var share: ShareResult?
    @State private var abstractExpanded = true

    private var current: Post {
        model.feed?.posts.first(where: { $0.id == post.id }) ?? model.readFeed?.posts.first(where: { $0.id == post.id }) ?? model.bookmarks.first(where: { $0.id == post.id }) ?? post
    }

    var body: some View {
        AppScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if current.isDemo { Text("Sample edition").font(.caption).foregroundStyle(.secondary) }
                Text(current.title).font(.title2.bold())
                if !current.hook.isEmpty { Text(current.hook).font(.subheadline).foregroundStyle(.secondary) }
                ForEach(current.tags ?? []) { tag in
                    Label(tag.label, systemImage: "clock").font(.caption).foregroundStyle(.orange)
                    if let detail = tag.detail { Text(detail).font(.caption).foregroundStyle(.secondary) }
                }
                VStack(alignment: .leading, spacing: 6) {
                    if !current.gallery.isEmpty { PostVisualView(post: current) }
                    else if ["needs_image_generator", "failed"].contains(current.visual?.status ?? "") {
                        Button("Retry image") { Task { await model.retryVisual(current) } }.disabled(model.busy || model.offline)
                    }
                    ReadableText(text: current.finding).textSelection(.enabled)
                }
                if let steps = current.analysis?.cookingSteps, !steps.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Instructions").font(.headline)
                        ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                            HStack(alignment: .top, spacing: 12) {
                                Text("\(index + 1).").font(.body.weight(.medium)).foregroundStyle(.secondary)
                                ReadableText(text: step).textSelection(.enabled)
                            }
                        }
                    }.accessibilityIdentifier("post.cookingSteps")
                }
                ForEach(current.analysis?.sections ?? []) { section in
                    Text(section.heading).font(.headline)
                    ReadableText(text: section.text).textSelection(.enabled)
                }
                if let abstract = current.paper?.abstract, !abstract.isEmpty {
                    DisclosureGroup("Abstract", isExpanded: $abstractExpanded) {
                        Text(abstract).textSelection(.enabled).padding(.top, 8)
                    }.accessibilityIdentifier("post.abstract")
                } else if current.isResearchPaper {
                    Text("The abstract is unavailable. Open the paper to read it.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let caveat = current.sourceCaveat { Text(caveat).foregroundStyle(.secondary) }
                VenueFactsView(facts: current.analysis?.venueFacts)
                Text("From \(current.source.name)").font(.caption)
                Divider()
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: FeedStyle.actionSpacing) {
                        Button { Task { await model.markRead(current) } } label: {
                            Image(systemName: current.read ? "checkmark.circle.fill" : "checkmark")
                                .modifier(PostActionPill())
                        }
                        .disabled(current.read)
                        .accessibilityLabel(current.read ? "Marked as read" : "Mark as read")
                        .accessibilityIdentifier("post.markRead")
                        if !current.isEmailSource {
                            Button { Task { share = await model.share(current) } } label: {
                                Image(systemName: "square.and.arrow.up").modifier(PostActionPill())
                            }
                            .accessibilityLabel("Share original source")
                            .accessibilityIdentifier("post.share")
                        }
                        PostContextActionView(post: current)
                        Button { Task { await model.bookmark(current) } } label: {
                            Image(systemName: current.bookmarked ? "bookmark.fill" : "bookmark")
                                .modifier(BookmarkButtonSizing())
                        }.accessibilityLabel(current.bookmarked ? "Remove bookmark" : "Bookmark post")
                            .disabled(model.busy || model.offline)
                    }.buttonStyle(.plain).disabled(model.busy || model.offline)
                }
            }.padding(.horizontal, FeedStyle.gutter).padding(.vertical, 24)
                .frame(maxWidth: FeedStyle.feedWidth, alignment: .leading).frame(maxWidth: .infinity)
        }
        .background(.background)
        .navigationTitle("Post")
        .sheet(item: $share) { result in
            VStack(alignment: .leading, spacing: 18) {
                Text(result.mode == "public-preview" ? "Share preview" : "Share original source").font(.title2.bold())
                if result.localOnly { Text("This prototype link opens only on this Mac or its iPhone simulator. Public hosting comes later.").foregroundStyle(.secondary) }
                else { Text("This post is private. Only its original source URL will be shared.") }
                Text(result.url).textSelection(.enabled)
                if let url = URL(string: result.url) {
                    ShareLink(item: url)
                    Link("Open link", destination: url)
                }
                Button("Done") { share = nil }
            }.padding(30).frame(minWidth: 320, minHeight: 280)
        }
    }
}

struct ManageView: View {
    @EnvironmentObject private var model: AppModel
    @State private var addingLink = false
    @State private var creatingNewsletter = false
    @State private var pendingUnsubscribe: NewsletterSource?
    @State private var confirmingUnsubscribe = false
    @State private var pendingDelete: NewsletterSource?
    @State private var confirmingDelete = false
    @State private var handoff: NewsletterUnsubscribeHandoff?
    @State private var options: NewsletterSource?
    var importEmail: () -> Void

    private var subscribed: [NewsletterSource] { model.sources.filter { !$0.isUnsubscribed } }
    private var newsletters: [NewsletterSource] { subscribed.filter { !$0.isCustom } }
    private var custom: [NewsletterSource] { subscribed.filter { $0.isCustom } }
    private var unsubscribed: [NewsletterSource] { model.sources.filter { $0.isUnsubscribed } }

    var body: some View {
        AppSourceList {
            VStack(spacing: 0) {
                PageHeader(title: "Your newsletters", trailing: "\(subscribed.count) subscribed")
                HStack(alignment: .top, spacing: 12) {
                    topAction("Add newsletter", symbol: "plus") { addingLink = true }
                    topAction("Import .eml", symbol: "square.and.arrow.down", action: importEmail)
                    topAction("Create newsletter", symbol: "sparkles") { creatingNewsletter = true }
                }.frame(maxWidth: .infinity).padding(.horizontal, FeedStyle.gutter).padding(.vertical, 18)
                ForEach(model.newsletterConfirmations) { confirmation in
                    NewsletterConfirmationPrompt(confirmation: confirmation)
                }
                ForEach(model.inboxCandidates.filter { $0.status == "pending" }) { candidate in
                    EmailNewsletterPrompt(candidate: candidate)
                }
            }.listRowInsets(EdgeInsets()).listRowSeparator(.hidden).listRowBackground(Color.clear)

            Section {
                subsection("Newsletters")
                if newsletters.isEmpty { emptyRow("No newsletter subscriptions yet.") }
                ForEach(newsletters) { source in subscriptionRow(source) }
                subsection("Custom")
                if custom.isEmpty { emptyRow("Muse, Claude, and personal newsletters appear here.") }
                ForEach(custom) { source in subscriptionRow(source) }
            } header: { Text("Subscribed").font(.headline) }

            Section {
                if unsubscribed.isEmpty { emptyRow("No unsubscribed newsletters.") }
                ForEach(unsubscribed) { source in
                    HStack {
                        Text(source.displayName).font(.body)
                        Spacer()
                        Button { Task { await model.retrySubscription(source) } } label: {
                            Text("Resubscribe").modifier(CapsuleButtonLabelSizing(fontSize: 12, weight: .medium))
                        }.buttonStyle(.bordered).buttonBorderShape(.capsule)
                            .disabled(model.busy || model.offline || model.account?.aliasStatus != "active")
                            .accessibilityIdentifier("newsletter.resubscribe")
                    }.listRowBackground(Color.clear)
                        .accessibilityActions {
                            Button("Delete") { askToDelete(source) }
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                            Button { askToDelete(source) } label: { Label("Delete", systemImage: "trash") }
                                .tint(.red).disabled(model.busy || model.offline)
                                .accessibilityIdentifier("newsletter.deleteUnsubscribed")
                        }
                }
            } header: { Text("Unsubscribed").font(.headline) }
              footer: { Text("Available to resubscribe for 30 days. Existing posts are kept.").font(.caption) }
        }
        .toolbar(.hidden).refreshable { await model.refresh() }
        .sheet(isPresented: $addingLink) { SourceLinkView() }
        .sheet(isPresented: $creatingNewsletter) { NewsletterComposerView() }
        .sheet(item: $handoff) { value in PublisherUnsubscribeSheet(source: value.source, url: value.url) }
        .sheet(item: $options) { source in
            NavigationStack {
                ScrollView { SourceEditor(source: model.sources.first { $0.id == source.id } ?? source) }
                    .navigationTitle(source.displayName)
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { options = nil } } }
            }
        }
        .confirmationDialog(pendingUnsubscribe?.isCustom == true ? "Stop processing \(pendingUnsubscribe?.displayName ?? "these emails")?" : "Unsubscribe from \(pendingUnsubscribe?.displayName ?? "this newsletter")?",
                            isPresented: $confirmingUnsubscribe, titleVisibility: .visible) {
            Button(pendingUnsubscribe?.isCustom == true ? "Stop processing" : "Unsubscribe", role: .destructive) {
                guard let source = pendingUnsubscribe else { return }
                Task {
                    if let url = await model.unsubscribe(source) { handoff = NewsletterUnsubscribeHandoff(source: source, url: url) }
                }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text(pendingUnsubscribe?.isCustom == true
                 ? "Stop creating posts from these emails. Disable the sending task in Muse, Claude, or the sending app to stop delivery. Existing posts are kept."
                 : "Stop new posts and unsubscribe at the publisher. A confirmation page will open if needed. Existing posts are kept.")
        }
        .confirmationDialog("Delete \(pendingDelete?.displayName ?? "this newsletter")?", isPresented: $confirmingDelete, titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                guard let source = pendingDelete else { return }
                Task { await model.removeSource(source) }
            }
            Button("Cancel", role: .cancel) { }
        } message: { Text("Remove it from the Unsubscribed list now. Existing posts are kept, and future emails stay ignored.") }
    }

    private func topAction(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Image(systemName: symbol).font(.system(size: 23, weight: .medium))
                    .frame(width: 60, height: 60)
                    .background(.primary.opacity(0.07), in: Circle())
                    .overlay(Circle().stroke(.primary.opacity(0.12), lineWidth: 0.5))
                Text(title).font(.system(size: 12, weight: .medium)).multilineTextAlignment(.center).lineLimit(2)
            }.frame(maxWidth: .infinity).foregroundStyle(.primary)
        }.buttonStyle(.plain).disabled(model.busy || model.offline)
    }
    private func subsection(_ title: String) -> some View {
        Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            .listRowSeparator(.hidden).listRowBackground(Color.clear)
    }
    private func emptyRow(_ text: String) -> some View {
        Text(text).font(.caption).foregroundStyle(.secondary)
            .listRowSeparator(.hidden).listRowBackground(Color.clear)
    }
    private func askToUnsubscribe(_ source: NewsletterSource) {
        pendingUnsubscribe = source
        confirmingUnsubscribe = true
    }
    private func askToDelete(_ source: NewsletterSource) {
        pendingDelete = source
        confirmingDelete = true
    }
    private func subscriptionRow(_ source: NewsletterSource) -> some View {
        Toggle(source.displayName, isOn: Binding(get: { source.muted == 0 }, set: { shown in
            Task { await model.updateSource(source, muted: !shown) }
        }))
        .toggleStyle(.switch).tint(.green).disabled(model.busy || model.offline)
        .accessibilityHint(source.canUnsubscribe ? (source.isCustom ? "Show or hide this newsletter in Home. Swipe left to stop processing." : "Show or hide this newsletter in Home. Swipe left to unsubscribe.") : "Show or hide this newsletter in Home.")
        .accessibilityActions {
            if source.canUnsubscribe { Button(source.isCustom ? "Stop processing" : "Unsubscribe") { askToUnsubscribe(source) } }
            else if source.canRemoveSetup { Button("Delete unfinished signup") { Task { await model.removePendingSetup(source) } } }
        }
        .listRowBackground(Color.clear)
        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
            if source.canUnsubscribe {
                Button { askToUnsubscribe(source) } label: { Label(source.isCustom ? "Stop processing" : "Unsubscribe", systemImage: "minus.circle") }
                    .tint(.red).disabled(model.busy || model.offline)
            } else if source.canRemoveSetup {
                Button(role: .destructive) { Task { await model.removePendingSetup(source) } } label: {
                    Label("Delete", systemImage: "trash")
                }.tint(.red).disabled(model.busy || model.offline).accessibilityIdentifier("newsletter.removeSetup")
            }
        }
        .contextMenu { Button("Subscription options") { options = source } }
    }
}

struct NewsletterUnsubscribeHandoff: Identifiable {
    let source: NewsletterSource
    let url: URL
    var id: String { source.id }
}

struct SourceLinkView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            PageScroll(showsHeader: false) {
                SectionHeader("Newsletter link")
                NewsletterLinkForm(onAdded: { dismiss() })
                    .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 16)
                InfoRow("Signup uses your account's newsletter address. Supported forms are submitted automatically; confirmation, CAPTCHA, or unsupported forms may need your help. Posts are created automatically from available content.")
            }
            .navigationTitle("Add newsletter")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }.frame(minWidth: 340, minHeight: 450)
    }
}
struct SourcesView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.colorScheme) private var colorScheme
    @AppStorage("appAppearance") private var appearance = "system"
    let openBookmarks: () -> Void
    let openRead: () -> Void
    var body: some View {
        PageScroll {
            HStack {
                Text("Your account").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                Spacer()
                NavigationLink { ProfileSettingsView(startEditing: true) } label: {
                    Text("Edit").font(.system(size: 13 * FeedStyle.buttonScale, weight: .medium))
                        .padding(.horizontal, 14 * FeedStyle.buttonScale).padding(.vertical, 9 * FeedStyle.buttonScale)
                        .background(.primary.opacity(0.06), in: Capsule())
                }.buttonStyle(.plain)
            }.padding(.horizontal, FeedStyle.gutter).padding(.vertical, 14)
            VStack(spacing: 12) {
                ProfilePhotoPicker()
                Text(model.account?.displayName?.isEmpty == false ? model.account!.displayName! : "Your profile")
                    .font(.system(size: 25, weight: .semibold)).tracking(-0.5)
                AccountEmailRow()
            }
            .frame(maxWidth: .infinity).padding(.horizontal, FeedStyle.gutter)
            .padding(.top, 16).padding(.bottom, 12)
            SectionHeader("Your library")
            Button(action: openBookmarks) {
                AccountOptionRow(title: "Bookmarks", symbol: "bookmark")
            }.buttonStyle(.plain)
            RowDivider()
            Button(action: openRead) {
                AccountOptionRow(title: "Read posts", symbol: "checkmark.circle")
            }.buttonStyle(.plain)
            RowDivider()
            SectionHeader("Account")
            NavigationLink { ProfileSettingsView() } label: {
                AccountOptionRow(title: "Profile", symbol: "person.crop.circle")
            }.buttonStyle(.plain)
            RowDivider()
            NavigationLink { ManageAccountView() } label: {
                AccountOptionRow(title: "Manage account", symbol: "lock.shield")
            }.buttonStyle(.plain)
            RowDivider()
            SectionHeader("Appearance")
            Toggle(isOn: Binding(get: {
                appearance == "system" ? colorScheme == .dark : appearance == "dark"
            }, set: { appearance = $0 ? "dark" : "light" })) {
                Label("Dark mode", systemImage: "moon")
                    .font(.system(size: 15))
            }
            .toggleStyle(.switch).tint(.green)
            .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 16)
            .accessibilityIdentifier("profile.darkMode")
            if appearance != "system" {
                Button("Use system appearance") { appearance = "system" }
                    .font(.system(size: 13)).foregroundStyle(.secondary)
                    .buttonStyle(.plain).frame(minHeight: 44)
                    .padding(.horizontal, FeedStyle.gutter)
                    .accessibilityIdentifier("profile.systemAppearance")
            }
            RowDivider()
            Button { Task { await model.signOut() } } label: {
                Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                    .font(.system(size: 15 * FeedStyle.buttonScale, weight: .medium))
                    .frame(maxWidth: .infinity).padding(.vertical, 15 * FeedStyle.buttonScale)
                    .background(.primary.opacity(0.06), in: Capsule())
            }
            .buttonStyle(.plain).disabled(model.busy)
            .padding(.horizontal, FeedStyle.gutter).padding(.top, 42).padding(.bottom, 28)
        }.toolbar(.hidden)
    }
}

struct SourceEditor: View {
    @EnvironmentObject private var model: AppModel
    let source: NewsletterSource
    @State private var signup = ""
    @State private var confirmingUnsubscribe = false
    @State private var finishingSignup = false
    @Environment(\.openURL) private var openURL
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(source.displayName).font(.system(size: 17, weight: .semibold)).tracking(-0.3)
            if let status = source.subscriptionStatus, status != "not_requested" {
                Text(source.subscriptionTitle).font(.system(size: 11)).foregroundStyle(.secondary)
                if let detail = source.subscriptionDetail { Text(detail).font(.system(size: 11)).foregroundStyle(.secondary) }
            }
            if source.subscriptionStatus != "unsubscribed" {
                Toggle("Show in daily feed", isOn: Binding(get: { source.muted == 0 }, set: { shown in
                    Task { await model.updateSource(source, muted: !shown) }
                })).font(.system(size: 15)).tint(.green).disabled(model.busy || model.offline)
            }
            if !source.isCustom {
            VStack(alignment: .leading, spacing: 7) {
                TextField("Publication signup URL", text: $signup)
                    .textFieldStyle(.plain).font(.system(size: 14))
                    .autocorrectionDisabled()
                Divider()
            }
            Button("Save signup URL") { Task { await model.updateSource(source, signup: signup) } }
                .font(.system(size: 14, weight: .medium)).buttonStyle(.plain)
                .disabled(model.busy || model.offline || signup == source.signupUrl)
                .opacity(signup == source.signupUrl ? 0.4 : 1)
            Text("Hiding a source does not unsubscribe at the publisher.")
                .font(.system(size: 12)).foregroundStyle(.secondary).lineSpacing(3)
            } else {
                Text("Posts come from emails you chose to connect. Hiding them in the feed does not stop new email processing.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if ["manual_action", "failed", "needs_agent", "needs_inbox", "confirmation_required", "unsubscribed", "unsubscribe_pending"].contains(source.subscriptionStatus ?? "") {
                if !source.isCustom, !["unsubscribed", "unsubscribe_pending"].contains(source.subscriptionStatus ?? ""), model.account?.aliasStatus == "active" {
                    Button {
                        if let url = URL(string: source.signupUrl), ["https", "http"].contains(url.scheme ?? "") {
                            Task {
                                if await model.beginNewsletterSetup(source) {
                                    copyText(model.account?.alias ?? "")
                                    finishingSignup = true
                                }
                            }
                        }
                    } label: {
                        Text("Finish setup").modifier(CapsuleButtonLabelSizing(fontSize: 14, weight: .medium))
                    }
                        .buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
                        .tint(.blue)
                        .disabled(model.busy || model.offline)
                        .accessibilityIdentifier("newsletter.finishSignup")
                }
                if source.subscriptionStatus != "confirmation_required" {
                Button {
                    Task { await model.retrySubscription(source) }
                } label: {
                    Text(source.isCustom ? "Resume email processing" : ["unsubscribed", "unsubscribe_pending"].contains(source.subscriptionStatus ?? "") ? "Subscribe again" : "Retry")
                        .modifier(CapsuleButtonLabelSizing(fontSize: 14, weight: .medium))
                }
                    .buttonStyle(.bordered).buttonBorderShape(.capsule)
                    .disabled(model.busy || model.offline || model.account?.aliasStatus != "active")
                }
            }
            if source.canUnsubscribe {
            Button(role: .destructive) {
                confirmingUnsubscribe = true
            } label: {
                Text(source.isCustom ? "Stop processing" : "Unsubscribe from newsletter")
                    .modifier(CapsuleButtonLabelSizing(fontSize: 14, weight: .medium))
            }
            .buttonStyle(.bordered).buttonBorderShape(.capsule).tint(.red)
            .accessibilityIdentifier("newsletter.stopProcessing")
            .disabled(model.busy || model.offline || source.subscriptionStatus == "unsubscribed")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 18)
        .onAppear { signup = source.signupUrl }
        .sheet(isPresented: $finishingSignup) {
            if let url = URL(string: source.signupUrl) { PublisherSignupSheet(url: url, alias: model.account?.alias ?? "", sourceID: source.id) }
        }
        .confirmationDialog(source.isCustom ? "Stop processing \(source.displayName)?" : "Unsubscribe from \(source.displayName)?", isPresented: $confirmingUnsubscribe, titleVisibility: .visible) {
            Button(source.isCustom ? "Stop processing" : "Unsubscribe", role: .destructive) {
                Task {
                    if let url = await model.unsubscribe(source) { openURL(url) }
                }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text(source.isCustom ? "Stop creating posts from these emails. To stop delivery, disable the scheduled email in the sending app. Existing posts stay available." : "Stop creating new posts and request unsubscribe from the publisher. Existing posts stay available. Some publishers require an extra confirmation.")
        }
    }
}
