import SwiftUI

/// Live state for the canvas graph manifest (`GET /agentic/graph`).
@MainActor
final class GraphStore: ObservableObject {
    enum Phase { case loading, unconfigured, unreachable, unsupported, ready }
    @Published var phase: Phase = .loading
    @Published var graph: AgenticGraph?

    private var client: GraphClient?
    var baseDescription: String { client?.base.absoluteString ?? "vera-api" }

    func configure(base: URL?) {
        client = base.map { GraphClient(base: $0) }
        if client == nil { phase = .unconfigured }
    }

    func refresh() async {
        guard let client else { phase = .unconfigured; return }
        switch await client.fetch() {
        case .unreachable: phase = .unreachable
        case .unsupported: phase = .unsupported
        case .ok(let g):
            graph = g
            phase = .ready
        }
    }
}

/// A recent autonomous event traveling its edge — the breathing layer's unit of motion.
struct CanvasPulse: Identifiable, Equatable {
    let id: String
    let flowID: String
    let surfaceID: String
    let startedAt: Date
    static let duration: TimeInterval = 2.4
}

/// How one flow node reads at a glance, merged from the live job row and the manifest.
enum FlowStatus {
    case never, ok, warn, fail, off

    var dotColor: Color {
        switch self {
        case .never: return Theme.textSecondary.opacity(0.5)
        case .ok: return Color(red: 0.36, green: 0.78, blue: 0.50)
        case .warn: return Color(red: 0.90, green: 0.62, blue: 0.30)
        case .fail: return Color(red: 0.92, green: 0.42, blue: 0.38)
        case .off: return Theme.textSecondary.opacity(0.5)
        }
    }
}

func flowStatus(_ flow: GraphFlow, job: SchedulerJob?) -> FlowStatus {
    guard let job else { return .never }
    if !job.enabled { return .off }
    guard let ok = job.lastRunOK else { return .never }
    if !ok { return .fail }
    if let st = flow.pulseState, !st.warnings.isEmpty { return .warn }
    return .ok
}

// MARK: - Geometry

/// Deterministic organism-map layout: flow nodes flank the center spine of surfaces
/// they feed. Pure geometry from the manifest, so the canvas and the screenshot
/// renderer share one source of truth.
struct OrganismLayout {
    static let flowSize = CGSize(width: 196, height: 92)
    static let heartbeatSize = CGSize(width: 208, height: 110)
    static let surfaceSize = CGSize(width: 188, height: 66)
    static let groupLabelHeight: CGFloat = 16
    static let nodeGap: CGFloat = 10
    static let groupGap: CGFloat = 18

    var flowRects: [String: CGRect] = [:]
    var surfaceRects: [String: CGRect] = [:]
    var groupLabels: [(text: String, origin: CGPoint)] = []
    var size: CGSize = .zero

    /// Side assignment per flow id (true = left of the spine).
    var leftSide: Set<String> = []

    init(graph: AgenticGraph, viewport: CGSize) {
        // Group flows preserving manifest order; the heartbeat anchors the right side.
        var groups: [(name: String, flows: [GraphFlow])] = []
        for flow in graph.flows {
            if let i = groups.firstIndex(where: { $0.name == flow.group }) {
                groups[i].flows.append(flow)
            } else {
                groups.append((flow.group, [flow]))
            }
        }
        var left: [(name: String, flows: [GraphFlow])] = []
        var right: [(name: String, flows: [GraphFlow])] = []
        func stackHeight(_ side: [(name: String, flows: [GraphFlow])]) -> CGFloat {
            side.reduce(0) { acc, g in
                let labeled = !(g.flows.count == 1 && g.flows[0].kind == "heartbeat")
                let nodes = g.flows.reduce(CGFloat(0)) { a, f in
                    a + (f.kind == "heartbeat" ? Self.heartbeatSize.height : Self.flowSize.height) + Self.nodeGap
                }
                return acc + (labeled ? Self.groupLabelHeight : 0) + nodes + Self.groupGap
            }
        }
        // The heartbeat anchors the top of the right column; everything else balances.
        if let i = groups.firstIndex(where: { $0.flows.contains { $0.kind == "heartbeat" } }) {
            right.append(groups.remove(at: i))
        }
        for group in groups {
            if stackHeight(left) <= stackHeight(right) {
                left.append(group)
            } else {
                right.append(group)
            }
        }

        let topPad: CGFloat = 24
        let contentH = max(max(stackHeight(left), stackHeight(right)) + topPad, viewport.height)
        let leftX: CGFloat = 28
        let width = max(viewport.width, 960)
        let centerX = max(leftX + Self.flowSize.width + 96, (width - Self.surfaceSize.width) / 2)
        let rightX = max(centerX + Self.surfaceSize.width + 96, width - Self.heartbeatSize.width - 28)
        size = CGSize(width: max(width, rightX + Self.heartbeatSize.width + 28), height: contentH)

        func place(_ side: [(name: String, flows: [GraphFlow])], x: CGFloat, isLeft: Bool) {
            var y = topPad
            for group in side {
                let labeled = !(group.flows.count == 1 && group.flows[0].kind == "heartbeat")
                if labeled {
                    groupLabels.append((group.name, CGPoint(x: x + 2, y: y)))
                    y += Self.groupLabelHeight
                }
                for flow in group.flows {
                    let s = flow.kind == "heartbeat" ? Self.heartbeatSize : Self.flowSize
                    flowRects[flow.id] = CGRect(origin: CGPoint(x: x, y: y), size: s)
                    if isLeft { leftSide.insert(flow.id) }
                    y += s.height + Self.nodeGap
                }
                y += Self.groupGap
            }
        }
        place(left, x: leftX, isLeft: true)
        place(right, x: rightX, isLeft: false)

        let n = graph.surfaces.count
        let surfH = Self.surfaceSize.height
        let spacing = n > 1 ? max(surfH + 60, (contentH - 110 - surfH) / CGFloat(n - 1)) : 0
        for (i, s) in graph.surfaces.enumerated() {
            surfaceRects[s.id] = CGRect(x: centerX, y: 64 + CGFloat(i) * spacing,
                                        width: Self.surfaceSize.width, height: surfH)
        }
    }

    /// Edge endpoints: a flow's spine-facing port to the surface's near port.
    func edge(from flowID: String, to surfaceID: String) -> (CGPoint, CGPoint)? {
        guard let f = flowRects[flowID], let s = surfaceRects[surfaceID] else { return nil }
        if leftSide.contains(flowID) {
            return (CGPoint(x: f.maxX, y: f.midY), CGPoint(x: s.minX, y: s.midY))
        }
        return (CGPoint(x: f.minX, y: f.midY), CGPoint(x: s.maxX, y: s.midY))
    }
}

/// Cubic edge between two ports, bowing horizontally toward the spine.
func edgePath(_ a: CGPoint, _ b: CGPoint) -> Path {
    var p = Path()
    let dx = (b.x - a.x) * 0.42
    p.move(to: a)
    p.addCurve(to: b,
               control1: CGPoint(x: a.x + dx, y: a.y),
               control2: CGPoint(x: b.x - dx, y: b.y))
    return p
}

/// Point along that cubic at parameter t (for the traveling event pulse).
func edgePoint(_ a: CGPoint, _ b: CGPoint, t: CGFloat) -> CGPoint {
    let dx = (b.x - a.x) * 0.42
    let c1 = CGPoint(x: a.x + dx, y: a.y)
    let c2 = CGPoint(x: b.x - dx, y: b.y)
    let u = 1 - t
    func blend(_ p0: CGFloat, _ p1: CGFloat, _ p2: CGFloat, _ p3: CGFloat) -> CGFloat {
        u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3
    }
    return CGPoint(x: blend(a.x, c1.x, c2.x, b.x), y: blend(a.y, c1.y, c2.y, b.y))
}

// MARK: - The organism map

/// The top-level living map: every flow, the spine of surfaces, edges, and the
/// breathing layer. Pure render over its inputs so the screenshot path reuses it.
struct OrganismMap: View {
    let graph: AgenticGraph
    let jobs: [String: SchedulerJob]
    let size: CGSize
    var selected: String?
    var pulses: [CanvasPulse] = []
    var animated: Bool = true
    var onSelect: (String) -> Void = { _ in }
    var onDrill: (String) -> Void = { _ in }

    private var layout: OrganismLayout {
        OrganismLayout(graph: graph, viewport: size)
    }

    /// With a selection active, everything outside its neighborhood recedes.
    private func dimmed(_ id: String, neighbors: Set<String>) -> Bool {
        guard let selected else { return false }
        return id != selected && !neighbors.contains(id)
    }

    var body: some View {
        let layout = self.layout
        let neighbors: Set<String> = selected.flatMap { graph.flow($0).map { Set($0.feeds) } } ?? []
        ZStack(alignment: .topLeading) {
            DotGrid()
            edgeLayer(layout)
            ForEach(layout.groupLabels, id: \.text) { label in
                Text(label.text.uppercased())
                    .font(.system(size: 10, weight: .semibold)).tracking(1.2)
                    .foregroundStyle(Theme.textSecondary.opacity(0.65))
                    .position(x: label.origin.x + 40, y: label.origin.y + 6)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
            ForEach(graph.surfaces) { surface in
                if let rect = layout.surfaceRects[surface.id] {
                    SurfaceNode(surface: surface)
                        .opacity(selected != nil && !neighbors.contains(surface.id) ? 0.45 : 1)
                        .frame(width: rect.width, height: rect.height)
                        .position(x: rect.midX, y: rect.midY)
                }
            }
            ForEach(graph.flows) { flow in
                if let rect = layout.flowRects[flow.id] {
                    FlowNode(flow: flow, job: jobs[flow.id],
                             isSelected: selected == flow.id, animated: animated)
                        .opacity(dimmed(flow.id, neighbors: neighbors) ? 0.45 : 1)
                        .frame(width: rect.width, height: rect.height)
                        .position(x: rect.midX, y: rect.midY)
                        .onTapGesture(count: 2) { if !flow.stages.isEmpty { onDrill(flow.id) } }
                        .onTapGesture { onSelect(flow.id) }
                }
            }
        }
        .frame(width: layout.size.width, height: layout.size.height, alignment: .topLeading)
    }

    @ViewBuilder
    private func edgeLayer(_ layout: OrganismLayout) -> some View {
        let edges: [(flow: GraphFlow, surface: String, a: CGPoint, b: CGPoint)] =
            graph.flows.flatMap { flow in
                flow.feeds.compactMap { sid in
                    layout.edge(from: flow.id, to: sid).map { (flow, sid, $0.0, $0.1) }
                }
            }
        TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: !animated || pulses.isEmpty)) { context in
            Canvas { ctx, _ in
                let now = context.date
                for e in edges {
                    let isSelected = selected == e.flow.id
                    let hasPulse = pulses.contains { $0.flowID == e.flow.id && $0.surfaceID == e.surface }
                    let color: Color = (isSelected || hasPulse)
                        ? Theme.accent.opacity(0.42)
                        : .white.opacity(0.10)
                    ctx.stroke(edgePath(e.a, e.b), with: .color(color), lineWidth: 1.5)
                }
                for pulse in pulses {
                    let t = CGFloat(now.timeIntervalSince(pulse.startedAt) / CanvasPulse.duration)
                    guard t >= 0, t <= 1,
                          let flow = graph.flow(pulse.flowID),
                          let (a, b) = layout.edge(from: pulse.flowID, to: pulse.surfaceID) else { continue }
                    let pt = edgePoint(a, b, t: t)
                    let fade = t < 0.15 ? t / 0.15 : (t > 0.85 ? (1 - t) / 0.15 : 1)
                    ctx.fill(Path(ellipseIn: CGRect(x: pt.x - 6, y: pt.y - 6, width: 12, height: 12)),
                             with: .color(Theme.accent.opacity(0.22 * fade)))
                    ctx.fill(Path(ellipseIn: CGRect(x: pt.x - 2.6, y: pt.y - 2.6, width: 5.2, height: 5.2)),
                             with: .color(Color(red: 0.56, green: 0.68, blue: 0.97).opacity(fade)))
                    _ = flow
                }
            }
        }
    }
}

/// The faint dot field that signals "this is a canvas" (and reserves the editor lane's
/// spatial language) without adding chrome.
struct DotGrid: View {
    var body: some View {
        Canvas { ctx, size in
            let step: CGFloat = 24
            var y: CGFloat = step / 2
            while y < size.height {
                var x: CGFloat = step / 2
                while x < size.width {
                    ctx.fill(Path(ellipseIn: CGRect(x: x, y: y, width: 2, height: 2)),
                             with: .color(.white.opacity(0.05)))
                    x += step
                }
                y += step
            }
        }
    }
}

// MARK: - Nodes

/// One flow's face: capability-tinted icon, label, status dot, plain-English schedule,
/// one-phrase state, next-fire countdown, tool badge, and the reserved editor ports.
struct FlowNode: View {
    let flow: GraphFlow
    let job: SchedulerJob?
    var isSelected: Bool = false
    var animated: Bool = true
    @State private var breathing = false

    private var status: FlowStatus { flowStatus(flow, job: job) }

    private var stateLine: (text: String, color: Color)? {
        guard let job else { return nil }
        if flow.running { return ("running now", Theme.accent) }
        if !job.enabled { return ("off", Theme.textSecondary) }
        guard let last = job.lastRunAt else { return ("never run", Theme.textSecondary) }
        let ago = relativeTime(last)
        switch status {
        case .fail: return ("failed · \(ago)", FlowStatus.fail.dotColor)
        case .warn:
            if let st = flow.pulseState { return ("\(st.injected) cards · \(ago)", FlowStatus.warn.dotColor) }
            return ("ok · \(ago)", FlowStatus.warn.dotColor)
        default: return ("ok · \(ago)", Theme.textSecondary)
        }
    }

    private var borderColor: Color {
        if isSelected { return Theme.accent.opacity(0.85) }
        if flow.running { return Theme.accent.opacity(0.55) }
        switch status {
        case .warn: return FlowStatus.warn.dotColor.opacity(0.42)
        case .fail: return FlowStatus.fail.dotColor.opacity(0.45)
        default: return Theme.hairline
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 6)
                    .fill(graphTint(flow.tint).opacity(0.14))
                    .frame(width: 24, height: 24)
                    .overlay(Image(systemName: flow.icon)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(graphTint(flow.tint)))
                Text(flow.label).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Spacer(minLength: 4)
                Circle().fill(flow.running ? Theme.accent : status.dotColor)
                    .frame(width: 7, height: 7)
                    .shadow(color: flow.running ? Theme.accent.opacity(0.8) : .clear, radius: 3)
            }
            if let job {
                Text(cronSummary(job.cron)).font(.system(size: 11))
                    .foregroundStyle(Theme.textSecondary).lineLimit(1)
                    .padding(.top, 7)
            }
            HStack(spacing: 6) {
                if let line = stateLine {
                    Text(line.text).font(.system(size: 10.5)).foregroundStyle(line.color).lineLimit(1)
                }
                Spacer(minLength: 4)
                if let tool = flow.tools.first {
                    ToolBadge(tool: tool, extra: flow.tools.count - 1)
                }
                if let job, job.enabled, let next = job.nextRun, !flow.running {
                    Text(relativeTime(next)).font(.system(size: 10.5))
                        .foregroundStyle(Theme.textSecondary).lineLimit(1)
                }
            }
            .padding(.top, 8)
            if flow.kind == "heartbeat" {
                Text(branchSummary).font(.system(size: 10))
                    .foregroundStyle(Theme.textSecondary).lineLimit(1)
                    .padding(.top, 8)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12)
            .stroke(borderColor, lineWidth: isSelected ? 1.5 : 1))
        .overlay(alignment: .leading) { PortDot().offset(x: -3.5) }
        .overlay(alignment: .trailing) { PortDot().offset(x: 3.5) }
        .shadow(color: glowColor, radius: flow.running ? (breathing ? 16 : 9) : 0)
        .opacity(status == .off ? 0.55 : 1)
        .onAppear {
            guard animated, flow.running else { return }
            withAnimation(.easeInOut(duration: 0.85).repeatForever(autoreverses: true)) {
                breathing = true
            }
        }
    }

    private var glowColor: Color {
        guard flow.running else { return .clear }
        if !animated { return Theme.accent.opacity(0.30) }
        return Theme.accent.opacity(breathing ? 0.45 : 0.15)
    }

    private var branchSummary: String {
        let n = flow.stages.count
        if flow.running, let live = flow.branchState.max(by: { $0.value.ts < $1.value.ts }) {
            let label = flow.stages.first { $0.id == live.key }?.label.lowercased() ?? live.key
            return "\(n) branches · \(label) just ran"
        }
        return "\(n) branches"
    }
}

/// One surface node on the spine — quieter than flows (sidebar tone), with a live stat.
struct SurfaceNode: View {
    let surface: GraphSurface

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 6)
                    .fill(Theme.accent.opacity(0.13))
                    .frame(width: 24, height: 24)
                    .overlay(Image(systemName: surface.icon)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Theme.accent))
                Text(surface.label).font(.system(size: 13, weight: .semibold))
                Spacer(minLength: 0)
            }
            if let stat = surface.stat {
                Text(stat).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    .padding(.leading, 32)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.sidebar)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
        .overlay(alignment: .leading) { PortDot().offset(x: -3.5) }
        .overlay(alignment: .trailing) { PortDot().offset(x: 3.5) }
    }
}

/// Connection-point affordance — the visual reservation for the future editor lane.
struct PortDot: View {
    var body: some View {
        Circle().fill(Theme.bg)
            .overlay(Circle().stroke(.white.opacity(0.22), lineWidth: 1))
            .frame(width: 7, height: 7)
    }
}

/// Subtle tool/plugin attribution on a node face; hover names the tool.
struct ToolBadge: View {
    let tool: String
    var extra: Int = 0

    var body: some View {
        RoundedRectangle(cornerRadius: 4)
            .fill(.white.opacity(0.06))
            .frame(width: 16, height: 16)
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(Theme.hairline, lineWidth: 1))
            .overlay(Image(systemName: toolGlyph(tool))
                .font(.system(size: 8.5)).foregroundStyle(Theme.textSecondary))
            .help(extra > 0 ? "\(toolDisplayName(tool)) and \(extra) more" : toolDisplayName(tool))
    }
}
