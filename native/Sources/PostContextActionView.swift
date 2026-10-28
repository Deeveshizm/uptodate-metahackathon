import SwiftUI
import EventKit
#if os(iOS)
import UIKit
#endif

struct PostContextActionView: View {
    let post: Post
    @State private var showing = false
    private var symbol: String {
        switch post.actionKind {
        case "ingredients": "checklist"
        case "places": "mappin.and.ellipse"
        default: "sparkles"
        }
    }
    var body: some View {
        Button { showing = true } label: {
            Image(systemName: symbol)
                .font(.system(size: 15, weight: .medium))
                .frame(width: 42, height: 30)
                .foregroundStyle(.primary)
                .background(.primary.opacity(0.07), in: Capsule())
                .overlay(Capsule().stroke(.primary.opacity(0.12), lineWidth: 0.5))
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityLabel(post.actionTitle).accessibilityIdentifier("post.contextAction")
        .sheet(isPresented: $showing) {
            switch post.actionKind {
            case "ingredients": IngredientsSheet(post: post)
            case "places": PlacesSheet(post: post)
            default: PostChatSheet(post: post)
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

private struct PlacesSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    let post: Post
    var body: some View {
        NavigationStack {
            List {
                ForEach(post.contentAction?.places ?? []) { place in
                    Button {
                        if let value = place.mapUrl, let map = URL(string: value), map.scheme == "https" {
                            openURL(map); return
                        }
                        var url = URLComponents(string: "https://maps.apple.com/")!
                        url.queryItems = [URLQueryItem(name: "q", value: [place.name, place.address ?? ""].filter { !$0.isEmpty }.joined(separator: ", "))]
                        if let destination = url.url { openURL(destination) }
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(place.name, systemImage: "mappin.and.ellipse")
                            if let address = place.address, !address.isEmpty { Text(address).font(.caption).foregroundStyle(.secondary) }
                            Text(place.mapUrl == nil ? "Search in Apple Maps" : "Open source map").font(.caption)
                        }
                    }
                }
            }.navigationTitle("Places in this post")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }.frame(minWidth: 320, minHeight: 400)
    }
}

private struct PostChatSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let post: Post
    @State private var turns: [PostChatTurn] = []
    @State private var question = ""
    @State private var sending = false
    @State private var error: String?
    @State private var sharing = false
    @State private var task: Task<Void, Never>?
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 18) {
                            Text(post.title).font(.headline)
                            Text("Ask about the source material available for this post. Answers may be incomplete; check the original source.").font(.caption).foregroundStyle(.secondary)
                            #if os(iOS)
                            Button { sharing = true } label: { Label("Choose another AI app…", systemImage: "square.and.arrow.up") }
                                .accessibilityIdentifier("post.chat.share")
                            Text("The sharing sheet shows compatible installed apps. If your AI app is missing, copy the context there and paste it into that app.")
                                .font(.caption).foregroundStyle(.secondary)
                            #else
                            ShareLink(item: post.aiContext) { Label("Share post context", systemImage: "square.and.arrow.up") }
                            #endif
                            Divider()
                            Text("Ask in UpToDate").font(.subheadline.bold())
                            ForEach(turns) { turn in
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(turn.role == "user" ? "You" : "UpToDate · Spark").font(.caption.bold()).foregroundStyle(.secondary)
                                    Text(turn.text).textSelection(.enabled)
                                }.frame(maxWidth: .infinity, alignment: .leading).id(turn.id)
                            }
                            if sending { ProgressView("Reading this post’s sources…") }
                            if let error { Text(error).foregroundStyle(.red).font(.callout) }
                            if !post.isEmailSource, let url = URL(string: post.originalUrl) { Link("Open original source", destination: url) }
                        }.padding()
                    }.onChange(of: turns.count) { _, _ in
                        if let last = turns.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
                    }
                }
                Divider()
                HStack(alignment: .bottom) {
                    TextField("Ask about this post", text: $question, axis: .vertical).lineLimit(1...4)
                        .textFieldStyle(.roundedBorder).accessibilityIdentifier("post.chat.question")
                        .onChange(of: question) { _, value in if value.count > 1000 { question = String(value.prefix(1000)) } }
                    Button { send() } label: { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                        .disabled(sending || model.offline || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        .accessibilityLabel("Send question").accessibilityIdentifier("post.chat.send")
                }.padding()
            }.navigationTitle("Ask AI")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }.frame(minWidth: 320, minHeight: 480)
            .onDisappear { task?.cancel() }
            #if os(iOS)
            .sheet(isPresented: $sharing) { PostAIShareSheet(context: post.aiContext) }
            #endif
    }

    private func send() {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        let history = Array(turns.suffix(6))
        turns.append(PostChatTurn(role: "user", text: text)); question = ""; sending = true; error = nil
        task = Task {
            defer { sending = false }
            do {
                let reply = try await model.ask(post, question: text, history: history)
                guard !Task.isCancelled else { return }
                let references = reply.evidenceIds.compactMap { index -> String? in
                    guard post.evidence.indices.contains(index) else { return nil }
                    return post.evidence[index].source
                }
                let suffix = references.isEmpty ? "" : "\n\nSource excerpts: " + references.joined(separator: "; ")
                turns.append(PostChatTurn(role: "assistant", text: reply.answer + suffix))
            } catch {
                if !Task.isCancelled { self.error = error.localizedDescription; question = text; turns.removeLast() }
            }
        }
    }
}

#if os(iOS)
private struct PostAIShareSheet: UIViewControllerRepresentable {
    let context: String
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [self.context], applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
#endif
