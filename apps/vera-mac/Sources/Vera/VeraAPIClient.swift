import Foundation

func stringifyAttr(_ v: Any) -> String {
    if let s = v as? String { return s }
    if let n = v as? NSNumber { return n.stringValue }
    if let data = try? JSONSerialization.data(withJSONObject: v),
       let s = String(data: data, encoding: .utf8) { return s }
    return "\(v)"
}

enum PulseFeedResult: Sendable {
    case success(cards: [PulseCard], rawIDs: Set<String>)
    case unconfigured
    case transport
    case malformed
}

protocol PulseFeedProviding: Sendable {
    func pulseFeed() async -> PulseFeedResult
}

func parseGroomSnapshot(_ d: [String: Any]) -> GroomSnapshot {
    var s = GroomSnapshot(kind: d["kind"] as? String ?? "belief", id: d["id"] as? String ?? "")
    s.topic = d["topic"] as? String ?? ""
    s.content = d["content"] as? String ?? ""
    s.tier = d["tier"] as? String ?? "archive"
    s.type = d["type"] as? String ?? ""
    s.name = d["name"] as? String ?? ""
    if let a = d["attrs"] as? [String: Any] { s.attrs = a.mapValues(stringifyAttr) }
    if let schema = d["schema"] as? [String: Any], let req = schema["required"] as? [String] {
        s.schemaFields = req
    }
    if let n = d["entity_count"] as? Int { s.entityCount = n }
    if let mig = d["migrated"] as? [[String: Any]] {
        s.migrated = mig.compactMap { ($0["name"] as? String) ?? ($0["id"] as? String) }
    }
    return s
}

struct VeraAPIClient: Sendable, PulseFeedProviding {
    let base: URL
    var model: String = ""

    static func effectiveBase(mode: String?, remote: String?, port: Int) -> URL? {
        let trimmedRemote = remote?.trimmingCharacters(in: .whitespaces)
        let hasRemote = (trimmedRemote?.isEmpty == false)
        let resolved = mode ?? (hasRemote ? "remote" : "off")
        switch resolved {
        case "local": return URL(string: "http://127.0.0.1:\(port)")
        case "off": return nil
        default: return hasRemote ? URL(string: trimmedRemote!) : nil
        }
    }

    static func resolvedBase() -> URL? {
        let env = ProcessInfo.processInfo.environment
        let file = ConfigFile.read()
        let remote = env["VERA_API_BASE"] ?? file["vera_api_base"] as? String
        let port = (file["engine_port"] as? Int)
            ?? Int((file["engine_port"] as? String)?.trimmingCharacters(in: .whitespaces) ?? "")
            ?? 8089
        return effectiveBase(mode: file["engine_mode"] as? String, remote: remote, port: port)
    }

    static func resolved(model: String = "") -> VeraAPIClient? {
        resolvedBase().map { VeraAPIClient(base: $0, model: model) }
    }

    private func url(_ path: String) -> URL {
        base.appendingPathComponent(path)
    }

    private func post(_ path: String, _ fields: [String: Any]) async -> [String: Any]? {
        var r = URLRequest(url: url(path))
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: fields)
        guard let (data, _) = try? await URLSession.shared.data(for: r) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    func postFeedback(_ fields: [String: Any]) async {
        _ = await post("/feedback", fields)
    }

    func fetchPulseCards() async -> [PulseCard] {
        if case .success(let cards, _) = await pulseFeed() { return cards }
        return []
    }

    func pulseFeed() async -> PulseFeedResult {
        guard let (data, response) = try? await URLSession.shared.data(from: url("/pulse/cards")) else { return .transport }
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) { return .transport }
        return Self.parsePulseFeed(data)
    }

    static func parsePulseFeed(_ data: Data) -> PulseFeedResult {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = obj["cards"] as? [[String: Any]] else { return .malformed }
        let rawIDs = Set(arr.compactMap { $0["id"] as? String })
        let cards = arr.compactMap { c -> PulseCard? in
            guard let id = c["id"] as? String, let title = c["title"] as? String else { return nil }
            let sourceList: [PulseSource] = (c["sources"] as? [[String: Any]] ?? []).compactMap { s in
                guard let n = s["n"] as? Int, let url = s["url"] as? String else { return nil }
                return PulseSource(n: n, title: (s["title"] as? String) ?? url, url: url)
            }
            let inline: [PulseInlineImage] = (c["inline_images"] as? [[String: Any]] ?? []).compactMap { im in
                guard let n = im["n"] as? Int, let url = im["url"] as? String else { return nil }
                let sn = im["sourceN"] as? Int
                return PulseInlineImage(n: n, url: url, caption: (im["caption"] as? String) ?? "", sourceN: (sn == 0 ? nil : sn))
            }
            let summary = (c["summary"] as? String) ?? ""
            let body = (c["body"] as? String) ?? ""
            let preview = summary.isEmpty ? String(body.strippedMarkdown(droppingTitle: title).prefix(200)) : summary
            var action: PulseAction? = nil
            if let a = c["action"] as? [String: Any],
               let verb = a["verb"] as? String, let token = a["token"] as? String {
                action = PulseAction(verb: verb,
                                     preview: (a["preview"] as? String) ?? verb,
                                     risk: (a["risk"] as? String) ?? "low",
                                     reversible: (a["reversible"] as? Bool) ?? true,
                                     token: token)
            }
            let changeSet: [GroomOp] = (c["change_set"] as? [[String: Any]] ?? []).enumerated().compactMap { (i, op) in
                guard let type = op["type"] as? String else { return nil }
                let before = (op["before"] as? [[String: Any]] ?? []).map(parseGroomSnapshot)
                let after = (op["after"] as? [String: Any]).map(parseGroomSnapshot)
                return GroomOp(index: i, type: type, store: op["store"] as? String ?? "memory",
                               reason: op["reason"] as? String ?? "", before: before, after: after)
            }
            let items: [PulseDigestItem] = (c["items"] as? [[String: Any]] ?? []).compactMap { it in
                guard let iid = it["item_id"] as? String, let t = it["title"] as? String else { return nil }
                let act = it["action"] as? [String: Any]
                return PulseDigestItem(itemID: iid, title: t, subtitle: (it["subtitle"] as? String) ?? "",
                                       mediaType: it["media_type"] as? String, tmdbID: it["tmdb_id"] as? Int,
                                       token: act?["token"] as? String, state: (it["state"] as? String) ?? "pending",
                                       poster: it["poster"] as? String, link: it["link"] as? String,
                                       group: it["group"] as? String)
            }
            return PulseCard(id: id, title: title, preview: preview, subtitle: "Pulse",
                             imageURL: c["image_url"] as? String, tint: c["tint"] as? String,
                             sources: sourceList.map { $0.url }, sourceList: sourceList, inlineImages: inline,
                             body: body, status: c["status"] as? String,
                             kind: (c["kind"] as? String) ?? "research", severity: c["severity"] as? String,
                             action: action, provenance: (c["provenance"] as? String) ?? "scheduled",
                             read: (c["read"] as? Bool) ?? false,
                             category: c["category"] as? String, changeSet: changeSet, items: items)
        }
        return .success(cards: cards, rawIDs: rawIDs)
    }

    func fetchPulseVeins() async -> [PulseVein] {
        guard let (data, _) = try? await URLSession.shared.data(from: url("/pulse/veins")),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = obj["veins"] as? [[String: Any]] else { return [] }
        return arr.compactMap { l in
            guard let kind = l["kind"] as? String, let label = l["label"] as? String else { return nil }
            return PulseVein(kind: kind, label: label,
                             icon: (l["icon"] as? String) ?? "circle",
                             order: (l["order"] as? Int) ?? 0,
                             nominalLabel: (l["nominal_label"] as? String) ?? "nominal",
                             unread: (l["unread"] as? Int) ?? 0,
                             maxSeverity: l["max_severity"] as? String)
        }.sorted { $0.order < $1.order }
    }

    func markPulseRead(id: String) async {
        _ = await post("/pulse/read", ["card_id": id])
    }

    func decideGroomOp(store: String, mode: String, cardID: String, opIndex: Int) async -> String {
        let path = (store == "knowledge" ? "/knowledge/" : "/memory/") + mode
        guard let obj = await post(path, ["card_id": cardID, "op_index": opIndex]) else { return "failed" }
        if (obj["stale"] as? Bool) == true { return "stale" }
        return (obj["ok"] as? Bool) == true ? "done" : "failed"
    }

    func commitAction(token: String) async -> [String: Any]? {
        await post("/actions/commit", ["token": token])
    }

    func dismissAction(token: String) async {
        _ = await post("/actions/dismiss", ["token": token])
    }

    func decideDigestItem(cardID: String, itemID: String, approve: Bool) async -> Bool {
        let obj = await post("/actions/card/item", [
            "card_id": cardID, "item_id": itemID, "decision": approve ? "approve" : "skip"])
        return (obj?["ok"] as? Bool) == true
    }

    func decideDigestAll(cardID: String, approve: Bool) async {
        _ = await post("/actions/card/all", ["card_id": cardID, "decision": approve ? "approve" : "skip"])
    }

    @discardableResult
    func checkUpdatesNow() async -> Bool {
        var r = URLRequest(url: url("/pulse/veins/status/run"))
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: [String: Any]())
        guard let (_, resp) = try? await URLSession.shared.data(for: r),
              let code = (resp as? HTTPURLResponse)?.statusCode, (200..<300).contains(code) else { return false }
        return true
    }

    func setPulseBookmark(id: String, on: Bool) async {
        _ = await post("/pulse/\(id)/bookmark", ["on": on])
    }
}
