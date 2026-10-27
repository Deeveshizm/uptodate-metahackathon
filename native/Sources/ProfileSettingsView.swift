import SwiftUI

struct ProfileSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var timezone = "UTC"

    var body: some View {
        NavigationStack {
            Form {
                TextField("Display name (optional)", text: $name)
                Picker("Timezone", selection: $timezone) {
                    ForEach(TimeZone.knownTimeZoneIdentifiers, id: \.self) { Text($0).tag($0) }
                }
                Button("Save") {
                    Task { if await model.updateProfile(name: name, timezone: timezone) { dismiss() } }
                }.disabled(model.busy || name.count > 100)
                if let error = model.error { Text(error).foregroundStyle(.red) }
            }
            .navigationTitle("Edit profile")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
        .onAppear {
            name = model.account?.displayName ?? ""
            timezone = model.account?.timezone ?? "UTC"
        }
    }
}
