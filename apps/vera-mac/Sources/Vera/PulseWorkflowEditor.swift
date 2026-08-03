import SwiftUI

struct PulseWorkflowNode: Identifiable, Hashable {
    var id: String
    var type: String
    var config: [String: String]

    var label: String {
        switch type {
        case "pulse.triage": return "Triage"
        case "pulse.gates": return "Gates"
        case "pulse.synthesis": return "Synthesis"
        case "pulse.claim_audit": return "Claim audit"
        case "pulse.cover_art": return "Cover art"
        case "pulse.visual_review": return "Visual review"
        case "pulse.cover_retry": return "One retry"
        case "pulse.inject": return "Publish"
        default: return type
        }
    }

    var icon: String {
        switch type {
        case "pulse.triage": return "globe"
        case "pulse.gates": return "line.3.horizontal.decrease.circle"
        case "pulse.synthesis": return "sparkles"
        case "pulse.claim_audit": return "checkmark.shield"
        case "pulse.cover_art": return "photo"
        case "pulse.visual_review": return "eye"
        case "pulse.cover_retry": return "arrow.clockwise"
        case "pulse.inject": return "arrow.down.to.line"
        default: return "square.stack.3d.up"
        }
    }

    var tint: String {
        switch type {
        case "pulse.gates", "pulse.cover_retry": return "orange"
        case "pulse.synthesis", "pulse.cover_art": return "purple"
        case "pulse.claim_audit", "pulse.visual_review": return "cyan"
        case "pulse.inject": return "green"
        default: return "accent"
        }
    }

    var jsonConfig: [String: Any] {
        if type == "pulse.visual_review" {
            return ["threshold": Double(config["threshold"] ?? "0.8") ?? 0.8]
        }
        if type == "pulse.cover_retry" {
            return ["max_attempts": Int(config["max_attempts"] ?? "1") ?? 1]
        }
        return config
    }
}

struct PulseWorkflowEdge: Hashable {
    var from: String
    var to: String
}

struct PulseWorkflowPoint: Hashable {
    var x: CGFloat
    var y: CGFloat

    static func parse(_ value: Any) -> PulseWorkflowPoint? {
        guard let point = value as? [String: Any],
              let x = point["x"] as? Double,
              let y = point["y"] as? Double else { return nil }
        return PulseWorkflowPoint(x: x, y: y)
    }

    var jsonObject: [String: Double] { ["x": x, "y": y] }
}

struct PulseWorkflowDefinition: Hashable {
    var id: String
    var nodes: [PulseWorkflowNode]
    var edges: [PulseWorkflowEdge]
    var positions: [String: PulseWorkflowPoint]

    var hasVisualLoop: Bool {
        nodes.contains { $0.type == "pulse.visual_review" }
    }

    func jsonObject() -> [String: Any] {
        ["id": id,
         "nodes": nodes.map { node in
             ["id": node.id, "type": node.type, "config": node.jsonConfig]
         },
         "edges": edges.map { ["from": $0.from, "to": $0.to] },
         "positions": positions.mapValues(\.jsonObject)]
    }

    static func parse(_ value: Any) -> PulseWorkflowDefinition? {
        guard let object = value as? [String: Any],
              let id = object["id"] as? String,
              let rawNodes = object["nodes"] as? [[String: Any]],
              let rawEdges = object["edges"] as? [[String: Any]] else { return nil }
        let nodes = rawNodes.compactMap { raw -> PulseWorkflowNode? in
            guard let id = raw["id"] as? String, let type = raw["type"] as? String else { return nil }
            let config = (raw["config"] as? [String: Any] ?? [:]).reduce(into: [String: String]()) { result, item in
                result[item.key] = String(describing: item.value)
            }
            return PulseWorkflowNode(id: id, type: type, config: config)
        }
        let edges = rawEdges.compactMap { raw -> PulseWorkflowEdge? in
            guard let from = raw["from"] as? String, let to = raw["to"] as? String else { return nil }
            return PulseWorkflowEdge(from: from, to: to)
        }
        let savedPositions = (object["positions"] as? [String: Any] ?? [:]).compactMapValues(PulseWorkflowPoint.parse)
        let positions = nodes.enumerated().reduce(into: savedPositions) { result, item in
            if result[item.element.id] == nil {
                result[item.element.id] = PulseWorkflowPoint(x: 110 + CGFloat(item.offset) * 165, y: 210)
            }
        }
        return nodes.isEmpty ? nil : PulseWorkflowDefinition(id: id, nodes: nodes, edges: edges, positions: positions)
    }

}

struct PulseWorkflowVersion: Hashable {
    var id: String
    var number: Int
    var state: String
    var definition: PulseWorkflowDefinition

    static func parse(_ value: Any) -> PulseWorkflowVersion? {
        guard let object = value as? [String: Any],
              let id = object["id"] as? String,
              let number = object["version"] as? Int,
              let state = object["state"] as? String,
              let definition = PulseWorkflowDefinition.parse(object["definition"] as Any) else { return nil }
        return PulseWorkflowVersion(id: id, number: number, state: state, definition: definition)
    }
}

@MainActor
struct PulseWorkflowClient {
    let base: URL

    private func request(path: String, method: String = "GET", body: [String: Any]? = nil) async -> PulseWorkflowVersion? {
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 8
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (200..<300).contains((response as? HTTPURLResponse)?.statusCode ?? 0),
              let object = try? JSONSerialization.jsonObject(with: data),
              let wrapped = object as? [String: Any] else { return nil }
        return PulseWorkflowVersion.parse(wrapped["workflow"] as Any)
    }

    func active() async -> PulseWorkflowVersion? {
        await request(path: "/agentic/workflows/pulse")
    }

    func createDraft() async -> PulseWorkflowVersion? {
        await request(path: "/agentic/workflows/pulse/drafts", method: "POST")
    }

    func save(_ version: PulseWorkflowVersion) async -> PulseWorkflowVersion? {
        await request(path: "/agentic/workflow-drafts/\(version.id)", method: "PUT", body: ["definition": version.definition.jsonObject()])
    }

    func promote(_ version: PulseWorkflowVersion) async -> PulseWorkflowVersion? {
        await request(path: "/agentic/workflow-drafts/\(version.id)/promote", method: "POST")
    }
}

@MainActor
final class PulseWorkflowStore: ObservableObject {
    enum Phase { case loading, unavailable, ready }
    @Published var phase: Phase = .loading
    @Published var active: PulseWorkflowVersion?
    @Published var draft: PulseWorkflowVersion?
    @Published var selectedNodeID: String?
    @Published var busy = false
    @Published var note: String?

    private var client: PulseWorkflowClient?

    var displayed: PulseWorkflowVersion? { draft ?? active }
    var isEditing: Bool { draft != nil }
    var changed: Bool { draft?.definition != active?.definition }

    func configure(base: URL?) {
        client = base.map { PulseWorkflowClient(base: $0) }
        if client == nil { phase = .unavailable }
    }

    func refresh() async {
        guard let client else { phase = .unavailable; return }
        guard let workflow = await client.active() else { phase = .unavailable; return }
        active = workflow
        if draft == nil { selectedNodeID = workflow.definition.nodes.first?.id }
        phase = .ready
    }

    func beginDraft() async {
        guard let client else { return }
        busy = true
        defer { busy = false }
        guard let workflow = await client.createDraft() else { note = "Couldn’t create a draft."; return }
        draft = workflow
        selectedNodeID = workflow.definition.nodes.first?.id
        note = "Draft ready. Changes are not live until you promote it."
    }

    func discardDraft() {
        draft = nil
        selectedNodeID = active?.definition.nodes.first?.id
        note = "Draft discarded."
    }

    func addVisualLoop() {
        guard var draft, !draft.definition.hasVisualLoop,
              let cover = draft.definition.nodes.firstIndex(where: { $0.type == "pulse.cover_art" }),
              let publish = draft.definition.nodes.firstIndex(where: { $0.type == "pulse.inject" }) else { return }
        let review = PulseWorkflowNode(id: "visual_review", type: "pulse.visual_review", config: ["threshold": "0.8"])
        let retry = PulseWorkflowNode(id: "cover_retry", type: "pulse.cover_retry", config: ["max_attempts": "1"])
        draft.definition.nodes.insert(review, at: cover + 1)
        draft.definition.nodes.insert(retry, at: publish + 2)
        draft.definition.edges = orderedEdges(for: draft.definition.nodes)
        draft.definition.positions = defaultPositions(for: draft.definition.nodes)
        self.draft = draft
        selectedNodeID = review.id
    }

    func removeVisualLoop() {
        guard var draft, draft.definition.hasVisualLoop else { return }
        draft.definition.nodes.removeAll { $0.type == "pulse.visual_review" || $0.type == "pulse.cover_retry" }
        draft.definition.edges = orderedEdges(for: draft.definition.nodes)
        draft.definition.positions = defaultPositions(for: draft.definition.nodes)
        self.draft = draft
        selectedNodeID = draft.definition.nodes.first?.id
    }

    func setStyle(_ style: String) {
        mutateSelected { node in node.config["style"] = style }
    }

    func setThreshold(_ threshold: Double) {
        mutateSelected { node in node.config["threshold"] = threshold.formatted(.number.precision(.fractionLength(1))) }
    }

    func setAttempts(_ attempts: Int) {
        mutateSelected { node in node.config["max_attempts"] = String(attempts) }
    }

    func moveNode(_ id: String, by translation: CGSize) {
        guard var draft, var point = draft.definition.positions[id] else { return }
        point.x = max(75, point.x + translation.width)
        point.y = max(75, point.y + translation.height)
        draft.definition.positions[id] = point
        self.draft = draft
    }

    func wireInSequence() {
        guard var draft else { return }
        draft.definition.edges = orderedEdges(for: draft.definition.nodes)
        self.draft = draft
        note = "Nodes are wired in their displayed sequence."
    }

    func save() async {
        guard let client, let draft else { return }
        busy = true
        defer { busy = false }
        guard let saved = await client.save(draft) else { note = "The server rejected this graph."; return }
        self.draft = saved
        note = "Draft saved. Promote it when you want Pulse to use it."
    }

    func promote() async {
        guard let client, let draft else { return }
        busy = true
        defer { busy = false }
        guard let saved = await client.save(draft) else { note = "The server rejected this graph."; return }
        guard let promoted = await client.promote(saved) else { note = "Couldn’t promote this draft."; return }
        active = promoted
        self.draft = nil
        note = "This workflow is active for future Pulse runs."
    }

    private func mutateSelected(_ change: (inout PulseWorkflowNode) -> Void) {
        guard var draft, let id = selectedNodeID,
              let index = draft.definition.nodes.firstIndex(where: { $0.id == id }) else { return }
        change(&draft.definition.nodes[index])
        self.draft = draft
    }

    private func orderedEdges(for nodes: [PulseWorkflowNode]) -> [PulseWorkflowEdge] {
        Array(zip(nodes, nodes.dropFirst())).map { PulseWorkflowEdge(from: $0.id, to: $1.id) }
    }

    private func defaultPositions(for nodes: [PulseWorkflowNode]) -> [String: PulseWorkflowPoint] {
        Dictionary(uniqueKeysWithValues: nodes.enumerated().map { item in
            (item.element.id, PulseWorkflowPoint(x: 110 + CGFloat(item.offset) * 165, y: 210))
        })
    }

    static func fixture() -> PulseWorkflowStore {
        let store = PulseWorkflowStore()
        let nodes = [
            PulseWorkflowNode(id: "triage", type: "pulse.triage", config: [:]),
            PulseWorkflowNode(id: "gates", type: "pulse.gates", config: [:]),
            PulseWorkflowNode(id: "synthesis", type: "pulse.synthesis", config: [:]),
            PulseWorkflowNode(id: "claim_audit", type: "pulse.claim_audit", config: [:]),
            PulseWorkflowNode(id: "cover_art", type: "pulse.cover_art", config: ["style": "editorial"]),
            PulseWorkflowNode(id: "visual_review", type: "pulse.visual_review", config: ["threshold": "0.8"]),
            PulseWorkflowNode(id: "cover_retry", type: "pulse.cover_retry", config: ["max_attempts": "1"]),
            PulseWorkflowNode(id: "inject", type: "pulse.inject", config: [:])
        ]
        let definition = PulseWorkflowDefinition(id: "pulse", nodes: nodes,
                                                 edges: Array(zip(nodes, nodes.dropFirst())).map { PulseWorkflowEdge(from: $0.id, to: $1.id) },
                                                 positions: Dictionary(uniqueKeysWithValues: nodes.enumerated().map { item in
                                                     (item.element.id, PulseWorkflowPoint(x: 95 + CGFloat(item.offset % 4) * 175, y: item.offset < 4 ? 150 : 315))
                                                 }))
        store.active = PulseWorkflowVersion(id: "fixture", number: 2, state: "active", definition: definition)
        store.selectedNodeID = "visual_review"
        store.phase = .ready
        return store
    }
}

struct PulseWorkflowEditor: View {
    @ObservedObject var store: PulseWorkflowStore

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider().overlay(Theme.hairline)
            switch store.phase {
            case .loading:
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            case .unavailable:
                CanvasStatusCard(icon: "exclamationmark.triangle", title: "Workflow unavailable", note: "Connect vera-api to edit Pulse.")
            case .ready:
                HStack(spacing: 0) {
                    palette
                    Divider().overlay(Theme.hairline)
                    canvas
                    Divider().overlay(Theme.hairline)
                    inspector
                }
            }
        }
        .background(Theme.bg)
    }

    private var toolbar: some View {
        HStack(spacing: 10) {
            Text("Pulse workflow").font(.system(size: 20, weight: .bold))
            if let workflow = store.displayed {
                Text(store.isEditing ? "Draft v\(workflow.number)" : "Active v\(workflow.number)")
                    .font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 4).background(Theme.surface).clipShape(Capsule())
            }
            Spacer()
            if store.isEditing {
                Button("Discard") { store.discardDraft() }.buttonStyle(.plain).foregroundStyle(Theme.textSecondary)
                Button("Save draft") { Task { await store.save() } }.buttonStyle(.bordered)
                Button("Promote") { Task { await store.promote() } }.buttonStyle(.borderedProminent)
            } else {
                Button("Edit workflow") { Task { await store.beginDraft() } }.buttonStyle(.borderedProminent)
            }
        }
        .padding(.horizontal, 28).padding(.vertical, 14)
    }

    private var palette: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Nodes").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
            if store.isEditing {
                Button { store.addVisualLoop() } label: { Label("Add visual review", systemImage: "eye") }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.accent)
                Button { store.removeVisualLoop() } label: { Label("Remove visual review", systemImage: "minus.circle") }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
                Button { store.wireInSequence() } label: { Label("Wire sequence", systemImage: "point.3.connected.trianglepath.dotted") }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
            } else {
                Label("Visual review is available in a draft", systemImage: "eye")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                Text("Create a draft to add or configure nodes.").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
        }
        .padding(16).frame(width: 190, alignment: .topLeading)
    }

    private var canvas: some View {
        ScrollView([.horizontal, .vertical]) {
            if let workflow = store.displayed {
                ZStack(alignment: .topLeading) {
                    Canvas { context, _ in
                        for edge in workflow.definition.edges {
                            guard let from = workflow.definition.positions[edge.from],
                                  let to = workflow.definition.positions[edge.to] else { continue }
                            context.stroke(edgePath(CGPoint(x: from.x + 64, y: from.y),
                                                    CGPoint(x: to.x - 64, y: to.y)),
                                           with: .color(Theme.textSecondary.opacity(0.55)), lineWidth: 1.5)
                        }
                    }
                    ForEach(workflow.definition.nodes) { node in
                        if let point = workflow.definition.positions[node.id] {
                            WorkflowNodeCard(node: node, selected: store.selectedNodeID == node.id)
                                .position(x: point.x, y: point.y)
                                .onTapGesture { store.selectedNodeID = node.id }
                                .gesture(store.isEditing ? DragGesture().onEnded { value in store.moveNode(node.id, by: value.translation) } : nil)
                        }
                    }
                }
                .frame(width: max(900, (workflow.definition.positions.values.map(\.x).max() ?? 0) + 130), height: 460)
            }
        }
        .background(DotGrid())
    }

    @ViewBuilder private var inspector: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let selected = store.displayed?.definition.nodes.first(where: { $0.id == store.selectedNodeID }) {
                Text(selected.label).font(.system(size: 15, weight: .semibold))
                if store.isEditing { controls(for: selected) }
                else { Text("Create a draft to edit this node.").font(.system(size: 11)).foregroundStyle(Theme.textSecondary) }
            } else {
                Text("Select a node").font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.textSecondary)
            }
            if let note = store.note { Text(note).font(.system(size: 11)).foregroundStyle(Theme.textSecondary).fixedSize(horizontal: false, vertical: true) }
            Spacer()
        }
        .padding(16).frame(width: 250, alignment: .topLeading)
    }

    @ViewBuilder private func controls(for node: PulseWorkflowNode) -> some View {
        switch node.type {
        case "pulse.cover_art":
            Picker("Style", selection: Binding(get: { node.config["style"] ?? "rotating" }, set: { value in store.setStyle(value) })) {
                Text("Rotating").tag("rotating")
                Text("Photographic").tag("photographic")
                Text("Illustrated").tag("illustrated")
                Text("Editorial").tag("editorial")
            }.pickerStyle(.menu)
        case "pulse.visual_review":
            let threshold = Double(node.config["threshold"] ?? "0.8") ?? 0.8
            VStack(alignment: .leading, spacing: 5) { Text("Review threshold \(threshold.formatted(.number.precision(.fractionLength(1))))").font(.system(size: 11)); Slider(value: Binding(get: { threshold }, set: { value in store.setThreshold(value) }), in: 0...1, step: 0.1) }
        case "pulse.cover_retry":
            let attempts = Int(node.config["max_attempts"] ?? "1") ?? 1
            Toggle("One retry", isOn: Binding(get: { attempts == 1 }, set: { store.setAttempts($0 ? 1 : 0) })).toggleStyle(.switch)
        default:
            Text("This approved node has no editable settings.").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        }
    }
}

struct WorkflowNodeCard: View {
    let node: PulseWorkflowNode
    var selected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Image(systemName: node.icon).font(.system(size: 15, weight: .medium)).foregroundStyle(graphTint(node.tint))
            Text(node.label).font(.system(size: 13, weight: .semibold)).lineLimit(2)
        }
        .padding(13).frame(width: 126, height: 86, alignment: .topLeading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(selected ? Theme.accent : Theme.hairline, lineWidth: selected ? 1.5 : 1))
    }
}

struct PulseWorkflowEditorShot: View {
    private let workflow = PulseWorkflowStore.fixture().active!

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Pulse workflow").font(.system(size: 20, weight: .bold))
                Text("Active v2").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 4).background(Theme.surface).clipShape(Capsule())
                Spacer()
                Button("Edit workflow") {}.buttonStyle(.borderedProminent)
            }.padding(.horizontal, 28).padding(.vertical, 14)
            Divider().overlay(Theme.hairline)
            HStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Nodes").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    Text("Create a draft to add or configure nodes.").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    Spacer()
                }.padding(16).frame(width: 190, alignment: .topLeading)
                Divider().overlay(Theme.hairline)
                ZStack {
                    DotGrid()
                    VStack(alignment: .leading, spacing: 18) {
                        Text("Approved Pulse nodes").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                        HStack(spacing: 0) {
                            ForEach(Array(workflow.definition.nodes.enumerated()), id: \.element.id) { index, node in
                                if index > 0 { Image(systemName: "arrow.right").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary).frame(width: 34) }
                                WorkflowNodeCard(node: node, selected: node.id == "visual_review")
                            }
                        }
                    }.padding(32).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                }
                Divider().overlay(Theme.hairline)
                VStack(alignment: .leading, spacing: 14) {
                    Text("Visual review").font(.system(size: 15, weight: .semibold))
                    Text("Create a draft to edit this node.").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    Spacer()
                }.padding(16).frame(width: 250, alignment: .topLeading)
            }
        }.background(Theme.bg)
    }
}
