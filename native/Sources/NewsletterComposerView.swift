import SwiftUI
#if os(iOS)
import UIKit
#endif

struct NewsletterComposerView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var draft = NewsletterPromptDraft()
    @State private var sharedPrompt: NewsletterPromptShare?
    @State private var copyNotice: String?

    private var prompt: String? {
        guard model.account?.aliasStatus == "active", let alias = model.account?.alias else { return nil }
        return draft.prompt(alias: alias, timezone: TimeZone.current.identifier)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    Text("Describe your newsletter. We'll prepare a prompt for Muse using your private newsletter address.")
                        .font(.subheadline).foregroundStyle(.secondary)

                    VStack(alignment: .leading, spacing: 10) {
                        Text("Newsletter type").font(.subheadline.weight(.semibold))
                        Picker("Newsletter type", selection: $draft.kind) {
                            ForEach(NewsletterPromptKind.allCases) { kind in
                                Text(kind.title).tag(kind)
                            }
                        }.pickerStyle(.menu).tint(.primary)
                            .padding(.horizontal, 10).padding(.vertical, 4)
                            .background(.primary.opacity(0.05), in: Capsule())
                            .accessibilityIdentifier("newsletter.create.type")
                        Text(draft.kind.description).font(.caption).foregroundStyle(.secondary)
                    }

                    field("Newsletter name", hint: draft.kind.nameHint, text: $draft.name)
                    field("What should it cover?", hint: draft.kind.topicHint, text: $draft.topic, multiline: true)
                    field(draft.kind == .researchPapers ? "How should papers be covered?" : "What kinds of posts?",
                          hint: draft.kind.preferencesHint, text: $draft.postPreferences, multiline: true)
                    if draft.kind == .researchPapers {
                        researchLinkOptions
                        field("Other link preferences", hint: "Optional: talks, benchmarks, or other supporting sources",
                              text: $draft.researchLinkNotes, multiline: true)
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        Text("How often?").font(.subheadline.weight(.semibold))
                        Picker("Delivery frequency", selection: $draft.frequency) {
                            ForEach(NewsletterDeliveryFrequency.allCases) { frequency in
                                Text(frequency.title).tag(frequency)
                            }
                        }.pickerStyle(.menu).tint(.primary)
                            .padding(.horizontal, 10).padding(.vertical, 4)
                            .background(.primary.opacity(0.05), in: Capsule())
                            .accessibilityIdentifier("newsletter.create.frequency")
                    }
                    field("Preferred day or time", hint: "Optional: Mondays at 9 AM", text: $draft.scheduleNotes)

                    if model.account?.aliasStatus != "active" {
                        Text("Your newsletter address needs to be ready before you can prepare a prompt.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }

                    if let prompt {
                        DisclosureGroup("Preview prompt") {
                            Text(prompt).font(.footnote).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 10)
                        }.font(.subheadline).tint(.primary)
                            .accessibilityIdentifier("newsletter.create.preview")
                    }

                    handoffActions

                    Text("Choose Muse in the share sheet if available, or copy the prompt and paste it into Muse. Finish creating the schedule there. When the first email arrives, UpToDate will ask to add it under Custom.")
                        .font(.footnote).foregroundStyle(.secondary)
                }.padding(20).frame(maxWidth: FeedStyle.feedWidth)
                    .frame(maxWidth: .infinity)
            }
            .navigationTitle("Create newsletter")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
            #if os(iOS)
            .scrollDismissesKeyboard(.interactively)
            #endif
        }.frame(minWidth: 340, minHeight: 500)
            .sheet(item: $sharedPrompt) { item in
                #if os(iOS)
                NewsletterPromptActivity(text: item.text)
                #else
                ShareLink(item: item.text) { Label("Share prompt", systemImage: "square.and.arrow.up") }
                    .padding(28)
                #endif
            }
    }

    private var researchLinkOptions: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Links to include").font(.subheadline.weight(.semibold))
            Text("The original paper link is always included. Choose extra links to add when available.")
                .font(.caption).foregroundStyle(.secondary)
            VStack(spacing: 12) {
                ForEach(NewsletterResearchLink.allCases) { link in
                    Toggle(link.title, isOn: Binding(get: { draft.researchLinks.contains(link) }, set: { selected in
                        if selected { draft.researchLinks.insert(link) }
                        else { draft.researchLinks.remove(link) }
                    })).toggleStyle(.switch).tint(.green)
                        .accessibilityIdentifier("newsletter.create.link.\(link.rawValue)")
                }
            }.font(.subheadline).padding(14)
                .background(.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 22))
        }
    }

    private func field(_ title: String, hint: String, text: Binding<String>, multiline: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.subheadline.weight(.semibold))
            TextField(hint, text: text, axis: multiline ? .vertical : .horizontal)
                .lineLimit(multiline ? 3...6 : 1...1)
                .textFieldStyle(.plain).padding(14)
                .background(.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 22))
                .accessibilityLabel(title)
        }
    }

    private var handoffActions: some View {
        VStack(alignment: .leading, spacing: 12) {
            #if os(iOS)
            Button {
                guard let prompt else { return }
                sharedPrompt = NewsletterPromptShare(text: prompt)
            } label: {
                Label("Share with Muse", systemImage: "square.and.arrow.up")
                    .modifier(CapsuleButtonLabelSizing())
            }.buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
                .disabled(prompt == nil).accessibilityIdentifier("newsletter.create.share")
            #else
            if let prompt {
                ShareLink(item: prompt) {
                    Label("Share prompt", systemImage: "square.and.arrow.up")
                        .modifier(CapsuleButtonLabelSizing())
                }.buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
            }
            #endif
            HStack(spacing: 12) {
                Button {
                    guard let prompt else { return }
                    copyText(prompt)
                    copyNotice = "Prompt copied. Paste it in Muse to finish setup."
                } label: {
                    Label("Copy prompt", systemImage: "doc.on.doc").modifier(CapsuleButtonLabelSizing(fontSize: 12))
                }.accessibilityIdentifier("newsletter.create.copy")
                Button {
                    guard let prompt else { return }
                    copyText(prompt)
                    copyNotice = "Prompt copied. Paste it in Muse to finish setup."
                    openURL(URL(string: "https://muse.ai/")!)
                } label: {
                    Label("Open Muse", systemImage: "arrow.up.forward.app").modifier(CapsuleButtonLabelSizing(fontSize: 12))
                }.accessibilityIdentifier("newsletter.create.open")
            }.buttonStyle(.bordered).buttonBorderShape(.capsule).disabled(prompt == nil)
            if let copyNotice {
                Text(copyNotice).font(.caption).foregroundStyle(.secondary)
                    .accessibilityIdentifier("newsletter.create.notice")
            }
        }
    }
}

private struct NewsletterPromptShare: Identifiable {
    let id = UUID()
    let text: String
}

#if os(iOS)
private struct NewsletterPromptActivity: UIViewControllerRepresentable {
    let text: String
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [text], applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
#endif
