import SwiftUI

/// Reads and writes `~/.vera/config.json` — the single on-disk config the app and its clients
/// share. Values are flat string keys; unknown keys in the file are preserved on save.
/// Environment variables always win over file values at resolve time (the file is the editable
/// layer underneath).
enum ConfigFile {
    /// `~/.vera/config.json`, or `$VERA_CONFIG_DIR/config.json` when set — the override
    /// supports test sandboxes and non-standard layouts without touching the real config.
    static var defaultURL: URL {
        if let dir = ProcessInfo.processInfo.environment["VERA_CONFIG_DIR"], !dir.isEmpty {
            return URL(fileURLWithPath: dir).appendingPathComponent("config.json")
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".vera/config.json")
    }

    static func read(at url: URL = defaultURL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return obj
    }

    /// Atomic write, creating the parent directory if needed.
    static func write(_ raw: [String: Any], at url: URL = defaultURL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        let data = try JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url, options: .atomic)
    }
}

@MainActor
final class ConfigStore: ObservableObject {
    /// Raw file contents. String values are editable in Settings; unknown keys are preserved.
    @Published private(set) var raw: [String: Any]
    @Published var showOnboarding: Bool
    @Published var nativeSettings: NativeChatSettings
    @Published private var nativeAPIKeys: [String: String]
    @Published var visionBridgeAPIKey: String
    @Published private(set) var capabilityTools: NativeCapabilityToolCatalog
    private let credentialStore: any NativeCredentialStore

    static let visionBridgeCredentialID = "vision-bridge"

    /// THE `~/.vera/config.json` key ↔ environment-variable mapping (env wins at resolve
    /// time). Canonical names match the backend's convention (`OWUI_KEY`); deprecated
    /// aliases keep working for one release.
    static let envNames: [String: String] = [
        "model_base": "VERA_MODEL_BASE", "model_api_key": "VERA_MODEL_API_KEY",
        "base": "OWUI_BASE", "api_key": "OWUI_KEY", "model": "VERA_MODEL",
        "completions_url": "VERA_COMPLETIONS_URL", "voice_base": "VERA_VOICE_BASE",
        "vera_api_base": "VERA_API_BASE", "owui_email": "OWUI_EMAIL",
        "owui_password": "OWUI_PASSWORD", "owner_name": "VERA_OWNER_NAME",
        "chat_template_kwargs": "VERA_CHAT_TEMPLATE_KWARGS",
    ]
    /// canonical env name → deprecated alias still honored this release.
    static let envAliases: [String: String] = ["OWUI_KEY": "OWUI_API_KEY"]

    init(credentialStore: any NativeCredentialStore = KeychainNativeCredentialStore()) {
        let loaded = ConfigFile.read()
        let settings = NativeChatSettings.load(from: loaded)
        var keys: [String: String] = [:]
        for profile in settings.profiles {
            if let value = credentialStore.value(for: profile.id), !value.isEmpty {
                keys[profile.id] = value
            }
        }
        if let profile = settings.activeProfile,
           keys[profile.id] == nil,
           let legacy = loaded["model_api_key"] as? String,
           !legacy.isEmpty {
            keys[profile.id] = legacy
        }
        raw = loaded
        nativeSettings = settings
        nativeAPIKeys = keys
        visionBridgeAPIKey = credentialStore.value(for: Self.visionBridgeCredentialID) ?? ""
        capabilityTools = NativeCapabilityTools.catalog()
        self.credentialStore = credentialStore
        showOnboarding = !settings.hasValidConfiguration
            && settings.onboardingState != .skipped
            && settings.onboardingState != .complete
        try? save()
    }

    subscript(_ key: String) -> String {
        get { (raw[key] as? String) ?? "" }
        set {
            if newValue.isEmpty { raw.removeValue(forKey: key) } else { raw[key] = newValue }
        }
    }

    /// A SwiftUI binding onto one file key (edits are in-memory until `save()`).
    func binding(_ key: String) -> Binding<String> {
        Binding(get: { self[key] }, set: { self[key] = $0 })
    }

    /// The app-wide appearance override from the `appearance` key: `"light"`/`"dark"` pin a
    /// scheme, anything else follows the system (nil).
    var colorSchemeOverride: ColorScheme? {
        switch self["appearance"] {
        case "light": return .light
        case "dark": return .dark
        default: return nil
        }
    }

    /// The active environment override for a key, if one is set (it wins over the file value).
    func envOverride(_ key: String) -> String? {
        guard let name = Self.envNames[key] else { return nil }
        let env = ProcessInfo.processInfo.environment
        if let v = env[name], !v.isEmpty { return name }
        if let old = Self.envAliases[name], let v = env[old], !v.isEmpty { return old }
        return nil
    }

    /// Persist the current values to disk.
    func save() throws {
        for profile in nativeSettings.profiles {
            try credentialStore.set(nativeAPIKeys[profile.id], for: profile.id)
        }
        try credentialStore.set(visionBridgeAPIKey, for: Self.visionBridgeCredentialID)
        raw = nativeSettings.merging(into: raw)
        try ConfigFile.write(raw)
        capabilityTools = NativeCapabilityTools.catalog()
    }

    func reloadCapabilityTools() {
        capabilityTools = NativeCapabilityTools.catalog()
    }

    /// The fully resolved connection config (env over file). Nil until OWUI base + key exist.
    var resolved: OWUIConfig? { OWUIConfig.load() }

    var nativeResolved: NativeChatConfig? {
        let env = ProcessInfo.processInfo.environment
        let profile = nativeSettings.activeProfile
        let rawBase = env["VERA_MODEL_BASE"]?.nilIfBlank ?? profile?.baseURL.nilIfBlank
        let model = env["VERA_MODEL"]?.nilIfBlank ?? profile?.selectedModel.nilIfBlank
        guard let rawBase,
              let base = URL(string: rawBase),
              base.scheme == "http" || base.scheme == "https",
              base.lastPathComponent == "v1",
              let model else { return nil }
        let apiKey = env["VERA_MODEL_API_KEY"]?.nilIfBlank
            ?? profile.flatMap { nativeAPIKeys[$0.id]?.nilIfBlank }
        let kwargs = env["VERA_CHAT_TEMPLATE_KWARGS"]?.nilIfBlank
            ?? self["chat_template_kwargs"].nilIfBlank
        return NativeChatConfig(
            baseURL: base, apiKey: apiKey, model: model, chatTemplateKwargs: kwargs,
            streaming: nativeSettings.resolveCapabilities(model: model).profile.supportsStreaming)
    }

    var activeNativeProfile: NativeEndpointProfile? { nativeSettings.activeProfile }
    var activeNativeAPIKey: String {
        guard let id = nativeSettings.activeProfileID else { return "" }
        return nativeAPIKeys[id] ?? ""
    }
    var nativeDiscoveryConfiguration: NativeDiscoveryConfiguration {
        NativeChatConfigurationResolver.discovery(
            profile: activeNativeProfile,
            apiKey: activeNativeAPIKey,
            environment: ProcessInfo.processInfo.environment)
    }

    var visionBridgeResolved: VisionBridgeConfig? {
        VisionBridgeConfig.resolve(
            settings: nativeSettings.visionBridge,
            apiKey: visionBridgeAPIKey,
            environment: ProcessInfo.processInfo.environment)
    }

    var nativeMemoryService: NativeMemoryService? {
        guard let config = nativeResolved else { return nil }
        return NativeMemoryService(configuration: NativeMemoryServiceConfiguration(
            baseURL: config.baseURL, apiKey: config.apiKey,
            embeddingsModel: nativeSettings.memory.embeddingsModel,
            extractionModel: nativeSettings.memory.extractionModel))
    }

    func activeProfileBinding(_ keyPath: WritableKeyPath<NativeEndpointProfile, String>) -> Binding<String> {
        Binding(
            get: { self.activeNativeProfile?[keyPath: keyPath] ?? "" },
            set: { value in self.updateActiveNativeProfile { $0[keyPath: keyPath] = value } })
    }

    func selectProfile(_ id: String) {
        var updated = nativeSettings
        updated.activeProfileID = id
        nativeSettings = updated
    }

    func addNativeProfile() {
        var updated = nativeSettings
        updated.addProfile()
        nativeSettings = updated
    }

    func updateActiveNativeProfile(_ update: (inout NativeEndpointProfile) -> Void) {
        var updated = nativeSettings
        updated.updateActiveProfile(update)
        nativeSettings = updated
    }

    func cacheDiscoveredModels(_ models: [String]) {
        var updated = nativeSettings
        updated.cacheModels(models)
        nativeSettings = updated
    }

    func selectNativeModel(_ model: String) {
        var updated = nativeSettings
        updated.selectModel(model, basis: .user)
        nativeSettings = updated
    }

    func apiKeyBinding() -> Binding<String> {
        Binding(
            get: {
                guard let id = self.nativeSettings.activeProfileID else { return "" }
                return self.nativeAPIKeys[id] ?? ""
            },
            set: { value in
                guard let id = self.nativeSettings.activeProfileID else { return }
                self.nativeAPIKeys[id] = value
            })
    }

    func systemPromptBinding() -> Binding<String> {
        Binding(
            get: { self.nativeSettings.systemPrompt },
            set: { value in
                var updated = self.nativeSettings
                updated.systemPrompt = value
                self.nativeSettings = updated
            })
    }

    func resetSystemPrompt() {
        var updated = nativeSettings
        updated.resetSystemPrompt()
        nativeSettings = updated
    }

    func setNativeTool(_ id: String, enabled: Bool) {
        var updated = nativeSettings
        if enabled { updated.enabledToolIDs.insert(id) } else { updated.enabledToolIDs.remove(id) }
        nativeSettings = updated
        if enabled, id == "apple-reminders" {
            Task { _ = await RemindersBridge.shared.nativeRequestAccess() }
        }
    }

    func visionBridgeBinding(_ keyPath: WritableKeyPath<VisionBridgeSettings, String>) -> Binding<String> {
        Binding(
            get: { self.nativeSettings.visionBridge[keyPath: keyPath] },
            set: { value in
                var updated = self.nativeSettings
                updated.visionBridge[keyPath: keyPath] = value
                self.nativeSettings = updated
            })
    }

    func updateCapability(model: String, _ update: (inout ModelCapabilityProfile) -> Void) {
        var updated = nativeSettings
        var profile = updated.resolveCapabilities(model: model).profile
        update(&profile)
        updated.setCapabilityOverride(model: model, profile: profile)
        nativeSettings = updated
    }

    func clearCapabilityOverride(model: String) {
        var updated = nativeSettings
        updated.clearCapabilityOverride(model: model)
        nativeSettings = updated
    }

    func updateMemory(_ update: (inout NativeMemorySettings) -> Void) {
        var settings = nativeSettings
        update(&settings.memory)
        nativeSettings = settings
    }

    func memoryStringBinding(_ keyPath: WritableKeyPath<NativeMemorySettings, String>) -> Binding<String> {
        Binding(
            get: { self.nativeSettings.memory[keyPath: keyPath] },
            set: { value in self.updateMemory { $0[keyPath: keyPath] = value } })
    }

    func memoryBoolBinding(_ keyPath: WritableKeyPath<NativeMemorySettings, Bool>) -> Binding<Bool> {
        Binding(
            get: { self.nativeSettings.memory[keyPath: keyPath] },
            set: { value in self.updateMemory { $0[keyPath: keyPath] = value } })
    }

    func beginOnboarding(at step: Int? = nil) {
        var updated = nativeSettings
        let target = step ?? updated.onboardingStep
        updated.onboardingState = .inProgress
        updated.onboardingStep = target
        nativeSettings = updated
        showOnboarding = true
        try? save()
    }

    func updateOnboarding(step: Int) {
        var updated = nativeSettings
        updated.onboardingState = .inProgress
        updated.onboardingStep = step
        nativeSettings = updated
        try? save()
    }

    func skipOnboarding() {
        var updated = nativeSettings
        updated.onboardingState = .skipped
        nativeSettings = updated
        showOnboarding = false
        try? save()
    }

    func completeOnboarding() {
        var updated = nativeSettings
        updated.onboardingState = .complete
        updated.onboardingStep = 0
        nativeSettings = updated
        showOnboarding = false
        try? save()
    }

    var veraAPIBase: URL? {
        OWUIConfig.resolvedVeraAPIBase()
    }

    /// The person's name — drives the greeting and the sidebar chip. Nil when unset.
    var ownerName: String? {
        let env = ProcessInfo.processInfo.environment["VERA_OWNER_NAME"]
        let v = (env?.isEmpty == false ? env : nil) ?? (raw["owner_name"] as? String)
        let trimmed = v?.trimmingCharacters(in: .whitespaces)
        return (trimmed?.isEmpty == false) ? trimmed : nil
    }
}

/// One-shot connectivity checks used by Settings and onboarding. Each returns a short
/// human-readable success line or throws with a human-readable failure.
enum ConnectionTest {
    enum TestError: Error, LocalizedError {
        case message(String)
        var errorDescription: String? { switch self { case .message(let m): return m } }
    }

    /// Exercise an OWUI sign-in (the credential path the live socket uses).
    static func owui(base: String, email: String, password: String) async throws -> String {
        guard let url = URL(string: base), url.scheme != nil else {
            throw TestError.message("Enter a valid OWUI URL first")
        }
        guard !email.isEmpty, !password.isEmpty else {
            throw TestError.message("Enter the OWUI email and password")
        }
        var req = URLRequest(url: url.appendingPathComponent("api/v1/auths/signin"))
        req.httpMethod = "POST"
        req.timeoutInterval = 8
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["email": email, "password": password])
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              obj["token"] is String else {
            throw TestError.message("Sign-in failed (HTTP \(code))")
        }
        let name = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["name"] as? String
        return "Signed in" + (name.map { " as \($0)" } ?? "")
    }

    static func models(base: String, apiKey: String) async throws -> [String] {
        let trimmed = base.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed),
              url.scheme == "http" || url.scheme == "https",
              url.lastPathComponent == "v1" else {
            throw TestError.message("Enter a valid model endpoint ending in /v1")
        }
        let config = NativeChatConfig(
            baseURL: url,
            apiKey: apiKey.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
            model: "discovery",
            chatTemplateKwargs: nil)
        return try await NativeChatClient(config: config).discoverModels()
    }

    /// Probe a service health endpoint — any 2xx counts as alive.
    static func http(base: String, path: String, label: String) async throws -> String {
        guard let url = URL(string: base)?.appendingPathComponent(path), url.scheme != nil else {
            throw TestError.message("Enter a valid \(label) URL first")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 6
        let (_, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw TestError.message("\(label) answered HTTP \(code)")
        }
        return "\(label) is reachable"
    }

    /// Probe the voice host by opening (and closing) a Wyoming ASR connection.
    static func voice(base: String) async throws -> String {
        guard let url = URL(string: base), url.host != nil else {
            throw TestError.message("Enter a valid voice URL first")
        }
        guard await VoiceClient(base: url).health() else {
            throw TestError.message("Voice host not answering on the Wyoming port")
        }
        return "Voice service is reachable"
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
    var nilIfBlank: String? {
        let value = trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }
}
