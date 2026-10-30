import SwiftUI
import EventKit
#if os(iOS)
import UIKit
#endif

struct PostContextActionView: View {
    let post: Post
    var isFeed = false
    @State private var showing = false
    @State private var showingBiggie = false
    @State private var sharing = false
    @Environment(\.openURL) private var openURL
    @State private var phoneUnavailable = false
    @State private var phoneNumber = ""
    private var sourceURL: URL? {
        guard !post.isEmailSource, let url = URL(string: post.originalUrl),
              ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else { return nil }
        return PostLinkAction(kind: "github", label: "Original repository", url: url.absoluteString).repositoryURL ?? url
    }
    private var sourceIsGitHub: Bool {
        guard let url = sourceURL else { return false }
        return PostLinkAction(kind: "github", label: "Repository", url: url.absoluteString).repositoryURL != nil
    }
    private var links: [PostLinkAction] {
        var values = post.linkActions ?? []
        if sourceIsGitHub, let url = sourceURL {
            values.insert(PostLinkAction(kind: "github", label: "Original repository", url: url.absoluteString), at: 0)
        }
        for place in post.contentAction?.places ?? [] {
            if let website = place.websiteUrl {
                values.append(PostLinkAction(kind: "website", label: place.name, url: website))
            }
        }
        return PostLinkAction.consolidated(values).filter { item in
            guard let url = item.destination else { return false }
            let key = url.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            if !sourceIsGitHub, item.kind != "map", key == sourceURL?.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) { return false }
            return true
        }
    }
    private var locations: [PlaceAction] {
        let extra = links.filter { $0.kind == "map" }.map {
            PlaceAction(name: $0.name ?? $0.label, address: $0.address, sourceUrl: post.originalUrl,
                        mapUrl: $0.url, identityKnown: $0.name != nil || $0.address != nil)
        }
        var urls = Set<String>(), addresses = Set<String>()
        return ((post.contentAction?.places ?? []) + extra).filter { place in
            let address = (place.address ?? "").lowercased()
            if !address.isEmpty, addresses.contains(address) { return false }
            if let url = place.mapUrl, urls.contains(url) { return false }
            if !address.isEmpty { addresses.insert(address) }
            if let url = place.mapUrl { urls.insert(url) }
            return true
        }
    }
    private var githubMark: Image {
        #if SWIFT_PACKAGE
        return Image("GitHubMark", bundle: .module)
        #else
        return Image("GitHubMark")
        #endif
    }
    private func pill(_ symbol: String, gitHub: Bool = false) -> some View {
        Group {
            if gitHub {
                githubMark.resizable().renderingMode(.template).scaledToFit()
                    .frame(width: 14 * FeedStyle.buttonScale, height: 14 * FeedStyle.buttonScale)
            } else {
                Image(systemName: symbol).font(.system(size: 13 * FeedStyle.buttonScale, weight: .medium))
            }
        }.modifier(PostActionPill())
    }
    private var symbol: String {
        switch post.actionKind {
        case "ingredients": "checklist"
        case "places": "mappin.and.ellipse"
        default: "sparkles"
        }
    }
    private func installed(_ app: MapsApp) -> Bool {
        #if os(iOS)
        guard let url = URL(string: app.scheme + "://") else { return false }
        return UIApplication.shared.canOpenURL(url)
        #else
        return app == .apple
        #endif
    }
    @ViewBuilder private func mapOptions(for place: PlaceAction) -> some View {
        let apps = MapsApp.allCases.filter { installed($0) && place.mapURL(for: $0) != nil }
        if !apps.isEmpty {
            ForEach(apps) { app in
                if let url = place.mapURL(for: app) { Link(app.title, destination: url) }
            }
        } else if let url = place.sourceMapURL {
            Link("Open source map", destination: url)
        } else {
            Text("No supported map app available").disabled(true)
        }
    }
    var body: some View {
        HStack(spacing: FeedStyle.actionSpacing) {
            if isFeed {
                feedSourceControl
                linkControl("phone")
            } else {
                if let url = sourceURL, !sourceIsGitHub {
                    Link(destination: url) { pill("link") }
                        .accessibilityLabel("Open original source")
                        .accessibilityIdentifier("post.sourceLink")
                }
                ForEach(["website", "github", "phone"], id: \.self) { kind in
                    linkControl(kind)
                }
                if post.actionKind != "places", !locations.isEmpty {
                    Menu { locationOptions } label: { pill("mappin.and.ellipse") }
                        .menuOrder(.fixed).accessibilityLabel("Locations")
                        .accessibilityIdentifier("post.locations")
                }
            }
            if post.actionKind == "places" {
                Menu { locationOptions } label: { pill(symbol) }
                    .menuOrder(.fixed)
                    .accessibilityLabel("Open location in Maps")
                    .accessibilityIdentifier("post.contextAction")
            } else if post.actionKind == "ingredients" {
                Button { showing = true } label: { pill(symbol) }
                    .accessibilityLabel(post.actionTitle).accessibilityIdentifier("post.contextAction")
                aiControl
            } else {
                aiControl
            }
        }.buttonStyle(.plain)
        .sheet(isPresented: $showing) { IngredientsSheet(post: post) }
        .alert("Phone unavailable", isPresented: $phoneUnavailable) {
            Button("Copy number") { copyText(phoneNumber) }
            Button("OK", role: .cancel) { }
        } message: {
            #if targetEnvironment(simulator)
            Text("The simulator has no Phone app. Test calling on an iPhone.\n\n\(phoneNumber)")
            #else
            Text("No available app could open this phone number.\n\n\(phoneNumber)")
            #endif
        }
        #if os(iOS)
        .fullScreenCover(isPresented: $showingBiggie) { BiggieChatView(post: post) }
        .sheet(isPresented: $sharing) {
            PostAIShareSheet(post: post)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
                .presentationCornerRadius(28)
        }
        #else
        .sheet(isPresented: $showingBiggie) { BiggieChatView(post: post) }
        .sheet(isPresented: $sharing) { PostAIShareSheet(post: post) }
        #endif
    }
    private var aiControl: some View {
        Menu {
            Button { showingBiggie = true } label: { Label("Ask Biggie", systemImage: "sparkles") }
                .accessibilityIdentifier("post.ai.biggie")
            Button { sharing = true } label: { Label("Open in…", systemImage: "arrow.up.forward.app") }
                .accessibilityIdentifier("post.ai.openIn")
        } label: { pill("sparkles") }
            .menuOrder(.fixed)
            .accessibilityLabel(post.isResearchPaper ? "Ask about this paper" : "Ask AI")
            .accessibilityIdentifier(post.actionKind == "ingredients" ? "post.askAI" : "post.contextAction")
    }
    @ViewBuilder private func linkControl(_ kind: String) -> some View {
        let actions = links.filter { $0.kind == kind }
        if actions.count == 1, let action = actions.first, let url = action.destination {
            Group {
                if kind == "phone" {
                    Button { call(action) } label: { pill(action.symbol) }
                } else {
                    Link(destination: url) { pill(action.symbol, gitHub: action.isGitHub) }
                }
            }.accessibilityLabel(action.label).accessibilityIdentifier("post.\(kind)")
        } else if actions.count > 1, let first = actions.first {
            Menu {
                ForEach(actions) { action in
                    if action.kind == "phone" {
                        Button(action.label) { call(action) }
                    } else if let url = action.destination { Link(action.label, destination: url) }
                }
            } label: { pill(first.symbol, gitHub: first.isGitHub) }
                .accessibilityLabel(kind == "phone" ? "Phone numbers" : kind == "github" ? "GitHub repositories" : "Websites")
                .accessibilityIdentifier("post.\(kind)")
        }
    }
    private func call(_ action: PostLinkAction) {
        guard let url = action.destination, action.kind == "phone" else { return }
        phoneNumber = action.url.replacingOccurrences(of: "tel:", with: "")
        #if targetEnvironment(simulator)
        phoneUnavailable = true
        #else
        openURL(url) { accepted in if !accepted { phoneUnavailable = true } }
        #endif
    }
    @ViewBuilder private var feedSourceControl: some View {
        if let url = sourceURL {
            Link(destination: url) { pill("link", gitHub: sourceIsGitHub) }
                .accessibilityLabel(post.isResearchPaper ? "Open paper" : "Open original source")
                .accessibilityIdentifier("post.sourceLink")
        } else {
            let websites = links.filter { $0.kind == "website" }
            if websites.count == 1, let action = websites.first, let url = action.destination {
                Link(destination: url) { pill("globe") }
                    .accessibilityLabel(action.label)
                    .accessibilityIdentifier("post.website")
            } else if websites.count > 1 {
                Menu {
                    ForEach(websites) { action in
                        if let url = action.destination { Link(action.label, destination: url) }
                    }
                } label: { pill("globe") }
                    .accessibilityLabel("Websites")
                    .accessibilityIdentifier("post.website")
            }
        }
    }
    @ViewBuilder private var locationOptions: some View {
        if locations.count == 1, let place = locations.first {
            Section(place.name) { mapOptions(for: place) }
        } else {
            ForEach(locations) { place in
                Menu(place.name) { mapOptions(for: place) }
            }
        }
    }
}

private struct IngredientsSheet: View {
    @Environment(\.dismiss) private var dismiss
    let post: Post
    @State private var selected = Set<Int>()
    @State private var lists: [EKCalendar] = []
    @State private var calendarID = ""
    @State private var store = EKEventStore()
    @State private var working = false
    @State private var message: String?
    @State private var saved = false
    private var ingredients: [String] { post.contentAction?.recipe?.ingredients ?? [] }

    var body: some View {
        NavigationStack {
            List {
                if let note = post.contentAction?.recipe?.note { Text(note).font(.callout).foregroundStyle(.secondary) }
                Section("Choose ingredients") {
                    ForEach(Array(ingredients.enumerated()), id: \.offset) { index, ingredient in
                        Button {
                            if selected.contains(index) { selected.remove(index) } else { selected.insert(index) }
                        } label: {
                            HStack { Image(systemName: selected.contains(index) ? "checkmark.circle.fill" : "circle"); Text(ingredient) }
                        }.foregroundStyle(.primary).disabled(saved)
                    }
                }
                if !lists.isEmpty {
                    Picker("Reminders list", selection: $calendarID) {
                        ForEach(lists, id: \.calendarIdentifier) { list in Text(list.title).tag(list.calendarIdentifier) }
                    }
                }
                if let message { Text(message).font(.callout).accessibilityIdentifier("ingredients.result") }
                Button(lists.isEmpty ? "Choose a Reminders list" : "Add \(selected.count) ingredients") {
                    Task { await addIngredients() }
                }.disabled(working || selected.isEmpty || saved).accessibilityIdentifier("ingredients.add")
                Text("Only the ingredients you select are added. UpToDate doesn’t upload your Reminders.").font(.caption).foregroundStyle(.secondary)
            }
            .navigationTitle("Grocery list")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }.onAppear { selected = Set(ingredients.indices) }
        .frame(minWidth: 320, minHeight: 450)
    }

    @MainActor private func addIngredients() async {
        working = true; message = nil
        defer { working = false }
        do {
            if lists.isEmpty {
                guard try await store.requestFullAccessToReminders() else {
                    message = "Reminders access was declined. You can enable it for UpToDate in Settings."; return
                }
                lists = store.calendars(for: .reminder).filter { $0.allowsContentModifications }
                calendarID = store.defaultCalendarForNewReminders()?.calendarIdentifier ?? lists.first?.calendarIdentifier ?? ""
                if !lists.contains(where: { $0.calendarIdentifier == calendarID }) { calendarID = lists.first?.calendarIdentifier ?? "" }
                if lists.isEmpty { message = "Create a list in Reminders, then reopen this sheet." }
                return
            }
            guard let calendar = lists.first(where: { $0.calendarIdentifier == calendarID }) else { return }
            for index in selected.sorted() {
                let reminder = EKReminder(eventStore: store)
                reminder.title = ingredients[index]; reminder.calendar = calendar
                try store.save(reminder, commit: false)
            }
            try store.commit()
            saved = true; message = "Added \(selected.count) ingredients to \(calendar.title)."
        } catch {
            store.reset(); lists = []; calendarID = ""
            message = "Could not save ingredients. Check Reminders access and try again."
        }
    }
}

private struct BiggieChatView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let post: Post
    @State private var turns: [PostChatTurn] = []
    @State private var question = ""
    @State private var sending = false
    @State private var error: String?
    @State private var sharing = false
    @State private var task: Task<Void, Never>?
    @State private var suggestOpenIn = false
    @FocusState private var questionFocused: Bool
    private var canSend: Bool {
        !sending && !model.offline && !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    private var avatar: Image {
        #if SWIFT_PACKAGE
        return Image("BiggieAvatar", bundle: .module)
        #else
        return Image("BiggieAvatar")
        #endif
    }
    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(turns) { turn in
                            BiggieMessageBubble(turn: turn).id(turn.id)
                        }
                        if sending { BiggieThinkingBubble().id("replyStatus") }
                        if let error { Text(error).foregroundStyle(.red).font(.callout).id("replyError") }
                    }
                    .padding(.horizontal, 20).padding(.vertical, 16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: turns.count) { _, _ in
                    if let last = turns.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
                }
                .onChange(of: sending) { _, value in
                    if value { withAnimation { proxy.scrollTo("replyStatus", anchor: .bottom) } }
                }
                .onChange(of: error) { _, value in
                    if value != nil { withAnimation { proxy.scrollTo("replyError", anchor: .bottom) } }
                }
            }
            .background(.background)
            .safeAreaInset(edge: .top, spacing: 0) { chatHeader }
            .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            .toolbar(.hidden)
        }
        .background(.background)
        .onDisappear { task?.cancel() }
        #if os(iOS)
        .sheet(isPresented: $sharing) {
            PostAIShareSheet(post: post)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
                .presentationCornerRadius(28)
        }
        #else
        .frame(minWidth: 360, minHeight: 420)
        .sheet(isPresented: $sharing) { PostAIShareSheet(post: post) }
        #endif
    }
    private var chatHeader: some View {
        ZStack {
            VStack(spacing: 4) {
                avatar.resizable().scaledToFill()
                    .frame(width: 56, height: 56).clipShape(Circle())
                    .accessibilityHidden(true)
                Text("Biggie").font(.caption.weight(.semibold))
            }.accessibilityElement(children: .combine)
            HStack {
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.subheadline.weight(.medium))
                        .frame(width: 44, height: 44)
                }.foregroundStyle(.secondary).accessibilityLabel("Close Ask AI")
            }
        }.frame(maxWidth: .infinity)
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(.background)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            #if os(iOS)
            Button {
                questionFocused = false
                sharing = true
            } label: { openInIcon }
                .accessibilityLabel("Open in another app")
                .accessibilityIdentifier("post.chat.share")
            #else
            Button { questionFocused = false; sharing = true } label: { openInIcon }
                .accessibilityLabel("Open in another app")
            #endif

            TextField("Ask about this post", text: $question, axis: .vertical)
                .lineLimit(1...3).textFieldStyle(.plain).focused($questionFocused)
                .font(.system(size: 16))
                .padding(.horizontal, 16).padding(.vertical, 8)
                .frame(minHeight: 36)
                .background(.primary.opacity(0.05), in: Capsule())
                .overlay(Capsule().stroke(.primary.opacity(0.12), lineWidth: 0.5))
                .frame(minHeight: 44, alignment: .bottom)
                .accessibilityIdentifier("post.chat.question")
                .onChange(of: question) { _, value in if value.count > 1000 { question = String(value.prefix(1000)) } }

            Button { send() } label: {
                Image(systemName: "arrow.up").font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white).frame(width: 36, height: 36)
                    .background(Color.blue, in: Circle())
                    .frame(width: 44, height: 44, alignment: .bottom)
                    .contentShape(Rectangle())
            }
            .opacity(canSend ? 1 : 0.45).disabled(!canSend)
            .accessibilityLabel("Send to Biggie").accessibilityIdentifier("post.chat.send")
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16).padding(.top, 6).padding(.bottom, 2)
        .background(.background)
    }

    private var openInIcon: some View {
        Image(systemName: "arrow.up.forward.app").font(.system(size: 15, weight: .medium))
            .foregroundStyle(suggestOpenIn ? Color.blue : Color.primary).frame(width: 36, height: 36)
            .background(suggestOpenIn ? Color.blue.opacity(0.12) : Color.primary.opacity(0.07), in: Circle())
            .overlay(Circle().stroke(suggestOpenIn ? Color.blue : Color.primary.opacity(0.12), lineWidth: suggestOpenIn ? 2 : 0.5))
            .frame(width: 44, height: 44, alignment: .bottom)
            .contentShape(Rectangle())
    }

    private func send() {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend else { return }
        let history = Array(turns.suffix(6))
        turns.append(PostChatTurn(role: "user", text: text)); question = ""; sending = true; error = nil
        task = Task {
            defer { sending = false }
            do {
                let reply = try await model.ask(post, question: text, history: history)
                guard !Task.isCancelled else { return }
                suggestOpenIn = reply.handoffSuggested == true
                let references = reply.evidenceIds.compactMap { index -> String? in
                    guard post.evidence.indices.contains(index) else { return nil }
                    return post.evidence[index].source
                }
                let suffix = references.isEmpty ? "" : "\n\nSource excerpts: " + references.joined(separator: "; ")
                turns.append(PostChatTurn(role: "assistant", text: reply.answer + suffix, citations: reply.citations ?? []))
            } catch {
                if !Task.isCancelled { self.error = error.localizedDescription; question = text; turns.removeLast() }
            }
        }
    }
}

private struct BiggieMessageBubble: View {
    let turn: PostChatTurn
    private var outgoing: Bool { turn.role == "user" }

    var body: some View {
        HStack(alignment: .bottom, spacing: 0) {
            if outgoing { Spacer(minLength: 52) }
            VStack(alignment: outgoing ? .trailing : .leading, spacing: 4) {
                Group {
                    if outgoing { Text(turn.text) }
                    else { ReadableText(text: turn.text, spacing: 8, fillWidth: false) }
                }
                    .font(.body).textSelection(.enabled)
                    .padding(.horizontal, 16).padding(.vertical, 12)
                    .foregroundStyle(outgoing ? Color.white : Color.primary)
                    .background {
                        UnevenRoundedRectangle(topLeadingRadius: 20,
                                               bottomLeadingRadius: outgoing ? 20 : 5,
                                               bottomTrailingRadius: outgoing ? 5 : 20,
                                               topTrailingRadius: 20)
                            .fill(outgoing ? Color(red: 0.02, green: 0.36, blue: 0.82) : Color.gray.opacity(0.22))
                    }
                    .accessibilityLabel((outgoing ? "You: " : "Biggie: ") + turn.text)
                if !outgoing, !turn.citations.isEmpty {
                    Menu {
                        ForEach(turn.citations) { citation in
                            if let url = citation.destination { Link(citation.title, destination: url) }
                        }
                    } label: {
                        Label("Sources", systemImage: "globe").font(.system(size: 12 * FeedStyle.buttonScale))
                            .padding(.horizontal, 12 * FeedStyle.buttonScale).padding(.vertical, 7 * FeedStyle.buttonScale)
                            .background(.primary.opacity(0.06), in: Capsule())
                    }.accessibilityIdentifier("chat.webSources")
                }
                Text(outgoing ? "you" : "Biggie")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .accessibilityHidden(true)
            }
            if !outgoing { Spacer(minLength: 52) }
        }
        .frame(maxWidth: .infinity, alignment: outgoing ? .trailing : .leading)
    }
}

private struct BiggieThinkingBubble: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduceMotion)) { timeline in
            HStack(spacing: 5) {
                ForEach(0..<3) { index in
                    let phase = (timeline.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.2) / 1.2 - Double(index) * 0.15 + 1).truncatingRemainder(dividingBy: 1)
                    let bounce = reduceMotion ? 0 : max(0, sin(phase * 2 * .pi))
                    Circle().fill(Color.secondary).frame(width: 7, height: 7)
                        .offset(y: -5 * bounce)
                }
            }
            .padding(.horizontal, 17).padding(.vertical, 18)
            .background(Color.gray.opacity(0.22), in: UnevenRoundedRectangle(topLeadingRadius: 20,
                        bottomLeadingRadius: 5, bottomTrailingRadius: 20, topTrailingRadius: 20))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Biggie is thinking")
        .accessibilityIdentifier("post.chat.thinking")
    }
}

private struct PostAIShareSheet: View {
    @EnvironmentObject private var model: AppModel
    let post: Post
    @State private var source: String?
    @State private var error: String?
    var body: some View {
        Group {
            if let source {
                #if os(iOS)
                PostSourceActivity(context: source)
                #else
                VStack(spacing: 16) {
                    Text("Open the original source").font(.headline)
                    ShareLink(item: source) { Label("Choose an app", systemImage: "arrow.up.forward.app") }
                }.padding(28)
                #endif
            } else if let error { Text(error).foregroundStyle(.secondary).padding() }
            else { ProgressView("Preparing source…").padding() }
        }.task {
            do {
                if post.isEmailSource {
                    let original = try await model.sourceEmail(post)
                    source = original.title + "\n\n" + original.text
                } else { source = post.originalUrl }
            } catch { self.error = error.localizedDescription }
        }
    }
}

#if os(iOS)
private struct PostSourceActivity: UIViewControllerRepresentable {
    let context: String
    func makeUIViewController(context: Context) -> UIActivityViewController {
        let item: Any
        if let url=URL(string:self.context),url.host != nil,["https","http"].contains(url.scheme ?? "") {
            item=url
        } else { item=self.context }
        let controller = UIActivityViewController(activityItems: [item], applicationActivities: nil)
        controller.excludedActivityTypes = [.postToFacebook, .postToTwitter, .postToWeibo, .message, .mail,
                                          .print, .assignToContact, .saveToCameraRoll, .addToReadingList,
                                          .postToFlickr, .postToVimeo, .postToTencentWeibo, .airDrop]
        return controller
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
#endif
