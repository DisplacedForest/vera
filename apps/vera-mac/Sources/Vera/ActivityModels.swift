import Foundation

/// One autonomous-activity event from vera-api (`GET /agentic/activity`): something
/// Vera did on her own, normalized across the heartbeat, action, scheduler, and OWUI
/// automation sources.
struct ActivityEvent: Identifiable, Sendable, Hashable {
    let id: String
    var ts: Date
    var source: String      // scheduler | heartbeat | action | owui
    var kind: String
    var title: String
    var detail: String
    var tool: String?
    var ref: String?

    /// SF Symbol per source; unknown sources get a neutral mark.
    var icon: String {
        switch source {
        case "scheduler": return "clock"
        case "heartbeat": return "heart"
        case "action": return "bolt"
        case "owui": return "wand.and.stars"
        default: return "circle.dashed"
        }
    }

    /// Failure-shaped kinds tint the icon; everything else stays neutral.
    var failed: Bool { kind == "fail" || kind == "error" }

    static func mock() -> [ActivityEvent] {
        let now = Date()
        func ev(_ source: String, _ kind: String, _ title: String, _ detail: String,
                ago: TimeInterval, tool: String? = nil) -> ActivityEvent {
            ActivityEvent(id: "\(source)-\(ago)", ts: now.addingTimeInterval(-ago),
                          source: source, kind: kind, title: title, detail: detail,
                          tool: tool, ref: nil)
        }
        return [
            ev("heartbeat", "watch", "Watching a situation", "garage door open after dark", ago: 480),
            ev("scheduler", "ok", "Heartbeat tick", "nominal", ago: 1200, tool: "heartbeat"),
            ev("action", "auto", "media.request", "applied: title=Dune Part Two", ago: 4200, tool: "media.request"),
            ev("scheduler", "fail", "Signals check", "feed timeout after 30s", ago: 7600, tool: "signals"),
            ev("heartbeat", "learn", "Studied the house", "ha.service:climate.office", ago: 9800),
            ev("owui", "success", "WWDC M5 Mac Studio Monitor", "Check for Apple WWDC 2026 news", ago: 14000, tool: "owui.automation"),
        ]
    }
}

/// Thin client for vera-api's agentic activity endpoint.
struct ActivityClient: Sendable {
    let base: URL

    enum Fetch: Sendable {
        case ok([ActivityEvent])
        case unsupported(Int)   // vera-api answered, but has no activity feed (older build)
        case unreachable
    }

    func fetch(hours: Int = 24) async -> Fetch {
        var comps = URLComponents(url: base.appendingPathComponent("/agentic/activity"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "hours", value: String(hours))]
        guard let url = comps?.url else { return .unreachable }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return .unreachable }
        guard (200..<300).contains(code) else { return .unsupported(code) }
        guard let json = try? JSONSerialization.jsonObject(with: data),
              let obj = json as? [String: Any],
              let arr = obj["events"] as? [[String: Any]] else { return .unsupported(code) }
        let events: [ActivityEvent] = arr.enumerated().compactMap { i, e in
            guard let ts = schedulerDate(e["ts"]),
                  let source = e["source"] as? String,
                  let title = e["title"] as? String else { return nil }
            return ActivityEvent(
                id: "\(i)-\(source)-\(ts.timeIntervalSince1970)",
                ts: ts,
                source: source,
                kind: (e["kind"] as? String) ?? "",
                title: title,
                detail: (e["detail"] as? String) ?? "",
                tool: e["tool"] as? String,
                ref: e["ref"] as? String)
        }
        return .ok(events)
    }
}
