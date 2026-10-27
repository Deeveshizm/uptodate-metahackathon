import SwiftUI

struct AuthView: View {
    @EnvironmentObject private var model: AppModel
    private enum Step { case signIn, signUp, confirm, recovery, reset }
    @State private var step: Step = .signIn
    @State private var email = ""
    @State private var password = ""
    @State private var repeatedPassword = ""

    private var address: String { email.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var newPasswordValid: Bool {
        password.count >= 12 && password.utf8.count <= 72 && password == repeatedPassword
    }
    private var title: String {
        switch step {
        case .signIn: "Welcome back"
        case .signUp: "Create your account"
        case .confirm: "Verify your email"
        case .recovery: "Reset your password"
        case .reset: "Choose a new password"
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text(FeedStyle.wordmark).font(.system(size: 38, weight: .semibold, design: .serif).italic())
                if model.authConfiguration?.mode == "local" {
                    Text("Local development mode").font(.title2)
                    Text("Start a prototype account to test imports and local generation. Verified accounts require Supabase configuration.")
                        .foregroundStyle(.secondary)
                    Button("Start local prototype") { Task { await model.start() } }
                        .buttonStyle(.borderedProminent).disabled(model.busy)
                } else if model.authConfiguration == nil {
                    Text("Connecting to your account service…")
                    Button("Retry") { Task { await model.loadAuthConfiguration() } }
                } else {
                    Text(title).font(.title2.bold())
                    if step == .confirm {
                        Text("Open the confirmation link sent to \(model.pendingEmail ?? address) on this device. It will return you to UpToDate.")
                    } else if step == .reset {
                        Text(model.recoveryReady ? "Your email is verified. Choose a new password." : "If an account exists for \(address), a recovery link has been sent. Open it on this device to continue.")
                    } else {
                        TextField("Email address", text: $email).textContentType(.emailAddress)
                            .autocorrectionDisabled()
                        #if os(iOS)
                            .textInputAutocapitalization(.never).keyboardType(.emailAddress)
                        #endif
                    }
                    if [.signIn, .signUp].contains(step) || (step == .reset && model.recoveryReady) {
                        SecureField(step == .reset ? "New password" : "Password", text: $password)
                            .textContentType(step == .signIn ? .password : .newPassword)
                    }
                    if step == .signUp || (step == .reset && model.recoveryReady) {
                        SecureField("Confirm password", text: $repeatedPassword).textContentType(.newPassword)
                        Text("Use a passphrase with at least 12 characters.").font(.caption).foregroundStyle(.secondary)
                        if password.utf8.count > 72 {
                            Text("This password is too long. Please use a shorter passphrase.").font(.caption).foregroundStyle(.red)
                        }
                        if !repeatedPassword.isEmpty && password != repeatedPassword {
                            Text("Passwords do not match.").font(.caption).foregroundStyle(.red)
                        }
                    }
                    switch step {
                    case .signIn:
                        Button("Sign in") { Task { await model.signIn(email: address, password: password); password = "" } }
                            .buttonStyle(.borderedProminent).disabled(address.isEmpty || password.isEmpty || model.busy)
                        Button("Create an account") { changeStep(.signUp) }
                        Button("Forgot password?") { changeStep(.recovery) }
                    case .signUp:
                        Button("Create account") {
                            Task {
                                if await model.signUp(email: address, password: password) { changeStep(.confirm) }
                                password = ""; repeatedPassword = ""
                            }
                        }.buttonStyle(.borderedProminent).disabled(address.isEmpty || !newPasswordValid || model.busy)
                        Button("Already have an account? Sign in") { changeStep(.signIn) }
                    case .confirm:
                        Button("Resend confirmation link") { Task { await model.resendConfirmation(email: address) } }
                        Button("Back to sign in") { model.cancelEmailFlow(); changeStep(.signIn) }
                    case .recovery:
                        Button("Send recovery link") {
                            Task { if await model.requestRecovery(email: address) { changeStep(.reset) } }
                        }.buttonStyle(.borderedProminent).disabled(address.isEmpty || model.busy)
                        Button("Back to sign in") { changeStep(.signIn) }
                    case .reset:
                        if model.recoveryReady {
                            Button("Update password") {
                                Task { await model.recoverAccount(password: password); password = ""; repeatedPassword = "" }
                            }.buttonStyle(.borderedProminent).disabled(!newPasswordValid || model.busy)
                        }
                        Button("Request another link") { model.cancelEmailFlow(); changeStep(.recovery) }
                        Button("Back to sign in") { model.cancelEmailFlow(); changeStep(.signIn) }
                    }
                    Text("Your sign-in email is separate from your newsletter address.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if model.busy { ProgressView() }
                if let notice = model.notice { Text(notice).font(.caption) }
            }
            .textFieldStyle(.roundedBorder)
            .disabled(model.busy)
            .padding(30).frame(maxWidth: 500)
        }
        .onDisappear { password = ""; repeatedPassword = "" }
        .onAppear {
            if let pending = model.pendingEmail { email = pending }
            if model.recoveryReady || model.pendingAuthKind == .recovery { step = .reset }
            else if model.pendingAuthKind == .signup { step = .confirm }
        }
        .onChange(of: model.recoveryReady) { _, ready in
            if ready { changeStep(.reset) }
        }
    }

    private func changeStep(_ next: Step) {
        step = next; password = ""; repeatedPassword = ""
        model.notice = nil; model.error = nil
    }
}
