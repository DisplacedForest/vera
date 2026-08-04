import Foundation

struct NativeChatConfig: Sendable, Equatable {
    let baseURL: URL
    let apiKey: String?
    let model: String
    let chatTemplateKwargs: String?
    let streaming: Bool

    init(
        baseURL: URL, apiKey: String?, model: String, chatTemplateKwargs: String?,
        streaming: Bool = true
    ) {
        self.baseURL = baseURL
        self.apiKey = apiKey
        self.model = model
        self.chatTemplateKwargs = chatTemplateKwargs
        self.streaming = streaming
    }

    var completionsURL: URL { baseURL.appendingPathComponent("chat/completions") }
    var modelsURL: URL { baseURL.appendingPathComponent("models") }

    static func load() -> NativeChatConfig? {
        let env = ProcessInfo.processInfo.environment
        let file = ConfigFile.read()
        func value(_ envKey: String, _ fileKey: String) -> String? {
            if let raw = env[envKey]?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty {
                return raw
            }
            if let raw = (file[fileKey] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty {
                return raw
            }
            return nil
        }
        guard let rawBase = value("VERA_MODEL_BASE", "model_base"),
              let base = URL(string: rawBase),
              base.scheme == "http" || base.scheme == "https",
              base.lastPathComponent == "v1",
              let model = value("VERA_MODEL", "model") else { return nil }
        return NativeChatConfig(
            baseURL: base,
            apiKey: value("VERA_MODEL_API_KEY", "model_api_key"),
            model: model,
            chatTemplateKwargs: value("VERA_CHAT_TEMPLATE_KWARGS", "chat_template_kwargs"))
    }

    func chatTemplateKwargsObject() -> [String: Any]? {
        guard let raw = chatTemplateKwargs,
              let data = raw.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              !object.isEmpty else { return nil }
        return object
    }
}

struct NativeChatMessage: Sendable, Equatable {
    let role: String
    let content: String
    let toolCallID: String?
    let toolCalls: [NativeChatToolCall]
    let images: [String]

    init(
        role: String, content: String, toolCallID: String? = nil,
        toolCalls: [NativeChatToolCall] = [], images: [String] = []
    ) {
        self.role = role
        self.content = content
        self.toolCallID = toolCallID
        self.toolCalls = toolCalls
        self.images = images
    }
}

struct NativeChatStreamSnapshot: Sendable, Equatable {
    let content: String
    let toolCalls: [NativeChatToolCall]
    let finishReason: String?
}

protocol NativeChatTransport: Sendable {
    func discoverModels() async throws -> [String]
    func stream(
        messages: [NativeChatMessage], model: String, tools: [NativeToolSchema]
    ) -> AsyncThrowingStream<NativeChatStreamSnapshot, Error>
}

struct NativeChatDeltaAccumulator: Sendable {
    private struct Builder: Sendable {
        var id = ""
        var name = ""
        var arguments = ""
    }

    private var content = ""
    private var builders: [Int: Builder] = [:]

    mutating func consume(_ payload: String) throws -> NativeChatStreamSnapshot {
        guard let data = payload.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NativeChatClient.ClientError.malformedEvent
        }
        if let error = object["error"] as? [String: Any] {
            throw NativeChatClient.ClientError.server((error["message"] as? String) ?? "The model endpoint reported an error")
        }
        guard let choice = (object["choices"] as? [[String: Any]])?.first else {
            throw NativeChatClient.ClientError.malformedEvent
        }
        let delta = choice["delta"] as? [String: Any]
        if let fragment = delta?["content"] as? String { content += fragment }
        if let calls = delta?["tool_calls"] as? [[String: Any]] {
            for call in calls {
                guard let index = call["index"] as? Int else {
                    throw NativeChatClient.ClientError.malformedEvent
                }
                var builder = builders[index] ?? Builder()
                if let id = call["id"] as? String, !id.isEmpty { builder.id = id }
                if let function = call["function"] as? [String: Any] {
                    if let name = function["name"] as? String { builder.name += name }
                    if let arguments = function["arguments"] as? String { builder.arguments += arguments }
                }
                builders[index] = builder
            }
        }
        let finishReason = choice["finish_reason"] as? String
        let toolCalls = builders.keys.sorted().compactMap { index -> NativeChatToolCall? in
            guard let builder = builders[index] else { return nil }
            return NativeChatToolCall(id: builder.id, name: builder.name, arguments: builder.arguments)
        }
        if finishReason == "tool_calls", toolCalls.contains(where: { $0.id.isEmpty || $0.name.isEmpty }) {
            throw NativeChatClient.ClientError.malformedEvent
        }
        return NativeChatStreamSnapshot(content: content, toolCalls: toolCalls, finishReason: finishReason)
    }
}

struct ChatSSEDecoder: Sendable {
    private var buffer = Data()

    mutating func feed(_ data: Data, finishing: Bool = false) throws -> [String] {
        buffer.append(data)
        var payloads: [String] = []
        while let boundary = Self.boundary(in: buffer) {
            let event = buffer.prefix(boundary.start)
            buffer.removeSubrange(..<boundary.end)
            if let payload = Self.payload(from: event) { payloads.append(payload) }
        }
        if finishing, !buffer.isEmpty {
            if let payload = Self.payload(from: buffer) { payloads.append(payload) }
            buffer.removeAll(keepingCapacity: false)
        }
        return payloads
    }

    private static func boundary(in data: Data) -> (start: Int, end: Int)? {
        let bytes = [UInt8](data)
        guard bytes.count >= 2 else { return nil }
        for i in 0..<(bytes.count - 1) {
            if bytes[i] == 10, bytes[i + 1] == 10 { return (i, i + 2) }
            if i + 3 < bytes.count,
               bytes[i] == 13, bytes[i + 1] == 10,
               bytes[i + 2] == 13, bytes[i + 3] == 10 { return (i, i + 4) }
        }
        return nil
    }

    private static func payload(from data: Data) -> String? {
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        let lines = text.split(whereSeparator: \.isNewline)
        let values = lines.compactMap { line -> String? in
            guard line.hasPrefix("data:") else { return nil }
            return String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces)
        }
        return values.isEmpty ? nil : values.joined(separator: "\n")
    }
}

struct NativeChatClient: NativeChatTransport, Sendable {
    enum ClientError: Error, LocalizedError, Equatable {
        case invalidResponse
        case http(Int, String)
        case malformedEvent
        case noUsableModels
        case server(String)
        case interrupted

        var errorDescription: String? {
            switch self {
            case .invalidResponse: return "The model endpoint returned an invalid response"
            case .http(let status, let detail):
                return detail.isEmpty ? "The model endpoint answered HTTP \(status)" : "The model endpoint answered HTTP \(status): \(detail)"
            case .malformedEvent: return "The model endpoint returned a malformed stream"
            case .noUsableModels: return "The endpoint responded, but none of its model entries had a usable identifier"
            case .server(let detail): return detail
            case .interrupted: return "The response stream ended before completion"
            }
        }
    }

    let config: NativeChatConfig
    var session: URLSession = .shared

    func discoverModels() async throws -> [String] {
        var request = URLRequest(url: config.modelsURL)
        request.timeoutInterval = 10
        applyAuthorization(to: &request)
        let (data, response) = try await session.data(for: request)
        let status = try statusCode(response)
        guard (200..<300).contains(status) else {
            throw ClientError.http(status, Self.errorDetail(data))
        }
        return try Self.modelIDs(from: data)
    }

    func request(
        messages: [NativeChatMessage], model: String, tools: [NativeToolSchema] = []
    ) throws -> URLRequest {
        var payload: [String: Any] = [
            "model": model,
            "stream": config.streaming,
            "messages": messages.map(Self.messageObject),
        ]
        if !tools.isEmpty {
            payload["tools"] = tools.map(\.requestObject)
            payload["tool_choice"] = "auto"
        }
        if let kwargs = config.chatTemplateKwargsObject() { payload["chat_template_kwargs"] = kwargs }
        var request = URLRequest(url: config.completionsURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        applyAuthorization(to: &request)
        return request
    }

    func stream(
        messages: [NativeChatMessage], model: String, tools: [NativeToolSchema]
    ) -> AsyncThrowingStream<NativeChatStreamSnapshot, Error> {
        guard config.streaming else {
            return AsyncThrowingStream { continuation in
                let task = Task {
                    do {
                        let request = try request(messages: messages, model: model, tools: tools)
                        let (data, response) = try await session.data(for: request)
                        let status = try statusCode(response)
                        guard (200..<300).contains(status) else {
                            throw ClientError.http(status, Self.errorDetail(data))
                        }
                        continuation.yield(try Self.completeSnapshot(from: data))
                        continuation.finish()
                    } catch is CancellationError {
                        continuation.finish()
                    } catch {
                        continuation.finish(throwing: error)
                    }
                }
                continuation.onTermination = { _ in task.cancel() }
            }
        }
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let request = try request(messages: messages, model: model, tools: tools)
                    let (bytes, response) = try await session.bytes(for: request)
                    let status = try statusCode(response)
                    guard (200..<300).contains(status) else {
                        var body = Data()
                        for try await byte in bytes { body.append(byte) }
                        throw ClientError.http(status, Self.errorDetail(body))
                    }
                    var decoder = ChatSSEDecoder()
                    var chunk = Data()
                    var accumulator = NativeChatDeltaAccumulator()
                    var lastSnapshot = NativeChatStreamSnapshot(content: "", toolCalls: [], finishReason: nil)
                    var completed = false
                    for try await byte in bytes {
                        try Task.checkCancellation()
                        chunk.append(byte)
                        if chunk.count >= 1024 || byte == 10 {
                            for payload in try decoder.feed(chunk) {
                                chunk.removeAll(keepingCapacity: true)
                                if payload == "[DONE]" { completed = true; break }
                                lastSnapshot = try accumulator.consume(payload)
                                continuation.yield(lastSnapshot)
                            }
                            chunk.removeAll(keepingCapacity: true)
                        }
                        if completed { break }
                    }
                    if !chunk.isEmpty {
                        for payload in try decoder.feed(chunk, finishing: true) {
                            if payload == "[DONE]" { completed = true; break }
                            lastSnapshot = try accumulator.consume(payload)
                            continuation.yield(lastSnapshot)
                        }
                    }
                    guard completed else { throw ClientError.interrupted }
                    if !lastSnapshot.toolCalls.isEmpty, lastSnapshot.finishReason != "tool_calls" {
                        throw ClientError.malformedEvent
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    static func completeSnapshot(from data: Data) throws -> NativeChatStreamSnapshot {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ClientError.invalidResponse
        }
        if let error = object["error"] as? [String: Any] {
            throw ClientError.server((error["message"] as? String) ?? "The model endpoint reported an error")
        }
        guard let choice = (object["choices"] as? [[String: Any]])?.first,
              let message = choice["message"] as? [String: Any] else {
            throw ClientError.invalidResponse
        }
        let content = (message["content"] as? String) ?? ""
        let calls = (message["tool_calls"] as? [[String: Any]] ?? []).map { call -> NativeChatToolCall in
            let function = call["function"] as? [String: Any]
            return NativeChatToolCall(
                id: (call["id"] as? String) ?? "",
                name: (function?["name"] as? String) ?? "",
                arguments: (function?["arguments"] as? String) ?? "")
        }
        let finishReason = choice["finish_reason"] as? String
        if !calls.isEmpty,
           finishReason != "tool_calls" || calls.contains(where: { $0.id.isEmpty || $0.name.isEmpty }) {
            throw ClientError.invalidResponse
        }
        return NativeChatStreamSnapshot(content: content, toolCalls: calls, finishReason: finishReason)
    }

    static func content(from payload: String) throws -> String? {
        var accumulator = NativeChatDeltaAccumulator()
        let snapshot = try accumulator.consume(payload)
        return snapshot.content.isEmpty ? nil : snapshot.content
    }

    static func modelIDs(from data: Data) throws -> [String] {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = object["data"] as? [[String: Any]] else { throw ClientError.invalidResponse }
        let models = rows.compactMap { row -> String? in
            guard let id = row["id"] as? String else { return nil }
            let value = id.trimmingCharacters(in: .whitespacesAndNewlines)
            return value.isEmpty ? nil : value
        }
        if !rows.isEmpty, models.isEmpty { throw ClientError.noUsableModels }
        guard models.count == rows.count, Set(models).count == models.count else {
            throw ClientError.invalidResponse
        }
        return models.sorted()
    }

    private func applyAuthorization(to request: inout URLRequest) {
        if let key = config.apiKey, !key.isEmpty {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        }
    }

    static func messageObject(_ message: NativeChatMessage) -> [String: Any] {
        var object: [String: Any] = ["role": message.role]
        if message.images.isEmpty {
            object["content"] = message.content.isEmpty ? NSNull() : message.content
        } else {
            var parts: [[String: Any]] = []
            if !message.content.isEmpty {
                parts.append(["type": "text", "text": message.content])
            }
            parts.append(contentsOf: message.images.map {
                ["type": "image_url", "image_url": ["url": $0]]
            })
            object["content"] = parts
        }
        if let toolCallID = message.toolCallID { object["tool_call_id"] = toolCallID }
        if !message.toolCalls.isEmpty {
            object["tool_calls"] = message.toolCalls.map { call in
                [
                    "id": call.id,
                    "type": "function",
                    "function": ["name": call.name, "arguments": call.arguments],
                ]
            }
        }
        return object
    }

    private func statusCode(_ response: URLResponse) throws -> Int {
        guard let http = response as? HTTPURLResponse else { throw ClientError.invalidResponse }
        return http.statusCode
    }

    private static func errorDetail(_ data: Data) -> String {
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let error = object["error"] as? [String: Any], let message = error["message"] as? String {
            return message
        }
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
}
