import SwiftUI

struct PersonalizationView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var isOnboarding = false
    var onFinish: (() -> Void)? = nil
    @State private var catalog: RecommendationCatalog?
    @State private var selected: Set<String> = []
    @State private var showingPicks = false
    @State private var working = false
    @State private var adding: String?
    @State private var added: Set<String> = []
    private let columns = [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack {
                    Text(showingPicks ? "Newsletter picks" : "Your interests")
                        .font(.system(size: 24, weight: .semibold))
                    Spacer()
                    if isOnboarding {
                        Button("Skip") { Task { await finish(skippingChoices: !showingPicks) } }
                            .font(.subheadline).buttonStyle(.bordered).buttonBorderShape(.capsule)
                            .disabled(working || adding != nil)
                            .accessibilityIdentifier("personalization.skip")
                    }
                }
                if showingPicks { recommendations }
                else { interests }
            }
            .padding(.horizontal, FeedStyle.gutter).padding(.top, 24).padding(.bottom, 24)
        }
        .background(.background)
        .safeAreaInset(edge: .bottom) {
            Button {
                Task { if showingPicks { await finish() } else { await continueToPicks() } }
            } label: {
                HStack(spacing: 8) {
                    if working { ProgressView().tint(.white) }
                    Text(showingPicks ? (isOnboarding ? "Go to my feed" : "Done") : "Continue")
                        .font(.system(size: 16, weight: .semibold))
                }.frame(maxWidth: .infinity).padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
            .disabled(working || adding != nil || (!showingPicks && (selected.isEmpty || catalog == nil)))
            .accessibilityIdentifier("personalization.continue")
            .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 12)
            .background(.regularMaterial)
        }
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .task {
            selected = Set(model.account?.preferences?.interests ?? [])
            catalog = await model.newsletterRecommendations()
        }
    }

    @ViewBuilder private var interests: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("Make your feed yours").font(.title3.weight(.semibold))
            Text("Choose what you’d like to keep up with. We’ll suggest a few newsletters to get you started.")
                .font(.subheadline).foregroundStyle(.secondary)
        }
        if let catalog {
            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(catalog.topics) { topic in
                    let active = selected.contains(topic.id)
                    Button {
                        if active { selected.remove(topic.id) } else { selected.insert(topic.id) }
                    } label: {
                        HStack(spacing: 7) {
                            Image(systemName: active ? "checkmark.circle.fill" : (topic.symbol ?? "circle"))
                            Text(topic.name).font(.system(size: 14, weight: .medium))
                        }
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .padding(.horizontal, 10).padding(.vertical, 5)
                        .foregroundStyle(active ? Color.green : Color.primary)
                        .background(active ? Color.green.opacity(0.13) : Color.primary.opacity(0.06), in: Capsule())
                        .overlay(Capsule().stroke(active ? Color.green.opacity(0.5) : .clear, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .accessibilityValue(active ? "Selected" : "Not selected")
                    .accessibilityIdentifier("personalization.interest.\(topic.id)")
                }
            }
            Text("You can change your interests in Profile.").font(.caption).foregroundStyle(.secondary)
        } else {
            ProgressView("Loading interests")
            Button("Try again") { Task { catalog = await model.newsletterRecommendations() } }
                .buttonStyle(.bordered).buttonBorderShape(.capsule)
        }
    }

    @ViewBuilder private var recommendations: some View {
        Text("A few newsletters for your interests. Subscribe to the ones you want, or head straight to your feed.")
            .font(.subheadline).foregroundStyle(.secondary)
        if let catalog, !catalog.newsletters.isEmpty {
            ForEach(catalog.newsletters) { newsletter in
                HStack(alignment: .center, spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(newsletter.name).font(.system(size: 17, weight: .semibold))
                        Text(newsletter.description).font(.subheadline).foregroundStyle(.secondary)
                        if newsletter.alreadyAdded || added.contains(newsletter.id) {
                            Text("Added to Manage").font(.caption).foregroundStyle(.green)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                    Button {
                        Task {
                            adding = newsletter.id
                            if await model.addNewsletter(url: newsletter.url, autoSubscribe: true) { added.insert(newsletter.id) }
                            adding = nil
                        }
                    } label: {
                        Group {
                            if adding == newsletter.id { ProgressView().tint(.white) }
                            else { Image(systemName: newsletter.alreadyAdded || added.contains(newsletter.id) ? "checkmark" : "plus").font(.system(size: 17, weight: .semibold)) }
                        }.frame(width: 42, height: 42)
                    }
                    .foregroundStyle(.white).background(.blue, in: Circle()).buttonStyle(.plain)
                    .disabled(working || adding != nil || newsletter.alreadyAdded || added.contains(newsletter.id))
                    .accessibilityLabel("Subscribe to \(newsletter.name)")
                    .accessibilityIdentifier("personalization.subscribe.\(newsletter.id)")
                }.padding(18).background(.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 22))
            }
        } else {
            Text("You can add any newsletter from Home or Manage, or create your own.")
                .font(.subheadline).foregroundStyle(.secondary)
        }
        Label("Subscriptions use your alias. Some publishers ask you to finish signup or confirm.", systemImage: "envelope")
            .font(.caption).foregroundStyle(.secondary)
    }

    private func continueToPicks() async {
        working = true
        defer { working = false }
        guard await model.savePersonalization(interests: selected.sorted(), complete: false) else { return }
        guard let updated = await model.newsletterRecommendations() else { return }
        catalog = updated
        withAnimation { showingPicks = true }
    }

    private func finish(skippingChoices: Bool = false) async {
        working = true
        defer { working = false }
        guard await model.savePersonalization(interests: skippingChoices ? [] : selected.sorted(), complete: true) else { return }
        if let onFinish { onFinish() } else { dismiss() }
    }
}
