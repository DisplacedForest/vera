import Foundation

struct DraftStep: Identifiable {
    let id = UUID()
    var block: String
    var params: [String: Any]
}

struct DraftProvider: Identifiable {
    let id: String
    var label: String
    var hint: String
    var defaultValue: String
}

struct DraftOptionField: Identifiable {
    let id: String
    var label: String
    var type: String
    var choices: [String]
    var hint: String
    var defaultValue: String
}

struct DraftOptionGroup: Identifiable {
    var id: String { group }
    let group: String
    var fields: [DraftOptionField]
}

struct VeinDraft {
    var kind: String
    var label: String
    var icon: String
    var blurb: String
    var nominalLabel: String
    var schedule: String
    var steps: [DraftStep]
    var providers: [DraftProvider]
    var options: [DraftOptionGroup]

    static func parse(_ j: [String: Any]) -> VeinDraft? {
        guard let kind = j["kind"] as? String else { return nil }
        let steps: [DraftStep] = (j["pipeline"] as? [[String: Any]] ?? []).compactMap { s in
            guard let block = s["block"] as? String else { return nil }
            return DraftStep(block: block, params: (s["params"] as? [String: Any]) ?? [:])
        }
        func str(_ v: Any?) -> String {
            if let s = v as? String { return s }
            if let n = v as? NSNumber { return n.doubleValue == n.doubleValue.rounded() ? String(format: "%g", n.doubleValue) : "\(n)" }
            if let b = v as? Bool { return b ? "true" : "false" }
            return ""
        }
        let providers: [DraftProvider] = (j["providers"] as? [[String: Any]] ?? []).compactMap { p in
            guard let pid = p["id"] as? String else { return nil }
            return DraftProvider(id: pid, label: (p["label"] as? String) ?? pid,
                                 hint: (p["hint"] as? String) ?? "",
                                 defaultValue: str(p["default"]))
        }
        let options: [DraftOptionGroup] = (j["options"] as? [[String: Any]] ?? []).compactMap { g in
            guard let name = g["group"] as? String else { return nil }
            let fields: [DraftOptionField] = (g["fields"] as? [[String: Any]] ?? []).compactMap { f in
                guard let fid = f["id"] as? String else { return nil }
                return DraftOptionField(id: fid, label: (f["label"] as? String) ?? fid,
                                        type: (f["type"] as? String) ?? "text",
                                        choices: (f["choices"] as? [String]) ?? [],
                                        hint: (f["hint"] as? String) ?? "",
                                        defaultValue: f["default"] is NSNull ? "" : str(f["default"]))
            }
            return DraftOptionGroup(group: name, fields: fields)
        }
        return VeinDraft(kind: kind,
                         label: (j["label"] as? String) ?? kind,
                         icon: (j["icon"] as? String) ?? "sparkles",
                         blurb: (j["blurb"] as? String) ?? "",
                         nominalLabel: (j["nominal_label"] as? String) ?? "quiet",
                         schedule: (j["schedule"] as? String) ?? "0 */6 * * *",
                         steps: steps, providers: providers, options: options)
    }

    func encode() -> [String: Any] {
        var out: [String: Any] = [
            "kind": kind, "label": label, "icon": icon,
            "nominal_label": nominalLabel, "blurb": blurb,
            "schedule": schedule,
            "pipeline": steps.map { ["block": $0.block, "params": $0.params] },
        ]
        if !providers.isEmpty {
            out["providers"] = providers.map { ["id": $0.id, "label": $0.label,
                                                "hint": $0.hint, "default": $0.defaultValue] }
        }
        if !options.isEmpty {
            out["options"] = options.map { g in
                ["group": g.group, "fields": g.fields.map { f -> [String: Any] in
                    var d: [String: Any] = ["id": f.id, "label": f.label, "type": f.type]
                    if !f.choices.isEmpty { d["choices"] = f.choices }
                    if !f.hint.isEmpty { d["hint"] = f.hint }
                    if !f.defaultValue.isEmpty {
                        if f.type == "number", let n = Double(f.defaultValue) { d["default"] = n }
                        else if f.type == "bool" { d["default"] = f.defaultValue == "true" }
                        else { d["default"] = f.defaultValue }
                    }
                    return d
                }]
            }
        }
        return out
    }

    var usedBlocks: [String] {
        var seen: Set<String> = []
        return steps.map(\.block).filter { seen.insert($0).inserted }
    }

    func stripping(_ removed: Set<String>) -> VeinDraft {
        var copy = self
        copy.steps = steps.filter { !removed.contains($0.block) }
        return copy
    }

    private func firstStep(_ block: String) -> Int? {
        steps.firstIndex { $0.block == block }
    }

    var hasBand: Bool { firstStep("trip_band") != nil }
    var hasBar: Bool { firstStep("llm_judge") != nil }

    var bandHi: String {
        get { bandParam("hi") }
        set { setBandParam("hi", newValue) }
    }
    var bandLo: String {
        get { bandParam("lo") }
        set { setBandParam("lo", newValue) }
    }
    var judgeBar: String {
        get {
            guard let i = firstStep("llm_judge") else { return "" }
            return (steps[i].params["bar"] as? String) ?? ""
        }
        set {
            guard let i = firstStep("llm_judge") else { return }
            steps[i].params["bar"] = newValue
        }
    }

    private func bandParam(_ key: String) -> String {
        guard let i = firstStep("trip_band"), let v = steps[i].params[key] else { return "" }
        if let n = v as? NSNumber { return n.doubleValue == n.doubleValue.rounded() ? String(format: "%g", n.doubleValue) : "\(n)" }
        return "\(v)"
    }

    private mutating func setBandParam(_ key: String, _ raw: String) {
        guard let i = firstStep("trip_band") else { return }
        let trimmed = raw.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { steps[i].params.removeValue(forKey: key) }
        else if let n = Double(trimmed) { steps[i].params[key] = n }
    }

    static func fixture() -> VeinDraft {
        VeinDraft(kind: "ferment_watch", label: "Fermentation science", icon: "flask",
                  blurb: "new fermentation research worth reading", nominalLabel: "quiet",
                  schedule: "0 7 * * *",
                  steps: [
                      DraftStep(block: "web_search",
                                params: ["query": "new research {options.focus}", "max_results": 8]),
                      DraftStep(block: "llm_judge",
                                params: ["bar": "reports a genuinely new finding about {options.focus}"]),
                      DraftStep(block: "llm_compose", params: [:]),
                  ],
                  providers: [],
                  options: [DraftOptionGroup(group: "Focus", fields: [
                      DraftOptionField(id: "focus", label: "Focus area", type: "text", choices: [],
                                       hint: "", defaultValue: "wine and mead fermentation")])])
    }
}

enum SchedulePreset: String, CaseIterable, Identifiable {
    case every15 = "Every 15 minutes"
    case every30 = "Every 30 minutes"
    case hourly = "Every hour"
    case every6h = "Every 6 hours"
    case morning = "Every morning"
    case evening = "Every evening"
    case weekly = "Weekly on Sunday"

    var id: String { rawValue }

    var cron: String {
        switch self {
        case .every15: return "*/15 * * * *"
        case .every30: return "*/30 * * * *"
        case .hourly: return "0 * * * *"
        case .every6h: return "0 */6 * * *"
        case .morning: return "0 7 * * *"
        case .evening: return "0 18 * * *"
        case .weekly: return "0 9 * * 0"
        }
    }

    static func match(_ cron: String) -> SchedulePreset? {
        allCases.first { $0.cron == cron.trimmingCharacters(in: .whitespaces) }
    }
}

enum BlockFacts {
    static func label(_ block: String) -> String {
        switch block {
        case "web_search": return "Web search"
        case "http_fetch": return "Fetch a URL"
        case "ha_state": return "Home Assistant state"
        case "trip_band": return "Threshold math"
        case "llm_judge": return "Model judgment"
        case "llm_compose": return "Model writing"
        default: return block.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func reach(_ block: String) -> String {
        switch block {
        case "web_search": return "Searches the web through your configured search endpoint."
        case "http_fetch": return "Fetches one URL and reads a value or its text."
        case "ha_state": return "Reads one Home Assistant entity's current state."
        case "trip_band": return "Pure math on fetched numbers. Decides every trip."
        case "llm_judge": return "Your model decides which findings clear the bar."
        case "llm_compose": return "Your model writes the card text."
        default: return "A capability registered by this deployment."
        }
    }

    static func icon(_ block: String) -> String {
        switch block {
        case "web_search": return "magnifyingglass"
        case "http_fetch": return "arrow.down.doc"
        case "ha_state": return "house"
        case "trip_band": return "chart.line.uptrend.xyaxis"
        case "llm_judge": return "scalemass"
        case "llm_compose": return "text.alignleft"
        default: return "puzzlepiece.extension"
        }
    }
}

struct WouldPostCard: Identifiable {
    let id = UUID()
    var title: String
    var summary: String
    var body: String
    var severity: String
}

struct BuilderTranscriptEntry: Identifiable {
    let id = UUID()
    var role: String
    var content: String
}

@MainActor
final class BuilderModel: ObservableObject, Identifiable {
    let id = UUID()
    @Published var transcript: [BuilderTranscriptEntry] = []
    @Published var draft: VeinDraft? {
        didSet { if !applyingServerDraft { dirty = draft != nil } }
    }
    @Published var dirty = false
    @Published var recommended: [String] = []
    @Published var confirmedTools: [String: Bool] = [:]
    @Published var problems: [String] = []
    @Published var done = false
    @Published var sending = false
    @Published var dryRunning = false
    @Published var creating = false
    @Published var error: String?
    @Published var wouldPost: [WouldPostCard] = []
    @Published var stepTrace: [(block: String, items: Int)] = []
    @Published var dryRanOnce = false
    @Published var kindConflict = false

    let base: URL?
    var onCreated: () -> Void = {}
    private var applyingServerDraft = false

    init(base: URL?) {
        self.base = base
    }

    var toolsResolved: Bool {
        guard let draft else { return false }
        return draft.usedBlocks.allSatisfy { confirmedTools[$0] != nil }
    }

    var removedTools: Set<String> {
        Set(confirmedTools.filter { !$0.value }.map(\.key))
    }

    var canCreate: Bool {
        draft != nil && toolsResolved && !creating
    }

    func send(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !sending, base != nil else { return }
        var content = trimmed
        if dirty, let draft,
           let data = try? JSONSerialization.data(withJSONObject: draft.encode()),
           let json = String(data: data, encoding: .utf8) {
            content += "\n\nI have edited the draft. Continue from my version:\n" + json
        }
        transcript.append(BuilderTranscriptEntry(role: "user", content: trimmed))
        sending = true
        error = nil
        let history = transcript.map { ["role": $0.role, "content": $0.content] }
            .dropLast().map { $0 } + [["role": "user", "content": content]]
        Task {
            await self.turn(history)
            self.sending = false
        }
    }

    private func turn(_ messages: [[String: String]]) async {
        guard let base else { return }
        var req = URLRequest(url: base.appendingPathComponent("/pulse/veins/builder/turn"))
        req.httpMethod = "POST"
        req.timeoutInterval = 300
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["messages": messages])
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (200..<300).contains((resp as? HTTPURLResponse)?.statusCode ?? 0),
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            error = "The builder is unreachable. The conversation is kept; try again."
            return
        }
        if (j["disabled"] as? Bool) == true {
            error = (j["detail"] as? String) ?? "The builder is not configured."
            return
        }
        let reply = (j["reply"] as? String) ?? ""
        if !reply.isEmpty {
            transcript.append(BuilderTranscriptEntry(role: "assistant", content: reply))
        }
        problems = (j["problems"] as? [String]) ?? []
        done = (j["done"] as? Bool) ?? false
        recommended = (j["recommended"] as? [String]) ?? recommended
        if let rawDraft = j["draft"] as? [String: Any], let parsed = VeinDraft.parse(rawDraft) {
            applyingServerDraft = true
            draft = parsed
            applyingServerDraft = false
            dirty = false
        }
    }

    func runDryRun() {
        guard let base, let draft, !dryRunning else { return }
        dryRunning = true
        error = nil
        let definition = draft.stripping(removedTools).encode()
        Task {
            var req = URLRequest(url: base.appendingPathComponent("/pulse/veins/builder/dry_run"))
            req.httpMethod = "POST"
            req.timeoutInterval = 120
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: ["definition": definition])
            defer { self.dryRunning = false }
            guard let (data, resp) = try? await URLSession.shared.data(for: req),
                  (200..<300).contains((resp as? HTTPURLResponse)?.statusCode ?? 0),
                  let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                self.error = "Dry run unreachable."
                return
            }
            self.dryRanOnce = true
            self.wouldPost = (j["would_post"] as? [[String: Any]] ?? []).map {
                WouldPostCard(title: ($0["title"] as? String) ?? "",
                              summary: ($0["summary"] as? String) ?? "",
                              body: ($0["body"] as? String) ?? "",
                              severity: ($0["severity"] as? String) ?? "notice")
            }
            self.stepTrace = (j["steps"] as? [[String: Any]] ?? []).map {
                (block: ($0["block"] as? String) ?? "", items: ($0["items"] as? Int) ?? 0)
            }
            let errs = (j["errors"] as? [String]) ?? []
            self.error = errs.isEmpty ? nil : errs.joined(separator: "\n")
        }
    }

    func create() {
        guard let base, let draft, canCreate else { return }
        creating = true
        error = nil
        kindConflict = false
        let definition = draft.stripping(removedTools).encode()
        Task {
            var req = URLRequest(url: base.appendingPathComponent("/pulse/veins"))
            req.httpMethod = "POST"
            req.timeoutInterval = 15
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: definition)
            defer { self.creating = false }
            guard let (data, resp) = try? await URLSession.shared.data(for: req),
                  let code = (resp as? HTTPURLResponse)?.statusCode else {
                self.error = "vera-api unreachable."
                return
            }
            if (200..<300).contains(code) {
                self.onCreated()
                return
            }
            let detail = ((try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String) ?? "HTTP \(code)"
            self.kindConflict = code == 409
            self.error = detail
        }
    }

    static func probe(base: URL?) async -> Bool {
        guard let base else { return false }
        var req = URLRequest(url: base.appendingPathComponent("/pulse/veins/builder"))
        req.timeoutInterval = 6
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (200..<300).contains((resp as? HTTPURLResponse)?.statusCode ?? 0),
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return (j["configured"] as? Bool) ?? false
    }

    static func fixture() -> BuilderModel {
        let m = BuilderModel(base: nil)
        m.transcript = [
            BuilderTranscriptEntry(role: "user",
                                   content: "Watch for new fermentation research, especially anything about wine and mead."),
            BuilderTranscriptEntry(role: "assistant",
                                   content: "Drafted a watcher. It searches for new research on your focus area every morning, keeps only genuinely new findings, and writes a card for each. Adjust the focus or the bar on the right."),
        ]
        m.applyingServerDraft = true
        m.draft = .fixture()
        m.applyingServerDraft = false
        m.recommended = ["web_search", "llm_judge", "llm_compose"]
        m.confirmedTools = ["web_search": true, "llm_judge": true]
        m.done = true
        m.wouldPost = [WouldPostCard(
            title: "Yeast strain doubles ester production",
            summary: "A new study reports a lab-evolved strain with twice the ester output in mead musts.",
            body: "Researchers evolved a strain that doubles ester production without raising fusel alcohols.",
            severity: "notice")]
        m.stepTrace = [(block: "web_search", items: 8), (block: "llm_judge", items: 1),
                       (block: "llm_compose", items: 1)]
        m.dryRanOnce = true
        return m
    }
}