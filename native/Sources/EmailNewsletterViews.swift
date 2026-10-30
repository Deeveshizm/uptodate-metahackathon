import SwiftUI
#if os(iOS)
import SafariServices
import UIKit
#else
import AppKit
#endif

struct NewsletterConfirmationPresenter: ViewModifier {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var presented: NewsletterConfirmation?
    @State private var shown = Set<String>()

    func body(content: Content) -> some View {
        content
            .sheet(item: $presented) { confirmation in
                NewsletterConfirmationPopup(confirmation: confirmation)
                    .presentationDetents([.height(330)])
                    .presentationDragIndicator(.visible)
            }
            .onChange(of: model.newsletterConfirmations.map(\.id)) { _, ids in
                if let presented, !ids.contains(presented.id) { self.presented = nil }
            }
            .task(id: model.account?.id) {
                presented = nil
                shown = []
                guard model.account != nil else { return }
                while !Task.isCancelled {
                    if scenePhase == .active, !model.busy, !model.offline,
                       model.error == nil, presented == nil, canPresent,
                       let next = model.newsletterConfirmations.first(where: { !shown.contains($0.id) }) {
                        shown.insert(next.id)
                        presented = next
                    }
                    do { try await Task.sleep(for: .seconds(3)) }
                    catch { return }
                }
            }
    }

    private var canPresent: Bool {
        #if os(iOS)
        // Wait for an open signup, post, or system picker to close first.
        let window = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }
            .flatMap(\.windows).first(where: \.isKeyWindow)
        guard let controller = window?.rootViewController else { return false }
        return controller.presentedViewController == nil
        #else
        return NSApplication.shared.keyWindow?.attachedSheet == nil
        #endif
    }
}

private struct NewsletterConfirmationPopup: View {
    @Environment(\.dismiss) private var dismiss
    let confirmation: NewsletterConfirmation

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Confirm your subscription").font(.title3.weight(.semibold))
                NewsletterConfirmationPrompt(confirmation: confirmation)
                Button("Later") { dismiss() }
                    .font(.subheadline).frame(maxWidth: .infinity)
                    .accessibilityIdentifier("newsletter.confirmLater")
            }.padding(20)
        }
    }
}

struct NewsletterConfirmationPrompt: View {
    @EnvironmentObject private var model: AppModel
    let confirmation: NewsletterConfirmation
    @State private var destination: ConfirmationDestination?
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(confirmation.sourceName ?? "Confirm newsletter signup").font(.headline)
            Text(confirmation.subject).font(.subheadline)
            Text("From \(confirmation.senderDomain)").font(.caption).foregroundStyle(.secondary)
            Button(confirmation.status == "opened" ? "Open confirmation again" : "Confirm subscription") {
                Task {
                    if let url = await model.newsletterConfirmationURL(confirmation) {
                        destination = ConfirmationDestination(url: url)
                    }
                }
            }
            .buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
            .disabled(model.busy || model.offline)
            .accessibilityIdentifier("newsletter.openConfirmation")
        }.frame(maxWidth: .infinity, alignment: .leading).padding(20)
            .background(.secondary.opacity(0.05))
            .sheet(item: $destination) { destination in
                NewsletterConfirmationSheet(confirmation: confirmation, url: destination.url)
            }
    }
}

private struct ConfirmationDestination: Identifiable {
    let id = UUID()
    let url: URL
}

private struct NewsletterConfirmationSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let confirmation: NewsletterConfirmation
    let url: URL
    var body: some View {
        NavigationStack {
            VStack(spacing: 14) {
                #if os(iOS)
                PublisherBrowser(url: url, onDone: { dismiss() })
                #else
                Link("Open confirmation page", destination: url)
                Spacer()
                #endif
                Text("Finish confirming on the publisher’s page. UpToDate will watch for its welcome email or next edition.")
                    .font(.caption).foregroundStyle(.secondary).padding(.horizontal)
                Button("I've confirmed") {
                    Task { if await model.completeNewsletterConfirmation(confirmation) { dismiss() } }
                }.buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
                    .disabled(model.busy || model.offline).padding(.bottom, 12)
            }
            .navigationTitle("Confirm subscription")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
    }
}

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
        #if os(iOS)
        PublisherBrowser(url: url, onDone: { dismiss() })
            .ignoresSafeArea()
            .onAppear { copyText(alias) }
        #else
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                Button("Copy private email") { copyText(alias) }.accessibilityIdentifier("signup.copyAlias")
                    .buttonStyle(.bordered).buttonBorderShape(.capsule).padding(.horizontal)
                Text("Your private email is copied. Paste it into the publisher’s email field to finish setup.")
                    .font(.caption).foregroundStyle(.secondary).padding(.horizontal)
                Link("Open publisher website", destination: url).padding()
                Spacer()
            }.navigationTitle("Finish newsletter signup")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }.frame(minWidth: 340,minHeight: 500).onAppear { copyText(alias) }
        #endif
    }
}

struct PublisherUnsubscribeSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let source: NewsletterSource
    let url: URL
    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                #if os(iOS)
                PublisherBrowser(url: url, onDone: { dismiss() })
                #else
                Link("Open unsubscribe page", destination: url)
                Spacer()
                #endif
                Text("Complete the publisher's unsubscribe steps, then confirm below.")
                    .font(.caption).foregroundStyle(.secondary).padding(.horizontal)
                Button("I've unsubscribed") {
                    Task { if await model.confirmUnsubscribe(source) { dismiss() } }
                }
                .buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
                .disabled(model.busy || model.offline)
                .accessibilityIdentifier("newsletter.confirmUnsubscribe")
                .padding(.bottom, 12)
            }
            .navigationTitle("Unsubscribe")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
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
