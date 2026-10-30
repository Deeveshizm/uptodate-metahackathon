import SwiftUI

struct AuthView: View {
    @EnvironmentObject private var model: AppModel
    private enum Step { case signIn, signUp, confirm, recovery, reset }
    private enum Field { case email, password, confirmation }
    @State private var step: Step = .signIn
    @State private var email = ""
    @State private var password = ""
    @State private var repeatedPassword = ""
    @FocusState private var focused: Field?

    private var address: String { email.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var newPasswordValid: Bool {
        PasswordPolicy.accepts(password, confirmation: repeatedPassword)
    }
    private var title: String {
        switch step {
        case .signIn: "Welcome back"
        case .signUp: "Create your account"
        case .confirm: "Check your email"
        case .recovery: "Reset your password"
        case .reset: model.recoveryReady ? "Choose a new password" : "Check your email"
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                VStack(alignment: .leading, spacing: 10) {
                    Text(FeedStyle.wordmark).font(.system(size: 34, weight: .semibold)).tracking(-1)
                    Text("Your newsletters, without the inbox.")
                        .font(.system(size: 15)).foregroundStyle(.secondary)
                }.padding(.bottom, 24)
                if model.authConfiguration?.mode == "local" {
                    Text("Local preview").font(.system(size: 26, weight: .semibold))
                    primary("Continue") { Task { await model.start() } }
                } else if model.authConfiguration == nil {
                    ProgressView("Connecting…")
                    secondary("Retry") { Task { await model.loadAuthConfiguration() } }
                } else {
                    Text(title).font(.system(size: 26, weight: .semibold)).tracking(-0.6)
                    if step == .confirm || step == .reset {
                        Text(model.recoveryReady ? "Enter and confirm your new password below." :
                             "Open the link sent to \(model.pendingEmail ?? address) on this device to continue.")
                            .font(.system(size: 14)).foregroundStyle(.secondary).lineSpacing(4)
                    }
                    VStack(spacing: 14) {
                        if [.signIn, .signUp, .recovery].contains(step) {
                            field(symbol: "envelope") {
                                TextField("Email address", text: $email)
                                    .textContentType(.emailAddress).autocorrectionDisabled().focused($focused, equals: .email)
                                    #if os(iOS)
                                    .textInputAutocapitalization(.never).keyboardType(.emailAddress)
                                    #endif
                                    .submitLabel(.next).onSubmit { focused = .password }
                            }
                        }
                        if [.signIn, .signUp].contains(step) || (step == .reset && model.recoveryReady) {
                            field(symbol: "lock") {
                                SecureField(step == .reset ? "New password" : "Password", text: $password)
                                    .textContentType(step == .signIn ? .password : .newPassword)
                                    .focused($focused, equals: .password)
                                    .submitLabel(step == .signIn ? .go : .next)
                                    .onSubmit {
                                        if step == .signIn { signIn() } else { focused = .confirmation }
                                    }
                            }
                        }
                        if step == .signUp || (step == .reset && model.recoveryReady) {
                            field(symbol: "lock.badge.checkmark") {
                                SecureField("Confirm password", text: $repeatedPassword)
                                    .textContentType(.newPassword).focused($focused, equals: .confirmation)
                            }
                            passwordChecklist
                            if password.utf8.count > PasswordPolicy.maximumUTF8Bytes {
                                validation("This password is too long. Use a shorter passphrase.")
                            }
                        }
                    }
                    actions
                    if [.signIn, .signUp].contains(step) {
                        Text("Newsletter subscriptions use a separate private address.")
                            .font(.system(size: 12)).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity).multilineTextAlignment(.center)
                    }
                }
                if model.busy { ProgressView().frame(maxWidth: .infinity) }
                if let notice = model.notice {
                    Text(notice).font(.system(size: 13)).foregroundStyle(.secondary).lineSpacing(3)
                }
            }
            .disabled(model.busy)
            .frame(maxWidth: 420).padding(.horizontal, 26).padding(.top, 72).padding(.bottom, 36)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively).background(.background)
        .onDisappear { password = ""; repeatedPassword = "" }
        .onAppear {
            if let pending = model.pendingEmail { email = pending }
            if model.recoveryReady || model.pendingAuthKind == .recovery { step = .reset }
            else if model.pendingAuthKind == .signup { step = .confirm }
        }
        .onChange(of: model.recoveryReady) { _, ready in if ready { changeStep(.reset) } }
    }

    private var passwordChecklist: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(PasswordRequirement.allCases) { requirement in
                let satisfied = requirement.isMet(password: password, confirmation: repeatedPassword)
                HStack(spacing: 8) {
                    Image(systemName: satisfied ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: 12)).frame(width: 15)
                    Text(requirement.title).font(.system(size: 12))
                }.foregroundStyle(satisfied ? Color.green : Color.secondary)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(requirement.title + (satisfied ? ", met" : ", not yet met"))
                    .accessibilityIdentifier("auth.password.\(requirement.rawValue)")
            }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 8).padding(.top, 2)
    }

    @ViewBuilder private var actions: some View {
        VStack(spacing: 18) {
            switch step {
            case .signIn:
                HStack { Spacer(); secondary("Forgot password?") { changeStep(.recovery) } }.padding(.top, -10)
                primary("Sign in", disabled: address.isEmpty || password.isEmpty) { signIn() }
                secondary("Create an account") { changeStep(.signUp) }
            case .signUp:
                primary("Create account", disabled: address.isEmpty || !newPasswordValid) {
                    focused = nil
                    Task {
                        if await model.signUp(email: address, password: password) { changeStep(.confirm) }
                        password = ""; repeatedPassword = ""
                    }
                }
                secondary("Already have an account? Sign in") { changeStep(.signIn) }
            case .confirm:
                primary("Resend confirmation link") { Task { await model.resendConfirmation(email: model.pendingEmail ?? address) } }
                secondary("Back to sign in") { model.cancelEmailFlow(); changeStep(.signIn) }
            case .recovery:
                primary("Send recovery link", disabled: address.isEmpty) {
                    focused = nil
                    Task { if await model.requestRecovery(email: address) { changeStep(.reset) } }
                }
                secondary("Back to sign in") { changeStep(.signIn) }
            case .reset:
                if model.recoveryReady {
                    primary("Update password", disabled: !newPasswordValid) {
                        focused = nil
                        Task { await model.recoverAccount(password: password); password = ""; repeatedPassword = "" }
                    }
                }
                secondary("Request another link") { model.cancelEmailFlow(); changeStep(.recovery) }
                secondary("Back to sign in") { model.cancelEmailFlow(); changeStep(.signIn) }
            }
        }
    }
    private func field<Content: View>(symbol: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).font(.system(size: 15)).foregroundStyle(.secondary).frame(width: 20)
            content().font(.system(size: 15)).textFieldStyle(.plain)
        }.padding(.horizontal, 18).padding(.vertical, 17)
            .background(.primary.opacity(0.05), in: Capsule())
            .overlay(Capsule().stroke(.primary.opacity(0.09), lineWidth: 0.5))
    }
    private func primary(_ title: String, disabled: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.system(size: 15 * FeedStyle.buttonScale, weight: .semibold))
                .foregroundStyle(.background).frame(maxWidth: .infinity).padding(.vertical, 17 * FeedStyle.buttonScale)
                .background(.primary, in: Capsule())
        }.buttonStyle(.plain).disabled(disabled).opacity(disabled ? 0.45 : 1)
    }
    private func secondary(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action).font(.system(size: 13, weight: .medium)).buttonStyle(.plain)
            .frame(minHeight: 30)
    }
    private func validation(_ text: String) -> some View {
        Text(text).font(.system(size: 12)).foregroundStyle(.red).frame(maxWidth: .infinity, alignment: .leading)
    }
    private func signIn() {
        guard !address.isEmpty, !password.isEmpty, !model.busy else { return }
        focused = nil
        Task { await model.signIn(email: address, password: password); password = "" }
    }
    private func changeStep(_ next: Step) {
        step = next; password = ""; repeatedPassword = ""; focused = nil
        model.notice = nil; model.error = nil
    }
}
