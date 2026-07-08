import SwiftUI

/// First-run sheet, shown when no usable config exists (instead of a silently dead UI).
/// Collects the OWUI connection + vera-api URL, tests, saves, and connects live; with a
/// vera-api URL set, a second page offers the vein catalog (fully skippable — skip = an
/// empty chip row). Skippable — the empty chat state offers to reopen it.
struct OnboardingSheet: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var store: ChatStore
    @State private var connecting = false
    @State private var error: String?
    @State private var veinsBase: URL?

    var body: some View {
        if let base = veinsBase {
            VeinsOnboardingStep(base: base) { config.showOnboarding = false }
                .padding(24).frame(width: 480)
        } else {
            connectionPage
        }
    }

    private var connectionPage: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                VeraMark(size: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Welcome to Vera").font(.system(size: 20, weight: .semibold))
                    Text("Point the app at your Open WebUI and vera-api, and you're chatting.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                field("Open WebUI URL", "base", placeholder: "http://my-owui-host:6590")
                field("Open WebUI email", "owui_email", placeholder: "you@example.com")
                field("Open WebUI password", "owui_password", secure: true)
                field("Open WebUI API key", "api_key", secure: true)
                field("Model id (as registered in OWUI)", "model", placeholder: "your-vera-model")
                field("vera-api URL (optional)", "vera_api_base", placeholder: "http://my-api-host:8089")
            }

            if let error {
                Label(error, systemImage: "xmark.circle.fill")
                    .font(.system(size: 12)).foregroundStyle(.red)
            }

            HStack {
                Spacer()
                Button("Skip for now") { config.showOnboarding = false }
                    .buttonStyle(.plain).font(.system(size: 12))
                    .foregroundStyle(Theme.textSecondary)
                Button(connecting ? "Connecting…" : "Test & Connect") { connect() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(connecting)
            }
        }
        .padding(24).frame(width: 480)
    }

    private func field(_ label: String, _ key: String, secure: Bool = false, placeholder: String = "") -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.textSecondary)
            if secure {
                SecureField("", text: config.binding(key))
                    .textFieldStyle(.roundedBorder)
            } else {
                TextField("", text: config.binding(key), prompt: Text(placeholder))
                    .textFieldStyle(.roundedBorder).autocorrectionDisabled()
            }
        }
    }

    private func connect() {
        error = nil
        func filled(_ key: String) -> Bool {
            !config[key].trimmingCharacters(in: .whitespaces).isEmpty
        }
        guard filled("base"), filled("api_key") else {
            error = "The OWUI URL and API key are required"
            return
        }
        guard filled("model") else {
            error = "The model id is required (the id your Vera model has in OWUI)"
            return
        }
        connecting = true
        Task {
            defer { connecting = false }
            // Exercise the sign-in path the live socket uses (credentials are how chat streams).
            if !config["owui_email"].isEmpty || !config["owui_password"].isEmpty {
                do {
                    _ = try await ConnectionTest.owui(base: config["base"],
                                                      email: config["owui_email"],
                                                      password: config["owui_password"])
                } catch {
                    self.error = error.localizedDescription
                    return
                }
            }
            do { try config.save() } catch {
                self.error = "Couldn't write ~/.vera/config.json: \(error.localizedDescription)"
                return
            }
            guard let resolved = config.resolved else {
                self.error = "The OWUI URL doesn't parse. Check it and try again"
                return
            }
            store.adopt(resolved)
            // With vera-api configured, offer the vein catalog as the next step;
            // without it there's nothing to pick — finish here.
            if let base = resolved.veraAPIBase {
                veinsBase = base
            } else {
                config.showOnboarding = false
            }
        }
    }
}
