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
            Text("Newsletter website").font(.subheadline.weight(.medium))
            HStack(spacing: 10) {
                TextField("https://newsletter.example.com", text: $link)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 16).padding(.vertical, 12)
                    .frame(minHeight: 44)
                    .background(.primary.opacity(0.05), in: Capsule())
                    .overlay(Capsule().stroke(.primary.opacity(0.12), lineWidth: 0.5))
                    .textContentType(.URL)
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
                PasteButton(payloadType: String.self) { values in
                    if let text = values.first {
                        link = text
                        linkFocused = true
                    }
                }
                .labelStyle(.iconOnly)
                .buttonStyle(.bordered).buttonBorderShape(.circle)
                .frame(width: 44, height: 44)
                .accessibilityLabel("Paste newsletter website link")
            }
            if let validation {
                Text(validation).font(.caption).foregroundStyle(.red)
                    .accessibilityIdentifier("newsletter.validation")
            } else {
                Text("We'll subscribe using your newsletter address automatically.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Button(action: submit) {
                Text("Subscribe to newsletter").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.capsule)
            .disabled(model.busy || model.offline || normalizedLink == nil)
            .accessibilityIdentifier("newsletter.subscribe")
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
