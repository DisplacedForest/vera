import Foundation

/// One candidate's structured outcome from a Pulse run, parsed from `GET /pulse/run_status`'s
/// `items`. It answers "why did this candidate ship or not" with the gate-specific evidence
/// the run captured (the dedup match target, the freshness date, the off-topic corpus subject,
/// the interest-cap collision).
struct PulseRunItem: Identifiable, Sendable, Hashable {
    let id: Int                 // position in the run; items have no server id of their own
    var round: Int
    var title: String
    var angle: String?
    var interest: String?
    var status: String          // injected | killed | cap | error
    var gate: String?           // dedup | freshness | coherence | empty | interest_cap
    var reason: String?
    var detail: String?         // matching card title / newest date / corpus subject / interest
    var cardID: String?
    var coverGenerated: Bool?
    var auditVerdict: String?   // clean | revised | unavailable
    var auditUnsupported: Int?
    var auditor: String?

    var injected: Bool { status == "injected" }
}

/// The structured last-run detail behind the pulse drill-in. Summary faces still come from the
/// lean graph manifest; this is fetched only when a flow with stages is opened.
struct PulseRunDetail: Sendable, Hashable {
    var state: String
    var items: [PulseRunItem]

    /// Items killed at a given gate, in the run's order — the named casualties behind a count.
    func killed(at gate: String) -> [PulseRunItem] {
        items.filter { ($0.status == "killed" || $0.status == "cap") && $0.gate == gate }
    }
    var injectedItems: [PulseRunItem] { items.filter(\.injected) }
}

/// Thin client for the pulse run-status endpoint (the drill-in's per-item evidence).
struct PulseRunClient: Sendable {
    let base: URL

    enum Fetch: Sendable {
        case ok(PulseRunDetail)
        case unsupported       // reachable, but no structured items (older build or no run yet)
        case unreachable
    }

    func fetch() async -> Fetch {
        var req = URLRequest(url: base.appendingPathComponent("/pulse/run_status"))
        req.timeoutInterval = 8
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let code = (resp as? HTTPURLResponse)?.statusCode,
              (200..<300).contains(code) else { return .unreachable }
        guard let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return .unsupported
        }
        let state = (obj["state"] as? String) ?? "idle"
        guard let raw = obj["items"] as? [[String: Any]] else { return .unsupported }
        let items: [PulseRunItem] = raw.enumerated().map { i, e in
            let audit = e["audit"] as? [String: Any]
            return PulseRunItem(
                id: i,
                round: (e["round"] as? Int) ?? 0,
                title: (e["title"] as? String) ?? "untitled",
                angle: e["angle"] as? String,
                interest: e["interest"] as? String,
                status: (e["status"] as? String) ?? "",
                gate: e["gate"] as? String,
                reason: e["reason"] as? String,
                detail: e["detail"] as? String,
                cardID: e["card_id"] as? String,
                coverGenerated: e["cover_generated"] as? Bool,
                auditVerdict: audit?["verdict"] as? String,
                auditUnsupported: audit?["unsupported"] as? Int,
                auditor: audit?["auditor"] as? String)
        }
        return .ok(PulseRunDetail(state: state, items: items))
    }

    static func mock() -> PulseRunDetail {
        PulseRunDetail(state: "ok", items: [
            PulseRunItem(id: 0, round: 1, title: "Forest hold on for a top-four finish", angle: "season climax",
                         interest: "Nottingham Forest", status: "injected", gate: nil, reason: nil, detail: nil,
                         cardID: "card-1", coverGenerated: true, auditVerdict: "clean", auditUnsupported: 0, auditor: "coder"),
            PulseRunItem(id: 1, round: 1, title: "New tannin research for cool-climate reds", angle: "winemaking",
                         interest: "winemaking", status: "injected", gate: nil, reason: nil, detail: nil,
                         cardID: "card-2", coverGenerated: false, auditVerdict: "revised", auditUnsupported: 2, auditor: "coder"),
            PulseRunItem(id: 2, round: 1, title: "Forest sign a new striker", angle: "transfer", interest: "Nottingham Forest",
                         status: "killed", gate: "dedup", reason: "already covered",
                         detail: "Forest close in on a deadline-day forward", cardID: nil, coverGenerated: nil,
                         auditVerdict: nil, auditUnsupported: nil, auditor: nil),
            PulseRunItem(id: 3, round: 2, title: "Last season's promotion run", angle: "history", interest: nil,
                         status: "killed", gate: "freshness", reason: "stale news", detail: "2025-05-19",
                         cardID: nil, coverGenerated: nil, auditVerdict: nil, auditUnsupported: nil, auditor: nil),
            PulseRunItem(id: 4, round: 2, title: "Midfield tactics deep dive", angle: "analysis", interest: nil,
                         status: "killed", gate: "coherence", reason: "off-topic corpus",
                         detail: "Bundesliga reserve fixtures", cardID: nil, coverGenerated: nil,
                         auditVerdict: nil, auditUnsupported: nil, auditor: nil),
            PulseRunItem(id: 5, round: 2, title: "A third Forest story", angle: "extra", interest: "Nottingham Forest",
                         status: "cap", gate: "interest_cap", reason: "interest cap", detail: "Nottingham Forest",
                         cardID: nil, coverGenerated: nil, auditVerdict: nil, auditUnsupported: nil, auditor: nil),
        ])
    }
}

/// Plain-English glossary for the run vocabulary surfaced on the canvas, so a jargon term is
/// understandable in place (shown as a tooltip / expanded note, never bare).
enum PulseVocabulary {
    static func explain(_ term: String) -> String {
        switch term {
        case "starved run":
            return "The run wanted a full set of cards but only a few survived the quality gates after every search round."
        case "gate kills":
            return "Candidates the quality gates removed before they could become cards."
        case "dedup":
            return "Already covered: a card on this story already exists, so a near-duplicate was skipped."
        case "freshness":
            return "Stale news: the newest source was too old for a current briefing."
        case "coherence":
            return "Off-topic corpus: the search drifted to a different subject, so the candidate was dropped."
        case "empty":
            return "Empty synthesis: research produced nothing worth a card."
        case "interest_cap", "interest cap":
            return "Interest cap: this interest already shipped its allowed card this run, so the extra was held back."
        default:
            return term
        }
    }
}
