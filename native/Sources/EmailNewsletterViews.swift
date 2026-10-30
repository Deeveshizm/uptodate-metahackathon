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
    @State private var code: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(confirmation.sourceName ?? "Confirm newsletter signup").font(.headline)
            Text(confirmation.subject).font(.subheadline)
            Text("From \(confirmation.senderDomain)").font(.caption).foregroundStyle(.secondary)
            if confirmation.isCode {
                if let code { Text(code).font(.title2.monospaced().weight(.semibold)).textSelection(.enabled) }
                Button {
                    Task {
                        if let value = await model.newsletterCode(confirmation) { code = value; copyText(value) }
                    }
                } label: { Text(code == nil ? "View and copy code" : "Copy code").modifier(CapsuleButtonLabelSizing()) }
                    .buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
                    .disabled(model.busy || model.offline)
                    .accessibilityIdentifier("newsletter.copyCode")
                if code != nil {
                    Button("I've entered it") { Task { _ = await model.completeNewsletterConfirmation(confirmation) } }
                        .buttonStyle(.bordered).buttonBorderShape(.capsule)
                        .disabled(model.busy || model.offline)
                }
            } else {
            Button {
                Task {
                    if let url = await model.newsletterConfirmationURL(confirmation) {
                        destination = ConfirmationDestination(url: url)
                    }
                }
            } label: {
                Text(confirmation.status == "opened" ? "Open confirmation again" : "Confirm subscription")
                    .modifier(CapsuleButtonLabelSizing())
            }
            .buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
            .disabled(model.busy || model.offline)
            .accessibilityIdentifier("newsletter.openConfirmation")
            }
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
                Button {
                    Task { if await model.completeNewsletterConfirmation(confirmation) { dismiss() } }
                } label: {
                    Text("I've confirmed").modifier(CapsuleButtonLabelSizing())
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
                Button { naming = true } label: {
                    Text("Yes").modifier(CapsuleButtonLabelSizing())
                }
                    .buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
                    .tint(Color(red: 0.12, green: 0.45, blue: 0.25)).foregroundStyle(.white)
                    .accessibilityIdentifier("inbox.useNewsletter")
                if candidate.status == "pending" {
                    Button { Task { await model.dismissEmailNewsletter(candidate) } } label: {
                        Text("Later").modifier(CapsuleButtonLabelSizing())
                    }
                        .buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
                        .tint(Color(red: 0.70, green: 0.15, blue: 0.15)).foregroundStyle(.white)
                        .accessibilityIdentifier("inbox.later")
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
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let url: URL
    let alias: String
    var sourceID: String? = nil
    @State private var showingCopyNotice = false
    @State private var copyNoticeVersion = 0
    @State private var code: String?
    private var codeConfirmation: NewsletterConfirmation? {
        guard let sourceID else { return nil }
        return model.newsletterConfirmations.first { $0.sourceId == sourceID && $0.isCode }
    }
    var body: some View {
        Group {
        #if os(iOS)
        PublisherBrowser(url: url, onDone: { dismiss() })
            .ignoresSafeArea()
        #else
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                Button { copyNoticeVersion += 1 } label: {
                    Text("Copy private email").modifier(CapsuleButtonLabelSizing())
                }.accessibilityIdentifier("signup.copyAlias")
                    .buttonStyle(.bordered).buttonBorderShape(.capsule).padding(.horizontal)
                Text("Your private email is copied. Paste it into the publisher’s email field to finish setup.")
                    .font(.caption).foregroundStyle(.secondary).padding(.horizontal)
                Link("Open publisher website", destination: url).padding()
                Spacer()
            }.navigationTitle("Finish newsletter signup")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }.frame(minWidth: 340,minHeight: 500)
        #endif
        }
        .overlay(alignment: .top) {
            if let code, let confirmation = codeConfirmation {
                HStack(spacing: 12) {
                    Button { copyText(code) } label: {
                        Label("Code \(code) copied", systemImage: "doc.on.doc")
                    }.accessibilityLabel("Copy verification code \(code)")
                    Button("I've entered it") { Task { _ = await model.completeNewsletterConfirmation(confirmation) } }
                        .foregroundStyle(.blue)
                }
                .font(.footnote.weight(.medium))
                .padding(.horizontal, 16).padding(.vertical, 11)
                .background(.regularMaterial, in: Capsule())
                .padding(.horizontal, 12).padding(.top, 64)
                .accessibilityIdentifier("signup.codePill")
            } else if showingCopyNotice {
                Label("Alias email copied to clipboard", systemImage: "checkmark.circle.fill")
                    .font(.footnote.weight(.medium))
                    .padding(.horizontal, 16).padding(.vertical, 11)
                    .background(.regularMaterial, in: Capsule())
                    .overlay(Capsule().stroke(.primary.opacity(0.1), lineWidth: 0.5))
                    .padding(.horizontal, 16).padding(.top, 64)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .accessibilityIdentifier("signup.aliasCopied")
                    .allowsHitTesting(false)
            }
        }
        .task(id: codeConfirmation?.id) {
            code = nil
            if let confirmation = codeConfirmation, let value = await model.newsletterCode(confirmation) {
                copyText(value)
                code = value
            }
        }
        .task {
            while !Task.isCancelled {
                await model.refreshNewsletterConfirmations()
                do { try await Task.sleep(for: .seconds(3)) }
                catch { return }
            }
        }
        .task(id: copyNoticeVersion) {
            guard !alias.isEmpty else { return }
            copyText(alias)
            withAnimation(.easeOut(duration: 0.2)) { showingCopyNotice = true }
            do { try await Task.sleep(for: .seconds(3)) }
            catch { return }
            withAnimation(.easeOut(duration: 0.2)) { showingCopyNotice = false }
        }
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
                Button {
                    Task { if await model.confirmUnsubscribe(source) { dismiss() } }
                } label: {
                    Text("I've unsubscribed").modifier(CapsuleButtonLabelSizing())
                        .foregroundStyle(.white)
                }
                .buttonStyle(.borderedProminent).buttonBorderShape(.capsule)
                .tint(.blue)
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
