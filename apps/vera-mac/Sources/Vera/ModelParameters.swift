import Foundation

enum ModelParameterScope: String, Codable, Equatable, Sendable {
    case request
    case model
    case providerRuntime

    var label: String {
        switch self {
        case .request: return "Request"
        case .model: return "Model"
        case .providerRuntime: return "Provider runtime"
        }
    }
}

enum ModelParameterGroup: String, Codable, CaseIterable, Equatable, Sendable {
    case sampling
    case tokens
    case reasoning
    case streamingContext
    case provider

    var label: String {
        switch self {
        case .sampling: return "Sampling"
        case .tokens: return "Tokens and stops"
        case .reasoning: return "Reasoning"
        case .streamingContext: return "Streaming and context"
        case .provider: return "Provider"
        }
    }
}

enum ModelParameterCapability: Equatable, Sendable {
    case always
    case streaming
    case reasoning

    func supported(by profile: ModelCapabilityProfile) -> Bool {
        switch self {
        case .always: return true
        case .streaming: return profile.supportsStreaming
        case .reasoning: return profile.supportsReasoning
        }
    }

    var requirement: String {
        switch self {
        case .always: return ""
        case .streaming: return "This model's capability profile has streaming replies turned off."
        case .reasoning: return "This model's capability profile does not declare reasoning support."
        }
    }
}

enum ModelParameterPlacement: Equatable, Sendable {
    case topLevel
    case templateKwargs
    case client
}

enum ModelParameterID: String, Codable, CaseIterable, Equatable, Sendable {
    case temperature
    case topP
    case topK
    case maxOutputTokens
    case stopSequences
    case seed
    case presencePenalty
    case frequencyPenalty
    case reasoningMode
    case reasoningEffort
    case streaming
    case contextCeiling
}

enum ModelParameterValue: Codable, Equatable, Hashable, Sendable {
    case number(Double)
    case integer(Int)
    case flag(Bool)
    case list([String])
    case choice(String)

    var jsonValue: NativeJSONValue {
        switch self {
        case .number(let value): return .number(value)
        case .integer(let value): return .number(Double(value))
        case .flag(let value): return .bool(value)
        case .list(let values): return .array(values.map { .string($0) })
        case .choice(let value): return .string(value)
        }
    }

    var display: String {
        switch self {
        case .number(let value):
            return value == value.rounded() ? String(Int(value)) : String(value)
        case .integer(let value): return String(value)
        case .flag(let value): return value ? "On" : "Off"
        case .list(let values): return values.isEmpty ? "None" : values.joined(separator: ", ")
        case .choice(let value): return value
        }
    }
}

enum ModelParameterKind: Equatable, Sendable {
    case number(min: Double, max: Double)
    case integer(min: Int, max: Int)
    case flag
    case list(maxCount: Int)
    case choice([String])
}

struct ModelParameterDeclaration: Identifiable, Sendable {
    let id: ModelParameterID
    let name: String
    let wireKey: String
    let kind: ModelParameterKind
    let scope: ModelParameterScope
    let placement: ModelParameterPlacement
    let capability: ModelParameterCapability
    let group: ModelParameterGroup
    let explanation: String
    let seedValue: ModelParameterValue

    func validate(_ value: ModelParameterValue) -> String? {
        switch (kind, value) {
        case (.number(let min, let max), .number(let candidate)):
            guard candidate.isFinite, candidate >= min, candidate <= max else {
                return "Enter a number between \(Self.bound(min)) and \(Self.bound(max))."
            }
            return nil
        case (.integer(let min, let max), .integer(let candidate)):
            guard candidate >= min, candidate <= max else {
                return "Enter a whole number between \(min) and \(max)."
            }
            return nil
        case (.flag, .flag):
            return nil
        case (.list(let maxCount), .list(let entries)):
            guard entries.count <= maxCount else {
                return "Use at most \(maxCount) entries."
            }
            guard !entries.contains(where: { $0.isEmpty }) else {
                return "Entries cannot be empty."
            }
            return nil
        case (.choice(let options), .choice(let candidate)):
            guard options.contains(candidate) else {
                return "Choose one of: \(options.joined(separator: ", "))."
            }
            return nil
        default:
            return "This value does not match the parameter type."
        }
    }

    func parse(_ raw: String) -> ModelParameterValue? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        switch kind {
        case .number:
            return Double(trimmed).map { .number($0) }
        case .integer:
            return Int(trimmed).map { .integer($0) }
        case .flag:
            return Bool(trimmed).map { .flag($0) }
        case .list:
            let entries = trimmed.split(separator: ",").map {
                String($0).trimmingCharacters(in: .whitespaces)
            }.filter { !$0.isEmpty }
            return .list(entries)
        case .choice:
            return .choice(trimmed)
        }
    }

    private static func bound(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(value)
    }
}

enum ModelParameterCatalog {
    static let all: [ModelParameterDeclaration] = [
        ModelParameterDeclaration(
            id: .temperature, name: "Temperature", wireKey: "temperature",
            kind: .number(min: 0, max: 2), scope: .request, placement: .topLevel,
            capability: .always, group: .sampling,
            explanation: "Higher values make replies more varied. Lower values make them more focused and repeatable.",
            seedValue: .number(0.7)),
        ModelParameterDeclaration(
            id: .topP, name: "Top P", wireKey: "top_p",
            kind: .number(min: 0, max: 1), scope: .request, placement: .topLevel,
            capability: .always, group: .sampling,
            explanation: "Limits sampling to the smallest set of tokens whose combined probability reaches this value.",
            seedValue: .number(0.9)),
        ModelParameterDeclaration(
            id: .topK, name: "Top K", wireKey: "top_k",
            kind: .integer(min: 1, max: 1000), scope: .providerRuntime, placement: .topLevel,
            capability: .always, group: .sampling,
            explanation: "Limits sampling to the K most likely tokens. Not part of the OpenAI schema; some endpoints ignore or reject it.",
            seedValue: .integer(40)),
        ModelParameterDeclaration(
            id: .seed, name: "Seed", wireKey: "seed",
            kind: .integer(min: 0, max: 2_147_483_647), scope: .request, placement: .topLevel,
            capability: .always, group: .sampling,
            explanation: "Asks the endpoint to make sampling repeatable. Identical requests with the same seed aim for identical replies.",
            seedValue: .integer(0)),
        ModelParameterDeclaration(
            id: .presencePenalty, name: "Presence penalty", wireKey: "presence_penalty",
            kind: .number(min: -2, max: 2), scope: .request, placement: .topLevel,
            capability: .always, group: .sampling,
            explanation: "Positive values discourage the model from repeating topics it has already mentioned.",
            seedValue: .number(0)),
        ModelParameterDeclaration(
            id: .frequencyPenalty, name: "Frequency penalty", wireKey: "frequency_penalty",
            kind: .number(min: -2, max: 2), scope: .request, placement: .topLevel,
            capability: .always, group: .sampling,
            explanation: "Positive values discourage the model from repeating the same words and phrases.",
            seedValue: .number(0)),
        ModelParameterDeclaration(
            id: .maxOutputTokens, name: "Max output tokens", wireKey: "max_tokens",
            kind: .integer(min: 1, max: 1_000_000), scope: .request, placement: .topLevel,
            capability: .always, group: .tokens,
            explanation: "Caps how many tokens the model may generate for one reply.",
            seedValue: .integer(1024)),
        ModelParameterDeclaration(
            id: .stopSequences, name: "Stop sequences", wireKey: "stop",
            kind: .list(maxCount: 4), scope: .request, placement: .topLevel,
            capability: .always, group: .tokens,
            explanation: "Generation stops when any of these strings appears. Separate entries with commas.",
            seedValue: .list([])),
        ModelParameterDeclaration(
            id: .reasoningMode, name: "Reasoning mode", wireKey: "enable_thinking",
            kind: .flag, scope: .providerRuntime, placement: .templateKwargs,
            capability: .reasoning, group: .reasoning,
            explanation: "Turns the model's thinking phase on or off through the chat template, for models whose template honors it.",
            seedValue: .flag(true)),
        ModelParameterDeclaration(
            id: .reasoningEffort, name: "Reasoning effort", wireKey: "reasoning_effort",
            kind: .choice(["low", "medium", "high"]), scope: .request, placement: .topLevel,
            capability: .reasoning, group: .reasoning,
            explanation: "How much thinking the model does before answering, on endpoints that accept the OpenAI reasoning field.",
            seedValue: .choice("medium")),
        ModelParameterDeclaration(
            id: .streaming, name: "Stream replies", wireKey: "stream",
            kind: .flag, scope: .request, placement: .client,
            capability: .streaming, group: .streamingContext,
            explanation: "Streams tokens as they generate. Turn off to receive each reply in one piece.",
            seedValue: .flag(true)),
        ModelParameterDeclaration(
            id: .contextCeiling, name: "Context length ceiling", wireKey: "context_ceiling",
            kind: .integer(min: 1024, max: 10_000_000), scope: .model, placement: .client,
            capability: .always, group: .streamingContext,
            explanation: "Approximate token budget for conversation history. Older turns are dropped from the request once the budget is exceeded. Never sent to the endpoint.",
            seedValue: .integer(8192)),
    ]

    static let byID: [ModelParameterID: ModelParameterDeclaration] =
        Dictionary(uniqueKeysWithValues: all.map { ($0.id, $0) })

    static func declaration(_ id: ModelParameterID) -> ModelParameterDeclaration? {
        byID[id]
    }

    static let reservedWireKeys: Set<String> = [
        "model", "stream", "messages", "tools", "tool_choice", "chat_template_kwargs",
    ]

    static let declaredWireKeys: Set<String> = Set(all.map(\.wireKey))
}

struct CustomModelParameter: Codable, Equatable, Hashable, Identifiable, Sendable {
    enum Kind: String, Codable, CaseIterable, Equatable, Sendable {
        case text
        case number
        case boolean
        case json

        var label: String {
            switch self {
            case .text: return "Text"
            case .number: return "Number"
            case .boolean: return "Boolean"
            case .json: return "JSON"
            }
        }
    }

    var id: String
    var key: String
    var kind: Kind
    var raw: String

    var trimmedKey: String { key.trimmingCharacters(in: .whitespacesAndNewlines) }

    func validationError(siblingKeys: Set<String>) -> String? {
        let name = trimmedKey
        guard !name.isEmpty else { return "Enter a key." }
        guard !name.contains(where: \.isWhitespace) else { return "Keys cannot contain spaces." }
        guard !ModelParameterCatalog.reservedWireKeys.contains(name) else {
            return "\(name) is managed by Vera and cannot be set here."
        }
        guard !ModelParameterCatalog.declaredWireKeys.contains(name) else {
            return "\(name) has a dedicated control above."
        }
        guard !siblingKeys.contains(name) else { return "This key is already used by another entry." }
        guard jsonValue() != nil else {
            switch kind {
            case .text: return "Enter a value."
            case .number: return "Enter a valid number."
            case .boolean: return "Enter true or false."
            case .json: return "Enter valid JSON, such as a number, string, array, or object."
            }
        }
        return nil
    }

    func jsonValue() -> NativeJSONValue? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        switch kind {
        case .text:
            return trimmed.isEmpty ? nil : .string(trimmed)
        case .number:
            guard let number = Double(trimmed), number.isFinite else { return nil }
            return .number(number)
        case .boolean:
            return Bool(trimmed).map { .bool($0) }
        case .json:
            guard let data = trimmed.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data, options: .fragmentsAllowed)
            else { return nil }
            return try? NativeJSONValue(any: object)
        }
    }

    static func empty() -> CustomModelParameter {
        CustomModelParameter(id: UUID().uuidString, key: "", kind: .text, raw: "")
    }
}

struct ModelParameterOverrides: Codable, Equatable, Sendable {
    var values: [ModelParameterID: ModelParameterValue]
    var custom: [CustomModelParameter]

    static let empty = ModelParameterOverrides(values: [:], custom: [])

    var isEmpty: Bool { values.isEmpty && custom.isEmpty }

    init(values: [ModelParameterID: ModelParameterValue] = [:], custom: [CustomModelParameter] = []) {
        self.values = values
        self.custom = custom
    }

    enum CodingKeys: String, CodingKey {
        case values, custom
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        values = try container.decodeIfPresent(
            [ModelParameterID: ModelParameterValue].self, forKey: .values) ?? [:]
        custom = try container.decodeIfPresent([CustomModelParameter].self, forKey: .custom) ?? []
    }
}

struct NativeChatRequestOptions: Equatable, Sendable {
    enum Source: String, Equatable, Sendable {
        case override
        case custom
        case environment
        case saved

        var label: String {
            switch self {
            case .override: return "Your override"
            case .custom: return "Custom parameter"
            case .environment: return "Environment"
            case .saved: return "Saved configuration"
            }
        }
    }

    struct Entry: Equatable, Identifiable, Sendable {
        let key: String
        let value: NativeJSONValue
        let source: Source

        var id: String { key }

        var payloadAny: Any {
            if case .number(let number) = value,
               number == number.rounded(), abs(number) < 1e15 {
                return Int(number)
            }
            return value.any
        }
    }

    var topLevel: [Entry]
    var kwargs: [Entry]
    var streamingOverride: Bool?
    var contextCeiling: Int?

    static let none = NativeChatRequestOptions(
        topLevel: [], kwargs: [], streamingOverride: nil, contextCeiling: nil)

    var isEmpty: Bool {
        topLevel.isEmpty && kwargs.isEmpty && streamingOverride == nil && contextCeiling == nil
    }

    func kwargsObject() -> [String: Any]? {
        guard !kwargs.isEmpty else { return nil }
        return Dictionary(uniqueKeysWithValues: kwargs.map { ($0.key, $0.payloadAny) })
    }

    static func resolve(
        overrides: ModelParameterOverrides,
        profile: ModelCapabilityProfile,
        savedTemplateKwargs: String?,
        environmentTemplateKwargs: String?
    ) -> NativeChatRequestOptions {
        var topLevel: [Entry] = []
        var declaredKwargs: [Entry] = []
        var streamingOverride: Bool?
        var contextCeiling: Int?
        for (id, value) in overrides.values {
            guard let declaration = ModelParameterCatalog.declaration(id),
                  declaration.capability.supported(by: profile),
                  declaration.validate(value) == nil else { continue }
            switch declaration.placement {
            case .client:
                if id == .streaming, case .flag(let flag) = value { streamingOverride = flag }
                if id == .contextCeiling, case .integer(let ceiling) = value { contextCeiling = ceiling }
            case .topLevel:
                topLevel.append(Entry(key: declaration.wireKey, value: value.jsonValue, source: .override))
            case .templateKwargs:
                declaredKwargs.append(Entry(key: declaration.wireKey, value: value.jsonValue, source: .override))
            }
        }
        topLevel.sort { $0.key < $1.key }
        var kwargs: [Entry] = []
        if environmentTemplateKwargs?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false {
            kwargs = (parsedKwargs(environmentTemplateKwargs) ?? [:])
                .map { Entry(key: $0.key, value: $0.value, source: .environment) }
                .sorted { $0.key < $1.key }
        } else {
            var merged: [String: Entry] = [:]
            if let saved = parsedKwargs(savedTemplateKwargs) {
                for (key, value) in saved {
                    merged[key] = Entry(key: key, value: value, source: .saved)
                }
            }
            for entry in declaredKwargs { merged[entry.key] = entry }
            var keyCounts: [String: Int] = [:]
            for parameter in overrides.custom {
                keyCounts[parameter.trimmedKey, default: 0] += 1
            }
            for parameter in overrides.custom {
                guard keyCounts[parameter.trimmedKey] == 1,
                      parameter.validationError(siblingKeys: []) == nil,
                      let value = parameter.jsonValue() else { continue }
                merged[parameter.trimmedKey] = Entry(
                    key: parameter.trimmedKey, value: value, source: .custom)
            }
            kwargs = merged.values.sorted { $0.key < $1.key }
        }
        return NativeChatRequestOptions(
            topLevel: topLevel, kwargs: kwargs,
            streamingOverride: streamingOverride, contextCeiling: contextCeiling)
    }

    private static func parsedKwargs(_ raw: String?) -> [String: NativeJSONValue]? {
        guard let raw, let data = raw.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              !object.isEmpty else { return nil }
        return try? object.mapValues { try NativeJSONValue(any: $0) }
    }
}

struct ModelParameterRejection: Equatable, Sendable {
    let key: String
    let displayName: String
    let parameterID: ModelParameterID?
    let isCustom: Bool
}

enum ModelParameterRejectionClassifier {
    static func classify(
        error: Error, options: NativeChatRequestOptions
    ) -> ModelParameterRejection? {
        guard let client = error as? NativeChatClient.ClientError else { return nil }
        switch client {
        case .http(let status, let detail):
            guard (400..<500).contains(status) else { return nil }
            return classify(detail: detail, options: options)
        case .server(let detail):
            return classify(detail: detail, options: options)
        case .invalidResponse, .malformedEvent, .noUsableModels, .interrupted:
            return nil
        }
    }

    static func classify(
        detail: String, options: NativeChatRequestOptions
    ) -> ModelParameterRejection? {
        let haystack = detail.lowercased()
        guard !haystack.isEmpty else { return nil }
        var candidates: [(key: String, parameterID: ModelParameterID?, isCustom: Bool)] = []
        for entry in options.topLevel + options.kwargs
        where entry.source == .override || entry.source == .custom {
            let id = ModelParameterCatalog.all.first { $0.wireKey == entry.key }?.id
            candidates.append((entry.key, id, entry.source == .custom))
        }
        if options.streamingOverride != nil {
            candidates.append(("stream", .streaming, false))
        }
        if options.contextCeiling != nil {
            candidates.append(("context_ceiling", .contextCeiling, false))
        }
        for candidate in candidates.sorted(by: { $0.key.count > $1.key.count }) {
            if containsToken(haystack, token: candidate.key.lowercased()) {
                let name = candidate.parameterID
                    .flatMap { ModelParameterCatalog.declaration($0)?.name } ?? candidate.key
                return ModelParameterRejection(
                    key: candidate.key, displayName: name,
                    parameterID: candidate.parameterID, isCustom: candidate.isCustom)
            }
        }
        return nil
    }

    private static func containsToken(_ haystack: String, token: String) -> Bool {
        guard !token.isEmpty else { return false }
        var searchRange = haystack.startIndex..<haystack.endIndex
        while let range = haystack.range(of: token, range: searchRange) {
            let beforeOK = range.lowerBound == haystack.startIndex
                || !isIdentifierChar(haystack[haystack.index(before: range.lowerBound)])
            let afterOK = range.upperBound == haystack.endIndex
                || !isIdentifierChar(haystack[range.upperBound])
            if beforeOK && afterOK { return true }
            searchRange = range.upperBound..<haystack.endIndex
        }
        return false
    }

    private static func isIdentifierChar(_ character: Character) -> Bool {
        character.isLetter || character.isNumber || character == "_"
    }
}

struct NativeRequestTrace: Equatable, Sendable {
    struct Item: Equatable, Identifiable, Sendable {
        let key: String
        let value: String
        let source: String
        let destination: String

        var id: String { destination + ":" + key }
    }

    let model: String
    let timestamp: Date
    let items: [Item]

    static func make(
        model: String, streaming: Bool, options: NativeChatRequestOptions, timestamp: Date
    ) -> NativeRequestTrace {
        var items: [Item] = [
            Item(
                key: "stream", value: streaming ? "true" : "false",
                source: options.streamingOverride == nil ? "Capability profile" : "Your override",
                destination: "payload"),
        ]
        for entry in options.topLevel {
            items.append(Item(
                key: entry.key, value: Self.render(entry.value),
                source: entry.source.label, destination: "payload"))
        }
        for entry in options.kwargs {
            items.append(Item(
                key: entry.key, value: Self.render(entry.value),
                source: entry.source.label, destination: "chat_template_kwargs"))
        }
        if let ceiling = options.contextCeiling {
            items.append(Item(
                key: "context_ceiling", value: String(ceiling),
                source: "Your override", destination: "request preparation"))
        }
        return NativeRequestTrace(model: model, timestamp: timestamp, items: items)
    }

    private static func render(_ value: NativeJSONValue) -> String {
        switch value {
        case .string(let string): return string
        case .number(let number):
            return number == number.rounded() && abs(number) < 1e15
                ? String(Int(number)) : String(number)
        case .bool(let flag): return flag ? "true" : "false"
        case .null: return "null"
        case .array, .object:
            guard let data = try? JSONSerialization.data(
                withJSONObject: value.any, options: [.sortedKeys, .fragmentsAllowed]),
                let text = String(data: data, encoding: .utf8) else { return "…" }
            return text
        }
    }
}
