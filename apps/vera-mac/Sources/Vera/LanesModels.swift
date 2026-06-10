import Foundation

/// One scoping field inside a lane's option group, declared by the server manifest.
/// The app renders by `type` and hardcodes no lane knowledge — unknown fields render
/// generically.
struct LaneField: Identifiable, Hashable, Sendable {
    let id: String
    var label: String
    var type: String          // bool | text | number | choice
    var choices: [String]
    var hint: String
    var value: String         // effective value, stringified ("" when unset)
    var isOn: Bool            // bool fields
}

struct LaneOptionGroup: Identifiable, Hashable, Sendable {
    var id: String { group }
    let group: String
    var fields: [LaneField]
}

/// A declared endpoint contract — point the lane at any compatible service.
struct LaneProvider: Identifiable, Hashable, Sendable {
    let id: String
    var label: String
    var hint: String
    var value: String
    var defaultValue: String
}

struct LaneRequirement: Hashable, Sendable {
    var label: String
    var met: Bool
    var detail: String
    var integration: String?   // when set, the requirement lives in the Plugins surface
}

struct LaneJob: Identifiable, Hashable, Sendable {
    let id: String
    var label: String
    var cron: String
    var enabled: Bool
    var gated: String?
}

/// One lane from `GET /pulse/lanes/catalog` — manifest merged with runtime state.
struct LaneEntry: Identifiable, Hashable, Sendable {
    var id: String { kind }
    let kind: String
    var label: String
    var icon: String
    var blurb: String
    var nominalLabel: String
    var enabled: Bool
    var canEnable: Bool
    var requires: [LaneRequirement]
    var providers: [LaneProvider]
    var options: [LaneOptionGroup]
    var jobs: [LaneJob]

    /// Tolerant decode of one catalog entry. Unknown fields degrade to blanks, never fake data.
    static func parse(_ j: [String: Any]) -> LaneEntry? {
        guard let kind = j["kind"] as? String else { return nil }
        func str(_ v: Any?) -> String {
            if let s = v as? String { return s }
            if let b = v as? Bool { return b ? "true" : "false" }
            if let n = v as? NSNumber { return n.doubleValue == n.doubleValue.rounded() && abs(n.doubleValue) < 1e12 ? String(format: "%g", n.doubleValue) : "\(n)" }
            return ""
        }
        let requires: [LaneRequirement] = (j["requires"] as? [[String: Any]] ?? []).map { r in
            LaneRequirement(label: (r["label"] as? String) ?? "",
                            met: (r["met"] as? Bool) ?? false,
                            detail: (r["detail"] as? String) ?? "",
                            integration: r["integration"] as? String)
        }
        let providers: [LaneProvider] = (j["providers"] as? [[String: Any]] ?? []).compactMap { p in
            guard let pid = p["id"] as? String else { return nil }
            return LaneProvider(id: pid, label: (p["label"] as? String) ?? pid,
                                hint: (p["hint"] as? String) ?? "",
                                value: str(p["value"]),
                                defaultValue: str(p["default"]))
        }
        let options: [LaneOptionGroup] = (j["options"] as? [[String: Any]] ?? []).compactMap { g in
            guard let name = g["group"] as? String else { return nil }
            let fields: [LaneField] = (g["fields"] as? [[String: Any]] ?? []).compactMap { f in
                guard let fid = f["id"] as? String else { return nil }
                return LaneField(id: fid, label: (f["label"] as? String) ?? fid,
                                 type: (f["type"] as? String) ?? "text",
                                 choices: (f["choices"] as? [String]) ?? [],
                                 hint: (f["hint"] as? String) ?? "",
                                 value: f["value"] is NSNull ? "" : str(f["value"]),
                                 isOn: (f["value"] as? Bool) ?? false)
            }
            return LaneOptionGroup(group: name, fields: fields)
        }
        let jobs: [LaneJob] = (j["jobs"] as? [[String: Any]] ?? []).compactMap { jb in
            guard let jid = jb["id"] as? String else { return nil }
            return LaneJob(id: jid, label: (jb["label"] as? String) ?? jid,
                           cron: (jb["cron"] as? String) ?? "",
                           enabled: (jb["enabled"] as? Bool) ?? false,
                           gated: jb["gated"] as? String)
        }
        return LaneEntry(kind: kind,
                         label: (j["label"] as? String) ?? kind,
                         icon: (j["icon"] as? String) ?? "rectangle.dashed",
                         blurb: (j["blurb"] as? String) ?? "",
                         nominalLabel: (j["nominal_label"] as? String) ?? "quiet",
                         enabled: (j["enabled"] as? Bool) ?? false,
                         canEnable: (j["can_enable"] as? Bool) ?? false,
                         requires: requires, providers: providers, options: options, jobs: jobs)
    }

    /// Demo entries for headless `--shot` renders (mixed states; mirrors the API shape).
    static func mock() -> [LaneEntry] {
        [
            LaneEntry(kind: "status", label: "System", icon: "gearshape",
                      blurb: "stack health and pending updates across your monitored sources",
                      nominalLabel: "nominal", enabled: true, canEnable: true, requires: [],
                      providers: [],
                      options: [LaneOptionGroup(group: "Monitored sources", fields: [
                          LaneField(id: "src_containers", label: "Containers", type: "bool",
                                    choices: [], hint: "", value: "true", isOn: true),
                          LaneField(id: "src_home_assistant", label: "Home Assistant + HACS", type: "bool",
                                    choices: [], hint: "", value: "true", isOn: true),
                          LaneField(id: "src_network", label: "Network gear", type: "bool",
                                    choices: [], hint: "", value: "false", isOn: false),
                      ])],
                      jobs: [LaneJob(id: "updates", label: "Stack updates check",
                                     cron: "30 7 * * *", enabled: true, gated: nil)]),
            LaneEntry(kind: "weather", label: "Weather", icon: "cloud.sun",
                      blurb: "severe-weather pre-warnings for the home's coordinates",
                      nominalLabel: "clear", enabled: true, canEnable: true,
                      requires: [LaneRequirement(label: "home coordinates", met: true,
                                                 detail: "", integration: nil)],
                      providers: [LaneProvider(id: "forecast_url", label: "Forecast endpoint",
                                               hint: "any Open-Meteo-compatible forecast API",
                                               value: "https://api.open-meteo.com/v1/forecast",
                                               defaultValue: "https://api.open-meteo.com/v1/forecast")],
                      options: [LaneOptionGroup(group: "Units & thresholds", fields: [
                          LaneField(id: "unit", label: "Temperature unit", type: "choice",
                                    choices: ["fahrenheit", "celsius"], hint: "",
                                    value: "fahrenheit", isOn: false),
                          LaneField(id: "gust_threshold", label: "Wind gust alert (mph)", type: "number",
                                    choices: [], hint: "", value: "45", isOn: false),
                      ])],
                      jobs: [LaneJob(id: "weather", label: "Weather check",
                                     cron: "0 */6 * * *", enabled: true, gated: nil)]),
            LaneEntry(kind: "signals", label: "Signals", icon: "antenna.radiowaves.left.and.right",
                      blurb: "an external-watch monitor — only what crosses pre-declared thresholds",
                      nominalLabel: "quiet", enabled: false, canEnable: true, requires: [],
                      providers: [],
                      options: [LaneOptionGroup(group: "Source groups", fields: [
                          LaneField(id: "grp_financial", label: "Financial stress", type: "bool",
                                    choices: [], hint: "Treasury yields, VIX, credit spreads",
                                    value: "true", isOn: true),
                          LaneField(id: "grp_geophysical", label: "Geophysical", type: "bool",
                                    choices: [], hint: "earthquakes, disaster alerts",
                                    value: "true", isOn: true),
                          LaneField(id: "grp_news", label: "News judge", type: "bool",
                                    choices: [], hint: "", value: "true", isOn: true),
                      ])],
                      jobs: [LaneJob(id: "signals", label: "Signals check",
                                     cron: "0 6,18 * * *", enabled: false,
                                     gated: "the Signals lane is off — enable it in Lanes")]),
            LaneEntry(kind: "media", label: "Media", icon: "film",
                      blurb: "a weekly worth-adding digest for your media library",
                      nominalLabel: "quiet", enabled: false, canEnable: false,
                      requires: [LaneRequirement(label: "Overseerr", met: false,
                                                 detail: "connect Overseerr in Plugins",
                                                 integration: "overseerr")],
                      providers: [], options: [],
                      jobs: [LaneJob(id: "media_curate", label: "Media curation digest",
                                     cron: "0 9 * * 0", enabled: false,
                                     gated: "the Media lane is off — enable it in Lanes")]),
        ]
    }
}

/// Thin client for vera-api's lane endpoints.
struct LanesClient: Sendable {
    let base: URL

    enum Fetch: Sendable {
        case ok([LaneEntry], active: Int, cap: Int)
        case unsupported(Int)   // vera-api answered, but has no lane catalog (older build)
        case unreachable
    }

    func fetch() async -> Fetch {
        var req = URLRequest(url: base.appendingPathComponent("/pulse/lanes/catalog"))
        req.timeoutInterval = 8
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return .unreachable }
        guard (200..<300).contains(code) else { return .unsupported(code) }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = json["lanes"] as? [[String: Any]] else { return .unsupported(code) }
        return .ok(arr.compactMap { LaneEntry.parse($0) },
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
        var req = URLRequest(url: base.appendingPathComponent("/pulse/lanes/\(kind)"))
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

    /// Exercise the lane's provider slots; per-slot results, nothing persisted.
    func test(kind: String) async -> [(slot: String, ok: Bool, detail: String)] {
        var req = URLRequest(url: base.appendingPathComponent("/pulse/lanes/\(kind)/test"))
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
}
