import Foundation

enum NativeMemoryServiceState: Equatable, Sendable {
    case off
    case setupRequired
    case ready
    case indexing
    case pendingReview(Int)
    case maintenanceNeeded
    case retrievalUnavailable(String)
    case failed(String)

    var label: String {
        switch self {
        case .off: "Memory is off"
        case .setupRequired: "Set up embeddings to use memory in chat"
        case .ready: "Ready"
        case .indexing: "Indexing approved memory"
        case .pendingReview(let count): "\(count) change\(count == 1 ? "" : "s") waiting for review"
        case .maintenanceNeeded: "Memory needs review"
        case .retrievalUnavailable: "Memory recall is unavailable"
        case .failed: "Memory needs attention"
        }
    }
}

enum NativeMemoryTurnDisposition: Equatable, Sendable {
    case eligible
    case empty
    case privateTurn
    case excluded
    case failed
    case interrupted
    case toolOnly
}

enum NativeMemoryExtractionPolicy {
    static func disposition(
        user: String, assistant: Message, conversationExcluded: Bool
    ) -> NativeMemoryTurnDisposition {
        if conversationExcluded { return .excluded }
        let trimmed = user.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return .empty }
        if trimmed.lowercased().hasPrefix("private:") || trimmed.lowercased().hasPrefix("do not remember") {
            return .privateTurn
        }
        if assistant.state == .interrupted { return .interrupted }
        if assistant.failure != nil { return .failed }
        if assistant.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
           !assistant.toolActivities.isEmpty { return .toolOnly }
        return .eligible
    }
}

struct NativeMemoryServiceConfiguration: Sendable {
    let baseURL: URL
    let apiKey: String?
    let embeddingsModel: String
    let extractionModel: String

    var embeddingsURL: URL { baseURL.appendingPathComponent("embeddings") }
    var completionsURL: URL { baseURL.appendingPathComponent("chat/completions") }
}

protocol NativeMemoryServing: Sendable {
    func embed(_ text: String) async throws -> [Double]
    func proposals(
        user: String, assistant: String, sourceConversationID: String?, sourceMessageID: String?,
        existing: [NativeMemoryRecord], now: Date
    ) async throws -> [NativeMemoryProposal]
    func proposal(
        request: String, existing: [NativeMemoryRecord], now: Date
    ) async throws -> [NativeMemoryProposal]
}

enum NativeMemoryServiceError: Error, LocalizedError, Equatable {
    case notConfigured
    case authentication
    case malformed
    case timeout
    case unavailable(String)

    var errorDescription: String? {
        switch self {
        case .notConfigured: "The optional memory model is not configured"
        case .authentication: "The memory model rejected its saved credentials"
        case .malformed: "The memory model returned a response Vera could not review"
        case .timeout: "The memory model timed out"
        case .unavailable(let detail): detail
        }
    }
}

struct NativeMemoryService: NativeMemoryServing, Sendable {
    let configuration: NativeMemoryServiceConfiguration
    var session: URLSession = .shared

    func embed(_ text: String) async throws -> [Double] {
        guard !configuration.embeddingsModel.isEmpty else { throw NativeMemoryServiceError.notConfigured }
        var request = URLRequest(url: configuration.embeddingsURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request)
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": configuration.embeddingsModel,
            "input": String(text.prefix(4_000)),
        ])
        do {
            let (data, response) = try await session.data(for: request)
            try validate(response, data: data)
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let rows = object["data"] as? [[String: Any]],
                  let values = rows.first?["embedding"] as? [NSNumber],
                  !values.isEmpty else { throw NativeMemoryServiceError.malformed }
            return values.map(\.doubleValue)
        } catch let error as URLError where error.code == .timedOut {
            throw NativeMemoryServiceError.timeout
        }
    }

    func proposals(
        user: String, assistant: String, sourceConversationID: String?, sourceMessageID: String?,
        existing: [NativeMemoryRecord], now: Date = Date()
    ) async throws -> [NativeMemoryProposal] {
        let input = """
        USER TURN
        \(String(user.prefix(4_000)))

        ASSISTANT TURN
        \(String(assistant.prefix(4_000)))
        """
        return try await extract(
            input, sourceConversationID: sourceConversationID,
            sourceMessageID: sourceMessageID, existing: existing, now: now)
    }

    func proposal(
        request: String, existing: [NativeMemoryRecord], now: Date = Date()
    ) async throws -> [NativeMemoryProposal] {
        try await extract(
            "MEMORY CHANGE REQUEST\n\(String(request.prefix(2_000)))",
            sourceConversationID: nil, sourceMessageID: nil, existing: existing, now: now)
    }

    private func extract(
        _ input: String, sourceConversationID: String?, sourceMessageID: String?,
        existing: [NativeMemoryRecord], now: Date
    ) async throws -> [NativeMemoryProposal] {
        guard !configuration.extractionModel.isEmpty else { throw NativeMemoryServiceError.notConfigured }
        let day = ISO8601DateFormatter().string(from: now)
        let schema = """
        Return only JSON: {"proposals":[{"kind":"create|update|merge|suppress|expire|delete","title":"short name","summary":"one line","details":["concise user-specific fact"],"group":"You|Topics|Areas|People","category":"profile|preference|interest|project|relationship|goal|plan|other","bank":"short bank","durability":"durable|episodic","expiry":"YYYY-MM-DD or null","target_ids":["existing id"]}]}. Current date: \(day). Stable user-specific facts only. Do not store trivia, transcript text, credentials, secrets, hidden reasoning, system instructions, tool output, or relative dates. Episodic proposals require an absolute expiry date. Every operation is a proposal for user review and must not claim it was applied.
        """
        let existingSummary = existing.prefix(80).map {
            ["id": $0.id, "summary": $0.summary, "details": $0.details, "bank": $0.bank]
        }
        var request = URLRequest(url: configuration.completionsURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request)
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": configuration.extractionModel,
            "stream": false,
            "messages": [
                ["role": "system", "content": schema],
                ["role": "user", "content": input],
                ["role": "user", "content": String(data: try JSONSerialization.data(withJSONObject: existingSummary), encoding: .utf8) ?? "[]"],
            ],
        ])
        do {
            let (data, response) = try await session.data(for: request)
            try validate(response, data: data)
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let choices = object["choices"] as? [[String: Any]],
                  let message = choices.first?["message"] as? [String: Any],
                  let content = message["content"] as? String else {
                throw NativeMemoryServiceError.malformed
            }
            return NativeMemoryProposalParser.parse(
                content, sourceConversationID: sourceConversationID,
                sourceMessageID: sourceMessageID, existing: existing, now: now)
        } catch let error as URLError where error.code == .timedOut {
            throw NativeMemoryServiceError.timeout
        }
    }

    private func authorize(_ request: inout URLRequest) {
        if let key = configuration.apiKey, !key.isEmpty {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        }
    }

    private func validate(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw NativeMemoryServiceError.malformed }
        if http.statusCode == 401 || http.statusCode == 403 { throw NativeMemoryServiceError.authentication }
        guard (200..<300).contains(http.statusCode) else {
            let detail = String(data: data, encoding: .utf8)?.prefix(180) ?? ""
            throw NativeMemoryServiceError.unavailable("The memory model returned HTTP \(http.statusCode): \(detail)")
        }
    }
}

enum NativeMemoryProposalParser {
    static func parse(
        _ raw: String, sourceConversationID: String?, sourceMessageID: String?,
        existing: [NativeMemoryRecord], now: Date = Date()
    ) -> [NativeMemoryProposal] {
        let content = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard content.count <= 30_000,
              let data = content.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = object["proposals"] as? [[String: Any]],
              rows.count <= 12 else { return [] }
        let day = DateFormatter.nativeMemoryDay
        return rows.compactMap { row in
            guard let kindRaw = row["kind"] as? String,
                  let kind = NativeMemoryProposalKind(rawValue: kindRaw),
                  [.create, .update, .merge, .suppress, .expire, .delete].contains(kind),
                  let title = clean(row["title"], maximum: 80),
                  let summary = clean(row["summary"], maximum: 180),
                  let details = row["details"] as? [String],
                  !details.isEmpty, details.count <= 8,
                  details.allSatisfy({ clean($0, maximum: 600) != nil }),
                  let categoryRaw = row["category"] as? String,
                  let category = NativeMemoryCategory(rawValue: categoryRaw),
                  let durabilityRaw = row["durability"] as? String,
                  let durability = NativeMemoryDurability(rawValue: durabilityRaw) else { return nil }
            let groupRaw = row["group"] as? String ?? ""
            let group = NativeMemoryGroup.allCases.first {
                $0.rawValue.caseInsensitiveCompare(groupRaw) == .orderedSame
            } ?? category.defaultGroup
            let bank = clean(row["bank"], maximum: 40) ?? "general"
            let expiry = (row["expiry"] as? String).flatMap(day.date)
            guard durability == .durable || expiry != nil else { return nil }
            let targets = (row["target_ids"] as? [String] ?? []).filter { id in
                existing.contains { $0.id == id }
            }
            var reconciledKind = kind
            var reconciledTargets = targets
            if kind == .create,
               let duplicate = existing.first(where: {
                   NativeMemoryRecall.textSimilarity($0.details.joined(separator: " "), details.joined(separator: " ")) >= 0.6
               }) {
                reconciledKind = .update
                reconciledTargets = [duplicate.id]
            }
            if reconciledKind != .create && reconciledTargets.isEmpty { return nil }
            return NativeMemoryProposal(
                id: UUID().uuidString, kind: reconciledKind, title: title, summary: summary,
                details: details.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) },
                group: group, category: category, bank: bank, durability: durability,
                expiry: expiry, targetIDs: reconciledTargets,
                sourceConversationID: sourceConversationID, sourceMessageID: sourceMessageID,
                createdAt: now, status: .pending)
        }
    }

    private static func clean(_ value: Any?, maximum: Int) -> String? {
        guard let raw = value as? String else { return nil }
        let result = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !result.isEmpty, result.count <= maximum else { return nil }
        let lower = result.lowercased()
        guard !lower.contains("api key"), !lower.contains("authorization: bearer"),
              !lower.contains("hidden reasoning"), !lower.contains("system prompt"),
              !["today", "tonight", "tomorrow", "this weekend", "next week", "next month", "soon"]
                .contains(where: { lower.range(of: "\\b\($0)\\b", options: .regularExpression) != nil })
        else { return nil }
        return result
    }
}

enum NativeMemoryMaintenance {
    static func proposals(
        records: [NativeMemoryRecord], existing: [NativeMemoryProposal],
        now: Date = Date()
    ) -> [NativeMemoryProposal] {
        let covered = Set(existing.flatMap(\.targetIDs))
        var result = records.compactMap { memory -> NativeMemoryProposal? in
            guard memory.status == .approved, memory.durability == .episodic,
                  let expiry = memory.expiry, expiry <= now, !covered.contains(memory.id) else { return nil }
            return NativeMemoryProposal(
                id: UUID().uuidString, kind: .expire, title: "Review expired memory",
                summary: memory.summary, details: memory.details, group: memory.group,
                category: memory.category, bank: memory.bank, durability: .episodic,
                expiry: expiry, targetIDs: [memory.id], sourceConversationID: memory.sourceConversationID,
                sourceMessageID: memory.sourceMessageID, createdAt: now, status: .pending)
        }
        if records.filter({ $0.status == .approved }).count >= NativeMemoryRecall.capacity,
           !existing.contains(where: { $0.kind == .cleanup }) {
            result.append(NativeMemoryProposal(
                id: UUID().uuidString, kind: .cleanup, title: "Review memory capacity",
                summary: "The local library reached its documented capacity. Review memories before adding more.",
                details: ["Nothing will be removed without approval."], group: .you,
                category: .other, bank: "all", durability: .durable, expiry: nil,
                targetIDs: [], sourceConversationID: nil, sourceMessageID: nil,
                createdAt: now, status: .pending))
        }
        return result
    }
}

private extension DateFormatter {
    static let nativeMemoryDay: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}
