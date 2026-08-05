import Foundation

struct ModelCapabilityProfile: Codable, Equatable, Sendable {
    var acceptsImages: Bool
    var supportsTools: Bool
    var supportsStreaming: Bool
    var maxImagesPerRequest: Int
    var supportsReasoning: Bool

    static let textOnly = ModelCapabilityProfile(
        acceptsImages: false, supportsTools: true, supportsStreaming: true, maxImagesPerRequest: 0)
    static let vision = ModelCapabilityProfile(
        acceptsImages: true, supportsTools: true, supportsStreaming: true, maxImagesPerRequest: 8)

    enum CodingKeys: String, CodingKey {
        case acceptsImages, supportsTools, supportsStreaming, maxImagesPerRequest, supportsReasoning
    }

    init(
        acceptsImages: Bool, supportsTools: Bool, supportsStreaming: Bool,
        maxImagesPerRequest: Int, supportsReasoning: Bool = false
    ) {
        self.acceptsImages = acceptsImages
        self.supportsTools = supportsTools
        self.supportsStreaming = supportsStreaming
        self.maxImagesPerRequest = maxImagesPerRequest
        self.supportsReasoning = supportsReasoning
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        acceptsImages = try values.decodeIfPresent(Bool.self, forKey: .acceptsImages) ?? false
        supportsTools = try values.decodeIfPresent(Bool.self, forKey: .supportsTools) ?? true
        supportsStreaming = try values.decodeIfPresent(Bool.self, forKey: .supportsStreaming) ?? true
        maxImagesPerRequest = try values.decodeIfPresent(Int.self, forKey: .maxImagesPerRequest)
            ?? (acceptsImages ? ModelCapabilityProfile.vision.maxImagesPerRequest : 0)
        supportsReasoning = try values.decodeIfPresent(Bool.self, forKey: .supportsReasoning) ?? false
    }
}

enum ModelCapabilityCatalog {
    enum Source: Equatable, Sendable {
        case override
        case pattern(String)
        case fallback
    }

    struct Resolution: Equatable, Sendable {
        let profile: ModelCapabilityProfile
        let source: Source
    }

    static let visionSubstrings: [String] = [
        "llava", "vision", "multimodal", "pixtral", "moondream", "internvl", "minicpm-v",
        "smolvlm", "paligemma", "gemma-3", "gemma3", "llama-4", "llama4",
        "gpt-4o", "gpt-4.1", "gpt-5", "claude", "gemini",
    ]

    static let visionTokens: Set<String> = ["vl", "4o", "o3", "o4", "omni"]

    static func defaultResolution(for model: String) -> Resolution {
        let normalized = model.lowercased()
        for pattern in visionSubstrings where normalized.contains(pattern) {
            return Resolution(profile: .vision, source: .pattern(pattern))
        }
        let tokens = normalized.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
        for token in tokens where visionTokens.contains(token) {
            return Resolution(profile: .vision, source: .pattern(token))
        }
        return Resolution(profile: .textOnly, source: .fallback)
    }

    static func resolve(
        model: String, overrides: [String: ModelCapabilityProfile]
    ) -> Resolution {
        if let override = overrides[model] {
            return Resolution(profile: override, source: .override)
        }
        return defaultResolution(for: model)
    }
}

struct VisionBridgeSettings: Codable, Equatable, Sendable {
    var baseURL: String
    var model: String

    static let fresh = VisionBridgeSettings(baseURL: "", model: "")

    var isConfigured: Bool {
        let base = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = model.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: base), !name.isEmpty else { return false }
        return ["http", "https"].contains(url.scheme?.lowercased() ?? "")
            && url.lastPathComponent == "v1"
    }
}

struct VisionBridgeConfig: Equatable, Sendable {
    let baseURL: URL
    let apiKey: String?
    let model: String

    func isSameEndpoint(as config: NativeChatConfig) -> Bool {
        model == config.model
            && baseURL.appendingPathComponent("chat/completions").absoluteString.lowercased()
                == config.completionsURL.absoluteString.lowercased()
    }

    static func resolve(
        settings: VisionBridgeSettings?, apiKey: String?, environment: [String: String]
    ) -> VisionBridgeConfig? {
        let base = environment["VERA_VISION_BRIDGE_BASE"]?.nonblankValue
            ?? settings?.baseURL.nonblankValue
        let model = environment["VERA_VISION_BRIDGE_MODEL"]?.nonblankValue
            ?? settings?.model.nonblankValue
        guard let base, let model, let url = URL(string: base),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.lastPathComponent == "v1" else { return nil }
        let key = environment["VERA_VISION_BRIDGE_API_KEY"]?.nonblankValue ?? apiKey?.nonblankValue
        return VisionBridgeConfig(baseURL: url, apiKey: key, model: model)
    }
}

enum AttachmentRoute: String, Equatable, Sendable {
    case direct
    case bridged
    case needsDecision
}

enum AttachmentPreflight {
    static func route(
        profile: ModelCapabilityProfile, imageCount: Int, bridgeConfigured: Bool
    ) -> AttachmentRoute? {
        guard imageCount > 0 else { return nil }
        if profile.acceptsImages, imageCount <= max(profile.maxImagesPerRequest, 1) {
            return .direct
        }
        return bridgeConfigured ? .bridged : .needsDecision
    }
}

struct MessageRouteNote: Codable, Hashable, Sendable {
    enum Route: String, Codable, Sendable {
        case direct
        case bridged
        case withheld
    }

    var route: Route
    var bridgeModel: String?
    var disclosure: String?

    init(route: Route, bridgeModel: String? = nil, disclosure: String? = nil) {
        self.route = route
        self.bridgeModel = bridgeModel
        self.disclosure = disclosure
    }
}

enum VisionBridgeDisclosure {
    static func block(
        descriptions: [(name: String, text: String)], bridgeModel: String
    ) -> String {
        var lines = [
            "[Attachment context from the vision bridge]",
            "The user attached image(s) this model did not receive. The descriptions below were produced by the separate vision model \"\(bridgeModel)\" from those images.",
        ]
        for entry in descriptions {
            lines.append("Image \"\(entry.name)\": \(entry.text)")
        }
        return lines.joined(separator: "\n")
    }
}

enum VisionBridgeError: Error, LocalizedError, Equatable {
    case emptyDescription(String)

    var errorDescription: String? {
        switch self {
        case .emptyDescription(let name):
            return "The vision bridge returned no usable description for \(name)"
        }
    }
}

struct VisionBridge: Sendable {
    static let instruction = "Describe this image accurately and completely for someone who cannot see it. Cover the subject, any visible text verbatim, layout, and notable details. Do not speculate beyond what is visible."

    let transport: any NativeChatTransport
    let model: String

    init(config: VisionBridgeConfig, transport: (any NativeChatTransport)? = nil) {
        self.model = config.model
        self.transport = transport ?? NativeChatClient(config: NativeChatConfig(
            baseURL: config.baseURL, apiKey: config.apiKey, model: config.model,
            chatTemplateKwargs: nil))
    }

    func describe(name: String, dataURL: String) async throws -> String {
        var final = ""
        for try await snapshot in transport.stream(
            messages: [NativeChatMessage(role: "user", content: Self.instruction, images: [dataURL])],
            model: model, tools: []
        ) {
            final = snapshot.content
        }
        let trimmed = final.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw VisionBridgeError.emptyDescription(name) }
        return trimmed
    }
}

private extension String {
    var nonblankValue: String? {
        let value = trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }
}
