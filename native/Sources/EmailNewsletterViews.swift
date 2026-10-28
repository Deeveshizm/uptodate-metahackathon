import SwiftUI
#if os(iOS)
import SafariServices
#endif

struct EmailNewsletterPrompt: View {
    @EnvironmentObject private var model: AppModel
    let candidate: IncomingNewsletter
    @State private var naming = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(candidate.provider == nil ? "UpToDate detected a new email edition." : "UpToDate received an email from \(candidate.displayName).").font(.headline)
            Text("Use it as a newsletter?").font(.subheadline)
            Text(candidate.subject).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            HStack {
                Button("Yes, add newsletter") { naming = true }.buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("inbox.useNewsletter")
                if candidate.status == "pending" {
                    Button("Not now") { Task { await model.dismissEmailNewsletter(candidate) } }.buttonStyle(.borderless)
                }
            }.disabled(model.busy || model.offline)
        }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
            .background(.secondary.opacity(0.05))
            .sheet(isPresented: $naming) { NameEmailNewsletter(candidate: candidate) }
    }
}

private struct NameEmailNewsletter: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let candidate: IncomingNewsletter
    @State private var name = ""
    var body: some View {
        NavigationStack {
            Form {
                Section("Name this newsletter") {
                    TextField("e.g. Tallahassee coffee", text: $name).accessibilityIdentifier("inbox.newsletterName")
                    Text("Start with the latest email and automatically turn future editions from this sender into posts.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Email sender") { Text(candidate.sender).font(.caption).textSelection(.enabled) }
                Button("Use as newsletter") {
                    Task { if await model.acceptEmailNewsletter(candidate, name: name.trimmingCharacters(in: .whitespacesAndNewlines)) { dismiss() } }
                }.disabled(model.busy || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("inbox.confirmNewsletter")
            }.navigationTitle("Add email newsletter")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }.onAppear { name = candidate.provider ?? (candidate.senderName.contains(".") ? String(candidate.subject.split(separator: ":", maxSplits: 1).first ?? "Email newsletter") : candidate.senderName) }
            .frame(minWidth: 340, minHeight: 400)
    }
}

struct SourceEmailSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let post: Post
    @State private var source: EmailSourceText?
    @State private var error: String?
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let source {
                        Text(source.title).font(.title2.bold())
                        Text(source.author).font(.caption).foregroundStyle(.secondary)
                        Text(source.text).textSelection(.enabled)
                    } else if let error { Text(error).foregroundStyle(.secondary) }
                    else { ProgressView("Loading source email") }
                }.padding().frame(maxWidth: .infinity,alignment: .leading)
            }.navigationTitle("Source email")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }.task {
            do { source = try await model.sourceEmail(post) }
            catch { self.error = error.localizedDescription }
        }.frame(minWidth: 340,minHeight: 450)
    }
}

struct PublisherSignupSheet: View {
    @Environment(\.dismiss) private var dismiss
    let url: URL
    let alias: String
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(alias).font(.caption).textSelection(.enabled)
                    Spacer()
                    Button("Copy address") { copyText(alias) }.accessibilityIdentifier("signup.copyAlias")
                }.padding(.horizontal)
                Text("Paste your newsletter address into the publisher’s email field and complete its verification. UpToDate will detect incoming confirmation or welcome mail.")
                    .font(.caption).foregroundStyle(.secondary).padding(.horizontal)
                #if os(iOS)
                PublisherBrowser(url: url, onDone: { dismiss() })
                #else
                Link("Open publisher website", destination: url).padding()
                Spacer()
                #endif
            }.navigationTitle("Finish newsletter signup")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }.frame(minWidth: 340,minHeight: 500)
    }
}

#if os(iOS)
private struct PublisherBrowser: UIViewControllerRepresentable {
    let url: URL
    let onDone: () -> Void
    func makeUIViewController(context: Context) -> SFSafariViewController {
        let controller = SFSafariViewController(url: url)
        controller.delegate = context.coordinator
        return controller
    }
    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(onDone: onDone) }
    @MainActor final class Coordinator: NSObject, @preconcurrency SFSafariViewControllerDelegate {
        let onDone: () -> Void
        init(onDone: @escaping () -> Void) { self.onDone = onDone }
        func safariViewControllerDidFinish(_ controller: SFSafariViewController) { onDone() }
    }
}
#endif
