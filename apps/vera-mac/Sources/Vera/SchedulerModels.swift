import Foundation

/// One autonomous job from vera-api's built-in scheduler (`GET /scheduler/jobs`).
struct SchedulerJob: Identifiable, Sendable, Hashable {
    let id: String
    var label: String
    var cron: String
    var enabled: Bool
    var envLocked: Bool          // forced off/on by the server's environment — not editable here
    var gated: String?           // off because an integration/feature gate is closed (the reason)
    var lastRunAt: Date?
    var lastRunOK: Bool?
    var lastRunDetail: String
    var nextRun: Date?

    static func mock() -> [SchedulerJob] {
        let now = Date()
        func job(_ id: String, _ label: String, _ cron: String, ok: Bool? = true,
                 detail: String = "completed", enabled: Bool = true, locked: Bool = false,
                 lastAgo: TimeInterval = 3600, nextIn: TimeInterval = 7200) -> SchedulerJob {
            SchedulerJob(id: id, label: label, cron: cron, enabled: enabled, envLocked: locked,
                         gated: nil,
                         lastRunAt: ok == nil ? nil : now.addingTimeInterval(-lastAgo), lastRunOK: ok,
                         lastRunDetail: ok == nil ? "" : detail,
                         nextRun: enabled ? now.addingTimeInterval(nextIn) : nil)
        }
        return [
            job("pulse", "Pulse briefing", "0 5 * * *", detail: "6 cards", lastAgo: 7 * 3600, nextIn: 17 * 3600),
            job("heartbeat", "Heartbeat tick", "*/20 * * * *", detail: "nominal", lastAgo: 600, nextIn: 900),
            job("vein_rivergauge", "River gauge run", "*/30 * * * *", detail: "no change", lastAgo: 300, nextIn: 1800),
            job("vein_geopolitics", "Geopolitics run", "0 */6 * * *", ok: false,
                detail: "feed timeout after 30s", lastAgo: 3 * 3600, nextIn: 9 * 3600),
            job("memory_groom", "Memory groom", "0 4 * * *", detail: "merged 2, promoted 1",
                lastAgo: 8 * 3600, nextIn: 16 * 3600),
            job("home_model", "Home model refresh", "30 3 * * *", detail: "295 patterns",
                lastAgo: 9 * 3600, nextIn: 15 * 3600),
            job("home_reconcile", "Home map reconcile", "0 3 * * *", detail: "7 of 7 ok",
                lastAgo: 9 * 3600, nextIn: 14 * 3600),
            job("home_digest", "Home rhythm digest", "0 2 * * *", detail: "quiet day",
                lastAgo: 10 * 3600, nextIn: 14 * 3600),
            job("healthcheck", "Health probe", "*/15 * * * *", detail: "all services up",
                lastAgo: 300, nextIn: 600),
            job("updates", "Stack updates check", "30 7 * * *", detail: "nothing pending",
                lastAgo: 5 * 3600, nextIn: 19 * 3600),
            job("media_curate", "Media curation", "0 9 * * 0", ok: nil, enabled: false, locked: true),
        ]
    }
}

/// The scheduler's full reported state: the master switch plus every job.
struct SchedulerState: Sendable {
    var masterEnabled: Bool
    var jobs: [SchedulerJob]

    /// Tolerant decode of the `GET /scheduler/jobs` payload — either `{enabled, jobs: [...]}`
    /// or a bare job array. Unknown/missing fields degrade to sensible blanks, never fake data.
    static func parse(_ json: Any) -> SchedulerState? {
        let obj = json as? [String: Any]
        guard let arr = (obj?["jobs"] as? [[String: Any]]) ?? (json as? [[String: Any]]) else { return nil }
        let master = (obj?["scheduler_enabled"] as? Bool) ?? (obj?["enabled"] as? Bool) ?? true
        let jobs: [SchedulerJob] = arr.compactMap { j in
            guard let id = (j["id"] as? String) ?? (j["name"] as? String) else { return nil }
            let last = j["last_run"] as? [String: Any]
            return SchedulerJob(
                id: id,
                label: (j["label"] as? String) ?? id,
                cron: (j["cron"] as? String) ?? (j["schedule"] as? String) ?? "",
                enabled: (j["enabled"] as? Bool) ?? true,
                envLocked: (j["env_locked"] as? Bool) ?? (j["locked"] as? Bool) ?? false,
                gated: j["gated"] as? String,
                lastRunAt: schedulerDate(last?["ts"]),
                lastRunOK: last?["ok"] as? Bool,
                lastRunDetail: (last?["detail"] as? String) ?? "",
                nextRun: schedulerDate(j["next_run"]))
        }
        return SchedulerState(masterEnabled: master, jobs: jobs)
    }
}

/// Decode a scheduler timestamp — epoch seconds (number or string) or an ISO-8601 string,
/// with or without a timezone suffix.
func schedulerDate(_ v: Any?) -> Date? {
    if let n = v as? Double { return Date(timeIntervalSince1970: n) }
    if let n = v as? Int { return Date(timeIntervalSince1970: Double(n)) }
    guard let s = v as? String, !s.isEmpty else { return nil }
    if let n = Double(s), n > 1_000_000 { return Date(timeIntervalSince1970: n) }
    let iso = ISO8601DateFormatter()
    if let d = iso.date(from: s) { return d }
    iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = iso.date(from: s) { return d }
    let naive = DateFormatter()
    naive.locale = Locale(identifier: "en_US_POSIX")
    for fmt in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss"] {
        naive.dateFormat = fmt
        if let d = naive.date(from: s) { return d }
    }
    return nil
}

/// Render a 5-field cron expression as a short human phrase ("Daily 5:00 AM", "Every 20 min").
/// Anything it can't summarize reads "Custom schedule" — plain English everywhere, never a
/// wrong guess; the raw expression renders only inside the schedule editors.
func cronSummary(_ cron: String) -> String {
    let fallback = "Custom schedule"
    let f = cron.split(separator: " ").map(String.init)
    guard f.count == 5 else { return fallback }
    let (m, h, dom, mon, dow) = (f[0], f[1], f[2], f[3], f[4])
    guard dom == "*", mon == "*" else { return fallback }
    func clock(_ hour: Int, _ minute: Int) -> String {
        let ampm = hour < 12 ? "AM" : "PM"
        var h12 = hour % 12
        if h12 == 0 { h12 = 12 }
        return String(format: "%d:%02d %@", h12, minute, ampm)
    }
    if h == "*", m.hasPrefix("*/"), let n = Int(m.dropFirst(2)), dow == "*" {
        return "Every \(n) min"
    }
    guard let minute = Int(m) else { return fallback }
    if h.hasPrefix("*/"), let n = Int(h.dropFirst(2)), dow == "*" {
        return n == 1 ? "Hourly" : "Every \(n) hours"
    }
    let hourParts = h.split(separator: ",")
    let hours = hourParts.compactMap { Int($0) }
    guard !hours.isEmpty, hours.count == hourParts.count else { return fallback }
    let times = hours.map { clock($0, minute) }.joined(separator: " & ")
    if dow == "*" { return "Daily \(times)" }
    if let d = Int(dow), (0...7).contains(d) {
        let days = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"]
        return "\(days[d % 7]) \(times)"
    }
    return fallback
}

/// Thin client for vera-api's scheduler endpoints.
struct SchedulerClient: Sendable {
    let base: URL

    enum Fetch: Sendable {
        case ok(SchedulerState)
        case unsupported(Int)   // vera-api answered, but has no scheduler (e.g. 404 — older build)
        case unreachable
    }

    /// Current scheduler state — distinguishing "service down" from "service too old".
    func fetch() async -> Fetch {
        var req = URLRequest(url: base.appendingPathComponent("/scheduler/jobs"))
        req.timeoutInterval = 8
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return .unreachable }
        guard (200..<300).contains(code) else { return .unsupported(code) }
        guard let json = try? JSONSerialization.jsonObject(with: data),
              let state = SchedulerState.parse(json) else { return .unsupported(code) }
        return .ok(state)
    }

    /// Update a job's cron and/or enabled flag. Returns whether the server accepted it.
    func update(id: String, cron: String? = nil, enabled: Bool? = nil) async -> Bool {
        var fields: [String: Any] = [:]
        if let cron { fields["cron"] = cron }
        if let enabled { fields["enabled"] = enabled }
        var req = URLRequest(url: base.appendingPathComponent("/scheduler/jobs/\(id)"))
        req.httpMethod = "PUT"
        req.timeoutInterval = 8
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: fields)
        guard let (_, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return false }
        return (200..<300).contains(code)
    }

    /// Fire a job now. Returns whether the server accepted the trigger.
    func runNow(id: String) async -> Bool {
        var req = URLRequest(url: base.appendingPathComponent("/scheduler/jobs/\(id)/run"))
        req.httpMethod = "POST"
        req.timeoutInterval = 8
        guard let (_, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return false }
        return (200..<300).contains(code)
    }
}
