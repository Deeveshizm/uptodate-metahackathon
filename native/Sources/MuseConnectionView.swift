import SwiftUI

struct MuseConnectionView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var connection: MuseConnectionStatus?
    // Setup credentials stay in account-bound app memory, never on disk.
    private var credential: MuseConnectionCredential? { model.museSetupCredential }
    @State private var working = false
    @State private var confirmingDisconnect = false
    @State private var confirmingReplace = false
    @State private var showingConnectedNotice = false
    @State private var setupNotice: String?
    @State private var setupNoticeVersion = 0

    private var verificationPollKey: String {
        "\(connection?.expiresAt ?? 0)|\(scenePhase == .active)"
    }

    var body: some View {
        PageScroll(showsHeader: false) {
            AccountPageHeader(title: "Muse")
            VStack(alignment: .leading, spacing: 20) {
                Label(statusTitle, systemImage: connection?.isVerified == true ? "link" : "link.badge.plus")
                    .font(.headline).foregroundStyle(connection?.isVerified == true ? Color.green : .primary)
                Text("Send custom newsletters directly from Muse to your private feed.")
                    .font(.subheadline).foregroundStyle(.secondary)
                if connection?.connected == true {
                    Text(connection?.isVerified == true
                         ? (connection?.lastDeliveryAt == nil ? "Connector access has been verified. You can now create newsletters using this connection." : "Muse has delivered an edition to your account.")
                         : "Your connection key is ready. Finish adding UpToDate in Muse, then ask Muse to check the connection by listing your custom newsletters. Return here for confirmation.")
                        .font(.subheadline)
                }
                if let credential, connection?.isVerified != true {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Connection key ready", systemImage: "key")
                            .font(.subheadline.weight(.semibold))
                        Text("Copy this key into Muse’s secure connector setup. It stays available only during this app session.")
                            .font(.caption).foregroundStyle(.secondary)
                        Button {
                            MuseHandoff.copyConnectionKey(credential.credential)
                            showSetupNotice("Key copied. Paste it into Muse’s secure credential field.")
                        } label: {
                            Label("Copy connection key", systemImage: "doc.on.doc")
                                .modifier(CapsuleButtonLabelSizing(fontSize: 13, weight: .medium))
                        }.buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
                        Text("Expires \(Date(timeIntervalSince1970: credential.expiresAt).formatted(date: .abbreviated, time: .omitted)).")
                            .font(.caption).foregroundStyle(.secondary)
                    }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
                        .background(.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 22))
                }
                if let connection {
                    if !connection.isVerified {
                        if connection.connected {
                            Button("Continue in Muse", action: openSetup)
                                .buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
                                .accessibilityIdentifier("muse.continueSetup")
                        }
                        Link(destination: connection.docsUrl) {
                            Label("Setup instructions", systemImage: "arrow.up.right")
                                .modifier(CapsuleButtonLabelSizing(fontSize: 13, weight: .medium))
                        }.buttonStyle(.bordered).buttonBorderShape(.capsule)
                    }
                    Button {
                        if connection.connected { confirmingReplace = true }
                        else { Task { await create() } }
                    } label: {
                        HStack(spacing: 8) {
                            if working { ProgressView() }
                            Text(connection.connected ? "Replace connection key" : "Start Muse setup")
                        }.modifier(CapsuleButtonLabelSizing(fontSize: 13, weight: .medium))
                    }.buttonStyle(.borderedProminent).buttonBorderShape(.capsule).tint(.blue)
                        .disabled(working || model.offline)
                        .accessibilityIdentifier("muse.connect")
                    if connection.connected {
                        Button("Disconnect Muse", role: .destructive) { confirmingDisconnect = true }
                            .modifier(CapsuleButtonLabelSizing(fontSize: 13, weight: .medium))
                            .buttonStyle(.bordered).buttonBorderShape(.capsule).tint(.red)
                            .disabled(working || model.offline)
                            .accessibilityIdentifier("muse.disconnect")
                    }
                } else {
                    ProgressView("Loading connection")
                    Button("Try again") { Task { connection = await model.museConnection() } }
                        .buttonStyle(.bordered).buttonBorderShape(.capsule)
                }
                Text("After setup in Muse, choose the newsletter and schedule there. To pause one newsletter, use Stop processing in Manage.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, FeedStyle.gutter).padding(.bottom, 28)
        }
        .toolbar(.hidden)
        .overlay(alignment: .top) {
            if showingConnectedNotice {
                Label("Muse connected", systemImage: "checkmark.circle.fill")
                    .font(.subheadline.weight(.medium)).foregroundStyle(.green)
                    .padding(.horizontal, 18).padding(.vertical, 12)
                    .background(.regularMaterial, in: Capsule())
                    .padding(.top, 12).transition(.opacity.combined(with: .move(edge: .top)))
                    .accessibilityIdentifier("muse.connectedConfirmation")
            } else if let setupNotice {
                Text(setupNotice).font(.footnote.weight(.medium)).multilineTextAlignment(.center)
                    .padding(.horizontal, 18).padding(.vertical, 12)
                    .background(.regularMaterial, in: Capsule()).padding(.horizontal, 18).padding(.top, 12)
            }
        }
        .task(id: setupNoticeVersion) {
            guard setupNotice != nil else { return }
            do { try await Task.sleep(for: .seconds(4)) }
            catch { return }
            withAnimation { setupNotice = nil }
        }
        .onChange(of: connection?.isVerified) { previous, verified in
            if verified == true, previous != true {
                withAnimation { showingConnectedNotice = true }
            } else if verified != true {
                showingConnectedNotice = false
            }
        }
        .task(id: showingConnectedNotice) {
            guard showingConnectedNotice else { return }
            do { try await Task.sleep(for: .seconds(4)) }
            catch { return }
            withAnimation { showingConnectedNotice = false }
        }
        .task { connection = await model.museConnection() }
        .task(id: verificationPollKey) {
            guard scenePhase == .active, connection?.connected == true,
                  connection?.isVerified != true else { return }
            // Check only while this setup page is visible and awaiting Muse.
            // An authenticated read verifies the key without creating a post.
            for _ in 0..<30 {
                do { try await Task.sleep(for: .seconds(4)) }
                catch { return }
                guard !Task.isCancelled, scenePhase == .active else { return }
                if let latest = await model.museConnection(reportFailure: false) {
                    connection = latest
                    if latest.isVerified || !latest.connected { return }
                }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { connection = await model.museConnection() } }
        }
        .confirmationDialog("Disconnect Muse?", isPresented: $confirmingDisconnect, titleVisibility: .visible) {
            Button("Disconnect", role: .destructive) {
                Task {
                    working = true
                    if await model.disconnectMuse() { connection = await model.museConnection() }
                    working = false
                }
            }
            Button("Cancel", role: .cancel) { }
        } message: { Text("Further deliveries will be blocked. You can create a new connection key later. Existing posts remain in your account.") }
        .confirmationDialog("Replace the connection key?", isPresented: $confirmingReplace, titleVisibility: .visible) {
            Button("Replace key") { Task { await create() } }
            Button("Cancel", role: .cancel) { }
        } message: { Text("The old key will stop working. Add the new key in Muse to resume delivery.") }
    }

    private var statusTitle: String {
        guard let connection else { return "Muse connection" }
        if !connection.connected { return "Not connected" }
        if !connection.isVerified { return "Finish setup in Muse" }
        return connection.lastDeliveryAt == nil ? "Connected to Muse" : "Receiving from Muse"
    }

    private func create() async {
        working = true
        defer { working = false }
        guard await model.createMuseConnection() != nil else { return }
        connection = await model.museConnection()
        openSetup()
    }

    private func openSetup() {
        guard let connection, let prompt = MuseSetupPrompt.make(schema: connection.schemaUrl) else { return }
        copyText(prompt)
        showSetupNotice("Setup request copied. Paste it in Muse; provide the key only in secure setup.")
        MuseHandoff.openChat { showSetupNotice("Request copied. Open Muse and paste it into a chat.") }
    }

    private func showSetupNotice(_ text: String) {
        setupNotice = text
        setupNoticeVersion += 1
    }
}
