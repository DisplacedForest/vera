import SwiftUI

/// The Agentic canvas screen: organism map, drill-ins, and the node inspector.
/// Read-only by design; every control the old job cards had lives in the inspector.
struct AgenticCanvasView: View {
    @ObservedObject var graphStore: GraphStore
    @ObservedObject var sched: SchedulerStore
    @ObservedObject var activity: ActivityStore
    @ObservedObject var pulseRun: PulseRunStore
    var onEditSchedule: (SchedulerJob) -> Void

    @State private var selected: String?
    @State private var drilled: String?
    @State private var pulses: [CanvasPulse] = []
    @State private var eventBaseline: Date?

    private var jobsByID: [String: SchedulerJob] {
        Dictionary(uniqueKeysWithValues: sched.jobs.map { ($0.id, $0) })
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                header
                if case .ready = graphStore.phase, !sched.masterEnabled, sched.phase == .ready {
                    pausedStrip
                }
                content
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            if let id = selected, let graph = graphStore.graph, let flow = graph.flow(id) {
                InspectorPanel(flow: flow, job: jobsByID[id], sched: sched,
                               events: nodeEvents(flow),
                               onEditSchedule: onEditSchedule,
                               onDrill: flow.stages.isEmpty ? nil : { drilled = id },
                               onClose: { selected = nil })
                    .transition(.move(edge: .trailing))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: selected)
        .onChange(of: activity.events) { _, events in spawnPulses(from: events) }
    }

    // MARK: header

    @ViewBuilder
    private var header: some View {
        if let id = drilled, let graph = graphStore.graph, let flow = graph.flow(id) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 10) {
                    Button {
                        drilled = nil
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: "chevron.left").font(.system(size: 10, weight: .bold))
                            Text("All flows").font(.system(size: 13, weight: .medium))
                        }
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 11).padding(.vertical, 5)
                        .background(Theme.surface).clipShape(Capsule())
                        .overlay(Capsule().stroke(Theme.hairline, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    Text(flow.label).font(.system(size: 22, weight: .bold))
                    if let job = jobsByID[id] {
                        Text(cronSummary(job.cron)).font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                            .padding(.horizontal, 9).padding(.vertical, 3)
                            .background(Theme.surface).clipShape(Capsule())
                    }
                    Spacer()
                }
                if let line = drillRunLine(flow) {
                    HStack(spacing: 7) {
                        Image(systemName: line.warn ? "exclamationmark.triangle" : "clock")
                            .font(.system(size: 11))
                            .foregroundStyle(line.warn ? FlowStatus.warn.dotColor : Theme.textSecondary)
                        Text(line.text).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    }
                }
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(spacing: 10) {
                Text("Agentic").font(.system(size: 22, weight: .bold))
                InfoTip(text: "Everything Vera runs on her own, as one living system. Click a flow to inspect it, double click to open its pipeline.", size: 13)
                if let graph = graphStore.graph, case .ready = graphStore.phase {
                    Text("\(graph.flows.count) flows").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 9).padding(.vertical, 3)
                        .background(Theme.surface).clipShape(Capsule())
                }
                Spacer()
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 14)
        }
    }

    private func drillRunLine(_ flow: GraphFlow) -> (text: String, warn: Bool)? {
        if flow.id == "pulse" {
            guard let st = flow.pulseState else { return ("No recent run recorded.", false) }
            if st.state == "running" { return ("Running now.", false) }
            var text = "Last run injected \(st.injected) card\(st.injected == 1 ? "" : "s")"
            if let at = st.finishedAt { text += " \(relativeTime(at))" }
            if let warning = st.warnings.first { return ("\(text). \(warning).", true) }
            return ("\(text).", false)
        }
        if flow.kind == "heartbeat" {
            return ("Each tick reads HEARTBEAT.md and decides which branches to take.", false)
        }
        return nil
    }

    private var pausedStrip: some View {
        HStack(spacing: 8) {
            Image(systemName: "pause.circle").font(.system(size: 13)).foregroundStyle(.orange)
            Text("Scheduler paused. The server's master switch is off, no jobs will fire.")
                .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            Spacer()
        }
        .padding(.horizontal, 28).padding(.bottom, 10)
    }

    // MARK: canvas content

    @ViewBuilder
    private var content: some View {
        switch graphStore.phase {
        case .loading:
            VStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Loading the canvas").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .unconfigured:
            CanvasStatusCard(icon: "gearshape", title: "vera-api isn't configured",
                             note: "Set the vera-api URL in Settings to see Vera's autonomous flows.")
        case .unreachable:
            CanvasStatusCard(icon: "exclamationmark.triangle", title: "vera-api unreachable",
                             note: "Couldn't load the canvas from \(graphStore.baseDescription).",
                             retry: { await graphStore.refresh() })
        case .unsupported:
            CanvasStatusCard(icon: "point.3.connected.trianglepath.dotted", title: "Canvas not available",
                             note: "This vera-api doesn't serve the flow graph yet. Update vera-api to see the canvas.",
                             retry: { await graphStore.refresh() })
        case .ready:
            if let graph = graphStore.graph {
                GeometryReader { geo in
                    ScrollView([.horizontal, .vertical]) {
                        Group {
                            if let id = drilled, let flow = graph.flow(id) {
                                drillView(flow, graph: graph, viewport: geo.size)
                            } else {
                                OrganismMap(graph: graph, jobs: jobsByID, size: geo.size,
                                            selected: selected, pulses: pulses,
                                            onSelect: { selected = $0 },
                                            onDrill: { drilled = $0; selected = nil })
                            }
                        }
                    }
                }
                .overlay(Rectangle().fill(Theme.hairline).frame(height: 1), alignment: .top)
            }
        }
    }

    @ViewBuilder
    private func drillView(_ flow: GraphFlow, graph: AgenticGraph, viewport: CGSize) -> some View {
        if flow.stageLayout == "fan" {
            HeartbeatDrill(flow: flow, graph: graph, viewport: viewport)
        } else {
            PulseDrill(flow: flow, graph: graph, viewport: viewport, detail: pulseRun.detail)
        }
    }

    // MARK: breathing layer

    /// Recent activity becomes pulses traveling the matching edges. The first batch after
    /// launch only sets the baseline — history shouldn't replay as motion.
    private func spawnPulses(from events: [ActivityEvent]) {
        guard let graph = graphStore.graph else { return }
        let newest = events.map(\.ts).max() ?? Date()
        guard let baseline = eventBaseline else {
            eventBaseline = newest
            return
        }
        let fresh = events.filter { $0.ts > baseline }
        guard !fresh.isEmpty else { return }
        eventBaseline = newest
        var spawned: [CanvasPulse] = []
        for (i, event) in fresh.prefix(6).enumerated() {
            let start = Date().addingTimeInterval(Double(i) * 0.35)
            for (flowID, surfaceID) in edges(for: event, graph: graph) {
                spawned.append(CanvasPulse(id: "\(event.id)-\(surfaceID)",
                                           flowID: flowID, surfaceID: surfaceID, startedAt: start))
            }
        }
        guard !spawned.isEmpty else { return }
        pulses.append(contentsOf: spawned)
        Task {
            try? await Task.sleep(nanoseconds: UInt64((CanvasPulse.duration + 2.5) * 1_000_000_000))
            let cutoff = Date().addingTimeInterval(-CanvasPulse.duration)
            pulses.removeAll { $0.startedAt < cutoff }
        }
    }

    /// Which canvas edges an event lights up. A failed run feeds nothing, so failures
    /// tint their node instead of traveling an edge.
    private func edges(for event: ActivityEvent, graph: AgenticGraph) -> [(String, String)] {
        if event.source == "scheduler", event.kind == "ok",
           let tool = event.tool, let flow = graph.flow(tool) {
            return flow.feeds.map { (flow.id, $0) }
        }
        if event.source == "heartbeat",
           let hb = graph.flows.first(where: { $0.kind == "heartbeat" }) {
            let branch = ["learn": "learn", "refine": "refine",
                          "propose": "propose", "confirmed": "propose", "dismissed": "propose",
                          "watch": "watch", "foryou": "foryou", "foryou_skip": "foryou"][event.kind]
            let feeds = hb.stages.first { $0.id == branch }?.feeds ?? []
            return feeds.map { (hb.id, $0) }
        }
        // Free-lane actions are autonomous by definition (today only the heartbeat runs
        // that lane), so they travel the heartbeat's edge to the Actions surface.
        if event.source == "action", event.kind == "auto",
           let hb = graph.flows.first(where: { $0.kind == "heartbeat" }),
           hb.feeds.contains("actions") {
            return [(hb.id, "actions")]
        }
        return []
    }

    /// Inspector activity: this node's recent events.
    private func nodeEvents(_ flow: GraphFlow) -> [ActivityEvent] {
        activity.events.filter { event in
            if flow.kind == "heartbeat" { return event.source == "heartbeat" || event.tool == flow.id }
            return event.tool == flow.id
        }
    }
}

/// Centered clean state for the canvas area (unconfigured, unreachable, unsupported).
struct CanvasStatusCard: View {
    let icon: String
    let title: String
    let note: String
    var retry: (() async -> Void)?

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: icon).font(.system(size: 22)).foregroundStyle(Theme.textSecondary)
            Text(title).font(.system(size: 14, weight: .semibold))
            Text(note).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center).frame(maxWidth: 380)
            if let retry {
                Button("Retry") { Task { await retry() } }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.accent).padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Pulse drill-in

/// The pulse pipeline: stage nodes in a line, ending at the surface it feeds.
/// Stage counts come from the last run's structured record; no record, no numbers.
struct PulseDrill: View {
    let flow: GraphFlow
    let graph: AgenticGraph
    let viewport: CGSize
    var detail: PulseRunDetail? = nil
    var initialExpandedStage: String? = nil   // lets the screenshot harness open a stage
    @State private var expandedStage: String?

    static let gateOrder = ["dedup", "freshness", "coherence", "empty", "interest_cap"]
    static let gateLabels = ["dedup": "Dedup", "freshness": "Freshness",
                             "coherence": "Coherence", "empty": "Empty",
                             "interest_cap": "Interest cap"]

    var body: some View {
        let active = expandedStage ?? initialExpandedStage
        VStack(spacing: 0) {
            Spacer(minLength: 20)
            HStack(spacing: 0) {
                ForEach(Array(flow.stages.enumerated()), id: \.element.id) { i, stage in
                    if i > 0 { connector }
                    StageNode(stage: stage, primary: primaryLine(stage), secondary: secondaryLine(stage),
                              gateRows: stage.id == "gates" ? gateRows : [],
                              warn: stage.id == "synthesis" && !(flow.pulseState?.warnings.isEmpty ?? true),
                              tool: stageTool(stage), selected: active == stage.id,
                              onTap: { expandedStage = (active == stage.id) ? nil : stage.id })
                }
                if let surface = graph.surfaces.first(where: { flow.feeds.contains($0.id) }) {
                    connector
                    SurfaceNode(surface: surface)
                        .frame(width: OrganismLayout.surfaceSize.width,
                               height: OrganismLayout.surfaceSize.height)
                }
            }
            .padding(.horizontal, 28)
            if let active, let stage = flow.stages.first(where: { $0.id == active }) {
                StageDetailPanel(stage: stage, detail: detail,
                                 warnings: flow.pulseState?.warnings ?? [])
                    .frame(maxWidth: 720, alignment: .leading)
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, 28).padding(.top, 16)
            }
            Spacer(minLength: 20)
        }
        .frame(minWidth: viewport.width, minHeight: viewport.height, alignment: .top)
        .background(DotGrid())
    }

    private var connector: some View {
        Rectangle().fill(.white.opacity(0.14)).frame(width: 14, height: 1.5)
    }

    private var gateRows: [(String, String)] {
        guard let gates = flow.pulseState?.gates, !gates.isEmpty else { return [] }
        return Self.gateOrder.compactMap { key in
            guard let n = gates[key] else { return nil }
            return (Self.gateLabels[key] ?? key, "\(n)")
        }
    }

    private func primaryLine(_ stage: GraphStage) -> String? {
        guard let st = flow.pulseState else { return nil }
        switch stage.id {
        case "triage": return "\(st.proposed) stories"
        case "synthesis": return "\(st.injected) cards"
        case "claim_audit": return "\(st.injected) passed"
        case "cover_art": return "\(st.injected) covers"
        case "inject": return "\(st.injected) cards"
        default: return nil
        }
    }

    private func secondaryLine(_ stage: GraphStage) -> String? {
        guard let st = flow.pulseState else { return nil }
        switch stage.id {
        case "triage": return "\(st.rounds) round\(st.rounds == 1 ? "" : "s")"
        case "synthesis": return st.warnings.isEmpty ? nil : "starved run"
        case "cover_art": return "vera-image"
        case "inject":
            guard let at = st.finishedAt else { return nil }
            let f = DateFormatter()
            f.timeStyle = .short
            return f.string(from: at)
        default: return nil
        }
    }

    private func stageTool(_ stage: GraphStage) -> String? {
        switch stage.id {
        case "triage": return "websearch"
        case "cover_art": return "vera-image"
        default: return nil
        }
    }
}

/// One pipeline stage face.
struct StageNode: View {
    let stage: GraphStage
    var primary: String?
    var secondary: String?
    var gateRows: [(String, String)] = []
    var warn: Bool = false
    var tool: String?
    var selected: Bool = false
    var onTap: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 7) {
                RoundedRectangle(cornerRadius: 6)
                    .fill(graphTint(stage.tint).opacity(0.14))
                    .frame(width: 22, height: 22)
                    .overlay(Image(systemName: stage.icon)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(graphTint(stage.tint)))
                Text(stage.label).font(.system(size: 12.5, weight: .semibold)).lineLimit(1)
                Spacer(minLength: 0)
                Image(systemName: selected ? "chevron.down" : "chevron.right")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary.opacity(0.7))
            }
            if !gateRows.isEmpty {
                VStack(spacing: 5) {
                    ForEach(gateRows, id: \.0) { row in
                        HStack {
                            Text(row.0).font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                            Spacer()
                            Text(row.1).font(.system(size: 10.5, weight: .semibold))
                                .foregroundStyle(FlowStatus.warn.dotColor)
                        }
                    }
                }
                .padding(.top, 9)
            } else {
                if let primary {
                    Text(primary).font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(warn ? FlowStatus.warn.dotColor : Theme.textPrimary)
                        .padding(.top, 9)
                }
                HStack(spacing: 5) {
                    if let secondary {
                        Text(secondary).font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                    }
                    if let tool { ToolBadge(tool: tool) }
                }
                .padding(.top, 3)
            }
        }
        .padding(11)
        .frame(width: 138, alignment: .topLeading)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12)
            .stroke(strokeColor, lineWidth: selected ? 1.5 : 1))
        .overlay(alignment: .leading) { PortDot().offset(x: -3.5) }
        .overlay(alignment: .trailing) { PortDot().offset(x: 3.5) }
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
    }

    private var strokeColor: Color {
        if selected { return Theme.accent.opacity(0.7) }
        return warn ? FlowStatus.warn.dotColor.opacity(0.42) : Theme.hairline
    }
}

/// The expanded detail for a tapped pipeline stage: the per-item evidence behind the summary
/// face. Triage lists every candidate and its outcome; Gates groups the named casualties; the
/// shipping stages show per-card audit, cover, and inject results. Jargon is explained in place.
struct StageDetailPanel: View {
    let stage: GraphStage
    var detail: PulseRunDetail?
    var warnings: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 7) {
                Image(systemName: stage.icon).font(.system(size: 12)).foregroundStyle(graphTint(stage.tint))
                Text(stage.label).font(.system(size: 14, weight: .semibold))
                Spacer()
            }
            if let note = stageNote {
                Text(note).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }

    @ViewBuilder private var content: some View {
        if let detail, !detail.items.isEmpty {
            switch stage.id {
            case "triage": triageList(detail)
            case "gates": gatesList(detail)
            default: injectedList(detail)
            }
        } else {
            Text("No detail recorded for this run.")
                .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        }
    }

    private var stageNote: String? {
        switch stage.id {
        case "gates": return PulseVocabulary.explain("gate kills")
        case "synthesis": return warnings.isEmpty ? nil : PulseVocabulary.explain("starved run")
        case "claim_audit": return "Each shipped card is checked against its own sources by a second model."
        case "cover_art": return "Generated is a fresh cover image; fallback reused a real photo from the sources."
        default: return nil
        }
    }

    private func triageList(_ d: PulseRunDetail) -> some View {
        VStack(spacing: 0) { ForEach(d.items) { itemRow($0) } }
    }

    private func gatesList(_ d: PulseRunDetail) -> some View {
        let groups = PulseDrill.gateOrder.compactMap { g -> (String, [PulseRunItem])? in
            let k = d.killed(at: g); return k.isEmpty ? nil : (g, k)
        }
        return VStack(alignment: .leading, spacing: 12) {
            if groups.isEmpty {
                Text("No candidates were cut by gates this run.")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            ForEach(groups, id: \.0) { g, items in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(PulseDrill.gateLabels[g] ?? g).font(.system(size: 11, weight: .semibold))
                        Text("\(items.count)").font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(FlowStatus.warn.dotColor)
                        InfoTip(text: PulseVocabulary.explain(g))
                        Spacer()
                    }
                    ForEach(items) { itemRow($0, showGate: false) }
                }
            }
        }
    }

    private func injectedList(_ d: PulseRunDetail) -> some View {
        let items = d.injectedItems
        return VStack(spacing: 0) {
            if items.isEmpty {
                Text("No cards shipped this run.")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            ForEach(items) { item in
                HStack(alignment: .top, spacing: 8) {
                    Circle().fill(FlowStatus.ok.dotColor).frame(width: 6, height: 6).padding(.top, 5)
                    Text(item.title).font(.system(size: 12)).lineLimit(2)
                    Spacer(minLength: 8)
                    Text(stageValue(item)).font(.system(size: 10.5, weight: .medium))
                        .foregroundStyle(Theme.textSecondary)
                }
                .padding(.vertical, 5)
            }
        }
    }

    private func stageValue(_ item: PulseRunItem) -> String {
        switch stage.id {
        case "claim_audit":
            let v = item.auditVerdict ?? "not audited"
            if v == "revised", let n = item.auditUnsupported { return "revised (\(n) fixed)" }
            return v
        case "cover_art": return (item.coverGenerated ?? false) ? "generated" : "fallback"
        case "inject": return item.cardID != nil ? "shipped" : ""
        default: return ""   // synthesis: the title alone is the per-card result
        }
    }

    private func itemRow(_ item: PulseRunItem, showGate: Bool = true) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Circle().fill(dotColor(item)).frame(width: 6, height: 6).padding(.top, 5)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.system(size: 12)).lineLimit(2)
                if let line = evidenceLine(item, showGate: showGate) {
                    Text(line).font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 8)
        }
        .padding(.vertical, 5)
    }

    private func dotColor(_ item: PulseRunItem) -> Color {
        switch item.status {
        case "injected": return FlowStatus.ok.dotColor
        case "error": return FlowStatus.fail.dotColor
        default: return FlowStatus.warn.dotColor
        }
    }

    private func evidenceLine(_ item: PulseRunItem, showGate: Bool) -> String? {
        if item.status == "injected" { return "shipped" }
        let label = item.gate.map { PulseDrill.gateLabels[$0] ?? $0 } ?? "skipped"
        let prefix = showGate ? "\(label): " : ""
        switch item.gate {
        case "dedup": return "\(prefix)already covered by \(item.detail ?? "an existing card")"
        case "freshness": return "\(prefix)newest source \(item.detail ?? "undated")"
        case "coherence": return "\(prefix)corpus about \(item.detail ?? "another subject")"
        case "interest_cap": return "\(prefix)\(item.detail ?? "this interest") already shipped this run"
        case "empty": return "\(prefix)research produced nothing"
        default: return item.reason.map { "\(prefix)\($0)" }
        }
    }
}

// MARK: - Heartbeat drill-in

/// The heartbeat fan: the trigger node branching into learn / refine / propose /
/// watches / for you, each connected to the surface it feeds. Branch state is the
/// latest outcome each branch produced.
struct HeartbeatDrill: View {
    let flow: GraphFlow
    let graph: AgenticGraph
    let viewport: CGSize

    private let branchSize = CGSize(width: 210, height: 86)
    private let triggerSize = OrganismLayout.heartbeatSize

    var body: some View {
        let rows = max(flow.stages.count, 1)
        let stackH = CGFloat(rows) * (branchSize.height + 22) - 22
        let height = max(stackH + 64, viewport.height)
        let branchX: CGFloat = 380
        let surfaceX = branchX + branchSize.width + 150
        let width = max(viewport.width, surfaceX + OrganismLayout.surfaceSize.width + 28)
        let topY = (height - stackH) / 2

        ZStack(alignment: .topLeading) {
            DotGrid()
            Canvas { ctx, _ in
                let triggerPort = CGPoint(x: 28 + triggerSize.width, y: height / 2)
                for (i, stage) in flow.stages.enumerated() {
                    let midY = topY + CGFloat(i) * (branchSize.height + 22) + branchSize.height / 2
                    let active = flow.branchState[stage.id] != nil
                    ctx.stroke(edgePath(triggerPort, CGPoint(x: branchX, y: midY)),
                               with: .color(active ? Theme.accent.opacity(0.30) : .white.opacity(0.10)),
                               lineWidth: 1.5)
                    if let surfaceID = stage.feeds.first,
                       graph.surfaces.contains(where: { $0.id == surfaceID }) {
                        ctx.stroke(edgePath(CGPoint(x: branchX + branchSize.width, y: midY),
                                            CGPoint(x: surfaceX, y: midY)),
                                   with: .color(.white.opacity(0.10)), lineWidth: 1.5)
                    }
                }
            }
            FlowNode(flow: flow, job: nil, animated: false)
                .frame(width: triggerSize.width, height: triggerSize.height)
                .position(x: 28 + triggerSize.width / 2, y: height / 2)
            ForEach(Array(flow.stages.enumerated()), id: \.element.id) { i, stage in
                let y = topY + CGFloat(i) * (branchSize.height + 22)
                BranchNode(stage: stage, outcome: flow.branchState[stage.id])
                    .frame(width: branchSize.width, height: branchSize.height)
                    .position(x: branchX + branchSize.width / 2, y: y + branchSize.height / 2)
                if let surfaceID = stage.feeds.first,
                   let surface = graph.surfaces.first(where: { $0.id == surfaceID }) {
                    SurfaceNode(surface: surface)
                        .frame(width: OrganismLayout.surfaceSize.width,
                               height: OrganismLayout.surfaceSize.height)
                        .position(x: surfaceX + OrganismLayout.surfaceSize.width / 2,
                                  y: y + branchSize.height / 2)
                }
            }
        }
        .frame(width: width, height: height, alignment: .topLeading)
    }
}

/// One heartbeat branch face: label, latest outcome, when.
struct BranchNode: View {
    let stage: GraphStage
    let outcome: BranchOutcome?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 6)
                    .fill(graphTint(stage.tint).opacity(0.14))
                    .frame(width: 24, height: 24)
                    .overlay(Image(systemName: stage.icon)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(graphTint(stage.tint)))
                Text(stage.label).font(.system(size: 13, weight: .semibold))
                Spacer(minLength: 0)
                Circle()
                    .fill(outcome == nil ? Theme.textSecondary.opacity(0.5)
                                         : Color(red: 0.36, green: 0.78, blue: 0.50))
                    .frame(width: 7, height: 7)
            }
            if let outcome {
                Text(outcome.detail.isEmpty ? outcome.kind : outcome.detail)
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary).lineLimit(1)
                Text(relativeTime(outcome.ts)).font(.system(size: 10.5))
                    .foregroundStyle(Theme.textSecondary.opacity(0.8))
            } else {
                Text("no recent activity").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
        .overlay(alignment: .leading) { PortDot().offset(x: -3.5) }
        .overlay(alignment: .trailing) { PortDot().offset(x: 3.5) }
    }
}

// MARK: - Inspector

/// Everything the old job cards could do, scoped to the selected node: run now,
/// enable/disable, schedule (plain English here; raw cron only inside the editor),
/// the last run's detail, this node's recent activity, and tool attribution.
struct InspectorPanel: View {
    let flow: GraphFlow
    let job: SchedulerJob?
    @ObservedObject var sched: SchedulerStore
    let events: [ActivityEvent]
    var onEditSchedule: (SchedulerJob) -> Void
    var onDrill: (() -> Void)?
    var onClose: () -> Void

    var body: some View {
        ScrollView {
            InspectorContent(flow: flow, job: job, sched: sched, events: events,
                             onEditSchedule: onEditSchedule, onDrill: onDrill, onClose: onClose)
                // 20 + the content's own 16 clears the hidden title bar, like every header.
                .padding(.top, 20)
        }
        .frame(width: 332)
        .background(Color(red: 0.118, green: 0.122, blue: 0.129))
        .overlay(Rectangle().fill(Theme.hairline).frame(width: 1), alignment: .leading)
    }
}

/// The inspector's sections, separate from the scroll container so the screenshot
/// renderer (which can't draw ScrollView contents) can render them directly.
struct InspectorContent: View {
    let flow: GraphFlow
    let job: SchedulerJob?
    @ObservedObject var sched: SchedulerStore
    let events: [ActivityEvent]
    var onEditSchedule: (SchedulerJob) -> Void
    var onDrill: (() -> Void)?
    var onClose: () -> Void
    var liveControls: Bool = true
    // Lets the screenshot harness open one activity entry; live use starts collapsed.
    var initialExpandedEventID: String? = nil
    // Which activity entry is expanded in place (secondary selection within the inspector).
    @State private var expandedEventID: String? = nil

    var body: some View {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 9) {
                    RoundedRectangle(cornerRadius: 7)
                        .fill(graphTint(flow.tint).opacity(0.14))
                        .frame(width: 28, height: 28)
                        .overlay(Image(systemName: flow.icon)
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(graphTint(flow.tint)))
                    Text(flow.label).font(.system(size: 15, weight: .semibold))
                    Spacer(minLength: 0)
                    Button(action: onClose) {
                        Image(systemName: "xmark").font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .buttonStyle(.plain).help("Close")
                }
                if let sub = subline {
                    Text(sub).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 7).padding(.leading, 37)
                }
                if let job {
                    actions(job)
                    if let note = sched.rowNote[job.id] {
                        Text(note).font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Theme.accent).padding(.top, 8)
                    }
                    schedule(job)
                    lastRun(job)
                }
                if let onDrill {
                    section(flow.stageLayout == "fan" ? "Branches" : "Pipeline") {
                        Button(action: onDrill) {
                            HStack(spacing: 6) {
                                Image(systemName: "point.3.connected.trianglepath.dotted")
                                    .font(.system(size: 11))
                                Text(flow.stageLayout == "fan" ? "Open the branch map" : "Open the pipeline")
                                    .font(.system(size: 12, weight: .medium))
                            }
                            .foregroundStyle(Theme.accent)
                        }
                        .buttonStyle(.plain)
                    }
                }
                if !events.isEmpty {
                    section("Activity") {
                        VStack(spacing: 0) {
                            ForEach(events.prefix(6)) { event in
                                let expanded = (expandedEventID ?? initialExpandedEventID) == event.id
                                VStack(alignment: .leading, spacing: 0) {
                                    Button {
                                        expandedEventID = expanded ? nil : event.id
                                    } label: {
                                        HStack(spacing: 8) {
                                            Image(systemName: event.icon).font(.system(size: 11))
                                                .foregroundStyle(event.failed ? FlowStatus.fail.dotColor : Theme.textSecondary)
                                                .frame(width: 14)
                                            Text(event.title).font(.system(size: 12)).lineLimit(1)
                                            Spacer(minLength: 8)
                                            Text(relativeTime(event.ts)).font(.system(size: 10))
                                                .foregroundStyle(Theme.textSecondary)
                                            Image(systemName: expanded ? "chevron.down" : "chevron.right")
                                                .font(.system(size: 9, weight: .semibold))
                                                .foregroundStyle(Theme.textSecondary.opacity(0.7))
                                        }
                                        .padding(.vertical, 5)
                                        .contentShape(Rectangle())
                                    }
                                    .buttonStyle(.plain)
                                    if expanded { eventDetail(event) }
                                }
                            }
                        }
                    }
                }
                if !flow.tools.isEmpty {
                    section("Tools") {
                        HStack(spacing: 6) {
                            ForEach(flow.tools, id: \.self) { tool in
                                HStack(spacing: 6) {
                                    Image(systemName: toolGlyph(tool)).font(.system(size: 10))
                                    Text(toolDisplayName(tool)).font(.system(size: 11.5))
                                }
                                .foregroundStyle(Theme.textSecondary)
                                .padding(.horizontal, 10).padding(.vertical, 5)
                                .background(Theme.surface)
                                .clipShape(RoundedRectangle(cornerRadius: 7))
                                .overlay(RoundedRectangle(cornerRadius: 7).stroke(Theme.hairline, lineWidth: 1))
                            }
                        }
                    }
                }
                Spacer(minLength: 20)
            }
            .padding(16)
    }

    private var subline: String? {
        guard let job else { return nil }
        var parts = [cronSummary(job.cron)]
        if flow.running {
            parts.append("running now")
        } else if job.enabled, let next = job.nextRun {
            parts.append("next run \(relativeTime(next))")
        }
        return parts.joined(separator: " · ")
    }

    private func actions(_ job: SchedulerJob) -> some View {
        HStack {
            if sched.busy.contains(job.id) {
                ProgressView().controlSize(.small)
            } else {
                Button { sched.runNow(job) } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "play.fill").font(.system(size: 10))
                        Text("Run now").font(.system(size: 12.5, weight: .semibold))
                    }
                    .foregroundStyle(Color(red: 0.09, green: 0.09, blue: 0.10))
                    .padding(.horizontal, 14).padding(.vertical, 7)
                    .background(Theme.accent)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .disabled(job.gated != nil)
                .help(job.gated ?? "Fire this flow immediately")
            }
            Spacer()
            Text("Enabled").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            if liveControls {
                Toggle("", isOn: Binding(get: { job.enabled }, set: { sched.setEnabled(job, $0) }))
                    .toggleStyle(.switch).controlSize(.small).labelsHidden()
                    .tint(Theme.accent)
                    .disabled(sched.busy.contains(job.id) || job.envLocked || job.gated != nil)
                    .help(toggleHelp(job))
            } else {
                // Render-safe stand-in for screenshots (ImageRenderer can't draw the live switch).
                Text(job.enabled ? "On" : "Off").font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(job.enabled ? Theme.accent : Theme.textSecondary)
                    .padding(.horizontal, 9).padding(.vertical, 3)
                    .background((job.enabled ? Theme.accent : Theme.textSecondary).opacity(0.16))
                    .clipShape(Capsule())
            }
        }
        .padding(.top, 16)
    }

    private func toggleHelp(_ job: SchedulerJob) -> String {
        if let gated = job.gated { return "Off while unavailable: \(gated)" }
        if job.envLocked { return "Fixed by the server's environment. Edit its env vars to change." }
        return "Run this flow on its schedule"
    }

    private func schedule(_ job: SchedulerJob) -> some View {
        section("Schedule") {
            HStack(spacing: 8) {
                Text(cronSummary(job.cron)).font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.accent)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Theme.accent.opacity(0.12))
                    .clipShape(RoundedRectangle(cornerRadius: 7))
                    .overlay(RoundedRectangle(cornerRadius: 7)
                        .stroke(Theme.accent.opacity(0.5), lineWidth: 1))
                    .help(job.envLocked ? "Fixed by the server's environment. Edit its env vars to change." : "The current schedule")
                if !job.envLocked {
                    Button("Change") { onEditSchedule(job) }
                        .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Theme.textSecondary)
                        .help("Pick a new schedule")
                }
                Spacer()
            }
        }
    }

    private func lastRun(_ job: SchedulerJob) -> some View {
        section("Last run") {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .top, spacing: 7) {
                    Circle().fill(flowStatus(flow, job: job).dotColor).frame(width: 7, height: 7)
                        .padding(.top, 4)
                    Text(lastRunLine(job)).font(.system(size: 12))
                        .fixedSize(horizontal: false, vertical: true)
                }
                // Plain-English summary from the producer (the raw run record is never serialized here).
                if !job.lastRunDetail.isEmpty {
                    Text(job.lastRunDetail).font(.system(size: 11))
                        .foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                // Run warnings as plain-English bullet items.
                if let st = flow.pulseState, !st.warnings.isEmpty {
                    ForEach(st.warnings, id: \.self) { warning in
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: "exclamationmark.triangle").font(.system(size: 9))
                                .foregroundStyle(.orange).padding(.top, 2)
                            Text(warning).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                if let st = flow.pulseState, !st.gates.isEmpty {
                    Text(gatesLine(st)).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func lastRunLine(_ job: SchedulerJob) -> String {
        guard let last = job.lastRunAt else { return "never run" }
        let when = relativeTime(last)
        if job.lastRunOK == false { return "\(when) · failed" }
        if let st = flow.pulseState, !st.warnings.isEmpty { return "\(when) · ok with warnings" }
        return "\(when) · ok"
    }

    private func gatesLine(_ st: PulseStageState) -> String {
        let order = ["dedup": "Dedup", "freshness": "Freshness", "coherence": "Coherence",
                     "empty": "Empty", "interest_cap": "Interest cap"]
        let parts = ["dedup", "freshness", "coherence", "empty", "interest_cap"].compactMap { key -> String? in
            guard let n = st.gates[key] else { return nil }
            return "\(order[key] ?? key) \(n)"
        }
        return "Gate kills: " + parts.joined(separator: " · ")
    }

    /// The full event behind an activity row: its detail text plus the labeled fields
    /// (source, kind, when, tool, ref). This is where a structured payload lives, formatted.
    private func eventDetail(_ event: ActivityEvent) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            if !event.detail.isEmpty {
                Text(event.detail).font(.system(size: 11))
                    .foregroundStyle(Theme.textPrimary.opacity(0.85))
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            detailRow("Source", event.source)
            detailRow("Kind", event.kind.isEmpty ? "event" : event.kind)
            detailRow("When", event.ts.formatted(date: .abbreviated, time: .shortened))
            if let tool = event.tool, !tool.isEmpty { detailRow("Tool", tool) }
            if let ref = event.ref, !ref.isEmpty { detailRow("Ref", ref) }
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
        .padding(.bottom, 5)
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text(label).font(.system(size: 10, weight: .semibold)).tracking(0.4)
                .foregroundStyle(Theme.textSecondary).frame(width: 44, alignment: .leading)
            Text(value).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }

    private func section(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(title.uppercased())
                .font(.system(size: 10, weight: .semibold)).tracking(1.2)
                .foregroundStyle(Theme.textSecondary.opacity(0.75))
            content()
        }
        .padding(.top, 20)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
