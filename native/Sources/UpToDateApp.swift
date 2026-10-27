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
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(model)
                .frame(minWidth: 340, minHeight: 600)
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

    var body: some View {
        Group {
            if model.account == nil {
                AuthView()
            } else {
                VStack(spacing: 0) {
                    HStack(spacing: 18) {
                        Menu {
                            // Mirrors the bottom navigation bar, in the same order.
                            Section {
                                Button("Home", systemImage: "house") { selection = 0 }
                                Button("Saved", systemImage: "bookmark") { selection = 1 }
                                Button("Manage", systemImage: "newspaper") { selection = 2 }
                            }
                            Section {
                                Button("Profile", systemImage: "person.crop.circle") { selection = 3 }
                            }
                        } label: { Image(systemName: "line.3.horizontal").font(.system(size: 25, weight: .regular)).frame(width: 36, height: 36) }
                            .accessibilityLabel("Open menu")
                        Spacer()
                        Button { selection = 0 } label: {
                            Text(FeedStyle.wordmark).font(.system(size: 25, weight: .semibold, design: .serif).italic())
                        }.buttonStyle(.plain).accessibilityLabel("Home")
                        Spacer()
                        if model.busy { ProgressView().controlSize(.small) }
                        Button { selection = 3 } label: {
                            SourceAvatar(name: "Your profile", size: 34, symbol: "person")
                                .overlay(alignment: .topTrailing) {
                                    if !model.review.isEmpty { Circle().fill(.pink).frame(width: 7, height: 7).offset(x: 4, y: -3) }
                                }
                        }.accessibilityLabel("Open your profile")
                    }.padding(.horizontal, 18).padding(.vertical, 10).foregroundStyle(.primary)
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
                    Group {
                        switch selection {
                        case 1: NavigationStack { LibraryView() }
                        case 2: NavigationStack { ManageView(importEmail: { importing = true }) }
                        case 3: NavigationStack { SourcesView() }
                        default: NavigationStack { FeedView() }
                        }
                    }.frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    VStack(spacing: 0) {
                        Divider()
                        HStack {
                            tab("house", selected: "house.fill", label: "Home", index: 0)
                            Spacer()
                            tab("bookmark", selected: "bookmark.fill", label: "Saved", index: 1)
                            Spacer()
                            tab("newspaper", selected: "newspaper.fill", label: "Manage", index: 2)
                            Spacer()
                            tab("person.crop.circle", selected: "person.crop.circle.fill", label: "Profile", index: 3)
                        }.padding(.horizontal, 34).padding(.top, 12).padding(.bottom, 8)
                    }.background(.background)
                }
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
        .alert("Prototype", isPresented: Binding(get: { model.error != nil }, set: { if !$0 { model.error = nil } })) {
            Button("OK") { model.error = nil }
        } message: { Text(model.error ?? "") }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.item]) { result in
            switch result {
            case .success(let url): Task { await model.importEmail(url) }
            case .failure(let error): model.error = error.localizedDescription
            }
        }
        .task(id: model.account?.id) {
            guard model.account != nil else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(3)) }
                catch { return }
                await model.pollJobs()
            }
        }
    }

    private func tab(_ symbol: String, selected: String, label: String, index: Int) -> some View {
        Button { selection = index } label: {
            VStack(spacing: 5) {
                Image(systemName: selection == index ? selected : symbol).font(.system(size: 23, weight: .regular))
                Text(label).font(.system(size: 9, weight: selection == index ? .semibold : .regular))
            }.frame(width: 48, height: 40).foregroundStyle(.primary)
        }.buttonStyle(.plain).accessibilityLabel(label)
            .accessibilityAddTraits(selection == index ? .isSelected : [])
    }
}

struct FeedView: View {
    @EnvironmentObject private var model: AppModel
    @State private var sourceFilter: String?
    private var needsNewsletterPrompt: Bool {
        (model.feed?.posts ?? []).allSatisfy { $0.isDemo }
    }
    private var hasNewsletter: Bool {
        let demoSourceIDs = Set((model.feed?.posts ?? []).filter { $0.isDemo }.map { $0.source.id })
        return model.sources.contains { !demoSourceIDs.contains($0.id) }
    }
    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                HStack {
                    Menu {
                        Button("All newsletters") { sourceFilter = nil }
                        ForEach(model.sources.filter { $0.muted == 0 }) { source in
                            Button(source.name) { sourceFilter = source.id }
                        }
                    } label: {
                        HStack(spacing: 5) {
                            Text(sourceFilter == nil ? "Today's edition" : "Filtered edition")
                            Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold))
                        }.font(.system(size: 12, weight: .medium))
                    }
                    Spacer()
                    Text("\(model.feed?.unread ?? 0) unread").font(.system(size: 11))
                }.foregroundStyle(.secondary).padding(.horizontal, 20).padding(.vertical, 14)
                if needsNewsletterPrompt {
                    VStack(spacing: 16) {
                        Image(systemName: "text.book.closed").font(.system(size: 40, weight: .ultraLight))
                        Text(hasNewsletter ? "Your newsletters, turned into posts." : "Subscribe to your first newsletter.").font(.title2.bold())
                        Text(hasNewsletter ? "Follow signup and confirmation progress in Manage. Posts will appear here as editions are processed." : "Paste a newsletter link. We'll use your private newsletter address for signup and create posts from incoming editions.")
                            .multilineTextAlignment(.center).foregroundStyle(.secondary)
                        NewsletterLinkForm()
                        Text("You can also import an .eml file from Manage.").font(.caption).foregroundStyle(.secondary)
                    }.padding(40)
                }
                ForEach(Array(model.sources.filter { ["manual_action", "needs_agent", "failed", "confirmation_required"].contains($0.subscriptionStatus ?? "") }.prefix(3))) { source in
                    InfoRow("\(source.name): \(source.subscriptionTitle). Open Manage to finish the subscription. Available public posts may already appear below.")
                }
                LazyVStack(spacing: 0) {
                    ForEach(Array((model.feed?.posts ?? []).filter { sourceFilter == nil || $0.source.id == sourceFilter }.enumerated()), id: \.element.id) { index, post in
                        FeedPostCard(post: post, index: index)
                        Divider().padding(.top, 14)
                    }
                }
                if model.feed?.posts.isEmpty == false {
                    VStack(spacing: 10) {
                        CaughtUpMark()
                        Text(model.feed?.unread == 0 ? "You're all caught up" : "You're at the end of today's edition").font(.subheadline.weight(.semibold))
                        Text("A little more informed. A little less scrolling.").font(.caption).foregroundStyle(.secondary)
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

struct PostDetail: View {
    @EnvironmentObject private var model: AppModel
    let post: Post
    @State private var share: ShareResult?

    private var current: Post {
        model.feed?.posts.first(where: { $0.id == post.id }) ?? model.bookmarks.first(where: { $0.id == post.id }) ?? post
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text(current.isDemo ? "SAMPLE EDITION · SUMMARIZED FROM A NEWSLETTER" : current.verification == "machine_checked" ? "MACHINE-CHECKED SOURCE SUMMARY" : "IMPORTED NEWSLETTER EXCERPT").font(.caption).foregroundStyle(.secondary)
                Text(current.title).font(.title.bold())
                Text(current.hook).font(.title3)
                ForEach(current.tags ?? []) { tag in
                    Label(tag.label, systemImage: "clock").font(.caption).foregroundStyle(.orange)
                    if let detail = tag.detail { Text(detail).font(.caption).foregroundStyle(.secondary) }
                }
                if current.visual?.assetId != nil { PostVisualView(post: current) }
                else if current.visual?.status == "needs_image_generator" {
                    Button("Retry source visual") { Task { await model.retryVisual(current) } }.disabled(model.busy || model.offline)
                }
                Text(current.finding).textSelection(.enabled)
                if let problem = current.analysis?.problem { Text("Problem").font(.headline); Text(problem) }
                if let approach = current.analysis?.approach { Text("Approach").font(.headline); Text(approach) }
                if let impact = current.analysis?.impact { Text("Why it matters").font(.headline); Text(impact) }
                Text("Limitations").font(.headline)
                Text(current.limitations).foregroundStyle(.secondary)
                Text("From \(current.source.name)").font(.caption)
                if let url = URL(string: current.originalUrl) {
                    Link("Open original source", destination: url)
                }
                Divider()
                HStack {
                    Button(current.bookmarked ? "Unsave" : "Bookmark") { Task { await model.bookmark(current) } }
                    Button(current.read ? "Read ✓" : "Mark read") { Task { await model.markRead(current) } }.disabled(current.read)
                    Button("Share") { Task { share = await model.share(current) } }
                }.buttonStyle(.bordered).disabled(model.busy || model.offline)
                DisclosureGroup("Evidence and provenance") {
                    ForEach(Array(current.evidence.enumerated()), id: \.offset) { _, evidence in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(evidence.source).font(.caption.bold())
                            Text(evidence.quote).textSelection(.enabled)
                        }.padding(.vertical, 8)
                    }
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
    var importEmail: () -> Void

    var body: some View {
        PageScroll {
            PageHeader(title: "Your newsletters", trailing: "\(model.sources.count) sources")
            InfoRow("Posts appear automatically in your private feed. Pending work is labeled on the post; processing issues appear here.")
            ActionRow(title: "Add newsletter", disabled: model.busy || model.offline) { addingLink = true }
            RowDivider()
            ActionRow(title: "Import newsletter .eml", disabled: model.busy || model.offline) { importEmail() }
            SectionHeader("Newsletters")
            if model.sources.isEmpty {
                InfoRow("Add a newsletter link or import an email to get started.")
            } else {
                ForEach(model.sources) { source in
                    SourceEditor(source: source)
                    RowDivider()
                }
            }
            SectionHeader("Processing jobs")
            if model.jobs.isEmpty { InfoRow("No jobs queued.") }
            ForEach(model.jobs) { job in
                VStack(alignment: .leading, spacing: 6) {
                    Text(job.subject).font(.system(size: 15))
                    Text((job.stage?.isEmpty == false ? job.stage! : job.status).replacingOccurrences(of: "_", with: " "))
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                    if let error = job.error { Text(error).font(.system(size: 11)).foregroundStyle(.red) }
                    ForEach(Array((job.result?.items ?? []).enumerated()), id: \.offset) { _, item in
                        if let error = item.error { Text(error).font(.system(size: 11)).foregroundStyle(.red) }
                    }
                    if job.status == "failed", job.kind != "email" {
                        Button("Retry") { Task { await model.retryJob(job) } }.disabled(model.busy || model.offline)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 14)
                RowDivider()
            }
        }
        .toolbar(.hidden)
        .refreshable { await model.refresh() }
        .sheet(isPresented: $addingLink) { SourceLinkView() }
    }
}

struct SourceLinkView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            PageScroll {
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
    @State private var deleting = false
    @State private var editingProfile = false
    private var alias: String { model.account?.alias ?? "" }

    var body: some View {
        PageScroll {
            PageHeader(title: "Your account")
            HStack(spacing: 20) {
                SourceAvatar(name: "Your profile", size: 66, symbol: "person")
                VStack(alignment: .leading, spacing: 10) {
                    Text(model.account?.displayName?.isEmpty == false ? model.account!.displayName! : "Your reading space")
                        .font(.system(size: 21, weight: .semibold)).tracking(-0.4)
                    HStack(spacing: 22) {
                        profileStat(model.sources.count, "sources")
                        profileStat(model.bookmarks.count, "saved")
                        profileStat(model.feed?.total ?? 0, "today")
                    }
                }
                Spacer()
            }.padding(.horizontal, FeedStyle.gutter).padding(.bottom, 16)
            InfoRow("Follow what you're curious about.")
            if let email = model.account?.email, !email.isEmpty { InfoRow(email) }
            if model.authConfiguration?.mode == "supabase" {
                ActionRow(title: "Edit profile", disabled: model.busy) { editingProfile = true }
            }

            SectionHeader("Your newsletter address")
            if alias.isEmpty {
                InfoRow("Your private newsletter address will appear here once receiving is set up. Your sign-in email is kept separate.")
            } else {
            Button {
                copyText(alias)
                model.notice = model.account?.aliasStatus == "active" ? "Private newsletter address copied." :
                    (model.account?.aliasStatus == "provisioning" ? "Address copied. Receiving still needs its delivery test." : "Placeholder alias copied; it cannot receive mail yet.")
            } label: {
                HStack(spacing: 12) {
                    Text(alias).font(.system(size: 14, design: .monospaced))
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .layoutPriority(1)
                    Spacer()
                    Image(systemName: "doc.on.doc").font(.system(size: 14))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 16)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain).accessibilityLabel("Copy newsletter address").accessibilityValue(alias)
            if model.account?.aliasStatus == "active" {
                InfoRow("Use this address for newsletter subscriptions. Mail goes to UpToDate, with no forwarding to your sign-in email.")
            } else if model.account?.aliasStatus == "provisioning" {
                InfoRow("Address assigned. We still need to verify incoming delivery before newsletter signup is enabled.")
            } else {
                InfoRow("Placeholder only. Import .eml files until receiving is connected.")
            }
            if model.account?.aliasScope == "temporary_test" {
                InfoRow("Temporary test address. Newsletter subscriptions will need updating when we connect our own domain.")
            }
            }
            if model.authConfiguration?.mode == "supabase" && model.account?.aliasStatus != "active" {
                ActionRow(title: "Check newsletter address", disabled: model.busy || model.offline) {
                    Task { await model.refreshAccount() }
                }
            }

            SectionHeader("Prototype tools")
            ActionRow(title: "Load sample Weekly Roundup edition", disabled: model.busy) {
                Task { await model.loadDemo() }
            }
            RowDivider()
            InfoRow("Account timezone: \(model.account?.timezone ?? "UTC")")
            RowDivider()
            ActionRow(title: "Sign out", disabled: model.busy) { Task { await model.signOut() } }
            RowDivider()
            ActionRow(title: "Delete account and data", destructive: true,
                      disabled: model.busy || model.offline) { deleting = true }
        }
        .toolbar(.hidden)
        .sheet(isPresented: $editingProfile) { ProfileSettingsView() }
        .confirmationDialog("Delete your account, imported excerpts, bookmarks, and share links?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete account", role: .destructive) { Task { await model.deleteAccount() } }
        }
    }

    private func profileStat(_ value: Int, _ label: String) -> some View {
        VStack(spacing: 3) {
            Text("\(value)").font(.system(size: 17, weight: .semibold))
            Text(label).font(.system(size: 11)).foregroundStyle(.secondary)
        }
    }
}

struct SourceEditor: View {
    @EnvironmentObject private var model: AppModel
    let source: NewsletterSource
    @State private var signup = ""
    @State private var confirmingUnsubscribe = false
    @Environment(\.openURL) private var openURL
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(source.name).font(.system(size: 17, weight: .semibold)).tracking(-0.3)
            Text(source.sender).font(.system(size: 12)).foregroundStyle(.secondary)
            if let status = source.subscriptionStatus, status != "not_requested" {
                Text(source.subscriptionTitle).font(.system(size: 11)).foregroundStyle(.secondary)
                if let detail = source.subscriptionDetail { Text(detail).font(.system(size: 11)).foregroundStyle(.secondary) }
            }
            Toggle("Show in daily feed", isOn: Binding(get: { source.muted == 0 }, set: { shown in
                Task { await model.updateSource(source, muted: !shown) }
            })).font(.system(size: 15)).disabled(model.busy || model.offline)
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
            if ["manual_action", "failed", "needs_agent", "needs_inbox"].contains(source.subscriptionStatus ?? "") {
                Button("Retry signup using my newsletter address") {
                    Task { await model.retrySubscription(source) }
                }.font(.system(size: 14)).disabled(model.busy || model.offline || model.account?.aliasStatus != "active")
            }
            Button(source.subscriptionStatus == "unsubscribed" ? "Unsubscribed" : "Unsubscribe from newsletter", role: .destructive) {
                confirmingUnsubscribe = true
            }
            .font(.system(size: 14)).buttonStyle(.plain)
            .disabled(model.busy || model.offline || source.subscriptionStatus == "unsubscribed")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 18)
        .onAppear { signup = source.signupUrl }
        .confirmationDialog("Unsubscribe from \(source.name)?", isPresented: $confirmingUnsubscribe, titleVisibility: .visible) {
            Button("Unsubscribe", role: .destructive) {
                Task {
                    if let url = await model.unsubscribe(source) { openURL(url) }
                }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Stop creating new posts and request unsubscribe from the publisher. Existing posts stay available. Some publishers require an extra confirmation.")
        }
    }
}
