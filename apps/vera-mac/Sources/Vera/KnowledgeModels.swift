import Foundation

struct KnowledgeCollection: Identifiable, Hashable, Sendable {
    let id: String
    var name: String
    var description: String
    var fileCount: Int
    var indexState: String
    var updatedAt: Date

    static func parse(_ j: [String: Any]) -> KnowledgeCollection? {
        guard let id = j["id"] as? String, let name = j["name"] as? String else { return nil }
        let updated = (j["updated_at"] as? Double) ?? (j["updated_at"] as? Int).map(Double.init) ?? 0
        return KnowledgeCollection(
            id: id, name: name,
            description: (j["description"] as? String) ?? "",
            fileCount: (j["file_count"] as? Int) ?? 0,
            indexState: (j["index_state"] as? String) ?? "empty",
            updatedAt: Date(timeIntervalSince1970: updated))
    }
}

struct KnowledgeFile: Identifiable, Hashable, Sendable {
    let id: String
    var name: String
    var format: String
    var size: Int
    var state: String
    var error: String?
    var updatedAt: Date

    static func parse(_ j: [String: Any]) -> KnowledgeFile? {
        guard let id = j["id"] as? String, let name = j["name"] as? String else { return nil }
        let updated = (j["updated_at"] as? Double) ?? (j["updated_at"] as? Int).map(Double.init) ?? 0
        return KnowledgeFile(
            id: id, name: name,
            format: (j["format"] as? String) ?? "",
            size: (j["size"] as? Int) ?? 0,
            state: (j["state"] as? String) ?? "pending",
            error: j["error"] as? String,
            updatedAt: Date(timeIntervalSince1970: updated))
    }
}

struct KnowledgePassage: Hashable, Sendable {
    let collectionID: String
    let collection: String
    let fileID: String
    let file: String
    let chunk: Int
    let score: Double
    let text: String

    static func parse(_ j: [String: Any]) -> KnowledgePassage? {
        guard let text = j["text"] as? String else { return nil }
        return KnowledgePassage(
            collectionID: (j["collection_id"] as? String) ?? "",
            collection: (j["collection"] as? String) ?? "",
            fileID: (j["file_id"] as? String) ?? "",
            file: (j["file"] as? String) ?? "",
            chunk: (j["chunk"] as? Int) ?? 0,
            score: (j["score"] as? Double) ?? 0,
            text: text)
    }
}

enum KnowledgeFileSort: String, CaseIterable, Identifiable {
    case name, date, size, state
    var id: String { rawValue }
    var title: String {
        switch self {
        case .name: "Name"; case .date: "Date"; case .size: "Size"; case .state: "State"
        }
    }
}

enum KnowledgeFiltering {
    static func apply(_ files: [KnowledgeFile], search: String, sort: KnowledgeFileSort) -> [KnowledgeFile] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        let filtered = q.isEmpty ? files : files.filter { $0.name.lowercased().contains(q) }
        switch sort {
        case .name:
            return filtered.sorted { ($0.name.lowercased(), $0.id) < ($1.name.lowercased(), $1.id) }
        case .date:
            return filtered.sorted { ($0.updatedAt, $0.id) > ($1.updatedAt, $1.id) }
        case .size:
            return filtered.sorted { ($0.size, $0.id) > ($1.size, $1.id) }
        case .state:
            return filtered.sorted { (stateRank($0.state), $0.name.lowercased(), $0.id)
                                   < (stateRank($1.state), $1.name.lowercased(), $1.id) }
        }
    }

    static func stateRank(_ state: String) -> Int {
        switch state {
        case "failed": 0
        case "indexing": 1
        case "pending": 2
        case "stale": 3
        case "ready": 4
        default: 5
        }
    }
}

enum KnowledgeStateBadge {
    static func label(_ state: String) -> String {
        switch state {
        case "ready": "Ready"
        case "pending": "Pending"
        case "indexing": "Indexing"
        case "failed": "Failed"
        case "stale": "Stale"
        case "empty": "Empty"
        default: state.capitalized
        }
    }

    static func icon(_ state: String) -> String {
        switch state {
        case "ready": "checkmark.circle"
        case "pending": "clock"
        case "indexing": "arrow.triangle.2.circlepath"
        case "failed": "exclamationmark.triangle"
        case "stale": "arrow.counterclockwise.circle"
        case "empty": "tray"
        default: "questionmark.circle"
        }
    }
}

enum KnowledgeGroundingStatus: Equatable, Sendable {
    case retrieving
    case grounded(Int)
    case empty
    case unconfigured
    case error(String)

    var label: String {
        switch self {
        case .retrieving: "Searching knowledge"
        case .grounded(let n): n == 1 ? "1 passage" : "\(n) passages"
        case .empty: "No relevant passages"
        case .unconfigured: "Knowledge unavailable"
        case .error: "Knowledge lookup failed"
        }
    }
}

enum KnowledgeGrounding {
    static let sourceURLMarker = "knowledge"
    static let numberingOffsetWithResearch = 20

    struct Assembly: Sendable {
        let block: String
        let sources: [PulseSource]
    }

    static func assemble(_ passages: [KnowledgePassage], startAt: Int = 1) -> Assembly {
        var lines: [String] = [
            "Reference passages retrieved from the user's document collections for this turn.",
            "Ground claims that rely on them with [n] citations matching the numbering below.",
            "If they don't answer the question, say so rather than inventing content.",
        ]
        var sources: [PulseSource] = []
        for (i, p) in passages.enumerated() {
            let n = startAt + i
            lines.append("[\(n)] \(p.collection) / \(p.file) (part \(p.chunk + 1))\n\(p.text)")
            sources.append(PulseSource(n: n, title: "\(p.collection): \(p.file)", url: sourceURLMarker))
        }
        return Assembly(block: lines.joined(separator: "\n\n"), sources: sources)
    }

    static func decodeSelection(_ raw: String?) -> [String] {
        guard let raw, let data = raw.data(using: .utf8),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [String] else { return [] }
        return arr
    }

    static func encodeSelection(_ ids: [String]) -> String? {
        guard !ids.isEmpty,
              let data = try? JSONSerialization.data(withJSONObject: ids) else { return nil }
        return String(data: data, encoding: .utf8)
    }
}

struct KnowledgeClient: Sendable {
    let base: URL

    enum Fetch: Sendable {
        case ok([KnowledgeCollection])
        case unsupported(Int)
        case unreachable
    }

    enum QueryResult: Sendable {
        case ok([KnowledgePassage])
        case unconfigured(String)
        case error(String)
    }

    private func url(_ path: String) -> URL { base.appendingPathComponent(path) }

    private func send(_ path: String, method: String = "GET", body: [String: Any]? = nil,
                      timeout: TimeInterval = 10) async -> (json: [String: Any]?, code: Int)? {
        var req = URLRequest(url: url(path))
        req.httpMethod = method
        req.timeoutInterval = timeout
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return nil }
        let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (json, code)
    }

    private static func detail(_ json: [String: Any]?, _ code: Int) -> String {
        (json?["detail"] as? String) ?? "HTTP \(code)"
    }

    func fetch() async -> Fetch {
        guard let (json, code) = await send("/documents/collections", timeout: 8) else { return .unreachable }
        guard (200..<300).contains(code) else {
            return code == 404 || code == 405 ? .unsupported(code) : .unreachable
        }
        guard let arr = json?["collections"] as? [[String: Any]] else { return .unsupported(code) }
        return .ok(arr.compactMap { KnowledgeCollection.parse($0) })
    }

    func status() async -> (configured: Bool, detail: String)? {
        guard let (json, code) = await send("/documents/status", timeout: 8),
              (200..<300).contains(code), let json else { return nil }
        return ((json["configured"] as? Bool) ?? false, "")
    }

    func createCollection(name: String, description: String) async -> String? {
        guard let (json, code) = await send("/documents/collections", method: "POST",
                                            body: ["name": name, "description": description])
        else { return "vera-api unreachable" }
        return (200..<300).contains(code) ? nil : Self.detail(json, code)
    }

    func updateCollection(id: String, name: String?, description: String?) async -> String? {
        var body: [String: Any] = [:]
        if let name { body["name"] = name }
        if let description { body["description"] = description }
        guard let (json, code) = await send("/documents/collections/\(id)", method: "PATCH", body: body)
        else { return "vera-api unreachable" }
        return (200..<300).contains(code) ? nil : Self.detail(json, code)
    }

    func deleteCollection(id: String) async -> String? {
        guard let (json, code) = await send("/documents/collections/\(id)", method: "DELETE")
        else { return "vera-api unreachable" }
        return (200..<300).contains(code) ? nil : Self.detail(json, code)
    }

    enum FilesFetch: Sendable {
        case ok([KnowledgeFile])
        case failed(String)
    }

    func files(collection: String) async -> FilesFetch {
        guard let (json, code) = await send("/documents/collections/\(collection)/files", timeout: 8)
        else { return .failed("vera-api unreachable") }
        guard (200..<300).contains(code), let arr = json?["files"] as? [[String: Any]] else {
            return .failed(Self.detail(json, code))
        }
        return .ok(arr.compactMap { KnowledgeFile.parse($0) })
    }

    func upload(collection: String, name: String, data: Data) async -> String? {
        let boundary = "vera-\(UUID().uuidString)"
        var req = URLRequest(url: url("/documents/collections/\(collection)/files"))
        req.httpMethod = "POST"
        req.timeoutInterval = 120
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"upload\"; filename=\"\(name)\"\r\n")
        append("Content-Type: application/octet-stream\r\n\r\n")
        body.append(data)
        append("\r\n--\(boundary)--\r\n")
        req.httpBody = body
        guard let (respData, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return "vera-api unreachable" }
        if (200..<300).contains(code) { return nil }
        let json = try? JSONSerialization.jsonObject(with: respData) as? [String: Any]
        return Self.detail(json, code)
    }

    func deleteFile(id: String) async -> String? {
        guard let (json, code) = await send("/documents/files/\(id)", method: "DELETE")
        else { return "vera-api unreachable" }
        return (200..<300).contains(code) ? nil : Self.detail(json, code)
    }

    func reindexFile(id: String) async -> String? {
        guard let (json, code) = await send("/documents/files/\(id)/reindex", method: "POST",
                                            timeout: 120)
        else { return "vera-api unreachable" }
        return (200..<300).contains(code) ? nil : Self.detail(json, code)
    }

    func reindexCollection(id: String) async -> String? {
        guard let (json, code) = await send("/documents/collections/\(id)/reindex", method: "POST",
                                            timeout: 300)
        else { return "vera-api unreachable" }
        return (200..<300).contains(code) ? nil : Self.detail(json, code)
    }

    func query(_ text: String, collections: [String], topK: Int = 6,
               charBudget: Int = 5000) async -> QueryResult {
        guard let (json, code) = await send("/documents/query", method: "POST",
                                            body: ["query": text, "collection_ids": collections,
                                                   "top_k": topK, "char_budget": charBudget],
                                            timeout: 20)
        else { return .error("vera-api unreachable") }
        guard (200..<300).contains(code), let json else {
            return .error(Self.detail(json, code))
        }
        let status = (json["status"] as? String) ?? "error"
        let passages = (json["passages"] as? [[String: Any]] ?? []).compactMap { KnowledgePassage.parse($0) }
        switch status {
        case "ok": return .ok(passages)
        case "unconfigured": return .unconfigured((json["detail"] as? String) ?? "")
        default: return .error((json["detail"] as? String) ?? "retrieval failed")
        }
    }
}

@MainActor
final class KnowledgeStore: ObservableObject {
    enum Phase { case loading, unconfigured, unreachable, unsupported, ready }

    @Published var phase: Phase = .loading
    @Published var collections: [KnowledgeCollection] = []
    @Published var embeddingsConfigured = true
    @Published var busy: Set<String> = []
    @Published var error: String?

    private(set) var client: KnowledgeClient?

    var baseDescription: String { client?.base.absoluteString ?? "vera-api" }

    func configure(base: URL?) {
        client = base.map { KnowledgeClient(base: $0) }
        if client == nil { phase = .unconfigured }
    }

    func refresh() async {
        guard let client else { phase = .unconfigured; return }
        switch await client.fetch() {
        case .ok(let cols):
            collections = cols
            phase = .ready
            if let st = await client.status() { embeddingsConfigured = st.configured }
        case .unsupported: phase = .unsupported
        case .unreachable: phase = .unreachable
        }
    }

    func run(_ key: String, _ op: @escaping () async -> String?) async {
        busy.insert(key)
        defer { busy.remove(key) }
        if let detail = await op() { error = detail }
        await refresh()
    }
}
