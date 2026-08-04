import Foundation

struct NativeWebToolClient: Sendable {
    let post: @Sendable (URL, Data, TimeInterval) async throws -> (Data, Int)

    static let live = NativeWebToolClient { url, body, timeout in
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        request.timeoutInterval = timeout
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        return (data, status)
    }
}

enum NativeWebTools {
    static let searchToolID = "web-search"
    static let researchToolID = "deep-research"
    static let researchToolName = "deep_research"
    static let researchTimeout = Duration.seconds(300)
    static let researchCapableIDs: Set<String> = [searchToolID, researchToolID]

    static func definitions(
        base: @escaping @Sendable () -> URL?,
        client: NativeWebToolClient = .live
    ) -> [NativeToolDefinition] {
        let available: @Sendable () -> Bool = { base() != nil }
        return [
            NativeToolDefinition(
                id: searchToolID,
                name: "web_search",
                title: "Search the web",
                description: "Search the web for current information. Returns result pages with title, url, and extracted content. Answer from the returned content and cite the result URLs you used.",
                parameters: objectSchema(
                    properties: [
                        "query": .object(["type": .string("string"), "description": .string("The search query")]),
                        "max_results": .object(["type": .string("integer"), "description": .string("Optional number of results, 1 to 10, default 5")]),
                    ], required: ["query"]),
                confirmation: .none,
                isAvailable: available,
                execute: { arguments in
                    guard let query = arguments.searchString("query") else {
                        throw NativeToolError.invalidArguments("A query is required")
                    }
                    var body: [String: Any] = ["query": query]
                    if let requested = arguments.searchInt("max_results") {
                        body["max_results"] = min(max(requested, 1), 10)
                    }
                    return try await post(path: "search", body: body, requestTimeout: 18, base: base, client: client)
                }),
            NativeToolDefinition(
                id: researchToolID,
                name: researchToolName,
                title: "Deep research",
                description: "Run a multi-step web research pass on a question. Takes minutes and returns a cited report plus a numbered source list. Present the report's findings and keep its bracketed [n] citations, which match the numbered sources.",
                parameters: objectSchema(
                    properties: [
                        "query": .object(["type": .string("string"), "description": .string("The research question")]),
                    ], required: ["query"]),
                confirmation: .none,
                timeout: researchTimeout,
                isAvailable: available,
                execute: { arguments in
                    guard let query = arguments.searchString("query") else {
                        throw NativeToolError.invalidArguments("A query is required")
                    }
                    let value = try await post(
                        path: "research", body: ["query": query], requestTimeout: 290,
                        base: base, client: client)
                    guard case .object(let fields) = value else { return value }
                    var trimmed: [String: NativeJSONValue] = [:]
                    for key in ["report", "sources", "errors"] {
                        if let field = fields[key] { trimmed[key] = field }
                    }
                    return .object(trimmed)
                }),
        ]
    }

    private static func post(
        path: String, body: [String: Any], requestTimeout: TimeInterval,
        base: @Sendable () -> URL?, client: NativeWebToolClient
    ) async throws -> NativeJSONValue {
        guard let base = base() else { throw NativeToolError.unavailable }
        let url = base.appendingPathComponent(path)
        let payload = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        let data: Data
        let status: Int
        do {
            (data, status) = try await client.post(url, payload, requestTimeout)
        } catch let error as NativeToolError {
            throw error
        } catch {
            throw NativeToolError.failed("The vera-api request failed: \(error.localizedDescription)")
        }
        guard (200..<300).contains(status) else {
            throw NativeToolError.failed(Self.errorDetail(data: data, status: status))
        }
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let value = try? NativeJSONValue(any: object) else {
            throw NativeToolError.failed("The vera-api response was not valid JSON")
        }
        return value
    }

    static func errorDetail(data: Data, status: Int) -> String {
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = object["detail"] as? String, !detail.isEmpty {
            return detail
        }
        return "The vera-api request failed with HTTP \(status)"
    }

    private static func objectSchema(
        properties: [String: NativeJSONValue], required: [String]
    ) -> NativeJSONValue {
        .object([
            "type": .string("object"),
            "properties": .object(properties),
            "required": .array(required.map(NativeJSONValue.string)),
            "additionalProperties": .bool(false),
        ])
    }
}

enum NativeResearchSources {
    static func lift(_ activities: [NativeToolActivity]) -> [PulseSource] {
        guard let activity = activities.last(where: {
            $0.name == NativeWebTools.researchToolName && $0.state == .succeeded
        }), let raw = activity.result, let data = raw.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let entries = object["sources"] as? [[String: Any]] else { return [] }
        return entries.compactMap { entry in
            guard let n = (entry["n"] as? NSNumber)?.intValue,
                  let url = entry["url"] as? String, !url.isEmpty else { return nil }
            let title = (entry["title"] as? String) ?? ""
            return PulseSource(n: n, title: title, url: url)
        }
    }
}

private extension Dictionary where Key == String, Value == NativeJSONValue {
    func searchString(_ key: String) -> String? {
        guard case .string(let value)? = self[key] else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    func searchInt(_ key: String) -> Int? {
        guard case .number(let value)? = self[key] else { return nil }
        return Int(value)
    }
}
