import Foundation

/// Stringify one knowledge-store attr value (string/number/bool/json) for display.
func stringifyAttr(_ v: Any) -> String {
    if let s = v as? String { return s }
    if let n = v as? NSNumber { return n.stringValue }
    if let data = try? JSONSerialization.data(withJSONObject: v),
       let s = String(data: data, encoding: .utf8) { return s }
    return "\(v)"
}

/// Decode one polymorphic grooming snapshot (belief | entity | type) from the change-set.
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

/// OWUI connection config — env vars over `~/.vera/config.json` (see ConfigFile). Endpoints with
/// no value stay nil and their features show an unconfigured state — never a baked-in address.
struct OWUIConfig: Sendable {
    var baseURL: URL          // OWUI — chats, memories, folders (REST, Bearer auth)
    var apiKey: String
    var model: String
    var completionsURL: URL   // raw OpenAI-style fallback path; derived from OWUI base when unset
    var voiceBase: URL?       // vera-voice STT/TTS service (in-app voice mode)
    var veraAPIBase: URL?     // vera-api (feedback, images, pulse, scheduler)
    var email: String?        // OWUI login — needed for the Socket.IO pipeline (JWT auth)
    var password: String?
    var ownerName: String?    // drives the greeting + sidebar chip
    /// Server-specific chat-template options as raw JSON (e.g. a hybrid-thinking toggle).
    /// Empty/unset keeps every completion request pure OpenAI — the field is omitted.
    var chatTemplateKwargs: String?

    /// The decoded template-kwargs object, or nil when unset/invalid/empty.
    func chatTemplateKwargsObject() -> [String: Any]? {
        guard let raw = chatTemplateKwargs, !raw.isEmpty,
              let data = raw.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              !obj.isEmpty else { return nil }
        return obj
    }

    static func load() -> OWUIConfig? {
        let env = ProcessInfo.processInfo.environment
        let file = ConfigFile.read()
        func value(_ envKey: String, _ fileKey: String) -> String? {
            // Whitespace-only counts as unset — a stray space must not satisfy a required field.
            if let e = env[envKey]?.trimmingCharacters(in: .whitespaces), !e.isEmpty { return e }
            if let f = (file[fileKey] as? String)?.trimmingCharacters(in: .whitespaces), !f.isEmpty { return f }
            return nil
        }
        // OWUI_KEY is the canonical env name (matches the backend); OWUI_API_KEY still
        // works as a deprecated fallback for one release.
        guard let b = value("OWUI_BASE", "base"), let u = URL(string: b),
              let k = value("OWUI_KEY", "api_key") ?? value("OWUI_API_KEY", "api_key") else { return nil }
        // OWUI proxies chat completions itself, so the raw path is derivable from the base.
        let compURL = value("VERA_COMPLETIONS_URL", "completions_url").flatMap(URL.init(string:))
            ?? u.appendingPathComponent("api/chat/completions")
        return OWUIConfig(baseURL: u, apiKey: k,
                          // No baked-in model id: unset stays empty and Settings/onboarding
                          // surface it as required.
                          model: value("VERA_MODEL", "model") ?? "",
                          completionsURL: compURL,
                          voiceBase: value("VERA_VOICE_BASE", "voice_base").flatMap(URL.init(string:)),
                          veraAPIBase: value("VERA_API_BASE", "vera_api_base").flatMap(URL.init(string:)),
                          email: value("OWUI_EMAIL", "owui_email"),
                          password: value("OWUI_PASSWORD", "owui_password"),
                          ownerName: value("VERA_OWNER_NAME", "owner_name"),
                          chatTemplateKwargs: value("VERA_CHAT_TEMPLATE_KWARGS", "chat_template_kwargs"))
    }
}

struct ChatSummary: Identifiable, Sendable, Decodable {
    let id: String
    let title: String
    let updated_at: Int?
}

/// Minimal OWUI REST client: list/load chats + stream a completion.
struct OWUIClient: Sendable {
    let config: OWUIConfig

    /// A vera-api URL, or nil when that service is unconfigured (callers no-op gracefully).
    private func veraAPI(_ path: String) -> URL? {
        config.veraAPIBase?.appendingPathComponent(path)
    }

    private func request(_ path: String, method: String = "GET", body: Data? = nil) -> URLRequest {
        var r = URLRequest(url: config.baseURL.appendingPathComponent(path))
        r.httpMethod = method
        r.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = body
        return r
    }

    /// The chat list. Throws on transport AND decode failure — a failed fetch must read as
    /// "unknown", never as "the server has zero chats" (which would wipe the sidebar on reconcile).
    func listChats() async throws -> [ChatSummary] {
        let (data, _) = try await URLSession.shared.data(for: request("/api/v1/chats/"))
        return try JSONDecoder().decode([ChatSummary].self, from: data)
    }

    /// Upload a document to OWUI (multipart) so it can be referenced in a completion's `files`.
    /// Returns the full file object (id, filename, meta, …) or nil on failure.
    func uploadFile(name: String, data: Data, mime: String) async -> [String: Any]? {
        let boundary = "vera-\(UUID().uuidString)"
        var r = URLRequest(url: config.baseURL.appendingPathComponent("/api/v1/files/"))
        r.httpMethod = "POST"
        r.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        r.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(name)\"\r\n")
        append("Content-Type: \(mime)\r\n\r\n")
        body.append(data)
        append("\r\n--\(boundary)--\r\n")
        r.httpBody = body
        guard let (resp, http) = try? await URLSession.shared.data(for: r),
              let code = (http as? HTTPURLResponse)?.statusCode, (200..<300).contains(code),
              let obj = try? JSONSerialization.jsonObject(with: resp) as? [String: Any] else { return nil }
        return obj
    }

    /// Build an OWUI chat object (flat messages + linear history graph) from (role, content) turns.
    private func chatObject(title: String, turns: [(String, String)]) -> [String: Any] {
        let now = Int(Date().timeIntervalSince1970)
        let ids = turns.map { _ in UUID().uuidString }
        var flat: [[String: Any]] = []
        var histMsgs: [String: Any] = [:]
        for (i, t) in turns.enumerated() {
            var m: [String: Any] = ["id": ids[i], "role": t.0, "content": t.1, "timestamp": now,
                                    "parentId": i > 0 ? ids[i - 1] : NSNull(),
                                    "childrenIds": i < ids.count - 1 ? [ids[i + 1]] : []]
            if t.0 == "assistant" { m["model"] = config.model; m["modelName"] = "Vera" }
            histMsgs[ids[i]] = m
            flat.append(["id": ids[i], "role": t.0, "content": t.1, "timestamp": now])
        }
        return ["title": title, "models": [config.model], "messages": flat,
                "history": ["currentId": ids.last as Any, "messages": histMsgs],
                "files": [], "tags": [], "params": [:], "timestamp": now * 1000]
    }

    /// Create a chat in OWUI; returns its id and the server's updated_at stamp (the reconcile
    /// baseline, so our own save never reads as an external change). Mirrors the web client.
    func createChat(title: String, turns: [(String, String)]) async -> (id: String, updatedAt: Int)? {
        let body = try? JSONSerialization.data(withJSONObject: ["chat": chatObject(title: title, turns: turns)])
        guard let (data, resp) = try? await URLSession.shared.data(for: request("/api/v1/chats/new", method: "POST", body: body)),
              let code = (resp as? HTTPURLResponse)?.statusCode, (200..<300).contains(code),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = obj["id"] as? String else { return nil }
        return (id, (obj["updated_at"] as? Int) ?? 0)
    }

    /// Update an existing OWUI chat's full message history. Returns the server's new
    /// updated_at stamp, or nil on failure.
    @discardableResult
    func saveChat(id: String, title: String, turns: [(String, String)]) async -> Int? {
        let body = try? JSONSerialization.data(withJSONObject: ["chat": chatObject(title: title, turns: turns)])
        guard let (data, resp) = try? await URLSession.shared.data(for: request("/api/v1/chats/\(id)", method: "POST", body: body)),
              let code = (resp as? HTTPURLResponse)?.statusCode, (200..<300).contains(code) else { return nil }
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (obj?["updated_at"] as? Int) ?? 0
    }

    /// Load a chat's message history from OWUI. A promoted Pulse briefing carries vera-* markers
    /// in its first assistant turn — reconstruct the rich card so the chat renders sources/inline
    /// photos/chips (not raw [n] / stripped prose).
    func loadMessages(chatID: String) async -> [Message] {
        guard let (data, _) = try? await URLSession.shared.data(for: request("/api/v1/chats/\(chatID)")),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let chat = obj["chat"] as? [String: Any] else { return [] }
        let msgs = ChatHistory.orderedMessages(chat)
        var title = (chat["title"] as? String) ?? "Pulse"
        if title.hasPrefix("Pulse · ") { title = String(title.dropFirst("Pulse · ".count)) }
        return msgs.compactMap { m in
            guard let role = m["role"] as? String, let content = m["content"] as? String, !content.isEmpty else { return nil }
            if role == "user" { return Message(role: .user, text: content) }
            let p = PulseMarkers.parse(content)
            if p.image != nil || !p.sources.isEmpty || !p.inlineImages.isEmpty {
                let card = PulseCard(id: chatID, title: title, preview: p.summary ?? "", subtitle: "Pulse",
                                     imageURL: p.image, tint: p.tint, sources: p.sources.map { $0.url },
                                     sourceList: p.sources, inlineImages: p.inlineImages, body: p.body)
                return Message(role: .assistant, text: p.body, pulse: card)
            }
            let cited = OWUISources.parse(m["sources"] as? [[String: Any]] ?? [])
            return Message.assistant(from: PulseMarkers.strip(content), sources: cited)
        }
    }

    /// OWUI stores a chat's turns twice: the flat `chat.messages` list and the canonical
    /// `chat.history.messages` graph (id-keyed nodes, `parentId` links, `currentId` at the tip).
    /// Automation-written chats populate only the graph, so the thread is reconstructed from it
    /// when present; the flat list is the fallback for records without one.
    enum ChatHistory {
        static func orderedMessages(_ chat: [String: Any]) -> [[String: Any]] {
            let flat = chat["messages"] as? [[String: Any]] ?? []
            guard let hist = chat["history"] as? [String: Any],
                  let nodes = hist["messages"] as? [String: [String: Any]], !nodes.isEmpty,
                  let tip = hist["currentId"] as? String else { return flat }
            var out: [[String: Any]] = []
            var seen = Set<String>()
            var id: String? = tip
            while let cur = id, let node = nodes[cur], seen.insert(cur).inserted {
                out.append(node)
                id = node["parentId"] as? String
            }
            return out.isEmpty ? flat : out.reversed()
        }
    }

    /// Load the Pulse folder's chats as cards (title with the "Pulse · " prefix stripped + a preview).
    func pulseCards(folderID: String) async -> [PulseCard] {
        guard let (data, _) = try? await URLSession.shared.data(for: request("/api/v1/chats/folder/\(folderID)")),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return [] }
        return arr.compactMap { c in
            guard let id = c["id"] as? String, var title = c["title"] as? String else { return nil }
            if title.hasPrefix("Pulse · ") { title = String(title.dropFirst("Pulse · ".count)) }
            let msgs = (c["chat"] as? [String: Any])?["messages"] as? [[String: Any]]
            let raw = (msgs?.first?["content"] as? String) ?? ""
            let p = PulseMarkers.parse(raw)
            // Prefer Vera's complete one-sentence summary; fall back to a stripped body snippet.
            let preview = (p.summary?.isEmpty == false) ? p.summary!
                : String(p.body.strippedMarkdown(droppingTitle: title).prefix(200))
            return PulseCard(id: id, title: title, preview: preview, subtitle: "Pulse",
                             imageURL: p.image, tint: p.tint,
                             sources: p.sources.map { $0.url },
                             sourceList: p.sources, inlineImages: p.inlineImages, body: p.body)
        }
    }

    /// Delete a conversation (best-effort; unknown/local ids 404 harmlessly).
    func deleteChat(id: String) async {
        _ = try? await URLSession.shared.data(for: request("/api/v1/chats/\(id)", method: "DELETE"))
    }

    /// Graduate a chat out of a folder (folder_id null) — used to "bookmark"/promote a Pulse card.
    func graduateChat(id: String) async {
        let body = try? JSONSerialization.data(withJSONObject: ["folder_id": NSNull()])
        _ = try? await URLSession.shared.data(for: request("/api/v1/chats/\(id)/folder", method: "POST", body: body))
    }

    /// Append a 👍/👎 record to the vera-api feedback log (RLHF/DPO preference data).
    func postFeedback(_ fields: [String: Any]) async {
        guard let url = veraAPI("/feedback"),
              let body = try? JSONSerialization.data(withJSONObject: fields) else { return }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = body
        _ = try? await URLSession.shared.data(for: r)
    }

    // MARK: - Pulse (vera-api standalone store)

    /// The Pulse feed, served by vera-api (not an OWUI folder). Decodes clean JSON → PulseCard.
    func fetchPulseCards() async -> [PulseCard] {
        guard let url = veraAPI("/pulse/cards"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = obj["cards"] as? [[String: Any]] else { return [] }
        return arr.compactMap { c in
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
    }

    /// The pinned ambient-vein catalog from vera-api, ordered for the chip row.
    func fetchPulseVeins() async -> [PulseVein] {
        guard let url = veraAPI("/pulse/veins"),
              let (data, _) = try? await URLSession.shared.data(from: url),
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

    /// Vera's self-authored journal of standing commitments (read-only surface).
    func fetchJournal() async -> ([JournalEntry], [JournalArchiveMonth]) {
        guard let url = veraAPI("/journal"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return ([], []) }
        let entries = (obj["entries"] as? [[String: Any]] ?? []).compactMap(JournalEntry.parse)
        let archive = (obj["archive"] as? [[String: Any]] ?? []).compactMap { m -> JournalArchiveMonth? in
            guard let month = m["month"] as? String, let text = m["text"] as? String else { return nil }
            return JournalArchiveMonth(id: month, text: text)
        }
        return (entries, archive)
    }

    /// Mark a Pulse card read — fired when its detail opens. Idempotent.
    func markPulseRead(id: String) async {
        guard let url = veraAPI("/pulse/read") else { return }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: ["card_id": id])
        _ = try? await URLSession.shared.data(for: r)
    }

    /// Reverse (restore) or reject one grooming op, routed to the correct store by `op.store`.
    /// `mode` is "restore" or "reject"; returns "done" | "failed" | "stale".
    func decideGroomOp(store: String, mode: String, cardID: String, opIndex: Int) async -> String {
        let base = store == "knowledge" ? "/knowledge/" : "/memory/"
        guard let url = veraAPI(base + mode) else { return "failed" }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: ["card_id": cardID, "op_index": opIndex])
        guard let (data, _) = try? await URLSession.shared.data(for: r),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "failed" }
        if (obj["stale"] as? Bool) == true { return "stale" }
        return (obj["ok"] as? Bool) == true ? "done" : "failed"
    }

    /// Confirm a Pulse action card → execute the staged action server-side. Returns the response dict.
    func commitAction(token: String) async -> [String: Any]? {
        guard let url = veraAPI("/actions/commit") else { return nil }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: ["token": token])
        guard let (data, _) = try? await URLSession.shared.data(for: r),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj
    }

    /// Dismiss a Pulse action card → drop the staged action without executing it.
    func dismissAction(token: String) async {
        guard let url = veraAPI("/actions/dismiss") else { return }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: ["token": token])
        _ = try? await URLSession.shared.data(for: r)
    }

    /// Approve/skip one item of a multi-item digest card. Returns whether it applied.
    func decideDigestItem(cardID: String, itemID: String, approve: Bool) async -> Bool {
        guard let url = veraAPI("/actions/card/item") else { return false }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: [
            "card_id": cardID, "item_id": itemID, "decision": approve ? "approve" : "skip"])
        guard let (data, _) = try? await URLSession.shared.data(for: r),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return (obj["ok"] as? Bool) == true
    }

    /// Approve/skip every still-pending item of a digest card.
    func decideDigestAll(cardID: String, approve: Bool) async {
        guard let url = veraAPI("/actions/card/all") else { return }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: [
            "card_id": cardID, "decision": approve ? "approve" : "skip"])
        _ = try? await URLSession.shared.data(for: r)
    }

    /// Force a fresh System vein run (POST /pulse/veins/status/run), covering the health probes
    /// and the stack-updates check. Returns true on a 2xx, so the caller can refresh the feed
    /// once the card has been reconciled against current reality.
    @discardableResult
    func checkUpdatesNow() async -> Bool {
        guard let url = veraAPI("/pulse/veins/status/run") else { return false }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: [String: Any]())
        guard let (_, resp) = try? await URLSession.shared.data(for: r),
              let code = (resp as? HTTPURLResponse)?.statusCode, (200..<300).contains(code) else { return false }
        return true
    }

    /// Promote a card → create/return its OWUI chat id (so it opens as a real chat).
    func promotePulse(id: String) async -> String? {
        guard let url = veraAPI("/pulse/\(id)/promote") else { return nil }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        guard let (data, _) = try? await URLSession.shared.data(for: r),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj["chat_id"] as? String
    }

    /// Bookmark/unbookmark a card → returns its backing OWUI chat id (created on first bookmark).
    @discardableResult
    func setPulseBookmark(id: String, on: Bool) async -> String? {
        guard let url = veraAPI("/pulse/\(id)/bookmark") else { return nil }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: ["on": on])
        guard let (data, _) = try? await URLSession.shared.data(for: r),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj["chat_id"] as? String
    }

    /// IDs of pinned chats (to restore the Pinned section on launch).
    func pinnedChatIDs() async -> Set<String> {
        guard let (data, _) = try? await URLSession.shared.data(for: request("/api/v1/chats/pinned")),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return [] }
        return Set(arr.compactMap { $0["id"] as? String })
    }

    /// Toggle a chat's pinned state in OWUI.
    func togglePin(id: String) async {
        _ = try? await URLSession.shared.data(for: request("/api/v1/chats/\(id)/pin", method: "POST", body: Data("{}".utf8)))
    }

    /// Delete a memory entry. Returns true on a 2xx.
    func deleteMemory(id: String) async -> Bool {
        guard let (_, resp) = try? await URLSession.shared.data(for: request("/api/v1/memories/\(id)", method: "DELETE")),
              let http = resp as? HTTPURLResponse else { return false }
        return (200..<300).contains(http.statusCode)
    }

    /// Load the user's memory entries from OWUI. Nil means the fetch failed — distinct from
    /// an empty list, so an unreachable server never reads as "all memories deleted".
    func memories() async -> [MemoryItem]? {
        guard let (data, _) = try? await URLSession.shared.data(for: request("/api/v1/memories/")),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return nil }
        return arr.compactMap { m in
            guard let id = m["id"] as? String, let content = m["content"] as? String else { return nil }
            return MemoryItem.parse(id: id, content: content)
        }
    }

    /// Stream an assistant reply token-by-token from /api/chat/completions (SSE).
    func streamReply(messages: [[String: String]]) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    var payload: [String: Any] = [
                        "model": config.model,
                        "stream": true,
                        "messages": messages,
                    ]
                    // Pure OpenAI unless server-specific template kwargs are configured.
                    if let kwargs = config.chatTemplateKwargsObject() {
                        payload["chat_template_kwargs"] = kwargs
                    }
                    let body = try JSONSerialization.data(withJSONObject: payload)
                    var req = URLRequest(url: config.completionsURL)
                    req.httpMethod = "POST"
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    req.httpBody = body
                    let (bytes, _) = try await URLSession.shared.bytes(for: req)
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data:") else { continue }
                        let raw = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if raw == "[DONE]" { break }
                        if let d = raw.data(using: .utf8),
                           let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                           let choices = obj["choices"] as? [[String: Any]],
                           let delta = choices.first?["delta"] as? [String: Any],
                           let content = delta["content"] as? String, !content.isEmpty {
                            continuation.yield(content)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
}
