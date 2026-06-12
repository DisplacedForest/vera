import SwiftUI

/// Which Agentic pane the sidebar has selected.
enum AgenticPane: String {
    case canvas, activity
}

/// One drill-in stage of a flow (a pipeline step or a heartbeat branch), as declared
/// by the server's graph manifest.
struct GraphStage: Identifiable, Sendable, Hashable {
    let id: String
    var label: String
    var icon: String
    var tint: String
    var feeds: [String]
}

/// The distilled last pulse run, for per-stage state on the pulse drill-in.
struct PulseStageState: Sendable, Hashable {
    var state: String           // ok | error | running | stale
    var rounds: Int
    var proposed: Int
    var gates: [String: Int]    // gate id -> kill count
    var injected: Int
    var warnings: [String]
    var finishedAt: Date?

    var gateKills: Int { gates.values.reduce(0, +) }
}

/// The latest outcome one heartbeat branch produced.
struct BranchOutcome: Sendable, Hashable {
    var kind: String
    var detail: String
    var ts: Date
}

/// One autonomous flow on the canvas: a scheduler job or the heartbeat. Topology and
/// presentation come from the manifest; live run state is merged from /scheduler/jobs.
struct GraphFlow: Identifiable, Sendable, Hashable {
    let id: String
    var label: String           // canvas label ("Pulse briefing")
    var title: String           // formal name ("Pulse briefing run")
    var kind: String            // job | heartbeat
    var icon: String
    var tint: String
    var group: String
    var feeds: [String]
    var tools: [String]
    var running: Bool
    var stageLayout: String?    // pipeline | fan
    var stages: [GraphStage]
    var pulseState: PulseStageState?
    var branchState: [String: BranchOutcome]
}

/// One surface autonomous work lands on (Pulse feed, Veins, Memory, Actions).
struct GraphSurface: Identifiable, Sendable, Hashable {
    let id: String
    var label: String
    var icon: String
    var stat: String?           // live one-phrase stat; nil when the server can't answer
}

/// The whole canvas manifest from `GET /agentic/graph`.
struct AgenticGraph: Sendable, Hashable {
    var flows: [GraphFlow]
    var surfaces: [GraphSurface]

    func flow(_ id: String) -> GraphFlow? { flows.first { $0.id == id } }

    /// Tolerant decode — unknown fields degrade to blanks, never fake data.
    static func parse(_ json: Any) -> AgenticGraph? {
        guard let obj = json as? [String: Any],
              let flowArr = obj["flows"] as? [[String: Any]],
              let surfArr = obj["surfaces"] as? [[String: Any]] else { return nil }
        let flows: [GraphFlow] = flowArr.compactMap { f in
            guard let id = f["id"] as? String else { return nil }
            let stages: [GraphStage] = ((f["stages"] as? [[String: Any]]) ?? []).compactMap { s in
                guard let sid = s["id"] as? String else { return nil }
                return GraphStage(id: sid,
                                  label: (s["label"] as? String) ?? sid,
                                  icon: (s["icon"] as? String) ?? "circle",
                                  tint: (s["tint"] as? String) ?? "gray",
                                  feeds: (s["feeds"] as? [String]) ?? [])
            }
            var pulseState: PulseStageState?
            if let st = f["stage_state"] as? [String: Any], let state = st["state"] as? String {
                var gates: [String: Int] = [:]
                for (k, v) in (st["gates"] as? [String: Any]) ?? [:] {
                    if let n = v as? Int { gates[k] = n }
                }
                pulseState = PulseStageState(
                    state: state,
                    rounds: (st["rounds"] as? Int) ?? 0,
                    proposed: (st["proposed"] as? Int) ?? 0,
                    gates: gates,
                    injected: (st["injected"] as? Int) ?? 0,
                    warnings: (st["warnings"] as? [String]) ?? [],
                    finishedAt: schedulerDate(st["finished_at"]))
            }
            var branchState: [String: BranchOutcome] = [:]
            for (branch, v) in (f["branch_state"] as? [String: Any]) ?? [:] {
                guard let o = v as? [String: Any], let ts = schedulerDate(o["ts"]) else { continue }
                branchState[branch] = BranchOutcome(kind: (o["kind"] as? String) ?? branch,
                                                    detail: (o["detail"] as? String) ?? "",
                                                    ts: ts)
            }
            return GraphFlow(
                id: id,
                label: (f["label"] as? String) ?? id,
                title: (f["title"] as? String) ?? (f["label"] as? String) ?? id,
                kind: (f["kind"] as? String) ?? "job",
                icon: (f["icon"] as? String) ?? "clock",
                tint: (f["tint"] as? String) ?? "gray",
                group: (f["group"] as? String) ?? "Other",
                feeds: (f["feeds"] as? [String]) ?? [],
                tools: (f["tools"] as? [String]) ?? [],
                running: (f["running"] as? Bool) ?? false,
                stageLayout: f["stage_layout"] as? String,
                stages: stages,
                pulseState: pulseState,
                branchState: branchState)
        }
        let surfaces: [GraphSurface] = surfArr.compactMap { s in
            guard let id = s["id"] as? String else { return nil }
            return GraphSurface(id: id,
                                label: (s["label"] as? String) ?? id,
                                icon: (s["icon"] as? String) ?? "square.grid.2x2",
                                stat: s["stat"] as? String)
        }
        guard !flows.isEmpty, !surfaces.isEmpty else { return nil }
        return AgenticGraph(flows: flows, surfaces: surfaces)
    }

    /// Demo topology for screenshots — mirrors the live manifest shape.
    static func mock() -> AgenticGraph {
        func stage(_ id: String, _ label: String, _ icon: String, _ tint: String,
                   feeds: [String] = []) -> GraphStage {
            GraphStage(id: id, label: label, icon: icon, tint: tint, feeds: feeds)
        }
        func flow(_ id: String, _ label: String, _ icon: String, _ tint: String, _ group: String,
                  feeds: [String], tools: [String] = [], kind: String = "job",
                  running: Bool = false, layout: String? = nil,
                  stages: [GraphStage] = []) -> GraphFlow {
            GraphFlow(id: id, label: label, title: label, kind: kind, icon: icon, tint: tint,
                      group: group, feeds: feeds, tools: tools, running: running,
                      stageLayout: layout, stages: stages, pulseState: nil, branchState: [:])
        }
        var pulse = flow("pulse", "Pulse briefing", "newspaper", "accent", "Ambient",
                         feeds: ["pulse_feed"], tools: ["websearch", "vera-image"],
                         layout: "pipeline", stages: [
                            stage("triage", "Triage", "globe", "accent"),
                            stage("gates", "Gates", "line.3.horizontal.decrease.circle", "orange"),
                            stage("synthesis", "Synthesis", "sparkles", "purple"),
                            stage("claim_audit", "Claim audit", "checkmark.shield", "cyan"),
                            stage("cover_art", "Cover art", "photo", "purple"),
                            stage("inject", "Inject", "arrow.down.to.line", "green")])
        pulse.pulseState = PulseStageState(state: "ok", rounds: 3, proposed: 24,
                                           gates: ["dedup": 6, "freshness": 1, "coherence": 1, "interest_cap": 1],
                                           injected: 6,
                                           warnings: ["starved run: 6/8 cards after 3 triage round(s)"],
                                           finishedAt: Date().addingTimeInterval(-7 * 3600))
        var heartbeat = flow("heartbeat", "Heartbeat", "heart", "accent", "Heartbeat",
                             feeds: ["pulse_feed", "veins", "memory", "actions"],
                             tools: ["websearch"], kind: "heartbeat", running: true,
                             layout: "fan", stages: [
                                stage("learn", "Learn", "sparkles", "accent", feeds: ["memory"]),
                                stage("refine", "Refine", "doc.text", "purple"),
                                stage("propose", "Propose", "bolt", "orange", feeds: ["actions"]),
                                stage("watch", "Watches", "waveform.path.ecg", "cyan", feeds: ["veins"]),
                                stage("foryou", "For you", "heart", "red", feeds: ["pulse_feed"])])
        heartbeat.branchState = [
            "learn": BranchOutcome(kind: "learn", detail: "Passive cooling and thermal mass",
                                   ts: Date().addingTimeInterval(-300)),
            "propose": BranchOutcome(kind: "confirmed", detail: "ha.service:climate.office",
                                     ts: Date().addingTimeInterval(-3 * 86400)),
            "foryou": BranchOutcome(kind: "foryou", detail: "shared a find",
                                    ts: Date().addingTimeInterval(-10800)),
        ]
        return AgenticGraph(
            flows: [
                pulse,
                flow("weather", "Weather check", "cloud.sun", "cyan", "Ambient", feeds: ["veins"]),
                flow("signals", "Signals check", "antenna.radiowaves.left.and.right", "orange",
                     "Ambient", feeds: ["veins"], tools: ["websearch"]),
                flow("memory_groom", "Memory groom", "archivebox", "purple", "Memory", feeds: ["memory"]),
                flow("home_model", "Home model", "house", "cyan", "Home", feeds: ["actions"]),
                flow("home_reconcile", "Map reconcile", "checklist", "cyan", "Home", feeds: ["veins"]),
                flow("home_digest", "Rhythm digest", "doc.text", "cyan", "Home", feeds: ["veins"]),
                heartbeat,
                flow("healthcheck", "Health probe", "waveform.path.ecg", "green", "System", feeds: ["veins"]),
                flow("updates", "Update check", "arrow.down.circle", "gray", "System", feeds: ["veins"]),
                flow("media_curate", "Media curate", "film", "red", "Media", feeds: ["veins"],
                     tools: ["overseerr"]),
            ],
            surfaces: [
                GraphSurface(id: "pulse_feed", label: "Pulse feed", icon: "newspaper", stat: "8 cards today"),
                GraphSurface(id: "veins", label: "Veins", icon: "drop", stat: "3 active cards"),
                GraphSurface(id: "memory", label: "Memory", icon: "archivebox", stat: "12 core facts"),
                GraphSurface(id: "actions", label: "Actions", icon: "bolt", stat: "2 pending proposals"),
            ])
    }
}

/// Map a manifest tint name onto the app's chart palette. Unknown names read neutral.
func graphTint(_ name: String) -> Color {
    switch name {
    case "accent": return Theme.accent
    case "cyan": return Color(red: 0.40, green: 0.78, blue: 0.78)
    case "orange": return Color(red: 0.90, green: 0.62, blue: 0.30)
    case "purple": return Color(red: 0.72, green: 0.55, blue: 0.90)
    case "green": return Color(red: 0.36, green: 0.78, blue: 0.50)
    case "red": return Color(red: 0.85, green: 0.45, blue: 0.50)
    default: return Theme.textSecondary
    }
}

/// Short human name for a tool id, for badges and chips.
func toolDisplayName(_ tool: String) -> String {
    switch tool {
    case "websearch": return "Web search"
    case "vera-image": return "Image generation"
    case "overseerr": return "Media requests"
    default: return tool
    }
}

/// SF Symbol for a tool badge.
func toolGlyph(_ tool: String) -> String {
    switch tool {
    case "websearch": return "globe"
    case "vera-image": return "photo"
    case "overseerr": return "film"
    default: return "wrench.adjustable"
    }
}

/// Thin client for vera-api's canvas graph endpoint.
struct GraphClient: Sendable {
    let base: URL

    enum Fetch: Sendable {
        case ok(AgenticGraph)
        case unsupported(Int)   // vera-api answered, but has no graph manifest (older build)
        case unreachable
    }

    func fetch() async -> Fetch {
        var req = URLRequest(url: base.appendingPathComponent("/agentic/graph"))
        req.timeoutInterval = 8
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode else { return .unreachable }
        guard (200..<300).contains(code) else { return .unsupported(code) }
        guard let json = try? JSONSerialization.jsonObject(with: data),
              let graph = AgenticGraph.parse(json) else { return .unsupported(code) }
        return .ok(graph)
    }
}
