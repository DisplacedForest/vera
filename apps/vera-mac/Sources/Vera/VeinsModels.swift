import Foundation

/// One scoping field inside a vein's option group, declared by the server manifest.
/// The app renders by `type` and hardcodes no vein knowledge — unknown fields render
/// generically.
struct VeinField: Identifiable, Hashable, Sendable {
    let id: String
    var label: String
    var type: String          // bool | text | number | choice
    var choices: [String]
    var hint: String
    var value: String         // effective value, stringified ("" when unset)
    var isOn: Bool            // bool fields
}

struct VeinOptionGroup: Identifiable, Hashable, Sendable {
    var id: String { group }
    let group: String
    var fields: [VeinField]
}

/// A declared endpoint contract — point the vein at any compatible service.
struct VeinProvider: Identifiable, Hashable, Sendable {
    let id: String
    var label: String
    var hint: String
    var value: String
    var defaultValue: String
}

struct VeinRequirement: Hashable, Sendable {
    var label: String
    var met: Bool
    var detail: String
    var integration: String?   // when set, the requirement lives in the Plugins surface
}

struct VeinJob: Identifiable, Hashable, Sendable {
    let id: String
    var label: String
    var cron: String
    var enabled: Bool
    var gated: String?
}

/// One vein from `GET /pulse/veins/catalog` — manifest merged with runtime state.
struct VeinEntry: Identifiable, Hashable, Sendable {
    var id: String { kind }
    let kind: String
    var label: String
    var icon: String
    var blurb: String
    var nominalLabel: String
    var enabled: Bool
    var canEnable: Bool
    var requires: [VeinRequirement]
    var providers: [VeinProvider]
    var options: [VeinOptionGroup]
    var jobs: [VeinJob]
    var exposed: Bool = true
    var requiresUnmet: [VeinRequirement] = []
    var origin: String = "custom"

    var isCustom: Bool { origin == "custom" }

    /// Tolerant decode of one catalog entry. Unknown fields degrade to blanks, never fake data.
    static func parse(_ j: [String: Any]) -> VeinEntry? {
        guard let kind = j["kind"] as? String else { return nil }
        func str(_ v: Any?) -> String {
            if let s = v as? String { return s }
            if let b = v as? Bool { return b ? "true" : "false" }
            if let n = v as? NSNumber { return n.doubleValue == n.doubleValue.rounded() && abs(n.doubleValue) < 1e12 ? String(format: "%g", n.doubleValue) : "\(n)" }
            return ""
        }
        let requires: [VeinRequirement] = (j["requires"] as? [[String: Any]] ?? []).map { r in
            VeinRequirement(label: (r["label"] as? String) ?? "",
                            met: (r["met"] as? Bool) ?? false,
                            detail: (r["detail"] as? String) ?? "",
                            integration: r["integration"] as? String)
        }
        let providers: [VeinProvider] = (j["providers"] as? [[String: Any]] ?? []).compactMap { p in
            guard let pid = p["id"] as? String else { return nil }
            return VeinProvider(id: pid, label: (p["label"] as? String) ?? pid,
                                hint: (p["hint"] as? String) ?? "",
                                value: str(p["value"]),
                                defaultValue: str(p["default"]))
        }
        let options: [VeinOptionGroup] = (j["options"] as? [[String: Any]] ?? []).compactMap { g in
            guard let name = g["group"] as? String else { return nil }
            let fields: [VeinField] = (g["fields"] as? [[String: Any]] ?? []).compactMap { f in
                guard let fid = f["id"] as? String else { return nil }
                return VeinField(id: fid, label: (f["label"] as? String) ?? fid,
                                 type: (f["type"] as? String) ?? "text",
                                 choices: (f["choices"] as? [String]) ?? [],
                                 hint: (f["hint"] as? String) ?? "",
                                 value: f["value"] is NSNull ? "" : str(f["value"]),
                                 isOn: (f["value"] as? Bool) ?? false)
            }
            return VeinOptionGroup(group: name, fields: fields)
        }
        let jobs: [VeinJob] = (j["jobs"] as? [[String: Any]] ?? []).compactMap { jb in
            guard let jid = jb["id"] as? String else { return nil }
            return VeinJob(id: jid, label: (jb["label"] as? String) ?? jid,
                           cron: (jb["cron"] as? String) ?? "",
                           enabled: (jb["enabled"] as? Bool) ?? false,
                           gated: jb["gated"] as? String)
        }
        let requiresUnmet: [VeinRequirement] = (j["requires_unmet"] as? [[String: Any]] ?? []).map { r in
            let idv = r["id"] as? String
            return VeinRequirement(label: (r["label"] as? String) ?? "", met: false, detail: "",
                                   integration: (idv?.isEmpty == false) ? idv : nil)
        }
        return VeinEntry(kind: kind,
                         label: (j["label"] as? String) ?? kind,
                         icon: (j["icon"] as? String) ?? "rectangle.dashed",
                         blurb: (j["blurb"] as? String) ?? "",
                         nominalLabel: (j["nominal_label"] as? String) ?? "quiet",
                         enabled: (j["enabled"] as? Bool) ?? false,
                         canEnable: (j["can_enable"] as? Bool) ?? false,
                         requires: requires, providers: providers, options: options, jobs: jobs,
                         exposed: (j["exposed"] as? Bool) ?? true, requiresUnmet: requiresUnmet,
                         origin: (j["origin"] as? String) ?? "custom")
    }

    static func mock() -> [VeinEntry] {
        [
            VeinEntry(kind: "rivergauge", label: "River gauge", icon: "water.waves",
                      blurb: "watches the river level and speaks up past flood stage",
                      nominalLabel: "steady", enabled: true, canEnable: true, requires: [],
                      providers: [VeinProvider(id: "gauge_url", label: "Gauge endpoint",
                                               hint: "any JSON gauge feed",
                                               value: "https://waterdata.example/gauge.json",
                                               defaultValue: "https://waterdata.example/gauge.json")],
                      options: [VeinOptionGroup(group: "Thresholds", fields: [
                          VeinField(id: "flood_stage", label: "Flood stage (ft)", type: "number",
                                    choices: [], hint: "", value: "21.5", isOn: false),
                          VeinField(id: "rate_alert", label: "Rapid-rise alert", type: "bool",
                                    choices: [], hint: "", value: "true", isOn: true),
                      ])],
                      jobs: [VeinJob(id: "vein_rivergauge", label: "River gauge run",
                                     cron: "*/30 * * * *", enabled: true, gated: nil)]),
            VeinEntry(kind: "geopolitics", label: "Geopolitics", icon: "globe",
                      blurb: "watches for developments that clear a pre-declared bar",
                      nominalLabel: "quiet", enabled: false, canEnable: true, requires: [],
                      providers: [],
                      options: [VeinOptionGroup(group: "Orientation", fields: [
                          VeinField(id: "bar", label: "What clears the bar", type: "text",
                                    choices: [], hint: "completes \"would plausibly affect …\"",
                                    value: "", isOn: false),
                          VeinField(id: "quiet_ok", label: "Quiet days post nothing", type: "bool",
                                    choices: [], hint: "", value: "true", isOn: true),
                      ])],
                      jobs: [VeinJob(id: "vein_geopolitics", label: "Geopolitics run",
                                     cron: "0 */6 * * *", enabled: false,
                                     gated: "the Geopolitics vein is off. Enable it in Veins.")]),
        ]
    }

    static func browseMock() -> [VeinEntry] {
        [
            VeinEntry(kind: "pantry", label: "Pantry", icon: "basket",
                      blurb: "a weekly restock digest from your inventory",
                      nominalLabel: "stocked", enabled: false, canEnable: false, requires: [],
                      providers: [], options: [],
                      jobs: [], exposed: false,
                      requiresUnmet: [VeinRequirement(label: "Grocy", met: false, detail: "",
                                                      integration: "grocy")]),
        ]
    }
}

struct VeinImportWarning: Hashable, Sendable, Identifiable {
    var id: String { "\(type):\(idValue):\(label)" }
    let type: String
    let idValue: String
    let label: String
}

enum VeinImportResult: Sendable {
    case ok(kind: String, warnings: [VeinImportWarning])
    case failure(String)
}

/// Thin client for vera-api's vein endpoints.
struct VeinsClient: Sendable {
    let base: URL

    enum Fetch: Sendable {
        case ok([VeinEntry], active: Int, cap: Int)
        case unsupported(Int)   // vera-api answered, but has no vein catalog (older build)
        case unreachable
    }

    func fetch(all: Bool = false) async -> Fetch {
        var comps = URLComponents(url: base.appendingPathComponent("/pulse/veins/catalog"),
                                  resolvingAgainstBaseURL: false)
        if all { comps?.queryItems = [URLQueryItem(name: "all", value: "true")] }
        guard let url = comps?.url else { return .unreachable }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return .unreachable }
        guard (200..<300).contains(code) else { return .unsupported(code) }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = json["veins"] as? [[String: Any]] else { return .unsupported(code) }
        return .ok(arr.compactMap { VeinEntry.parse($0) },
                   active: (json["active"] as? Int) ?? 0,
                   cap: (json["cap"] as? Int) ?? 6)
    }

    /// PUT enabled / option values / provider config / cron. Option values travel
    /// stringified — the server coerces each by its declared type. Returns the server's
    /// error detail on refusal (cap, unmet requirement), nil on success.
    func save(kind: String, enabled: Bool? = nil, options: [String: String]? = nil,
              providers: [String: String]? = nil, cron: String? = nil) async -> String? {
        var body: [String: Any] = [:]
        if let enabled { body["enabled"] = enabled }
        if let options { body["options"] = options }
        if let providers { body["providers"] = providers }
        if let cron { body["cron"] = cron }
        var req = URLRequest(url: base.appendingPathComponent("/pulse/veins/\(kind)"))
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

    func delete(kind: String) async -> String? {
        var req = URLRequest(url: base.appendingPathComponent("/pulse/veins/\(kind)"))
        req.httpMethod = "DELETE"
        req.timeoutInterval = 10
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return "vera-api unreachable" }
        if (200..<300).contains(code) { return nil }
        let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (json?["detail"] as? String) ?? "HTTP \(code)"
    }

    /// Exercise the vein's provider slots; per-slot results, nothing persisted.
    func test(kind: String) async -> [(slot: String, ok: Bool, detail: String)] {
        var req = URLRequest(url: base.appendingPathComponent("/pulse/veins/\(kind)/test"))
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (200..<300).contains((resp as? HTTPURLResponse)?.statusCode ?? 0),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = json["results"] as? [[String: Any]] else {
            return [("test", false, "vera-api unreachable")]
        }
        return arr.map { (($0["slot"] as? String) ?? "slot",
                          ($0["ok"] as? Bool) ?? false,
                          ($0["detail"] as? String) ?? "") }
    }

    func export(kind: String) async -> Data? {
        var req = URLRequest(url: base.appendingPathComponent("/pulse/veins/\(kind)/export"))
        req.timeoutInterval = 10
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (200..<300).contains((resp as? HTTPURLResponse)?.statusCode ?? 0) else { return nil }
        return data
    }

    func importVein(_ fileBody: Data) async -> VeinImportResult {
        var req = URLRequest(url: base.appendingPathComponent("/pulse/veins/import"))
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = fileBody
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return .failure("vera-api unreachable") }
        let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        if (200..<300).contains(code) {
            let warnings = ((json?["warnings"] as? [[String: Any]]) ?? []).map {
                VeinImportWarning(type: ($0["type"] as? String) ?? "",
                                  idValue: ($0["id"] as? String) ?? "",
                                  label: ($0["label"] as? String) ?? "")
            }
            return .ok(kind: (json?["kind"] as? String) ?? "", warnings: warnings)
        }
        return .failure((json?["detail"] as? String) ?? "HTTP \(code)")
    }
}
