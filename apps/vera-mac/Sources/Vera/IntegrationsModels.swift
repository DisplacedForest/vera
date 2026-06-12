import Foundation

/// One configurable field of an integration, as declared by vera-api's registry.
/// Secret fields never carry their value — only whether one is set.
struct PluginField: Identifiable, Hashable, Sendable {
    let id: String
    var label: String
    var secret: Bool
    var envLocked: Bool      // pinned by the server's environment — not editable here
    var hint: String
    var value: String        // non-secret fields only ("" for secrets)
    var isSet: Bool          // secrets: whether a value exists server-side
    var choices: [String] = []  // non-empty -> render a picker, not a text field
}

/// One experimental feature under an integration, with the server-owned ramifications
/// text the consent sheet must show verbatim.
struct PluginFeature: Identifiable, Hashable, Sendable {
    let id: String
    var label: String
    var ramifications: String
    var enabled: Bool
    var acked: Bool
}

/// The grocy⇄mealie style pairing hint — rendered only from API data, never hardcoded.
struct PluginPairing: Hashable, Sendable {
    var otherID: String
    var label: String
    var active: Bool
}

/// One integration card from `GET /integrations`.
struct PluginEntry: Identifiable, Hashable, Sendable {
    let id: String
    var displayName: String
    var status: String       // unconfigured | configured | enabled | error
    var enabled: Bool
    var configured: Bool
    var unlocks: [String]
    var fields: [PluginField]
    var features: [PluginFeature]
    var pairing: PluginPairing?
    var lastTestOK: Bool?
    var lastTestDetail: String

    var unlocksLine: String { unlocks.joined(separator: " · ") }

    /// Tolerant decode of one registry entry. Unknown fields degrade to blanks, never fake data.
    static func parse(_ j: [String: Any]) -> PluginEntry? {
        guard let id = j["id"] as? String else { return nil }
        let fields: [PluginField] = (j["fields"] as? [[String: Any]] ?? []).compactMap { f in
            guard let fid = f["id"] as? String else { return nil }
            let secret = (f["secret"] as? Bool) ?? false
            return PluginField(
                id: fid,
                label: (f["label"] as? String) ?? fid,
                secret: secret,
                envLocked: (f["env_locked"] as? Bool) ?? false,
                hint: (f["hint"] as? String) ?? "",
                value: (f["value"] as? String) ?? "",
                isSet: secret ? ((f["set"] as? Bool) ?? false) : !(((f["value"] as? String) ?? "").isEmpty),
                choices: (f["choices"] as? [String]) ?? [])
        }
        let features: [PluginFeature] = (j["features"] as? [[String: Any]] ?? []).compactMap { f in
            guard let fid = f["id"] as? String else { return nil }
            return PluginFeature(
                id: fid,
                label: (f["label"] as? String) ?? fid,
                ramifications: (f["ramifications"] as? String) ?? "",
                enabled: (f["enabled"] as? Bool) ?? false,
                acked: (f["acked"] as? Bool) ?? false)
        }
        var pairing: PluginPairing?
        if let p = j["paired_with"] as? [String: Any], let other = p["id"] as? String {
            pairing = PluginPairing(otherID: other,
                                    label: (p["label"] as? String) ?? "",
                                    active: (p["active"] as? Bool) ?? false)
        }
        let test = j["last_test"] as? [String: Any]
        return PluginEntry(
            id: id,
            displayName: (j["display_name"] as? String) ?? id,
            status: (j["status"] as? String) ?? "unconfigured",
            enabled: (j["enabled"] as? Bool) ?? false,
            configured: (j["configured"] as? Bool) ?? false,
            unlocks: (j["unlocks"] as? [String]) ?? [],
            fields: fields,
            features: features,
            pairing: pairing,
            lastTestOK: test?["ok"] as? Bool,
            lastTestDetail: (test?["detail"] as? String) ?? "")
    }

    /// Demo entries for headless `--shot` renders (mixed states; mirrors the API shape).
    static func mock() -> [PluginEntry] {
        func field(_ id: String, _ label: String, secret: Bool = false, value: String = "",
                   isSet: Bool = false, hint: String = "", choices: [String] = []) -> PluginField {
            PluginField(id: id, label: label, secret: secret, envLocked: false,
                        hint: hint, value: value, isSet: isSet || !value.isEmpty, choices: choices)
        }
        return [
            PluginEntry(id: "coder", displayName: "Coder / Dream model", status: "enabled",
                        enabled: true, configured: true,
                        unlocks: ["nightly dreaming consolidation and grooming",
                                  "fact verification research with web search"],
                        fields: [field("url", "OpenAI-compatible base URL", value: "http://192.0.2.10:8084/v1"),
                                 field("model", "Model id", value: "your-coder-model"),
                                 field("tool_protocol", "Tool-call protocol", value: "mlx",
                                       hint: "openai = standard tool_calls (default); mlx = text-protocol fallback for mlx_lm.server",
                                       choices: ["openai", "mlx"])],
                        features: [], pairing: nil, lastTestOK: true, lastTestDetail: "endpoint up (3 models)"),
            PluginEntry(id: "home_assistant", displayName: "Home Assistant", status: "enabled",
                        enabled: true, configured: true,
                        unlocks: ["live home state in chat and cards", "confirm-gated device actuation"],
                        fields: [field("url", "Base URL", value: "http://192.0.2.10:8123"),
                                 field("token", "Long-lived access token", secret: true, isSet: true)],
                        features: [PluginFeature(id: "home_modeling", label: "Home modeling",
                                                 ramifications: "Captures every Home Assistant state change house-wide (roughly 5,000–15,000 events per day on a 30-day rolling window) and models the household's rhythm from 10–90 days of accumulation. Experimental: unvalidated at scale.",
                                                 enabled: false, acked: false)],
                        pairing: nil, lastTestOK: true, lastTestDetail: "API running."),
            PluginEntry(id: "grocy", displayName: "Grocy", status: "enabled",
                        enabled: true, configured: true,
                        unlocks: ["kitchen inventory and expiry tracking", "shopping list"],
                        fields: [field("url", "Base URL", value: "http://192.0.2.10:9283"),
                                 field("api_key", "API key", secret: true, isSet: true)],
                        features: [],
                        pairing: PluginPairing(otherID: "mealie", label: "recipe suggestions from expiring inventory", active: true),
                        lastTestOK: true, lastTestDetail: "Grocy 4.6.0"),
            PluginEntry(id: "mealie", displayName: "Mealie", status: "enabled",
                        enabled: true, configured: true,
                        unlocks: ["recipe import and browse", "recipe classification"],
                        fields: [field("url", "Base URL", value: "http://192.0.2.10:9925"),
                                 field("api_key", "API token", secret: true, isSet: true)],
                        features: [],
                        pairing: PluginPairing(otherID: "grocy", label: "recipe suggestions from expiring inventory", active: true),
                        lastTestOK: nil, lastTestDetail: ""),
            PluginEntry(id: "overseerr", displayName: "Overseerr", status: "error",
                        enabled: true, configured: true,
                        unlocks: ["media requests from chat", "library availability checks"],
                        fields: [field("url", "Base URL", value: "http://192.0.2.10:5055"),
                                 field("api_key", "API key", secret: true, isSet: true)],
                        features: [PluginFeature(id: "media_curation", label: "Media curation digest",
                                                 ramifications: "Adds a weekly job that sweeps discovery sources through Overseerr, runs an LLM taste pass over the pool, and posts a worth-adding digest card. Experimental: it has run exactly once at scale.",
                                                 enabled: false, acked: false)],
                        pairing: nil, lastTestOK: false, lastTestDetail: "HTTP 502"),
            PluginEntry(id: "unraid", displayName: "Unraid", status: "unconfigured",
                        enabled: false, configured: false,
                        unlocks: ["confirm-gated container updates and host actuation"],
                        fields: [field("url", "GraphQL endpoint"),
                                 field("api_key", "API key", secret: true)],
                        features: [], pairing: nil, lastTestOK: nil, lastTestDetail: ""),
            PluginEntry(id: "searxng", displayName: "SearXNG", status: "unconfigured",
                        enabled: false, configured: false,
                        unlocks: ["web search for chat, research, and Pulse"],
                        fields: [field("url", "Search endpoint",
                                       hint: "the /search endpoint of your SearXNG instance")],
                        features: [], pairing: nil, lastTestOK: nil, lastTestDetail: ""),
        ]
    }
}

/// The OWUI side each plugin declares: which OWUI tools get attached to the Vera model
/// when the plugin is enabled. Plugins absent here have no OWUI step. The kitchen tool
/// is shared by grocy and mealie — it detaches only when BOTH are off (the store checks).
enum PluginOWUI {
    static let tools: [String: [String]] = [
        "home_assistant": ["home_assistant"],
        "grocy": ["kitchen"],
        "mealie": ["kitchen"],
        "overseerr": ["media_request"],
    ]
}

/// Thin client for vera-api's integrations endpoints.
struct IntegrationsClient: Sendable {
    let base: URL

    enum Fetch: Sendable {
        case ok([PluginEntry])
        case unsupported(Int)   // vera-api answered, but has no registry (older build)
        case unreachable
    }

    func fetch() async -> Fetch {
        var req = URLRequest(url: base.appendingPathComponent("/integrations"))
        req.timeoutInterval = 8
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return .unreachable }
        guard (200..<300).contains(code) else { return .unsupported(code) }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = json["integrations"] as? [[String: Any]] else { return .unsupported(code) }
        return .ok(arr.compactMap { PluginEntry.parse($0) })
    }

    /// Live connection check (nothing persisted). Optional field overrides let the sheet
    /// test values before saving them.
    func test(id: String, fields: [String: String]? = nil) async -> (ok: Bool, detail: String) {
        var req = URLRequest(url: base.appendingPathComponent("/integrations/\(id)/test"))
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = fields.map { ["fields": $0] } ?? [:]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else {
            return (false, "vera-api unreachable")
        }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return (false, "HTTP \(code)")
        }
        return ((json["ok"] as? Bool) ?? false, (json["detail"] as? String) ?? "HTTP \(code)")
    }

    /// PUT field values / enabled / feature toggles. Returns the server's error detail on
    /// refusal (the 400/409 ack and gating contract), nil on success.
    func save(id: String, fields: [String: String]? = nil, enabled: Bool? = nil,
              features: [String: (enabled: Bool, ack: Bool)]? = nil) async -> String? {
        var body: [String: Any] = [:]
        if let fields { body["fields"] = fields }
        if let enabled { body["enabled"] = enabled }
        if let features {
            body["features"] = features.mapValues { ["enabled": $0.enabled, "ack": $0.ack] }
        }
        var req = URLRequest(url: base.appendingPathComponent("/integrations/\(id)"))
        req.httpMethod = "PUT"
        req.timeoutInterval = 10
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return "vera-api unreachable" }
        if (200..<300).contains(code) { return nil }
        let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (json?["detail"] as? String) ?? "HTTP \(code)"
    }
}
