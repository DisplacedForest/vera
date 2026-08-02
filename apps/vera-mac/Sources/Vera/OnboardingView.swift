import SwiftUI

struct OnboardingSheet: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var store: ChatStore
    @State private var connecting = false
    @State private var error: String?
    @State private var veinsBase: URL?
    @State private var discovery: String?

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
                    Text("Point the app at an OpenAI-compatible model endpoint, and you're chatting.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                field("Model endpoint", "model_base", placeholder: "http://my-model-host:11434/v1")
                field("API key (optional)", "model_api_key", secure: true)
                field("Model id", "model", placeholder: "your-model-id")
                field("vera-api URL (optional)", "vera_api_base", placeholder: "http://my-api-host:8089")
                HStack(spacing: 10) {
                    Button("Discover models") { discoverModels() }
                    if let discovery {
                        Text(discovery).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    }
                    Spacer()
                }
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
                Button(connecting ? "Connecting…" : "Connect") { connect() }
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
        guard filled("model_base") else {
            error = "A model endpoint ending in /v1 is required"
            return
        }
        guard filled("model") else {
            error = "Choose a discovered model or enter its id"
            return
        }
        connecting = true
        Task {
            defer { connecting = false }
            do { try config.save() } catch {
                self.error = "Couldn't write ~/.vera/config.json: \(error.localizedDescription)"
                return
            }
            guard let resolved = config.nativeResolved else {
                self.error = "The model endpoint must be an HTTP or HTTPS URL ending in /v1"
                return
            }
            store.adoptNative(resolved)
            if let base = config.veraAPIBase {
                veinsBase = base
            } else {
                config.showOnboarding = false
            }
        }
    }

    private func discoverModels() {
        discovery = "Checking…"
        error = nil
        Task {
            do {
                let models = try await ConnectionTest.models(
                    base: config["model_base"], apiKey: config["model_api_key"])
                if config["model"].isEmpty, let first = models.first { config["model"] = first }
                discovery = models.isEmpty ? "No models returned. Enter an id manually." : "Found \(models.count). Selected \(config["model"])."
            } catch {
                discovery = "Discovery unavailable. Enter an id manually."
                self.error = error.localizedDescription
            }
        }
    }
}
