import Foundation

struct NativeHTTPToolClient: Sendable {
    let send: @Sendable (URL, String, Data?, TimeInterval) async throws -> (Data, Int)

    static let live = NativeHTTPToolClient { url, method, body, timeout in
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (stream, response) = try await URLSession.shared.bytes(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let limit = NativeCapabilityTools.responseByteCap + 1
        var data = Data()
        data.reserveCapacity(limit)
        for try await byte in stream {
            data.append(byte)
            if data.count >= limit { break }
        }
        return (data, status)
    }
}

enum NativeToolHTTPMethod: String, Equatable, Hashable, Sendable {
    case get = "GET"
    case post = "POST"
}

enum NativeToolPropertyType: String, Equatable, Hashable, Sendable {
    case string
    case boolean
}

struct NativeToolProperty: Equatable, Hashable, Sendable {
    let name: String
    let type: NativeToolPropertyType
    let description: String
}

struct NativeToolDeclarationError: Error, Equatable {
    let reason: String
}

struct NativeToolDeclarationFailure: Equatable, Hashable, Sendable, Identifiable {
    let file: String
    let reason: String

    var id: String { "\(file)|\(reason)" }
}

struct NativeToolDeclaration: Equatable, Hashable, Sendable, Identifiable {
    let name: String
    let title: String
    let description: String
    let properties: [NativeToolProperty]
    let required: [String]
    let endpoint: String
    let method: NativeToolHTTPMethod
    let confirmation: NativeToolConfirmation
    let timeoutSeconds: Double
    let enabled: Bool
    let jsonFields: [String: String]
    let source: String

    var id: String { NativeCapabilityTools.identifier(name: name) }

    var needsAPIBase: Bool { URL(string: endpoint)?.scheme == nil }

    var parameters: NativeJSONValue {
        var fields: [String: NativeJSONValue] = [:]
        for property in properties {
            var entry: [String: NativeJSONValue] = ["type": .string(property.type.rawValue)]
            if !property.description.isEmpty {
                entry["description"] = .string(property.description)
            }
            fields[property.name] = .object(entry)
        }
        return .object([
            "type": .string("object"),
            "properties": .object(fields),
            "required": .array(required.map(NativeJSONValue.string)),
            "additionalProperties": .bool(false),
        ])
    }
}

struct NativeCapabilityToolCatalog: Equatable, Sendable {
    var declarations: [NativeToolDeclaration] = []
    var failures: [NativeToolDeclarationFailure] = []

    static let empty = NativeCapabilityToolCatalog()
}

enum NativeCapabilityTools {
    static let responseByteCap = 32_768
    static let defaultTimeoutSeconds: Double = 20
    static let maximumTimeoutSeconds: Double = 300
    static let directoryName = "tools.d"

    static func identifier(name: String) -> String { "capability-\(name)" }

    static var defaultDirectory: URL {
        ConfigFile.defaultURL.deletingLastPathComponent()
            .appendingPathComponent(directoryName, isDirectory: true)
    }

    static func catalog(
        directory: URL = defaultDirectory, reservedNames: Set<String> = []
    ) -> NativeCapabilityToolCatalog {
        var declarations = bundled
        var taken = Set(declarations.map(\.name)).union(reservedNames)
        var failures: [NativeToolDeclarationFailure] = []
        let files = ((try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension.lowercased() == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        for file in files {
            let label = file.lastPathComponent
            guard let data = try? Data(contentsOf: file) else {
                failures.append(NativeToolDeclarationFailure(
                    file: label, reason: "The file could not be read"))
                continue
            }
            do {
                let declaration = try parse(data: data, source: label)
                guard !taken.contains(declaration.name) else {
                    failures.append(NativeToolDeclarationFailure(
                        file: label,
                        reason: reservedNames.contains(declaration.name)
                            ? "The name '\(declaration.name)' is reserved by a built-in tool"
                            : "A tool named '\(declaration.name)' is already loaded"))
                    continue
                }
                taken.insert(declaration.name)
                declarations.append(declaration)
            } catch let error as NativeToolDeclarationError {
                failures.append(NativeToolDeclarationFailure(file: label, reason: error.reason))
            } catch {
                failures.append(NativeToolDeclarationFailure(
                    file: label, reason: error.localizedDescription))
            }
        }
        return NativeCapabilityToolCatalog(declarations: declarations, failures: failures)
    }

    static func definitions(
        _ declarations: [NativeToolDeclaration],
        base: @escaping @Sendable () -> URL?,
        client: NativeHTTPToolClient = .live
    ) -> [NativeToolDefinition] {
        declarations.filter(\.enabled).map { definition($0, base: base, client: client) }
    }

    static func definition(
        _ declaration: NativeToolDeclaration,
        base: @escaping @Sendable () -> URL?,
        client: NativeHTTPToolClient = .live
    ) -> NativeToolDefinition {
        NativeToolDefinition(
            id: declaration.id,
            name: declaration.name,
            title: declaration.title,
            description: declaration.description,
            parameters: declaration.parameters,
            confirmation: declaration.confirmation,
            timeout: .seconds(declaration.timeoutSeconds),
            isAvailable: { resolve(declaration, base: base()) != nil },
            execute: { arguments in
                try await call(declaration, arguments: arguments, base: base(), client: client)
            })
    }

    static func descriptors(
        _ declarations: [NativeToolDeclaration], veraAPIConfigured: Bool
    ) -> [NativeChatToolDescriptor] {
        declarations.filter(\.enabled).map { declaration in
            let available = !declaration.needsAPIBase || veraAPIConfigured
            return NativeChatToolDescriptor(
                id: declaration.id,
                name: declaration.title,
                summary: declaration.description,
                available: available,
                setup: available
                    ? "Calls \(declaration.method.rawValue) \(declaration.endpoint)."
                    : "Set the vera-api base URL in the Connection tab to enable \(declaration.title).")
        }
    }

    static func resolve(_ declaration: NativeToolDeclaration, base: URL?) -> URL? {
        guard let url = URL(string: declaration.endpoint) else { return nil }
        guard url.scheme == nil else { return url }
        guard let base else { return nil }
        let parts = declaration.endpoint.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        var resolved = base
        for component in parts[0].split(separator: "/") where !component.isEmpty {
            resolved = resolved.appendingPathComponent(String(component))
        }
        guard parts.count > 1, !parts[1].isEmpty else { return resolved }
        guard var components = URLComponents(url: resolved, resolvingAgainstBaseURL: false) else {
            return resolved
        }
        components.percentEncodedQuery = String(parts[1])
        return components.url ?? resolved
    }

    private static func call(
        _ declaration: NativeToolDeclaration,
        arguments: [String: NativeJSONValue],
        base: URL?,
        client: NativeHTTPToolClient
    ) async throws -> NativeJSONValue {
        guard let endpoint = resolve(declaration, base: base) else {
            throw NativeToolError.unavailable
        }
        var target = endpoint
        var body: Data?
        switch declaration.method {
        case .get:
            guard var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false) else {
                throw NativeToolError.failed("The tool endpoint is not a valid URL")
            }
            var items = components.queryItems ?? []
            for key in arguments.keys.sorted() {
                guard let value = queryValue(arguments[key]) else { continue }
                items.append(URLQueryItem(name: key, value: value))
            }
            if !items.isEmpty { components.queryItems = items }
            guard let resolved = components.url else {
                throw NativeToolError.failed("The tool endpoint is not a valid URL")
            }
            target = resolved
        case .post:
            var payload: [String: Any] = [:]
            for (key, value) in arguments {
                guard let field = declaration.jsonFields[key] else {
                    payload[key] = value.any
                    continue
                }
                guard case .string(let text) = value,
                      let data = text.data(using: .utf8),
                      let decoded = try? JSONSerialization.jsonObject(with: data),
                      let object = decoded as? [String: Any] else {
                    throw NativeToolError.invalidArguments(
                        "Field '\(key)' must contain a JSON object")
                }
                payload[field] = object
            }
            guard let encoded = try? JSONSerialization.data(
                withJSONObject: payload, options: [.sortedKeys]) else {
                throw NativeToolError.invalidArguments("The arguments are not encodable as JSON")
            }
            body = encoded
        }
        let requestTimeout = max(declaration.timeoutSeconds - 1, 1)
        let data: Data
        let status: Int
        do {
            (data, status) = try await client.send(
                target, declaration.method.rawValue, body, requestTimeout)
        } catch let error as NativeToolError {
            throw error
        } catch {
            throw NativeToolError.failed("The request failed: \(error.localizedDescription)")
        }
        guard (200..<300).contains(status) else {
            throw NativeToolError.failed(
                "The request failed with HTTP \(status): \(errorDetail(data))")
        }
        return value(from: data)
    }

    static func value(from data: Data) -> NativeJSONValue {
        if data.count <= responseByteCap,
           let object = try? JSONSerialization.jsonObject(with: data),
           let parsed = try? NativeJSONValue(any: object) {
            if case .object = parsed { return parsed }
            return .object(["result": parsed])
        }
        let (text, truncated) = boundedText(data)
        return .object(["text": .string(text), "truncated": .bool(truncated)])
    }

    static func boundedText(_ data: Data) -> (String, Bool) {
        guard data.count > responseByteCap else {
            return (String(decoding: data, as: UTF8.self), false)
        }
        var slice = data.prefix(responseByteCap)
        while !slice.isEmpty, String(data: slice, encoding: .utf8) == nil {
            slice = slice.dropLast()
        }
        return (String(decoding: slice, as: UTF8.self), true)
    }

    static func errorDetail(_ data: Data) -> String {
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = object["detail"] as? String, !detail.isEmpty {
            return detail
        }
        let (text, _) = boundedText(data)
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "no response body" : String(trimmed.prefix(300))
    }

    private static func queryValue(_ value: NativeJSONValue?) -> String? {
        switch value {
        case .string(let text):
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        case .bool(let flag):
            return flag ? "true" : "false"
        default:
            return nil
        }
    }

    static func parse(data: Data, source: String) throws -> NativeToolDeclaration {
        guard let any = try? JSONSerialization.jsonObject(with: data),
              let object = any as? [String: Any] else {
            throw NativeToolDeclarationError(reason: "The file is not a JSON object")
        }
        return try parse(object: object, source: source)
    }

    static func parse(object: [String: Any], source: String) throws -> NativeToolDeclaration {
        let name = text(object["name"])
        guard !name.isEmpty else {
            throw NativeToolDeclarationError(reason: "A non-empty 'name' is required")
        }
        guard isFunctionName(name) else {
            throw NativeToolDeclarationError(
                reason: "'name' must start with a letter and use only letters, digits, or underscores")
        }
        let title = text(object["title"])
        guard !title.isEmpty else {
            throw NativeToolDeclarationError(reason: "A non-empty 'title' is required")
        }
        let description = text(object["description"])
        guard !description.isEmpty else {
            throw NativeToolDeclarationError(reason: "A non-empty 'description' is required")
        }
        guard let schema = object["parameters"] as? [String: Any] else {
            throw NativeToolDeclarationError(reason: "'parameters' must be a JSON Schema object")
        }
        guard (schema["type"] as? String) == "object" else {
            throw NativeToolDeclarationError(reason: "'parameters.type' must be \"object\"")
        }
        guard let rawProperties = schema["properties"] as? [String: Any] else {
            throw NativeToolDeclarationError(reason: "'parameters.properties' must be an object")
        }
        if let additional = schema["additionalProperties"] {
            guard let flag = additional as? Bool, flag == false else {
                throw NativeToolDeclarationError(
                    reason: "'parameters.additionalProperties' must be false")
            }
        }
        var properties: [NativeToolProperty] = []
        for key in rawProperties.keys.sorted() {
            guard let entry = rawProperties[key] as? [String: Any] else {
                throw NativeToolDeclarationError(reason: "Property '\(key)' must be an object")
            }
            guard let rawType = entry["type"] as? String else {
                throw NativeToolDeclarationError(reason: "Property '\(key)' needs a 'type'")
            }
            guard let type = NativeToolPropertyType(rawValue: rawType) else {
                throw NativeToolDeclarationError(
                    reason: "Property '\(key)' uses unsupported type '\(rawType)'; use string or boolean")
            }
            properties.append(NativeToolProperty(
                name: key, type: type, description: text(entry["description"])))
        }
        var required: [String] = []
        if let raw = schema["required"] {
            guard let names = raw as? [String] else {
                throw NativeToolDeclarationError(
                    reason: "'parameters.required' must be an array of strings")
            }
            for entry in names {
                guard properties.contains(where: { $0.name == entry }) else {
                    throw NativeToolDeclarationError(
                        reason: "'required' names '\(entry)', which is not a declared property")
                }
            }
            required = names.sorted()
        }
        let endpoint = text(object["endpoint"])
        guard !endpoint.isEmpty else {
            throw NativeToolDeclarationError(reason: "An 'endpoint' is required")
        }
        guard let endpointURL = URL(string: endpoint) else {
            throw NativeToolDeclarationError(reason: "'endpoint' is not a valid URL or path")
        }
        if let scheme = endpointURL.scheme?.lowercased() {
            guard scheme == "http" || scheme == "https" else {
                throw NativeToolDeclarationError(
                    reason: "'endpoint' must be an http or https URL, or a path")
            }
            guard endpointURL.host?.isEmpty == false else {
                throw NativeToolDeclarationError(reason: "'endpoint' needs a host")
            }
        }
        guard let rawMethod = object["method"] as? String,
              let method = NativeToolHTTPMethod(rawValue: rawMethod.uppercased()) else {
            throw NativeToolDeclarationError(reason: "'method' must be \"GET\" or \"POST\"")
        }
        guard let rawConfirmation = object["confirmation"] as? String,
              let confirmation = NativeToolConfirmation(rawValue: rawConfirmation.lowercased()) else {
            throw NativeToolDeclarationError(
                reason: "'confirmation' must be \"none\" or \"required\"")
        }
        var timeout = defaultTimeoutSeconds
        if let raw = object["timeout_s"] {
            guard let number = raw as? NSNumber, !(raw is Bool) else {
                throw NativeToolDeclarationError(reason: "'timeout_s' must be a number")
            }
            let value = number.doubleValue
            guard value >= 1, value <= maximumTimeoutSeconds else {
                throw NativeToolDeclarationError(
                    reason: "'timeout_s' must be between 1 and \(Int(maximumTimeoutSeconds))")
            }
            timeout = value
        }
        var enabled = true
        if let raw = object["enabled"] {
            guard let flag = raw as? Bool else {
                throw NativeToolDeclarationError(reason: "'enabled' must be true or false")
            }
            enabled = flag
        }
        var jsonFields: [String: String] = [:]
        if let raw = object["json_fields"] {
            guard let mapping = raw as? [String: String] else {
                throw NativeToolDeclarationError(
                    reason: "'json_fields' must map a declared string field to a request body field")
            }
            guard method == .post else {
                throw NativeToolDeclarationError(reason: "'json_fields' requires the POST method")
            }
            var claimed: [String: String] = [:]
            for key in mapping.keys.sorted() {
                let field = mapping[key] ?? ""
                guard properties.contains(where: { $0.name == key && $0.type == .string }) else {
                    throw NativeToolDeclarationError(
                        reason: "'json_fields' names '\(key)', which is not a declared string property")
                }
                guard !field.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    throw NativeToolDeclarationError(
                        reason: "'json_fields' needs a request body field name for '\(key)'")
                }
                if let existing = claimed[field] {
                    throw NativeToolDeclarationError(
                        reason: "'json_fields' maps both '\(existing)' and '\(key)' onto the request body field '\(field)'")
                }
                claimed[field] = key
                if field != key, properties.contains(where: { $0.name == field }) {
                    throw NativeToolDeclarationError(
                        reason: "'json_fields' sends '\(key)' as '\(field)', which would overwrite the declared property '\(field)'")
                }
            }
            jsonFields = mapping
        }
        return NativeToolDeclaration(
            name: name, title: title, description: description, properties: properties,
            required: required, endpoint: endpoint, method: method, confirmation: confirmation,
            timeoutSeconds: timeout, enabled: enabled, jsonFields: jsonFields, source: source)
    }

    private static func text(_ value: Any?) -> String {
        guard let value = value as? String else { return "" }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func isFunctionName(_ value: String) -> Bool {
        guard value.count <= 64, let first = value.first, first.isLetter || first == "_" else {
            return false
        }
        return value.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" }
    }

    static let bundledSource = "bundled"

    static let bundled: [NativeToolDeclaration] = bundledDefinitions.compactMap {
        try? parse(data: Data($0.utf8), source: bundledSource)
    }

    private static let bundledDefinitions: [String] = [
        """
        {
          "name": "actions_registry",
          "title": "List available actions",
          "description": "List the action verbs this deployment can propose, with the arguments each one takes. Call this before proposing an action so the verb and arguments are real.",
          "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": false},
          "endpoint": "/actions/registry",
          "method": "GET",
          "confirmation": "none"
        }
        """,
        """
        {
          "name": "propose_action",
          "title": "Propose an action",
          "description": "Propose an action for review. The verb must come from the action registry, and both title and body are required. Pass args_json as a JSON object string holding that verb's arguments, for example {\\"key\\": \\"value\\"}, when the verb takes any. The proposal is queued for a decision, not run.",
          "parameters": {
            "type": "object",
            "properties": {
              "verb": {"type": "string", "description": "An action verb returned by the action registry"},
              "args_json": {"type": "string", "description": "A JSON object string holding the verb's arguments"},
              "title": {"type": "string", "description": "Short headline for the proposal"},
              "body": {"type": "string", "description": "One or two sentences explaining why the action is worth taking"}
            },
            "required": ["verb", "title", "body"],
            "additionalProperties": false
          },
          "json_fields": {"args_json": "args"},
          "endpoint": "/actions/propose_card",
          "method": "POST",
          "confirmation": "none"
        }
        """,
        """
        {
          "name": "author_skill",
          "title": "Write a skill",
          "description": "Write or replace a named skill document. Use this only when asked to capture a repeatable procedure.",
          "parameters": {
            "type": "object",
            "properties": {
              "name": {"type": "string", "description": "Skill name"},
              "content": {"type": "string", "description": "Full skill body"},
              "description": {"type": "string", "description": "One line summary of what the skill covers"}
            },
            "required": ["name", "content"],
            "additionalProperties": false
          },
          "endpoint": "/authoring/skill",
          "method": "POST",
          "confirmation": "required"
        }
        """,
        """
        {
          "name": "author_heartbeat",
          "title": "Update the heartbeat document",
          "description": "Replace the heartbeat continuity document with new content. Read carefully before rewriting: this replaces the whole document.",
          "parameters": {
            "type": "object",
            "properties": {
              "content": {"type": "string", "description": "Full replacement content"}
            },
            "required": ["content"],
            "additionalProperties": false
          },
          "endpoint": "/authoring/heartbeat",
          "method": "POST",
          "confirmation": "required"
        }
        """,
        """
        {
          "name": "journal_read",
          "title": "Read the journal",
          "description": "Read standing journal entries. Pass months as a number of months of history to include, written as a string.",
          "parameters": {
            "type": "object",
            "properties": {
              "months": {"type": "string", "description": "Months of history to include, for example \\"3\\""}
            },
            "required": [],
            "additionalProperties": false
          },
          "endpoint": "/journal",
          "method": "GET",
          "confirmation": "none"
        }
        """,
        """
        {
          "name": "journal_commit",
          "title": "Commit a journal entry",
          "description": "Record a standing commitment in the journal. Use this only for something meant to persist beyond this conversation.",
          "parameters": {
            "type": "object",
            "properties": {
              "text": {"type": "string", "description": "The commitment to record"},
              "origin": {"type": "string", "description": "Where the commitment came from"}
            },
            "required": ["text"],
            "additionalProperties": false
          },
          "endpoint": "/journal/commit",
          "method": "POST",
          "confirmation": "required"
        }
        """,
    ]
}
