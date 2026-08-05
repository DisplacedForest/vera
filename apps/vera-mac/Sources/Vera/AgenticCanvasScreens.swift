import SwiftUI

struct AgenticCanvasView: View {
    @ObservedObject var graphStore: GraphStore
    @ObservedObject var sched: SchedulerStore
    @ObservedObject var activity: ActivityStore
    @ObservedObject var workflowEditor: WorkflowEditorStore
    @Binding var drilled: String?

    @State private var pulses: [CanvasPulse] = []
    @State private var eventBaseline: Date?

    private var jobsByID: [String: SchedulerJob] {
        Dictionary(uniqueKeysWithValues: sched.jobs.map { ($0.id, $0) })
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            if case .ready = graphStore.phase, !sched.masterEnabled, sched.phase == .ready {
                pausedStrip
            }
            content
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onChange(of: activity.events) { _, events in spawnPulses(from: events) }
    }

    // MARK: header

    @ViewBuilder
    private var header: some View {
        if drilled == nil {
            HStack(spacing: 10) {
                Text("Agentic").font(.system(size: 22, weight: .bold))
                InfoTip(text: "Everything Vera runs on her own, as one living system. Click a flow to open its workflow.", size: 13)
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
                if let id = drilled, graph.flow(id) != nil {
                    WorkflowEditor(store: workflowEditor)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .overlay(Rectangle().fill(Theme.hairline).frame(height: 1), alignment: .top)
                        .task(id: id) { await workflowEditor.select(id) }
                } else {
                    GeometryReader { geo in
                        ScrollView([.horizontal, .vertical]) {
                            OrganismMap(graph: graph, jobs: jobsByID, size: geo.size,
                                        pulses: pulses,
                                        onSelect: { drilled = $0 },
                                        onDrill: { drilled = $0 })
                        }
                    }
                    .overlay(Rectangle().fill(Theme.hairline).frame(height: 1), alignment: .top)
                }
            }
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
        return []
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
