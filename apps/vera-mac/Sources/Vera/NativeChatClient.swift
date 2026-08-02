import Foundation

struct NativeChatConfig: Sendable, Equatable {
    let baseURL: URL
    let apiKey: String?
    let model: String
    let chatTemplateKwargs: String?

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
}

protocol NativeChatTransport: Sendable {
    func discoverModels() async throws -> [String]
    func stream(messages: [NativeChatMessage], model: String) -> AsyncThrowingStream<String, Error>
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

    func request(messages: [NativeChatMessage], model: String) throws -> URLRequest {
        var payload: [String: Any] = [
            "model": model,
            "stream": true,
            "messages": messages.map { ["role": $0.role, "content": $0.content] },
        ]
        if let kwargs = config.chatTemplateKwargsObject() { payload["chat_template_kwargs"] = kwargs }
        var request = URLRequest(url: config.completionsURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        applyAuthorization(to: &request)
        return request
    }

    func stream(messages: [NativeChatMessage], model: String) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let request = try request(messages: messages, model: model)
                    let (bytes, response) = try await session.bytes(for: request)
                    let status = try statusCode(response)
                    guard (200..<300).contains(status) else {
                        var body = Data()
                        for try await byte in bytes { body.append(byte) }
                        throw ClientError.http(status, Self.errorDetail(body))
                    }
                    var decoder = ChatSSEDecoder()
                    var chunk = Data()
                    var accumulated = ""
                    var completed = false
                    for try await byte in bytes {
                        try Task.checkCancellation()
                        chunk.append(byte)
                        if chunk.count >= 1024 || byte == 10 {
                            for payload in try decoder.feed(chunk) {
                                chunk.removeAll(keepingCapacity: true)
                                if payload == "[DONE]" { completed = true; break }
                                if let delta = try Self.content(from: payload) {
                                    accumulated += delta
                                    continuation.yield(accumulated)
                                }
                            }
                            chunk.removeAll(keepingCapacity: true)
                        }
                        if completed { break }
                    }
                    if !chunk.isEmpty {
                        for payload in try decoder.feed(chunk, finishing: true) {
                            if payload == "[DONE]" { completed = true; break }
                            if let delta = try Self.content(from: payload) {
                                accumulated += delta
                                continuation.yield(accumulated)
                            }
                        }
                    }
                    guard completed else { throw ClientError.interrupted }
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

    static func content(from payload: String) throws -> String? {
        guard let data = payload.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ClientError.malformedEvent
        }
        if let error = object["error"] as? [String: Any] {
            throw ClientError.server((error["message"] as? String) ?? "The model endpoint reported an error")
        }
        guard let choice = (object["choices"] as? [[String: Any]])?.first else {
            throw ClientError.malformedEvent
        }
        let delta = choice["delta"] as? [String: Any]
        if let content = delta?["content"] as? String { return content }
        if choice["finish_reason"] is String || delta?["role"] is String { return nil }
        return nil
    }

    static func modelIDs(from data: Data) throws -> [String] {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = object["data"] as? [[String: Any]] else { throw ClientError.invalidResponse }
        let models = rows.compactMap { ($0["id"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .sorted()
        if !rows.isEmpty, models.isEmpty { throw ClientError.noUsableModels }
        return models
    }

    private func applyAuthorization(to request: inout URLRequest) {
        if let key = config.apiKey, !key.isEmpty {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        }
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
