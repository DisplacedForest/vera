import SwiftUI

struct OnboardingSheet: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var store: ChatStore
    @State private var step = 0
    @State private var loaded = false
    @State private var error: String?

    private let steps = ["Endpoint", "Model", "Persona", "Tools"]

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                VeraMark(size: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Set up Vera").font(.system(size: 20, weight: .semibold))
                    Text("Connect a model, choose how Vera responds, and review available tools.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
            }

            HStack(spacing: 8) {
                ForEach(Array(steps.enumerated()), id: \.offset) { index, title in
                    HStack(spacing: 5) {
                        Image(systemName: index < step ? "checkmark.circle.fill" : "\(index + 1).circle.fill")
                        Text(title)
                    }
                    .font(.system(size: 11, weight: index == step ? .semibold : .regular))
                    .foregroundStyle(index <= step ? Theme.accent : Theme.textSecondary)
                    if index < steps.count - 1 { Divider().frame(width: 18, height: 1) }
                }
            }

            ScrollView {
                Group {
                    switch step {
                    case 0: NativeEndpointEditor()
                    case 1: NativeModelEditor()
                    case 2: NativePersonaEditor()
                    default: NativeToolEditor()
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .background(Theme.surface)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }

            if let error {
                Label(error, systemImage: "xmark.circle.fill")
                    .font(.system(size: 12)).foregroundStyle(.red)
            }

            HStack {
                Button("Skip for now") { config.skipOnboarding() }
                    .buttonStyle(.plain).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                Spacer()
                if step > 0 { Button("Back") { move(to: step - 1) } }
                Button(step == steps.count - 1 ? "Finish" : "Continue") { advance() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 620, height: 620)
        .onAppear {
            guard !loaded else { return }
            loaded = true
            step = min(max(config.nativeSettings.onboardingStep, 0), steps.count - 1)
            if config.nativeSettings.onboardingState != .inProgress {
                config.updateOnboarding(step: step)
            }
        }
    }

    private func advance() {
        error = nil
        switch step {
        case 0:
            guard let profile = config.activeNativeProfile,
                  let url = URL(string: profile.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)),
                  url.scheme == "http" || url.scheme == "https",
                  url.lastPathComponent == "v1" else {
                error = "Enter an HTTP or HTTPS model endpoint ending in /v1."
                return
            }
        case 1:
            guard config.activeNativeProfile?.selectedModel.isEmpty == false else {
                error = "Choose one of the discovered model identifiers."
                return
            }
        case 2:
            guard !config.nativeSettings.systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                error = "Keep a system prompt or reset it to Vera’s default."
                return
            }
        default:
            finish()
            return
        }
        move(to: step + 1)
    }

    private func move(to value: Int) {
        step = value
        config.updateOnboarding(step: value)
    }

    private func finish() {
        do { try config.save() } catch let saveError {
            error = "Vera could not save these settings: \(saveError.localizedDescription)"
            return
        }
        guard let resolved = config.nativeResolved else {
            error = "Choose a valid endpoint and model before finishing."
            return
        }
        store.adoptNative(
            resolved,
            systemPrompt: config.nativeSettings.systemPrompt,
            enabledToolIDs: config.nativeSettings.enabledToolIDs)
        config.completeOnboarding()
    }
}
