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
        if NativeMemorySafety.containsSensitiveData(user)
            || NativeMemorySafety.containsSensitiveData(assistant.text) { return .privateTurn }
        if assistant.state == .interrupted { return .interrupted }
        if assistant.failure != nil { return .failed }
        if assistant.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
           !assistant.toolActivities.isEmpty { return .toolOnly }
        return .eligible
    }
}

enum NativeMemorySafety {
    private static let patterns = [
        #"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|action[_ -]?token|client[_ -]?secret|private[_ -]?key|authorization|password|secret)\b\s*[:=]\s*\S+"#,
        #"\bsk-[A-Za-z0-9_-]{8,}\b"#,
        #"\bact_(?:live|test)_[A-Za-z0-9_-]{8,}\b"#,
        #"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"#,
        #"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"#,
        #"\bAIza[A-Za-z0-9_-]{20,}\b"#,
        #"(?i)\bbearer\s+[A-Za-z0-9._-]{8,}"#,
        #"\bAKIA[0-9A-Z]{16}\b"#,
        #"-----BEGIN [A-Z ]*PRIVATE KEY-----"#,
        #"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"#,
        #"(?is)<(think|analysis|reasoning)>.*?</\1>"#,
        #"(?i)\b(hidden reasoning|chain[- ]of[- ]thought)\b\s*[:=]"#,
        #"(?is)(^|[\n;\"'])\s*(class\s+\w+\s*[:(]|(?:async\s+)?def\s+\w+\s*\(|from\s+\w+(?:\.\w+)*\s+import\s+|import\s+\w+)"#,
        #"(?i)\b(class\s+(filter|valves)\b|(?:async\s+)?def\s+(inlet|outlet)\s*\(|from\s+pydantic\s+import\b|self\.valves\b)"#,
    ]

    private static let markerPatterns = [
        #"(?i)<\s*/?\s*(think|analysis|reasoning)\b"#,
        #"(?i)<\|\s*(think|analysis|reasoning)\s*\|>"#,
        #"(?i)\[\s*(think|analysis|reasoning)\s*\]"#,
        #"(?i)[<\[{(][^>\]})\n]{0,20}(think|analysis|reasoning|scratchpad)[^>\]})\n]{0,20}[>\]})]"#,
        #"(?i)\b(begin|end)\s+(hidden\s+)?(reasoning|analysis|chain[- ]of[- ]thought)\b"#,
        #"(?i)\b(internal reasoning|hidden reasoning|chain[- ]of[- ]thought|private scratchpad)\b"#,
        #"(?im)^\s*(analysis|reasoning|thoughts?|scratchpad)\b"#,
    ]

    private static let sensitiveKeys: Set<String> = [
        "apikey", "accesstoken", "refreshtoken", "actiontoken", "clientsecret",
        "privatekey", "authorization", "password", "secret", "headers", "valves",
        "hiddenreasoning", "chainofthought", "reasoning", "scratchpad", "baseurl",
        "apiurl", "endpoint",
    ]

    static func containsSensitiveData(_ value: String) -> Bool {
        if patterns.contains(where: { value.range(of: $0, options: .regularExpression) != nil }) {
            return true
        }
        if markerPatterns.contains(where: { value.range(of: $0, options: .regularExpression) != nil }) {
            return true
        }
        guard let data = value.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) else { return false }
        if object is [String: Any] || object is [Any] { return true }
        return structuredValueIsSensitive(object)
    }

    static func boundedUTF8(_ value: String, maximumBytes: Int) -> String {
        guard maximumBytes > 0 else { return "" }
        if value.utf8.count <= maximumBytes { return value }
        var result = ""
        var used = 0
        for character in value {
            let text = String(character)
            let bytes = text.utf8.count
            guard used + bytes <= maximumBytes else { break }
            result.append(character)
            used += bytes
        }
        return result
    }

    static func recordIsSafe(_ record: NativeMemoryRecord) -> Bool {
        !containsSensitiveData(record.title)
            && !containsSensitiveData(record.summary)
            && !containsSensitiveData(record.bank)
            && record.details.allSatisfy { !containsSensitiveData($0) }
    }

    static func proposalIsSafe(_ proposal: NativeMemoryProposal) -> Bool {
        !containsSensitiveData(proposal.title)
            && !containsSensitiveData(proposal.summary)
            && !containsSensitiveData(proposal.bank)
            && proposal.details.allSatisfy { !containsSensitiveData($0) }
    }

    private static func structuredValueIsSensitive(_ value: Any) -> Bool {
        if let dictionary = value as? [String: Any] {
            for (key, child) in dictionary {
                let normalized = key.lowercased().filter(\.isLetter)
                if keyIsSensitive(normalized) { return true }
                if structuredValueIsSensitive(child) { return true }
            }
        } else if let array = value as? [Any] {
            return array.contains(where: structuredValueIsSensitive)
        } else if let text = value as? String {
            return patterns.contains { text.range(of: $0, options: .regularExpression) != nil }
                || markerPatterns.contains { text.range(of: $0, options: .regularExpression) != nil }
        }
        return false
    }

    private static func keyIsSensitive(_ normalized: String) -> Bool {
        sensitiveKeys.contains(normalized)
            || normalized == "key"
            || normalized == "token"
            || normalized == "credential"
            || normalized == "credentials"
            || normalized == "config"
            || normalized == "configuration"
            || normalized == "setting"
            || normalized == "settings"
            || normalized == "model"
            || normalized == "url"
            || normalized == "uri"
            || normalized == "host"
            || normalized == "server"
            || normalized == "service"
            || normalized == "provider"
            || normalized == "options"
            || normalized == "parameters"
            || normalized == "temperature"
            || normalized.contains("credential")
            || normalized.hasSuffix("token")
            || normalized.hasSuffix("secret")
            || normalized.contains("apiurl")
            || normalized.contains("baseurl")
            || normalized.contains("endpoint")
            || normalized.hasSuffix("url")
            || normalized.hasSuffix("uri")
            || normalized.hasSuffix("host")
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
    case sensitiveData

    var errorDescription: String? {
        switch self {
        case .notConfigured: "The optional memory model is not configured"
        case .authentication: "The memory model rejected its saved credentials"
        case .malformed: "The memory model returned a response Vera could not review"
        case .timeout: "The memory model timed out"
        case .unavailable(let detail): detail
        case .sensitiveData: "Memory skipped text that looks like a credential or secret"
        }
    }
}

struct NativeMemoryService: NativeMemoryServing, Sendable {
    let configuration: NativeMemoryServiceConfiguration
    var session: URLSession = .shared

    func embed(_ text: String) async throws -> [Double] {
        guard !configuration.embeddingsModel.isEmpty else { throw NativeMemoryServiceError.notConfigured }
        guard !NativeMemorySafety.containsSensitiveData(text) else { throw NativeMemoryServiceError.sensitiveData }
        var request = URLRequest(url: configuration.embeddingsURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request)
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": configuration.embeddingsModel,
            "input": NativeMemorySafety.boundedUTF8(text, maximumBytes: 4_000),
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
        guard !NativeMemorySafety.containsSensitiveData(user),
              !NativeMemorySafety.containsSensitiveData(assistant) else {
            throw NativeMemoryServiceError.sensitiveData
        }
        let input = """
        USER TURN
        \(NativeMemorySafety.boundedUTF8(user, maximumBytes: 4_000))

        ASSISTANT TURN
        \(NativeMemorySafety.boundedUTF8(assistant, maximumBytes: 4_000))
        """
        return try await extract(
            input, sourceConversationID: sourceConversationID,
            sourceMessageID: sourceMessageID, existing: existing, now: now)
    }

    func proposal(
        request: String, existing: [NativeMemoryRecord], now: Date = Date()
    ) async throws -> [NativeMemoryProposal] {
        guard !NativeMemorySafety.containsSensitiveData(request) else {
            throw NativeMemoryServiceError.sensitiveData
        }
        return try await extract(
            "MEMORY CHANGE REQUEST\n\(NativeMemorySafety.boundedUTF8(request, maximumBytes: 2_000))",
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
        let existingSummary = existing.filter(NativeMemorySafety.recordIsSafe).prefix(40).map {
            [
                "id": $0.id,
                "summary": NativeMemorySafety.boundedUTF8($0.summary, maximumBytes: 180),
                "details": NativeMemorySafety.boundedUTF8(
                    $0.details.joined(separator: "\n"), maximumBytes: 1_200),
                "bank": NativeMemorySafety.boundedUTF8($0.bank, maximumBytes: 40),
            ]
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
            let proposal = NativeMemoryProposal(
                id: UUID().uuidString, kind: reconciledKind, title: title, summary: summary,
                details: details.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) },
                group: group, category: category, bank: bank, durability: durability,
                expiry: expiry, targetIDs: reconciledTargets,
                sourceConversationID: sourceConversationID, sourceMessageID: sourceMessageID,
                createdAt: now, status: .pending)
            return NativeMemorySafety.proposalIsSafe(proposal) ? proposal : nil
        }
    }

    private static func clean(_ value: Any?, maximum: Int) -> String? {
        guard let raw = value as? String else { return nil }
        let result = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !result.isEmpty, result.count <= maximum,
              !NativeMemorySafety.containsSensitiveData(result) else { return nil }
        let lower = result.lowercased()
        guard !lower.contains("api key"), !lower.contains("authorization: bearer"),
              !lower.contains("hidden reasoning"), !lower.contains("system prompt"),
              !["today", "tonight", "tomorrow", "this weekend", "next week", "next month", "soon"]
                .contains(where: { lower.range(of: "\\b\($0)\\b", options: .regularExpression) != nil })
        else { return nil }
        return result
    }
}

enum NativeMemoryReconciliation {
    static let semanticDuplicateThreshold = 0.9
    static let consolidationThreshold = 0.94

    static func reconcile(
        _ proposal: NativeMemoryProposal, candidateEmbedding: [Double],
        existing: [NativeMemoryRecord]
    ) -> NativeMemoryProposal {
        guard proposal.kind == .create,
              let duplicate = existing.filter({
                  $0.status == .approved && $0.bank.caseInsensitiveCompare(proposal.bank) == .orderedSame
              }).compactMap({ memory -> (NativeMemoryRecord, Double)? in
                  guard let embedding = memory.embedding,
                        embedding.count == candidateEmbedding.count else { return nil }
                  return (memory, NativeMemoryRecall.cosineSimilarity(candidateEmbedding, embedding))
              }).filter({ $0.1 >= semanticDuplicateThreshold }).sorted(by: {
                  if $0.1 != $1.1 { return $0.1 > $1.1 }
                  return $0.0.id < $1.0.id
              }).first else { return proposal }
        var result = proposal
        result.kind = .update
        result.targetIDs = [duplicate.0.id]
        return result
    }
}

enum NativeMemoryMaintenance {
    static func proposals(
        records: [NativeMemoryRecord], existing: [NativeMemoryProposal],
        now: Date = Date(), capacity: Int = NativeMemoryRecall.capacity
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
        let approved = records.filter { $0.status == .approved }
        let candidates = approved.filter { !covered.contains($0.id) }
        if let pair = semanticPair(in: candidates) {
            result.append(NativeMemoryProposal(
                id: UUID().uuidString, kind: .consolidate, title: "Review related memories",
                summary: "These memories appear to describe the same durable context.",
                details: Array(Set(pair.0.details + pair.1.details)).sorted(), group: pair.0.group,
                category: pair.0.category, bank: pair.0.bank, durability: .durable, expiry: nil,
                targetIDs: [pair.0.id, pair.1.id], sourceConversationID: pair.0.sourceConversationID,
                sourceMessageID: pair.0.sourceMessageID, createdAt: now, status: .pending))
        }
        if approved.count >= capacity,
           !existing.contains(where: { $0.kind == .cleanup }) {
            let removable = candidates.sorted {
                if $0.updatedAt != $1.updatedAt { return $0.updatedAt < $1.updatedAt }
                return $0.id < $1.id
            }.first
            if let removable {
            result.append(NativeMemoryProposal(
                id: UUID().uuidString, kind: .cleanup, title: "Review memory capacity",
                summary: "The local library reached its documented capacity.",
                details: ["Remove \"\(removable.title)\" to make room. Nothing changes until you approve."],
                group: removable.group, category: removable.category, bank: removable.bank,
                durability: removable.durability, expiry: removable.expiry,
                targetIDs: [removable.id], sourceConversationID: removable.sourceConversationID,
                sourceMessageID: removable.sourceMessageID,
                createdAt: now, status: .pending))
            }
        }
        return result
    }

    private static func semanticPair(
        in records: [NativeMemoryRecord]
    ) -> (NativeMemoryRecord, NativeMemoryRecord)? {
        let ordered = records.sorted { $0.id < $1.id }
        for leftIndex in ordered.indices {
            guard let leftEmbedding = ordered[leftIndex].embedding else { continue }
            for rightIndex in ordered.index(after: leftIndex)..<ordered.endIndex {
                let right = ordered[rightIndex]
                guard ordered[leftIndex].bank.caseInsensitiveCompare(right.bank) == .orderedSame,
                      let rightEmbedding = right.embedding,
                      NativeMemoryRecall.cosineSimilarity(leftEmbedding, rightEmbedding)
                        >= NativeMemoryReconciliation.consolidationThreshold else { continue }
                return (ordered[leftIndex], right)
            }
        }
        return nil
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
