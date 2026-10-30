import SwiftUI

struct NewsletterLinkForm: View {
    @EnvironmentObject private var model: AppModel
    @State private var link = ""
    @FocusState private var linkFocused: Bool
    var onAdded: () -> Void = {}

    private var normalizedLink: String? { try? NewsletterLink.normalize(link) }
    private var validation: String? {
        guard !link.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        do { _ = try NewsletterLink.normalize(link); return nil }
        catch { return error.localizedDescription }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                TextField("", text: $link, prompt: Text("Paste a newsletter link").foregroundStyle(.secondary))
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 16).padding(.vertical, 12)
                    .frame(minHeight: 44)
                    .background(.primary.opacity(0.05), in: Capsule())
                    .overlay(Capsule().stroke(.primary.opacity(0.12), lineWidth: 0.5))
                    .textContentType(.URL)
                    .foregroundStyle(.primary)
                    .autocorrectionDisabled()
                    .focused($linkFocused)
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    .submitLabel(.go)
                    #endif
                    .onSubmit(submit)
                    .accessibilityLabel("Newsletter website link")
                    .accessibilityIdentifier("newsletter.website")
                Button(action: submit) {
                    Image(systemName: "arrow.right")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 46, height: 46)
                        .background(Color.primary.opacity(model.busy || model.offline || normalizedLink == nil ? 0.2 : 1), in: Circle())
                }
                .buttonStyle(.plain)
                .disabled(model.busy || model.offline || normalizedLink == nil)
                .accessibilityLabel("Subscribe")
                .accessibilityIdentifier("newsletter.subscribe")
            }
            if let validation {
                Text(validation).font(.caption).foregroundStyle(.red)
                    .accessibilityIdentifier("newsletter.validation")
            }
            if model.account?.aliasStatus != "active" {
                Text("Signup will start once your newsletter address is ready to receive mail.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func submit() {
        guard let url = normalizedLink, !model.busy, !model.offline else { return }
        linkFocused = false
        Task {
            if await model.addNewsletter(url: url, autoSubscribe: true) {
                link = ""
                onAdded()
            }
        }
    }
}
