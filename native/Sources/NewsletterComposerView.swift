import SwiftUI
#if os(iOS)
import UIKit
#endif

struct NewsletterComposerView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var draft = NewsletterPromptDraft()
    @State private var copyNotice: String?
    @State private var noticeVersion = 0
    @State private var guideTarget: NewsletterGuideTarget?

    private var museIsConnected: Bool {
        model.account?.privateMuseEnabled == true && model.museConnectionStatus?.isVerified == true
    }

    private var directPrompt: String? {
        guard museIsConnected else { return nil }
        return draft.prompt(alias: "", timezone: TimeZone.current.identifier, useExistingMuseConnection: true)
    }

    private var prompt: String? {
        guard model.account?.aliasStatus == "active", let alias = model.account?.alias else { return nil }
        return draft.prompt(alias: alias, timezone: TimeZone.current.identifier)
    }

    var body: some View {
        NavigationStack {
            ScrollViewReader { reader in formScroll(reader) }
            .navigationTitle("Create newsletter")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button { guideTarget = .kind } label: { Image(systemName: "questionmark.circle") }
                        .accessibilityLabel("Show newsletter guide")
                }
            }
            #if os(iOS)
            .scrollDismissesKeyboard(.interactively)
            #endif
        }.frame(minWidth: 340, minHeight: 500)
            .overlayPreferenceValue(NewsletterGuideAnchors.self) { anchors in
                GeometryReader { geometry in
                    if let guideTarget, let anchor = anchors[guideTarget] {
                        NewsletterFocusGuide(target: guideTarget, frame: geometry[anchor], size: geometry.size,
                                             onSkip: finishGuide, onNext: nextGuideStep)
                            .accessibilityAction(.escape, finishGuide)
                    }
                }
            }
            .overlay(alignment: .bottom) {
                if let copyNotice {
                    Text(copyNotice)
                        .font(.footnote.weight(.medium)).multilineTextAlignment(.center)
                        .padding(.horizontal, 18).padding(.vertical, 12)
                        .background(.regularMaterial, in: Capsule())
                        .overlay(Capsule().stroke(.primary.opacity(0.1), lineWidth: 0.5))
                        .padding(.horizontal, 20).padding(.bottom, 24)
                        .phaseAnimator([0.0, -5, 5, -3, 3, 0], trigger: noticeVersion) { content, offset in
                            content.offset(x: reduceMotion ? 0 : offset)
                        } animation: { _ in .easeInOut(duration: 0.08) }
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                        .accessibilityIdentifier("newsletter.create.notice")
                        .allowsHitTesting(false)
                }
            }
            .task(id: noticeVersion) {
                guard copyNotice != nil else { return }
                do { try await Task.sleep(for: .seconds(3.5)) }
                catch { return }
                withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) { copyNotice = nil }
            }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active, model.account?.privateMuseEnabled == true {
                    Task { _ = await model.museConnection() }
                }
            }
    }

    private func formScroll(_ reader: ScrollViewProxy) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Text(model.account?.privateMuseEnabled == true ? "Describe your newsletter and choose its schedule. Connect Muse in Profile → Connections to receive editions directly in UpToDate." : "Describe your newsletter. We'll prepare a prompt for Muse using your private newsletter address.")
                    .font(.subheadline).foregroundStyle(.secondary)
                typeAndNameSection
                detailsSection
                scheduleSection
                if model.account?.privateMuseEnabled == true, model.museConnectionStatus?.isVerified != true {
                    Text("Finish connecting Muse in Profile → Connections to enable direct delivery. You can still share a prompt using your newsletter address.")
                        .font(.footnote).foregroundStyle(.secondary)
                } else if model.account?.privateMuseEnabled != true, model.account?.aliasStatus != "active" {
                    Text("Your newsletter address needs to be ready before you can prepare a prompt.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                promptPreview
                handoffActions.newsletterGuideTarget(.handoff)
                Text(model.account?.privateMuseEnabled == true ? "Share opens Muse with a copied request for delivery to your newsletter email address. Create Directly uses your existing Muse connection without email. Paste the request in Muse and confirm the schedule there." : "Share opens Muse with your email newsletter request copied. Paste it there and confirm the schedule. You can also copy the request for another AI app. When the first email arrives, UpToDate will ask to add it under Custom.")
                    .font(.footnote).foregroundStyle(.secondary)
            }.padding(20).frame(maxWidth: FeedStyle.feedWidth).frame(maxWidth: .infinity)
        }
        .scrollDisabled(guideTarget != nil)
        .accessibilityHidden(guideTarget != nil)
        .onChange(of: guideTarget) { _, target in
            guard let target else { return }
            withAnimation(reduceMotion ? nil : .smooth(duration: 0.25)) { reader.scrollTo(target, anchor: .center) }
        }
        .task(id: model.account?.id) {
            if model.account?.privateMuseEnabled == true { _ = await model.museConnection() }
            await model.prepareGuides()
            if !Task.isCancelled, model.account != nil, !model.completedGuides.contains(.createNewsletter) { guideTarget = .kind }
        }
    }

    private var typeAndNameSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 10) {
                Text("Newsletter type").font(.subheadline.weight(.semibold))
                Picker("Newsletter type", selection: $draft.kind) {
                    ForEach(NewsletterPromptKind.allCases) { kind in Text(kind.title).tag(kind) }
                }.pickerStyle(.menu).tint(.primary)
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(.primary.opacity(0.05), in: Capsule())
                    .accessibilityIdentifier("newsletter.create.type")
                Text(draft.kind.description).font(.caption).foregroundStyle(.secondary)
            }
            field("Newsletter name", hint: draft.kind.nameHint, text: $draft.name)
        }.newsletterGuideTarget(.kind)
    }

    private var detailsSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            field("What should it cover?", hint: draft.kind.topicHint, text: $draft.topic, multiline: true)
            field(draft.kind == .researchPapers ? "How should papers be covered?" : "What kinds of posts?",
                  hint: draft.kind.preferencesHint, text: $draft.postPreferences, multiline: true)
            if draft.kind == .researchPapers {
                researchLinkOptions
                field("Other link preferences", hint: "Optional: talks, benchmarks, or other supporting sources",
                      text: $draft.researchLinkNotes, multiline: true)
            }
        }.newsletterGuideTarget(.details)
    }

    private var scheduleSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 10) {
                Text("How often?").font(.subheadline.weight(.semibold))
                Picker("Delivery frequency", selection: $draft.frequency) {
                    ForEach(NewsletterDeliveryFrequency.allCases) { frequency in Text(frequency.title).tag(frequency) }
                }.pickerStyle(.menu).tint(.primary)
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(.primary.opacity(0.05), in: Capsule())
                    .accessibilityIdentifier("newsletter.create.frequency")
            }
            field("Preferred day or time", hint: "Optional: Mondays at 9 AM", text: $draft.scheduleNotes)
        }.newsletterGuideTarget(.schedule)
    }

    @ViewBuilder private var promptPreview: some View {
        if let prompt {
            DisclosureGroup("Preview email prompt") {
                Text(prompt).font(.footnote).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 10)
            }.font(.subheadline).tint(.primary).accessibilityIdentifier("newsletter.create.preview")
        }
    }

    private func finishGuide() {
        model.finishGuide(.createNewsletter)
        guideTarget = nil
    }

    private func nextGuideStep() {
        guard let guideTarget, let index = NewsletterGuideTarget.allCases.firstIndex(of: guideTarget) else { return }
        if index + 1 == NewsletterGuideTarget.allCases.count { finishGuide() }
        else { self.guideTarget = NewsletterGuideTarget.allCases[index + 1] }
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
        HStack(spacing: 10) {
            if model.account?.privateMuseEnabled == true {
                Button(action: createDirectly) {
                    HStack(spacing: 6) {
                        Text("Create Directly with").lineLimit(1).minimumScaleFactor(0.75)
                        Image("MuseMark").resizable().renderingMode(.template)
                            .scaledToFit().frame(width: 20, height: 20)
                            .opacity(museIsConnected ? 1 : 0.5)
                    }.font(.system(size: 14, weight: .medium))
                        .padding(.horizontal, 12).frame(maxWidth: .infinity, minHeight: 44)
                        .foregroundStyle(museIsConnected ? Color.white : Color.secondary)
                        .background(museIsConnected ? .blue : Color.primary.opacity(0.09), in: Capsule())
                }.buttonStyle(.plain)
                    .accessibilityLabel("Create directly with Muse")
                    .accessibilityValue(museIsConnected ? "Connected" : "Unavailable until Muse is connected")
                    .accessibilityHint(museIsConnected ? "Copies your request and opens Muse to confirm the schedule" : "Connect with Muse in Profile, Connections first")
                    .accessibilityIdentifier("newsletter.create.direct")
            }
            Button(action: shareEmailRequest) {
                Image(systemName: "square.and.arrow.up").font(.system(size: 16, weight: .medium))
                    .frame(width: 44, height: 44).foregroundStyle(.white).background(.blue, in: Circle())
            }.buttonStyle(.plain).disabled(prompt == nil)
                .opacity(prompt == nil ? 0.45 : 1)
                .accessibilityLabel("Open Muse with an email newsletter request")
                .accessibilityHint("Copies a prompt that sends editions to your UpToDate newsletter address")
                .accessibilityIdentifier("newsletter.create.share")
            Button {
                guard let prompt else { return }
                copyText(prompt)
                showNotice("Email newsletter prompt copied.")
            } label: {
                Image(systemName: "doc.on.doc").font(.system(size: 16, weight: .medium))
                    .frame(width: 44, height: 44).background(.primary.opacity(0.07), in: Circle())
            }.buttonStyle(.plain).disabled(prompt == nil).opacity(prompt == nil ? 0.45 : 1)
                .accessibilityLabel("Copy email newsletter prompt")
                .accessibilityIdentifier("newsletter.create.copy")
        }
    }

    private func shareEmailRequest() {
        guard let prompt else { return }
        copyText(prompt)
        showNotice("Email newsletter request copied. Paste it in Muse.")
        MuseHandoff.openChat { showNotice("Request copied. Open Muse and paste it into a chat.") }
    }

    private func createDirectly() {
        guard museIsConnected else {
            showNotice("Connect with Muse first in Profile → Connections.")
            return
        }
        guard let directPrompt else {
            showNotice("Add a newsletter name and what it should cover first.")
            return
        }
        copyText(directPrompt)
        showNotice("Request copied. Paste it in Muse to create your newsletter.")
        MuseHandoff.openChat { showNotice("Request copied. Open the Muse app and paste it into a chat.") }
    }

    private func showNotice(_ text: String) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { copyNotice = text }
        noticeVersion += 1
    }
}
