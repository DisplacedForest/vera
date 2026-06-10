import Foundation

/// Vera's stored model record (raw OWUI JSON) + convenience accessors.
struct VeraModel {
    var raw: [String: Any]
    var toolIds: [String] { (raw["meta"] as? [String: Any])?["toolIds"] as? [String] ?? [] }
}

/// Admin-side OWUI REST: tools, functions, the model's enabled toolIds, and tool-server connections.
/// Auth reuses the JWT from `VeraSocket`'s sign-in (admin role) via the `token` provider.
struct OWUIAdminClient: Sendable {
    let baseURL: URL
    let modelID: String
    let token: @Sendable () async throws -> String

    // MARK: HTTP

    private func request(_ path: String, method: String, body: [String: Any]?) async throws -> Any {
        // Build the URL by string concat so query strings like `?id=` survive (appendingPathComponent escapes them).
        let base = baseURL.absoluteString.hasSuffix("/") ? String(baseURL.absoluteString.dropLast()) : baseURL.absoluteString
        guard let url = URL(string: base + path) else { throw URLError(.badURL) }
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.setValue("Bearer \(try await token())", forHTTPHeaderField: "Authorization")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body { r.httpBody = try JSONSerialization.data(withJSONObject: body) }
        let (data, resp) = try await URLSession.shared.data(for: r)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else {
            throw NSError(domain: "OWUIAdmin", code: code, userInfo: [NSLocalizedDescriptionKey:
                "\(method) \(path) → HTTP \(code): \(String(data: data, encoding: .utf8)?.prefix(160) ?? "")"])
        }
        return (try? JSONSerialization.jsonObject(with: data)) ?? [:]
    }
    private func get(_ path: String) async throws -> Any { try await request(path, method: "GET", body: nil) }
    private func post(_ path: String, _ body: [String: Any]) async throws -> Any { try await request(path, method: "POST", body: body) }

    /// POST a pre-serialized JSON body (keeps non-Sendable dicts off the actor hop).
    private func postData(_ path: String, _ json: Data) async throws {
        let base = baseURL.absoluteString.hasSuffix("/") ? String(baseURL.absoluteString.dropLast()) : baseURL.absoluteString
        guard let url = URL(string: base + path) else { throw URLError(.badURL) }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue("Bearer \(try await token())", forHTTPHeaderField: "Authorization")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = json
        let (data, resp) = try await URLSession.shared.data(for: r)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else {
            throw NSError(domain: "OWUIAdmin", code: code, userInfo: [NSLocalizedDescriptionKey:
                "POST \(path) → HTTP \(code): \(String(data: data, encoding: .utf8)?.prefix(160) ?? "")"])
        }
    }

    // MARK: Reads

    func listTools() async throws -> [ToolEntry] {
        let arr = try await get("/api/v1/tools/") as? [[String: Any]] ?? []
        return arr.compactMap { t in
            guard let id = t["id"] as? String, let name = t["name"] as? String else { return nil }
            let desc = (t["meta"] as? [String: Any])?["description"] as? String ?? ""
            return ToolEntry(id: id, name: name, description: desc, availableToVera: false, lastUsed: nil)
        }
    }

    func listFunctions() async throws -> [FunctionEntry] {
        let arr = try await get("/api/v1/functions/") as? [[String: Any]] ?? []
        return arr.compactMap { f in
            guard let id = f["id"] as? String, let name = f["name"] as? String else { return nil }
            return FunctionEntry(id: id, name: name, type: f["type"] as? String ?? "filter",
                                 isActive: f["is_active"] as? Bool ?? false)
        }
    }

    func toolServers() async throws -> [ToolServer] {
        let obj = try await get("/api/v1/configs/tool_servers") as? [String: Any] ?? [:]
        let arr = obj["TOOL_SERVER_CONNECTIONS"] as? [[String: Any]] ?? []
        return arr.compactMap { c in
            guard let url = c["url"] as? String else { return nil }
            let name = (c["info"] as? [String: Any])?["name"] as? String ?? url
            let enabled = (c["config"] as? [String: Any])?["enable"] as? Bool ?? true
            return ToolServer(url: url, name: name, key: c["key"] as? String ?? "", enabled: enabled)
        }
    }

    func veraModel() async throws -> VeraModel {
        let raw = try await get("/api/v1/models/model?id=\(modelID)") as? [String: Any] ?? [:]
        return VeraModel(raw: raw)
    }

    func currentRole() async throws -> String {
        let me = try await get("/api/v1/auths/") as? [String: Any] ?? [:]
        return me["role"] as? String ?? "user"
    }

    /// Merge a valves spec (JSON-Schema `properties`) with current values into editable fields.
    private func valveFields(spec: Any?, values: Any?) -> [ValveField] {
        guard let props = (spec as? [String: Any])?["properties"] as? [String: Any] else { return [] }
        let vals = values as? [String: Any] ?? [:]
        return props.keys.sorted().map { key in
            let p = props[key] as? [String: Any] ?? [:]
            let type: ValveType
            switch p["type"] as? String {
            case "string": type = .string
            case "integer": type = .int
            case "number": type = .number
            case "boolean": type = .bool
            default: type = .unknown
            }
            return ValveField(key: key,
                              title: (p["title"] as? String) ?? key,
                              help: (p["description"] as? String) ?? "",
                              type: type,
                              value: Self.stringify(vals[key] ?? p["default"]))
        }
    }

    func toolValves(id: String) async throws -> [ValveField] {
        async let values = get("/api/v1/tools/id/\(id)/valves")
        async let spec = get("/api/v1/tools/id/\(id)/valves/spec")
        return valveFields(spec: try await spec, values: try await values)
    }

    func functionValves(id: String) async throws -> [ValveField] {
        async let values = get("/api/v1/functions/id/\(id)/valves")
        async let spec = get("/api/v1/functions/id/\(id)/valves/spec")
        return valveFields(spec: try await spec, values: try await values)
    }

    // MARK: Writes

    /// Build the `/models/model/update` ModelForm body from a fetched record, applying optional
    /// meta/params overrides. Carries every field OWUI's ModelForm re-validates — notably
    /// `access_grants` (OWUI 0.9.6): the field is a bare `list` with a `None` default, so omitting it
    /// makes the server re-validate `None` against `list` and 500. Pass the record's value through
    /// (default `[]`) so the write is non-destructive. One place to update when the schema grows.
    private func modelUpdateForm(_ rec: [String: Any], meta: [String: Any]? = nil,
                                 params: [String: Any]? = nil) -> [String: Any] {
        [
            "id": rec["id"] ?? modelID,
            "base_model_id": rec["base_model_id"] ?? NSNull(),
            "name": rec["name"] ?? "Vera",
            "meta": meta ?? rec["meta"] ?? [:],
            "params": params ?? rec["params"] ?? [:],
            "access_control": rec["access_control"] ?? NSNull(),
            "access_grants": rec["access_grants"] ?? [],
            "is_active": rec["is_active"] as? Bool ?? true,
        ]
    }

    /// Read Vera's record, mutate only meta.toolIds, POST the full ModelForm back.
    func setVeraToolIds(_ ids: [String]) async throws {
        let rec = try await veraModel().raw
        var meta = rec["meta"] as? [String: Any] ?? [:]
        meta["toolIds"] = ids
        _ = try await post("/api/v1/models/model/update?id=\(modelID)", modelUpdateForm(rec, meta: meta))
    }

    func toggleFunction(id: String) async throws -> Bool {
        let r = try await post("/api/v1/functions/id/\(id)/toggle", [:]) as? [String: Any] ?? [:]
        return r["is_active"] as? Bool ?? false
    }

    func updateToolValves(id: String, json: Data) async throws {
        try await postData("/api/v1/tools/id/\(id)/valves/update", json)
    }
    func updateFunctionValves(id: String, json: Data) async throws {
        try await postData("/api/v1/functions/id/\(id)/valves/update", json)
    }

    static let askConventionMarker = "# Structured questions (vera:ask)"

    /// Append the `vera:ask` convention to Vera's system prompt (idempotent). Returns true if added.
    func ensureAskConvention() async throws -> Bool {
        let rec = try await veraModel().raw
        var params = rec["params"] as? [String: Any] ?? [:]
        let system = params["system"] as? String ?? ""
        if system.contains(Self.askConventionMarker) { return false }
        let convention = """


        \(Self.askConventionMarker)
        When you need the user to choose between a small set of options, you MAY emit a fenced block:
        ```vera:ask
        {"question":"...","multiSelect":false,"options":[{"label":"short label","description":"one line"}]}
        ```
        Use 2-4 options, each with a brief description; set multiSelect true to allow multiple. The app renders tappable choices and posts the user's selection back as their next message. Use only at genuine forks; otherwise just ask in plain text.
        """
        params["system"] = system + convention
        _ = try await post("/api/v1/models/model/update?id=\(modelID)", modelUpdateForm(rec, params: params))
        return true
    }

    static let artifactConventionMarker = "# Canvas artifacts (vera-artifact)"

    /// Append the `vera-artifact` convention to Vera's system prompt (idempotent). Returns true if added.
    func ensureArtifactConvention() async throws -> Bool {
        let rec = try await veraModel().raw
        var params = rec["params"] as? [String: Any] ?? [:]
        let system = params["system"] as? String ?? ""
        if system.contains(Self.artifactConventionMarker) { return false }
        let convention = """


        \(Self.artifactConventionMarker)
        For substantial standalone documents, code files, HTML pages, SVG drawings, or Mermaid
        diagrams, emit them as a Canvas artifact so they open in an editable side panel:
        :::vera-artifact id="short-stable-id" title="Human Title" type="html|svg|mermaid|code|markdown" language="swift"
        <the raw content, may span many lines>
        :::
        Reuse the same id when revising an existing artifact so it updates in place. Use plain inline
        code blocks for short snippets; reserve artifacts for things worth a panel.
        """
        params["system"] = system + convention
        _ = try await post("/api/v1/models/model/update?id=\(modelID)", modelUpdateForm(rec, params: params))
        return true
    }

    static let presentationConventionMarker = "# Presentation tools (tables, vera:chart, vera:stats)"

    /// Install/update the inline presentation-tools convention in Vera's system prompt. Idempotent
    /// as a SET: if the section already exists it's replaced (so wording can be tuned), else appended.
    /// Judgment-first, but with a concrete DO trigger so she doesn't avoid the tools when they fit.
    func ensurePresentationConventions() async throws -> Bool {
        let rec = try await veraModel().raw
        var params = rec["params"] as? [String: Any] ?? [:]
        var system = params["system"] as? String ?? ""
        // Replace any existing presentation section (it's appended last) so re-installs update it.
        if let r = system.range(of: Self.presentationConventionMarker) {
            system = String(system[..<r.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let convention = """


        \(Self.presentationConventionMarker)
        You can present information as more than prose, and you SHOULD when the shape of the data calls
        for it. Prose is still the default and a block that adds nothing is a mistake — but do not avoid
        these when they clearly fit.

        Decide by the SHAPE of the data:
        - Comparing the same metrics across different ENTITIES (two players, two clubs) -> markdown table.
        - Tracking ONE metric across an ordered SEQUENCE (seasons, months, years, steps) -> CHART, not a
          table. A single number moving over time is a chart. If you catch yourself listing a metric
          season-by-season or year-by-year (e.g. goals per season), that is a chart — emit a vera:chart,
          do not put it in a table. Use a line for a continuous trend, bars for discrete periods.

        - Markdown table — the same 3+ metrics across 2+ entities. GitHub-flavored syntax, header row.
          (One or two stray numbers: just write the sentence.)
        - Chart (a fenced ```vera:chart block) — one metric across an ordered sequence:
        ```vera:chart
        {"type":"bar|line|groupedBar","title":"...","yLabel":"goals","series":[{"name":"Isak","points":[{"x":"23-24","y":21},{"x":"24-25","y":23}]}]}
        ```
        - Stat cards (a fenced ```vera:stats block) — 2-4 headline numbers worth pulling out:
        ```vera:stats
        {"cards":[{"value":"23","label":"PL goals","sub":"34 games"},{"value":"7.30","label":"rating"}]}
        ```

        Worked example — a question about a value over time becomes a chart, NOT a bulleted list. If asked
        "how has X changed each year", you answer like this:
        ```vera:chart
        {"type":"bar","title":"Monthly active users","yLabel":"users","series":[{"name":"MAU","points":[{"x":"Jan","y":1200},{"x":"Feb","y":1850},{"x":"Mar","y":2600}]}]}
        ```
        Then one or two sentences interpreting it. Critically: even when you used tools (search, code) to
        gather the numbers, you STILL emit the chart block afterward — never fall back to listing the values
        as a bold/bulleted prose list. Use only real values you have; keep the "so what" in the prose around it.
        For large or interactive things, use a Canvas artifact instead.
        """
        params["system"] = system + convention
        _ = try await post("/api/v1/models/model/update?id=\(modelID)", modelUpdateForm(rec, params: params))
        return true
    }

    func setToolServers(_ servers: [ToolServer]) async throws {
        let conns: [[String: Any]] = servers.map {
            ["url": $0.url, "key": $0.key, "config": ["enable": $0.enabled], "info": ["name": $0.name]]
        }
        _ = try await post("/api/v1/configs/tool_servers", ["TOOL_SERVER_CONNECTIONS": conns])
    }

    static func stringify(_ v: Any?) -> String {
        switch v {
        case let s as String: return s
        case let b as Bool: return b ? "true" : "false"
        case let i as Int: return String(i)
        case let d as Double: return d == d.rounded() ? String(Int(d)) : String(d)
        case is NSNull, .none: return ""
        default: return String(describing: v!)
        }
    }
}
