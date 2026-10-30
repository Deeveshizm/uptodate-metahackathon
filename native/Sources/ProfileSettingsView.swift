import SwiftUI
import PhotosUI
import ImageIO
import UniformTypeIdentifiers
#if os(macOS)
import AppKit
#else
import UIKit
#endif

struct AccountAvatar: View {
    @EnvironmentObject private var model: AppModel
    let size: CGFloat
    var body: some View {
        Group {
            #if os(macOS)
            if let data = model.avatarData, let photo = NSImage(data: data) {
                Image(nsImage: photo).resizable().scaledToFill()
            } else { SourceAvatar(name: "Your profile", size: size, symbol: "person") }
            #else
            if let data = model.avatarData, let photo = UIImage(data: data) {
                Image(uiImage: photo).resizable().scaledToFill()
            } else { SourceAvatar(name: "Your profile", size: size, symbol: "person") }
            #endif
        }.frame(width: size, height: size).clipShape(Circle())
            .accessibilityLabel("Profile picture")
    }
}

struct ProfilePhotoPicker: View {
    @EnvironmentObject private var model: AppModel
    @State private var selectedPhoto: PhotosPickerItem?
    @State private var loading = false

    var body: some View {
        ZStack {
            AccountAvatar(size: 94)
                .overlay(alignment: .bottomTrailing) {
                    Image(systemName: loading ? "ellipsis" : "camera.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.background).frame(width: 29, height: 29)
                        .background(.primary, in: Circle())
                        .overlay(Circle().stroke(.background, lineWidth: 3))
                }.accessibilityHidden(true)
            PhotosPicker(selection: $selectedPhoto, matching: .images) {
                Circle().fill(.clear).frame(width: 94, height: 94).contentShape(Circle())
            }.buttonStyle(.plain)
        }
        .disabled(model.busy || model.offline || loading)
        .accessibilityLabel(model.avatarData == nil ? "Choose profile picture" : "Change profile picture")
        .contextMenu {
            if model.account?.avatarRevision != nil {
                Button("Remove photo", systemImage: "trash", role: .destructive) { Task { await model.removeAvatar() } }
            }
        }
        .task(id: selectedPhoto) {
            guard let selectedPhoto else { return }
            loading = true
            defer { loading = false }
            do {
                guard let raw = try await selectedPhoto.loadTransferable(type: Data.self),
                      raw.count <= 20_000_000,
                      let source = CGImageSourceCreateWithData(raw as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
                      let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                        kCGImageSourceCreateThumbnailFromImageAlways: true,
                        kCGImageSourceCreateThumbnailWithTransform: true,
                        kCGImageSourceThumbnailMaxPixelSize: 512
                      ] as CFDictionary) else {
                    throw APIError.response(422, "Choose a smaller, readable photo.")
                }
                let output = NSMutableData()
                guard let destination = CGImageDestinationCreateWithData(output, UTType.jpeg.identifier as CFString, 1, nil) else {
                    throw APIError.response(422, "This photo could not be prepared.")
                }
                CGImageDestinationAddImage(destination, thumbnail, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
                guard CGImageDestinationFinalize(destination) else {
                    throw APIError.response(422, "This photo could not be prepared.")
                }
                await model.updateAvatar(output as Data)
            } catch is CancellationError { }
            catch { model.error = error.localizedDescription }
            self.selectedPhoto = nil
        }
    }
}

struct AccountEmailRow: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        HStack(spacing: 10) {
            Text(model.account?.email ?? "")
                .font(.system(size: 13)).foregroundStyle(.secondary)
                .lineLimit(2).textSelection(.enabled)
            Button {
                guard let alias = model.account?.alias, !alias.isEmpty else { return }
                copyText(alias)
                model.notice = "Newsletter address copied."
            } label: {
                Label("Copy alias", systemImage: "doc.on.doc")
                    .font(.system(size: 11 * FeedStyle.buttonScale, weight: .medium)).fixedSize()
                    .padding(.horizontal, 10 * FeedStyle.buttonScale).padding(.vertical, 8 * FeedStyle.buttonScale)
                    .background(.primary.opacity(0.06), in: Capsule())
            }
            .buttonStyle(.plain)
            .disabled(model.account?.alias.isEmpty != false || model.account?.aliasStatus != "active")
            .accessibilityHint("Copies the separate address used for newsletter subscriptions")
        }
    }
}

struct AccountOptionRow: View {
    let title: String
    let symbol: String
    var destructive = false
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: symbol).font(.system(size: 18)).frame(width: 24)
            Text(title).font(.system(size: 15))
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
        }
        .foregroundStyle(destructive ? Color.red : Color.primary)
        .padding(.horizontal, FeedStyle.gutter).padding(.vertical, 20)
        .contentShape(Rectangle())
    }
}

struct AccountPageHeader: View {
    @Environment(\.dismiss) private var dismiss
    let title: String
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil
    var body: some View {
        HStack(spacing: 14) {
            Button { dismiss() } label: {
                Image(systemName: "chevron.left").font(.system(size: 15, weight: .medium))
                    .frame(width: 40, height: 40).background(.primary.opacity(0.06), in: Circle())
            }.buttonStyle(.plain).accessibilityLabel("Back")
            Text(title).font(.system(size: 23, weight: .semibold)).tracking(-0.4)
            Spacer()
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .font(.system(size: 13 * FeedStyle.buttonScale, weight: .medium)).buttonStyle(.plain)
                    .padding(.horizontal, 14 * FeedStyle.buttonScale).padding(.vertical, 9 * FeedStyle.buttonScale)
                    .background(.primary.opacity(0.06), in: Capsule())
            }
        }.padding(.horizontal, FeedStyle.gutter).padding(.top, 18).padding(.bottom, 30)
    }
}

struct ProfileSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var editing: Bool
    init(startEditing: Bool = false) { _editing = State(initialValue: startEditing) }
    var body: some View {
        PageScroll(showsHeader: false) {
            AccountPageHeader(title: "Profile", actionTitle: editing ? "Cancel" : "Edit", action: {
                name = model.account?.displayName ?? ""
                editing.toggle()
            })
            VStack(alignment: .leading, spacing: 20) {
                Text("Name").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                if editing {
                    TextField("Your name", text: $name)
                        .textFieldStyle(.plain).textContentType(.name)
                        .padding(.horizontal, 18).padding(.vertical, 15)
                        .background(.primary.opacity(0.05), in: Capsule())
                } else {
                    Text(model.account?.displayName?.isEmpty == false ? model.account!.displayName! : "Add your name")
                        .font(.system(size: 18, weight: .medium))
                }
                Text("Email").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                AccountEmailRow()
                if editing {
                Button {
                    Task { if await model.updateProfile(name: name) { editing = false } }
                } label: {
                    Text("Save changes").font(.system(size: 15 * FeedStyle.buttonScale, weight: .semibold))
                        .foregroundStyle(.background).frame(maxWidth: .infinity).padding(.vertical, 15 * FeedStyle.buttonScale)
                        .background(.primary, in: Capsule())
                }
                .buttonStyle(.plain).padding(.top, 16)
                .disabled(model.busy || model.offline || name.count > 100 || model.authConfiguration?.mode != "supabase")
                }
            }.padding(.horizontal, FeedStyle.gutter)
        }.toolbar(.hidden).onAppear { name = model.account?.displayName ?? "" }
    }
}

struct ManageAccountView: View {
    @EnvironmentObject private var model: AppModel
    @State private var resetting = false
    @State private var deleting = false
    var body: some View {
        PageScroll(showsHeader: false) {
            AccountPageHeader(title: "Manage account")
            Button { resetting = true } label: {
                AccountOptionRow(title: "Reset password", symbol: "key")
            }.buttonStyle(.plain).disabled(model.busy || model.offline || model.authConfiguration?.mode != "supabase")
            RowDivider()
            Button { deleting = true } label: {
                AccountOptionRow(title: "Delete account", symbol: "trash", destructive: true)
            }.buttonStyle(.plain).disabled(model.busy || model.offline)
            RowDivider()
        }
        .toolbar(.hidden)
        .confirmationDialog("Reset your password?", isPresented: $resetting, titleVisibility: .visible) {
            Button("Send recovery link") { Task { await model.resetPasswordFromAccount() } }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("We'll email a secure link to your sign-in address and sign you out. Open the link on this device to choose a new password.")
        }
        .confirmationDialog("Delete your account?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete account and data", role: .destructive) { Task { await model.deleteAccount() } }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Your profile, newsletter connections, posts and saved items will be deleted. This cannot be undone.")
        }
    }
}
