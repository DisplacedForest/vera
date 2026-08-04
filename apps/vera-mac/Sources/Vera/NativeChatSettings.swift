import Foundation
import Security

enum ModelSelectionBasis: String, Codable, Equatable, Sendable {
    case user
    case recommended
    case restored

    var explanation: String {
        switch self {
        case .user: return "You chose this model."
        case .recommended: return "Vera selected the first model returned alphabetically."
        case .restored: return "This model was restored from your saved configuration."
        }
    }
}

enum NativeOnboardingState: String, Codable, Equatable, Sendable {
    case notStarted
    case inProgress
    case skipped
    case complete
}

struct NativeEndpointProfile: Codable, Equatable, Identifiable, Sendable {
    var id: String
    var name: String
    var baseURL: String
    var discoveredModels: [String]
    var selectedModel: String
    var selectionBasis: ModelSelectionBasis?
    var lastDiscoveryAt: Date?

    static func empty() -> NativeEndpointProfile {
        NativeEndpointProfile(
            id: UUID().uuidString,
            name: "New endpoint",
            baseURL: "",
            discoveredModels: [],
            selectedModel: "",
            selectionBasis: nil,
            lastDiscoveryAt: nil)
    }
}

struct NativeDiscoveryConfiguration: Equatable, Sendable {
    let baseURL: String
    let apiKey: String
}

enum NativeChatConfigurationResolver {
    static func discovery(
        profile: NativeEndpointProfile?,
        apiKey: String,
        environment: [String: String]
    ) -> NativeDiscoveryConfiguration {
        NativeDiscoveryConfiguration(
            baseURL: environment["VERA_MODEL_BASE"]?.nonblank ?? profile?.baseURL.nonblank ?? "",
            apiKey: environment["VERA_MODEL_API_KEY"]?.nonblank ?? apiKey.nonblank ?? "")
    }
}

struct NativeChatSettings: Codable, Equatable, Sendable {
    static let defaultSystemPrompt = """
    You are Vera, a personal assistant running locally on your user's own hardware, working only with this machine and the services it is connected to.
    Tell the truth, including about yourself and your limits. When you are unsure, say so plainly instead of guessing.
    Be warm and direct. Write naturally and concisely, like a thoughtful person; skip filler and hedging.
    Push back respectfully when you disagree or a request looks like a mistake.
    What one household member shares with you stays private from the others unless they ask you to pass it on.
    If something is unavailable or outside what this request provides, say so instead of improvising.
    """

    var version: Int
    var profiles: [NativeEndpointProfile]
    var activeProfileID: String?
    var systemPrompt: String
    var activePersonaID: String?
    var enabledToolIDs: Set<String>
    var onboardingState: NativeOnboardingState
    var onboardingStep: Int
    var memory: NativeMemorySettings
    var capabilityOverrides: [String: ModelCapabilityProfile]
    var visionBridge: VisionBridgeSettings

    static var fresh: NativeChatSettings {
        NativeChatSettings(
            version: 2,
            profiles: [],
            activeProfileID: nil,
            systemPrompt: defaultSystemPrompt,
            activePersonaID: nil,
            enabledToolIDs: [],
            onboardingState: .notStarted,
            onboardingStep: 0,
            memory: .fresh,
            capabilityOverrides: [:],
            visionBridge: .fresh)
    }

    var activeProfile: NativeEndpointProfile? {
        guard let activeProfileID else { return nil }
        return profiles.first { $0.id == activeProfileID }
    }

    var hasValidConfiguration: Bool {
        guard let profile = activeProfile else { return false }
        let base = profile.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let model = profile.selectedModel.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: base) else { return false }
        return ["http", "https"].contains(url.scheme?.lowercased() ?? "")
            && url.lastPathComponent == "v1"
            && !model.isEmpty
    }

    static func load(from raw: [String: Any]) -> NativeChatSettings {
        if let object = raw["native_chat"] as? [String: Any],
           let data = try? JSONSerialization.data(withJSONObject: object),
           var decoded = try? JSONDecoder().decode(NativeChatSettings.self, from: data),
           decoded.version == 1 || decoded.version == 2 {
            decoded.version = 2
            if decoded.activeProfile == nil { decoded.activeProfileID = decoded.profiles.first?.id }
            if decoded.systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                decoded.systemPrompt = defaultSystemPrompt
            }
            return decoded
        }
        let base = (raw["model_base"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let model = (raw["model"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !base.isEmpty || !model.isEmpty else { return .fresh }
        let profile = NativeEndpointProfile(
            id: "default",
            name: "My model endpoint",
            baseURL: base,
            discoveredModels: [],
            selectedModel: model,
            selectionBasis: model.isEmpty ? nil : .restored,
            lastDiscoveryAt: nil)
        var settings = fresh
        settings.profiles = [profile]
        settings.activeProfileID = profile.id
        settings.onboardingState = settings.hasValidConfiguration ? .complete : .notStarted
        return settings
    }

    enum CodingKeys: String, CodingKey {
        case version, profiles, activeProfileID, systemPrompt, activePersonaID, enabledToolIDs
        case onboardingState, onboardingStep, memory, capabilityOverrides, visionBridge
    }

    init(
        version: Int, profiles: [NativeEndpointProfile], activeProfileID: String?,
        systemPrompt: String, activePersonaID: String? = nil,
        enabledToolIDs: Set<String>, onboardingState: NativeOnboardingState,
        onboardingStep: Int, memory: NativeMemorySettings,
        capabilityOverrides: [String: ModelCapabilityProfile] = [:],
        visionBridge: VisionBridgeSettings = .fresh
    ) {
        self.version = version
        self.profiles = profiles
        self.activeProfileID = activeProfileID
        self.systemPrompt = systemPrompt
        self.activePersonaID = activePersonaID
        self.enabledToolIDs = enabledToolIDs
        self.onboardingState = onboardingState
        self.onboardingStep = onboardingStep
        self.memory = memory
        self.capabilityOverrides = capabilityOverrides
        self.visionBridge = visionBridge
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        version = try values.decodeIfPresent(Int.self, forKey: .version) ?? 1
        profiles = try values.decodeIfPresent([NativeEndpointProfile].self, forKey: .profiles) ?? []
        activeProfileID = try values.decodeIfPresent(String.self, forKey: .activeProfileID)
        systemPrompt = try values.decodeIfPresent(String.self, forKey: .systemPrompt) ?? Self.defaultSystemPrompt
        activePersonaID = try values.decodeIfPresent(String.self, forKey: .activePersonaID)
        enabledToolIDs = try values.decodeIfPresent(Set<String>.self, forKey: .enabledToolIDs) ?? []
        onboardingState = try values.decodeIfPresent(NativeOnboardingState.self, forKey: .onboardingState) ?? .notStarted
        onboardingStep = try values.decodeIfPresent(Int.self, forKey: .onboardingStep) ?? 0
        memory = try values.decodeIfPresent(NativeMemorySettings.self, forKey: .memory) ?? .fresh
        capabilityOverrides = try values.decodeIfPresent(
            [String: ModelCapabilityProfile].self, forKey: .capabilityOverrides) ?? [:]
        visionBridge = try values.decodeIfPresent(
            VisionBridgeSettings.self, forKey: .visionBridge) ?? .fresh
    }

    func merging(into raw: [String: Any]) -> [String: Any] {
        var merged = raw
        if let data = try? JSONEncoder().encode(self),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            merged["native_chat"] = object
        }
        if let profile = activeProfile {
            merged["model_base"] = profile.baseURL
            merged["model"] = profile.selectedModel
        } else {
            merged.removeValue(forKey: "model_base")
            merged.removeValue(forKey: "model")
        }
        merged.removeValue(forKey: "model_api_key")
        return merged
    }

    mutating func resetSystemPrompt() {
        systemPrompt = Self.defaultSystemPrompt
    }

    mutating func addProfile() {
        let profile = NativeEndpointProfile.empty()
        profiles.append(profile)
        activeProfileID = profile.id
    }

    mutating func updateActiveProfile(_ update: (inout NativeEndpointProfile) -> Void) {
        guard let activeProfileID,
              let index = profiles.firstIndex(where: { $0.id == activeProfileID }) else { return }
        update(&profiles[index])
    }

    mutating func cacheModels(_ models: [String], at date: Date = Date()) {
        let sorted = Array(Set(models.filter { !$0.isEmpty })).sorted()
        updateActiveProfile {
            $0.discoveredModels = sorted
            $0.lastDiscoveryAt = date
            if $0.selectedModel.isEmpty, let first = sorted.first {
                $0.selectedModel = first
                $0.selectionBasis = .recommended
            }
        }
    }

    mutating func selectModel(_ model: String, basis: ModelSelectionBasis) {
        updateActiveProfile {
            $0.selectedModel = model
            $0.selectionBasis = basis
        }
    }

    func resolveCapabilities(model: String) -> ModelCapabilityCatalog.Resolution {
        ModelCapabilityCatalog.resolve(model: model, overrides: capabilityOverrides)
    }

    mutating func setCapabilityOverride(model: String, profile: ModelCapabilityProfile) {
        capabilityOverrides[model] = profile
    }

    mutating func clearCapabilityOverride(model: String) {
        capabilityOverrides.removeValue(forKey: model)
    }
}

enum ModelDiscoveryState: Equatable {
    case idle
    case loading
    case models([String])
    case empty
    case noUsableModels
    case authenticationFailed
    case networkFailed
    case malformed
    case failed(String)

    static func classify(_ error: Error) -> ModelDiscoveryState {
        if let client = error as? NativeChatClient.ClientError {
            switch client {
            case .http(let status, _):
                return status == 401 || status == 403 ? .authenticationFailed : .failed(client.localizedDescription)
            case .invalidResponse, .malformedEvent: return .malformed
            case .noUsableModels: return .noUsableModels
            case .server, .interrupted: return .failed(client.localizedDescription)
            }
        }
        if error is URLError { return .networkFailed }
        return .failed(error.localizedDescription)
    }
}

struct NativeChatToolDescriptor: Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let summary: String
    let available: Bool
    let setup: String
}

enum NativeChatToolCatalog {
    static var tools: [NativeChatToolDescriptor] {
        tools(veraAPIConfigured: OWUIConfig.resolvedVeraAPIBase() != nil)
    }

    static func tools(
        veraAPIConfigured: Bool, declarations: [NativeToolDeclaration] = []
    ) -> [NativeChatToolDescriptor] {
        builtIn(veraAPIConfigured: veraAPIConfigured)
            + NativeCapabilityTools.descriptors(declarations, veraAPIConfigured: veraAPIConfigured)
    }

    private static func builtIn(veraAPIConfigured: Bool) -> [NativeChatToolDescriptor] {
        [
            NativeChatToolDescriptor(
                id: "external-tools",
                name: "Connected service tools",
                summary: "Lets the model act through connected services when a native tool runtime is available.",
                available: false,
                setup: "Native chat cannot invoke connected service tools in this version."),
            NativeChatToolDescriptor(
                id: "apple-reminders",
                name: "Apple Reminders",
                summary: "Reads and updates reminder lists after you grant access.",
                available: true,
                setup: "Enable this tool to grant Reminders access for explicit chat requests."),
            NativeChatToolDescriptor(
                id: "web-search",
                name: "Web search",
                summary: "Searches the web through your vera-api and answers from the results.",
                available: veraAPIConfigured,
                setup: "Set the vera-api base URL in the Connection tab to enable web search."),
            NativeChatToolDescriptor(
                id: "deep-research",
                name: "Deep research",
                summary: "Runs a multi-step research pass through your vera-api and cites its sources.",
                available: veraAPIConfigured,
                setup: "Set the vera-api base URL in the Connection tab to enable deep research."),
        ]
    }

    static func exposed(enabledIDs: Set<String>) -> [NativeChatToolDescriptor] {
        tools.filter { $0.available && enabledIDs.contains($0.id) }
    }
}

protocol NativeCredentialStore {
    func value(for profileID: String) -> String?
    func set(_ value: String?, for profileID: String) throws
}

struct KeychainNativeCredentialStore: NativeCredentialStore {
    private let service = "org.displacedforest.vera.native-chat"

    func value(for profileID: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: profileID,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func set(_ value: String?, for profileID: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: profileID,
        ]
        SecItemDelete(query as CFDictionary)
        guard let value, !value.isEmpty else { return }
        var item = query
        item[kSecValueData as String] = Data(value.utf8)
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }
}

private extension String {
    var nonblank: String? {
        let value = trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }
}
