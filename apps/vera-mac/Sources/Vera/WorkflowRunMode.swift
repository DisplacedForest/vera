import SwiftUI

private func workflowEpoch(_ raw: Any?) -> Date? {
    guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
    return Date(timeIntervalSince1970: number.doubleValue)
}

func runStateColor(_ state: String) -> Color {
    switch state {
    case "ok", "accepted", "retried": return FlowStatus.ok.dotColor
    case "warning", "retrying": return FlowStatus.warn.dotColor
    case "error", "failed", "retry_failed": return FlowStatus.fail.dotColor
    case "running": return Theme.accent
    default: return Theme.textSecondary
    }
}

func runStateLabel(_ state: String) -> String {
    switch state {
    case "ok": return "Completed"
    case "warning": return "Completed with a warning"
    case "error", "failed": return "Failed"
    case "running": return "Running"
    default: return state.prefix(1).uppercased() + state.dropFirst()
    }
}

func runDurationText(_ interval: TimeInterval) -> String {
    let seconds = Int(interval.rounded())
    if seconds < 1 { return "under 1s" }
    if seconds < 90 { return "\(seconds)s" }
    return "\(seconds / 60)m \(seconds % 60)s"
}

struct WorkflowNodeRun: Hashable {
    var nodeID: String
    var state: String
    var input: [String: WorkflowConfigValue]
    var output: [String: WorkflowConfigValue]
    var error: String?
    var startedAt: Date?
    var finishedAt: Date?

    var duration: TimeInterval? {
        guard let startedAt, let finishedAt else { return nil }
        return finishedAt.timeIntervalSince(startedAt)
    }

    var itemCount: Int? {
        guard case .int(let count) = output["items"] else { return nil }
        return count
    }

    var countsLine: String? {
        let counts = output.compactMap { key, value -> (String, Int)? in
            guard case .int(let count) = value else { return nil }
            return (key, count)
        }
        .sorted { left, right in
            if (left.0 == "items") != (right.0 == "items") { return right.0 == "items" }
            return left.0 < right.0
        }
        guard let lead = counts.first else { return nil }
        return "\(lead.1) \(lead.0.replacingOccurrences(of: "_", with: " "))"
    }

    static func parse(_ raw: Any) -> WorkflowNodeRun? {
        guard let object = raw as? [String: Any],
              let id = object["id"] as? String,
              let state = object["state"] as? String else { return nil }
        func summary(_ value: Any?) -> [String: WorkflowConfigValue] {
            (value as? [String: Any] ?? [:])
                .filter { $0.value is NSNumber || $0.value is String }
                .mapValues(WorkflowConfigValue.parse)
        }
        return WorkflowNodeRun(nodeID: id, state: state,
                                    input: summary(object["input"]),
                                    output: summary(object["output"]),
                                    error: object["error"] as? String,
                                    startedAt: workflowEpoch(object["started_at"]),
                                    finishedAt: workflowEpoch(object["finished_at"]))
    }
}

struct WorkflowVisualRun: Hashable {
    var cardID: String
    var imageURL: String?
    var accept: Bool?
    var score: Double?
    var reason: String?
    var retryCount: Int
    var state: String

    var verdictLine: String {
        var parts = [runVerdictLabel]
        if let score { parts.append("score \(score.formatted(.number.precision(.fractionLength(2))))") }
        if retryCount > 0 { parts.append("retry \(retryCount)") }
        return parts.joined(separator: " · ")
    }

    private var runVerdictLabel: String {
        switch state {
        case "accepted": return "Accepted"
        case "retrying": return "Failed review"
        case "retried": return "Retried"
        case "retry_failed": return "Retry failed"
        default: return state.prefix(1).uppercased() + state.dropFirst()
        }
    }

    static func parse(_ raw: Any) -> WorkflowVisualRun? {
        guard let object = raw as? [String: Any],
              let cardID = object["card_id"] as? String,
              let state = object["state"] as? String else { return nil }
        let review = object["review"] as? [String: Any] ?? [:]
        return WorkflowVisualRun(cardID: cardID,
                                      imageURL: object["image_url"] as? String,
                                      accept: review["accept"] as? Bool,
                                      score: (review["score"] as? NSNumber)?.doubleValue,
                                      reason: review["reason"] as? String,
                                      retryCount: object["retry_count"] as? Int ?? 0,
                                      state: state)
    }
}

struct WorkflowRun: Hashable {
    var id: String
    var state: String
    var startedAt: Date
    var finishedAt: Date?
    var error: String?
    var version: WorkflowVersion?
    var nodes: [WorkflowNodeRun]
    var visualRuns: [WorkflowVisualRun]

    func nodeRun(_ nodeID: String) -> WorkflowNodeRun? {
        nodes.last { $0.nodeID == nodeID }
    }

    var cardEvidence: [WorkflowVisualRun] {
        var latest: [String: WorkflowVisualRun] = [:]
        var order: [String] = []
        for record in visualRuns {
            if latest[record.cardID] == nil { order.append(record.cardID) }
            latest[record.cardID] = record
        }
        return order.compactMap { latest[$0] }
    }

    static func parse(_ raw: Any) -> WorkflowRun? {
        guard let object = raw as? [String: Any],
              let id = object["id"] as? String,
              let state = object["state"] as? String,
              let startedAt = workflowEpoch(object["started_at"]) else { return nil }
        var version: WorkflowVersion?
        if let versionRaw = object["version"] {
            guard let parsed = WorkflowVersion.parse(versionRaw) else { return nil }
            version = parsed
        }
        let nodes = (object["nodes"] as? [Any] ?? []).compactMap(WorkflowNodeRun.parse)
        let visual = (object["visual_runs"] as? [Any] ?? []).compactMap(WorkflowVisualRun.parse)
        return WorkflowRun(id: id, state: state, startedAt: startedAt,
                                finishedAt: workflowEpoch(object["finished_at"]),
                                error: object["error"] as? String,
                                version: version,
                                nodes: nodes, visualRuns: visual)
    }

    static func classify(_ raw: Any?) -> (run: WorkflowRun?, unreadable: Bool) {
        guard let raw, !(raw is NSNull) else { return (nil, false) }
        guard let run = parse(raw) else { return (nil, true) }
        return (run, false)
    }
}

struct WorkflowRunNodeCard: View {
    let node: WorkflowNode
    var spec: WorkflowCatalogNode?
    var run: WorkflowNodeRun?
    var selected: Bool
    var hasInput: Bool = true
    var triggerJob: SchedulerJob?

    private var faded: Bool { run == nil && triggerJob == nil }

    var body: some View {
        WorkflowCardFace(spec: spec, fallbackType: node.type,
                         stroke: strokeColor, strokeWidth: selected ? 2 : 1,
                         fillOpacity: faded ? 0.55 : 1)
            .shadow(color: Theme.bg.opacity(0.35), radius: selected ? 9 : 4, y: 2)
            .opacity(faded ? 0.6 : 1)
            .overlay {
                WorkflowCardName(text: spec?.label ?? workflowDisplayName(node.type), muted: faded,
                                 caption: fireLine)
            }
            .overlay(alignment: .leading) {
                if hasInput { WorkflowPortDot().offset(x: -4) }
            }
            .overlay(alignment: .trailing) { WorkflowPortDot().offset(x: 4) }
            .overlay(alignment: .topTrailing) { badge }
    }

    private var fireLine: String? {
        guard let triggerJob else { return nil }
        var parts: [String] = []
        if let last = triggerJob.lastRunAt { parts.append("fired \(relativeTime(last))") }
        if let next = triggerJob.nextRun { parts.append("next \(relativeTime(next))") }
        return parts.isEmpty ? "never fired" : parts.joined(separator: " · ")
    }

    @ViewBuilder private var badge: some View {
        if let run {
            ZStack {
                Circle().fill(Theme.bg).frame(width: 19, height: 19)
                Image(systemName: badgeIcon(run.state))
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(runStateColor(run.state))
            }
            .offset(x: 7, y: -7)
            .help(runStateLabel(run.state))
        }
    }

    private func badgeIcon(_ state: String) -> String {
        switch state {
        case "ok", "accepted", "retried": return "checkmark.circle.fill"
        case "warning", "retrying": return "exclamationmark.circle.fill"
        case "error", "failed", "retry_failed": return "xmark.circle.fill"
        case "running": return "arrow.triangle.2.circlepath.circle.fill"
        default: return "circle.fill"
        }
    }

    private var strokeColor: Color {
        if selected { return Theme.accent }
        guard let run else { return Theme.hairline }
        switch run.state {
        case "error", "failed", "retry_failed": return runStateColor(run.state).opacity(0.7)
        case "warning", "retrying": return runStateColor(run.state).opacity(0.55)
        default: return Theme.hairline
        }
    }
}

struct WorkflowRunInspector: View {
    @ObservedObject var store: WorkflowEditorStore
    var snapshot = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let selected = store.runVersion?.definition.nodes.first(where: { $0.id == store.selectedNodeID }) {
                let spec = store.catalog?.node(for: selected.type)
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: spec?.icon ?? "puzzlepiece").font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(graphTint(spec?.tint ?? "accent")).frame(width: 34, height: 34)
                        .background(graphTint(spec?.tint ?? "accent").opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 9))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(spec?.label ?? workflowDisplayName(selected.type)).font(.system(size: 14, weight: .semibold))
                        Text(spec?.description ?? spec?.categoryLabel ?? "Node").font(.system(size: 10))
                            .foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(16)
                Divider().overlay(Theme.hairline)
                if snapshot {
                    content(selected, spec: spec)
                } else {
                    ScrollView { content(selected, spec: spec) }
                }
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    Image(systemName: "cursorarrow.click.2").font(.system(size: 18)).foregroundStyle(Theme.textSecondary)
                    Text("Select a node").font(.system(size: 13, weight: .semibold))
                    Text("What it did in the latest run will appear here.").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                }
                .padding(16)
                Spacer()
            }
        }
        .frame(width: 286).frame(maxHeight: .infinity, alignment: .topLeading).background(Theme.bg)
    }

    private func content(_ selected: WorkflowNode, spec: WorkflowCatalogNode?) -> some View {
        let run = store.latestRun?.nodeRun(selected.id)
        let isTrigger = spec?.category == "trigger"
        return VStack(alignment: .leading, spacing: 16) {
            if isTrigger {
                section("Schedule") {
                    VStack(alignment: .leading, spacing: 6) {
                        if let job = store.triggerJob {
                            if let last = job.lastRunAt { row("Last fire", relativeTime(last)) }
                            if let next = job.nextRun { row("Next fire", relativeTime(next)) }
                            if job.lastRunAt == nil, job.nextRun == nil {
                                Text("No fires recorded yet.")
                                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                            }
                        } else {
                            Text("The schedule record isn't available right now.")
                                .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            } else {
            section("Run") {
                if let run {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 7) {
                            Circle().fill(runStateColor(run.state)).frame(width: 7, height: 7)
                            Text(runStateLabel(run.state)).font(.system(size: 11.5, weight: .medium))
                        }
                        if let started = run.startedAt {
                            row("Started", started.formatted(date: .abbreviated, time: .shortened))
                        }
                        if let duration = run.duration {
                            row("Duration", runDurationText(duration))
                        }
                        if let error = run.error, !error.isEmpty {
                            Text(error).font(.system(size: 10.5)).foregroundStyle(FlowStatus.fail.dotColor)
                                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                        }
                    }
                } else {
                    Text("This node has no record in the latest run.")
                        .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            }
            if let run, !isTrigger {
                section("Input") { summaryRows(run.input, empty: "No input recorded.") }
                section("Output") { summaryRows(run.output, empty: "No output recorded.") }
            }
            if spec?.category == "visual", let evidence = store.latestRun?.cardEvidence, !evidence.isEmpty {
                section("Review evidence") {
                    VStack(spacing: 8) {
                        ForEach(evidence, id: \.cardID) { record in evidenceRow(record) }
                    }
                }
            }
        }
        .padding(16)
    }

    @ViewBuilder private func summaryRows(_ values: [String: WorkflowConfigValue], empty: String) -> some View {
        if values.isEmpty {
            Text(empty).font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
        } else {
            VStack(spacing: 4) {
                ForEach(values.keys.sorted(), id: \.self) { key in
                    HStack(alignment: .top, spacing: 6) {
                        Text(key.replacingOccurrences(of: "_", with: " "))
                            .font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                        Spacer(minLength: 8)
                        Text(values[key]?.display ?? "")
                            .font(.system(size: 10.5, weight: .semibold).monospacedDigit())
                    }
                }
            }
        }
    }

    private func evidenceRow(_ record: WorkflowVisualRun) -> some View {
        HStack(alignment: .top, spacing: 9) {
            evidenceThumb(record)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Circle().fill(runStateColor(record.state)).frame(width: 6, height: 6)
                    Text(record.verdictLine).font(.system(size: 10.5, weight: .medium)).lineLimit(1)
                }
                if let reason = record.reason, !reason.isEmpty {
                    Text(reason).font(.system(size: 10)).foregroundStyle(Theme.textSecondary).lineLimit(2)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface.opacity(0.72))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder private func evidenceThumb(_ record: WorkflowVisualRun) -> some View {
        let placeholder = RoundedRectangle(cornerRadius: 7).fill(Theme.surface)
            .overlay(Image(systemName: "photo").font(.system(size: 12)).foregroundStyle(Theme.textSecondary))
        if !snapshot, let url = record.imageURL.flatMap(URL.init(string:)) {
            AsyncImage(url: url) { image in
                image.resizable().aspectRatio(contentMode: .fill)
            } placeholder: { placeholder }
            .frame(width: 42, height: 42).clipShape(RoundedRectangle(cornerRadius: 7))
        } else {
            placeholder.frame(width: 42, height: 42)
        }
    }

    private func section(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(title.uppercased()).font(.system(size: 9, weight: .bold)).tracking(0.7)
                .foregroundStyle(Theme.textSecondary.opacity(0.72))
            content()
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text(label).font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
            Spacer(minLength: 8)
            Text(value).font(.system(size: 10.5, weight: .semibold))
        }
    }
}

struct WorkflowRunEmptyState: View {
    var icon = "clock.arrow.circlepath"
    var title = "No recorded runs"
    var note = "This workflow's next run will appear here, node by node."

    var body: some View {
        ZStack {
            WorkflowCanvasGrid(transform: WorkflowCanvasTransform())
            VStack(spacing: 14) {
                Image(systemName: icon).font(.system(size: 22, weight: .medium))
                    .foregroundStyle(Theme.accent)
                    .frame(width: 56, height: 56)
                    .background(workflowCardShape(trigger: false).fill(Theme.surface))
                    .overlay(workflowCardShape(trigger: false).stroke(Theme.hairline, lineWidth: 1))
                VStack(spacing: 5) {
                    Text(title).font(.system(size: 14, weight: .semibold))
                    Text(note).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                        .multilineTextAlignment(.center).frame(maxWidth: 380)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }
}

extension WorkflowEditorStore {
    static func runFixture() -> WorkflowEditorStore {
        let store = fixture()
        store.mode = .run
        let json = """
        {"id":"run-fixture","workflow_id":"pulse","state":"ok","started_at":1754250000,"finished_at":1754250340,
         "output":{},"error":null,
         "version":{"id":"fixture-pinned","version":2,"state":"archived","definition":{"id":"pulse",
           "nodes":[{"id":"schedule","type":"trigger.schedule","config":{"mode":"daily","time":"05:00"}},
                    {"id":"triage","type":"pulse.triage","config":{}},
                    {"id":"gates","type":"pulse.gates","config":{}},
                    {"id":"synthesis","type":"pulse.synthesis","config":{}},
                    {"id":"claim_audit","type":"pulse.claim_audit","config":{}},
                    {"id":"cover_art","type":"pulse.cover_art","config":{"style":"editorial"}},
                    {"id":"visual_review","type":"pulse.visual_review","config":{"threshold":0.8}},
                    {"id":"cover_retry","type":"pulse.cover_retry","config":{"max_attempts":1}},
                    {"id":"inject","type":"pulse.inject","config":{}}],
           "edges":[{"from":"schedule","to":"triage"},{"from":"triage","to":"gates"},{"from":"gates","to":"synthesis"},
                    {"from":"synthesis","to":"claim_audit"},{"from":"claim_audit","to":"cover_art"},
                    {"from":"cover_art","to":"visual_review"},{"from":"visual_review","to":"cover_retry"},
                    {"from":"cover_retry","to":"inject"}],
           "positions":{"schedule":{"x":105,"y":310},"triage":{"x":289,"y":310},"gates":{"x":473,"y":310},
                        "synthesis":{"x":657,"y":310},"claim_audit":{"x":841,"y":310},"cover_art":{"x":1025,"y":310},
                        "visual_review":{"x":1209,"y":310},"cover_retry":{"x":1393,"y":310},
                        "inject":{"x":1577,"y":310}}}},
         "nodes":[
           {"id":"triage","state":"ok","input":{"items":0},"output":{"items":9,"rounds":3},"started_at":1754250000,"finished_at":1754250060},
           {"id":"gates","state":"ok","input":{"items":9},"output":{"items":6},"started_at":1754250060,"finished_at":1754250061},
           {"id":"synthesis","state":"warning","input":{"items":6},"output":{"items":5},"error":"one card starved for sources","started_at":1754250061,"finished_at":1754250190},
           {"id":"claim_audit","state":"ok","input":{"items":5},"output":{"items":5,"revised":1},"started_at":1754250190,"finished_at":1754250228},
           {"id":"cover_art","state":"ok","input":{"items":5},"output":{"items":5,"generated":4},"started_at":1754250228,"finished_at":1754250300},
           {"id":"visual_review","state":"ok","input":{"items":5},"output":{"items":5,"reviewed":4},"started_at":1754250300,"finished_at":1754250321},
           {"id":"cover_retry","state":"ok","input":{"items":5},"output":{"items":5,"attempted":1},"started_at":1754250321,"finished_at":1754250332},
           {"id":"inject","state":"ok","input":{"items":5},"output":{"items":5,"cards":5},"started_at":1754250332,"finished_at":1754250340}
         ],
         "visual_runs":[
           {"card_id":"card-1","image_url":null,"review":{"accept":true,"score":0.94},"retry_count":0,"state":"accepted"},
           {"card_id":"card-2","image_url":null,"review":{"accept":false,"score":0.55,"reason":"headline text is cropped at the frame edge"},"retry_count":0,"state":"retrying"},
           {"card_id":"card-2","image_url":null,"review":{"accept":false,"score":0.55,"reason":"headline text is cropped at the frame edge"},"retry_count":1,"state":"retried"},
           {"card_id":"card-3","image_url":null,"review":{"accept":true,"score":0.88},"retry_count":0,"state":"accepted"}
         ]}
        """
        if let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)) {
            store.latestRun = WorkflowRun.parse(object)
        }
        store.triggerJob = SchedulerJob(id: "pulse", label: "Pulse briefing run", cron: "0 5 * * *",
                                        enabled: true, envLocked: false, gated: nil,
                                        lastRunAt: Date().addingTimeInterval(-7 * 3600), lastRunOK: true,
                                        lastRunDetail: "5 cards",
                                        nextRun: Date().addingTimeInterval(17 * 3600))
        store.selectedNodeID = "visual_review"
        return store
    }
}

struct WorkflowRunShot: View {
    @StateObject private var store = WorkflowEditorStore.runFixture()
    @State private var searchText = ""

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                HStack(spacing: 8) {
                    VeraMark(size: 18)
                    Text("Vera").font(.system(size: 14, weight: .semibold))
                    Spacer()
                }
                .padding(.horizontal, 14).frame(height: 42)
                WorkflowPalette(store: store, searchText: $searchText, snapshot: true)
            }
            .frame(width: 248).background(Theme.sidebar)
            Divider().overlay(Theme.hairline)
            WorkflowEditor(store: store, snapshot: true)
        }
    }
}
