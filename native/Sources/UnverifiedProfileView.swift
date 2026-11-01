import SwiftUI

/// A pending-account screen, without an authenticated session or relay address.
struct UnverifiedProfileView: View {
    @EnvironmentObject private var model: AppModel
    let email: String
    @State private var resendAvailableAt = Date.distantPast

    private func requireVerification() { model.error = "Verify your account first." }

    var body: some View {
        TabView(selection: Binding(get: { 3 }, set: { _ in requireVerification() })) {
            Color.clear.tabItem { Image(systemName: "house") }.tag(0)
            Color.clear.tabItem { Image(systemName: "square.grid.2x2") }.tag(1)
            Color.clear.tabItem { Image(systemName: "bookmark") }.tag(2)
            profile.tabItem { Image(systemName: "person.crop.circle") }.tag(3)
        }.tint(.primary)
    }

    private var profile: some View {
        ScrollView {
            VStack(spacing: 24) {
                Text("Profile").font(.system(size: 24, weight: .semibold))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button(action: requireVerification) {
                    Image(systemName: "person.crop.circle.fill")
                        .resizable().scaledToFit().frame(width: 90, height: 90)
                        .foregroundStyle(.secondary)
                }.buttonStyle(.plain).accessibilityLabel("Choose profile picture")
                VStack(spacing: 12) {
                    Text(email).font(.subheadline).multilineTextAlignment(.center)
                    Label("Unverified", systemImage: "envelope.badge")
                        .font(.caption.weight(.medium)).foregroundStyle(.orange)
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .background(.orange.opacity(0.12), in: Capsule())
                        .accessibilityIdentifier("account.unverified")
                }
                VStack(spacing: 14) {
                    Text("Verify your account to start using UpToDate.")
                        .font(.subheadline).multilineTextAlignment(.center)
                    Text("Open the confirmation link in your email on this device.")
                        .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let remaining = max(0, Int(ceil(resendAvailableAt.timeIntervalSince(context.date))))
                        Button {
                            Task {
                                resendAvailableAt = Date().addingTimeInterval(60)
                                await model.resendConfirmation(email: email)
                            }
                        } label: {
                            HStack(spacing: 8) {
                                if model.busy { ProgressView().tint(.white) }
                                Text(remaining > 0 ? "Resend in \(remaining)s" : "Resend verification link")
                            }.font(.subheadline.weight(.medium)).padding(.horizontal, 8).padding(.vertical, 5)
                        }
                        .buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
                        .disabled(model.busy || remaining > 0)
                        .accessibilityIdentifier("account.resendVerification")
                    }
                    if let notice = model.notice {
                        Text(notice).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                }.padding(20).frame(maxWidth: .infinity)
                    .background(.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 24))
                VStack(spacing: 0) {
                    ForEach([("Profile", "person.crop.circle"), ("Interests & newsletter picks", "heart.text.square"),
                             ("Manage account", "lock.shield"), ("Bookmarks", "bookmark"), ("Read posts", "checkmark.circle")], id: \.0) { title, symbol in
                        Button(action: requireVerification) { AccountOptionRow(title: title, symbol: symbol) }
                            .buttonStyle(.plain)
                    }
                }
                Button("Back to sign in") { model.cancelEmailFlow(); model.notice = nil }
                    .font(.subheadline).buttonStyle(.bordered).buttonBorderShape(.capsule)
                    .disabled(model.busy)
            }.padding(.horizontal, FeedStyle.gutter).padding(.top, 24).padding(.bottom, 32)
        }.background(.background)
    }
}
