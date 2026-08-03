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

struct PulseWorkflowNodeTemplate: Identifiable, Hashable {
    let id: String
    let type: String
    let label: String
    let group: String
    let detail: String
    let icon: String
    let tint: String
    let required: Bool
    let config: [String: String]

    func node() -> PulseWorkflowNode {
        PulseWorkflowNode(id: id, type: type, config: config)
    }

    static let all = [
        PulseWorkflowNodeTemplate(id: "triage", type: "pulse.triage", label: "Triage", group: "Research", detail: "Collect and rank signals", icon: "globe", tint: "accent", required: true, config: [:]),
        PulseWorkflowNodeTemplate(id: "gates", type: "pulse.gates", label: "Gates", group: "Research", detail: "Filter weak candidates", icon: "line.3.horizontal.decrease.circle", tint: "orange", required: true, config: [:]),
        PulseWorkflowNodeTemplate(id: "synthesis", type: "pulse.synthesis", label: "Synthesis", group: "Writing", detail: "Build the daily brief", icon: "sparkles", tint: "purple", required: true, config: [:]),
        PulseWorkflowNodeTemplate(id: "claim_audit", type: "pulse.claim_audit", label: "Claim audit", group: "Writing", detail: "Check sourced claims", icon: "checkmark.shield", tint: "cyan", required: true, config: [:]),
        PulseWorkflowNodeTemplate(id: "cover_art", type: "pulse.cover_art", label: "Cover art", group: "Image", detail: "Generate story artwork", icon: "photo", tint: "purple", required: true, config: ["style": "rotating"]),
        PulseWorkflowNodeTemplate(id: "visual_review", type: "pulse.visual_review", label: "Visual review", group: "Image", detail: "Score the generated image", icon: "eye", tint: "cyan", required: false, config: ["threshold": "0.8"]),
        PulseWorkflowNodeTemplate(id: "cover_retry", type: "pulse.cover_retry", label: "One retry", group: "Image", detail: "Retry a rejected image", icon: "arrow.clockwise", tint: "orange", required: false, config: ["max_attempts": "1"]),
        PulseWorkflowNodeTemplate(id: "inject", type: "pulse.inject", label: "Publish", group: "Output", detail: "Send approved cards", icon: "arrow.down.to.line", tint: "green", required: true, config: [:])
    ]

    static func template(for type: String) -> PulseWorkflowNodeTemplate? {
        all.first { $0.type == type }
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
        hasVisualReview && hasCoverRetry
    }

    var hasVisualReview: Bool {
        nodes.contains { $0.type == "pulse.visual_review" }
    }

    var hasCoverRetry: Bool {
        nodes.contains { $0.type == "pulse.cover_retry" }
    }

    var hasVisualNodes: Bool {
        hasVisualReview || hasCoverRetry
    }

    var validationMessage: String? {
        let types = nodes.map(\.type)
        let counts = Dictionary(grouping: types, by: { $0 }).mapValues(\.count)
        if counts.values.contains(where: { $0 > 1 }) {
            return "Pulse supports one instance of each node."
        }
        let missing = PulseWorkflowNodeTemplate.all.filter { $0.required && !types.contains($0.type) }
        if let node = missing.first {
            return "Add the required \(node.label) node."
        }
        if hasVisualReview != hasCoverRetry {
            return hasVisualReview ? "Add One retry to complete the visual path." : "Add Visual review to complete the visual path."
        }
        if edges != expectedEdges {
            return "Connect every node in the approved Pulse order."
        }
        return nil
    }

    var expectedEdges: [PulseWorkflowEdge] {
        let orderedTypes = PulseWorkflowNodeTemplate.all.filter { $0.required || hasVisualLoop }.map(\.type)
        let orderedNodes = orderedTypes.compactMap { type in nodes.first { $0.type == type } }
        return Array(zip(orderedNodes, orderedNodes.dropFirst())).map { PulseWorkflowEdge(from: $0.id, to: $1.id) }
    }

    mutating func normalizeEdgeOrder() {
        let order = Dictionary(uniqueKeysWithValues: nodes.enumerated().map { ($0.element.id, $0.offset) })
        edges.sort {
            let left = order[$0.from] ?? Int.max
            let right = order[$1.from] ?? Int.max
            return left == right ? (order[$0.to] ?? Int.max) < (order[$1.to] ?? Int.max) : left < right
        }
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
    @Published var connectionSourceID: String?
    @Published var busy = false
    @Published var note: String?

    private var client: PulseWorkflowClient?

    var displayed: PulseWorkflowVersion? { draft ?? active }
    var isEditing: Bool { draft != nil }
    var changed: Bool { draft?.definition != active?.definition }
    var validationMessage: String? { displayed?.definition.validationMessage }
    var canSave: Bool { isEditing && changed && validationMessage == nil && !busy }
    var canPromote: Bool { isEditing && validationMessage == nil && !busy }

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
        connectionSourceID = nil
        note = "Draft discarded."
    }

    func placeNode(_ type: String, at point: CGPoint) async {
        if !isEditing { await beginDraft() }
        placeNodeInDraft(type, at: point)
    }

    func placeNodeInDraft(_ type: String, at point: CGPoint) {
        guard var draft, let template = PulseWorkflowNodeTemplate.template(for: type) else { return }
        let position = PulseWorkflowPoint(x: max(90, point.x), y: max(70, point.y))
        if let existing = draft.definition.nodes.first(where: { $0.type == type }) {
            draft.definition.positions[existing.id] = position
            self.draft = draft
            selectedNodeID = existing.id
            note = "\(existing.label) moved."
            return
        }
        let canonicalTypes = PulseWorkflowNodeTemplate.all.map(\.type)
        let index = draft.definition.nodes.firstIndex { node in
            guard let nodeOrder = canonicalTypes.firstIndex(of: node.type),
                  let newOrder = canonicalTypes.firstIndex(of: type) else { return false }
            return nodeOrder > newOrder
        } ?? draft.definition.nodes.endIndex
        let node = template.node()
        draft.definition.nodes.insert(node, at: index)
        draft.definition.positions[node.id] = position
        if type == "pulse.visual_review" || type == "pulse.cover_retry" {
            draft.definition.edges.removeAll { edge in
                let source = draft.definition.nodes.first { $0.id == edge.from }?.type
                let target = draft.definition.nodes.first { $0.id == edge.to }?.type
                return source == "pulse.cover_art" && target == "pulse.inject"
            }
        }
        self.draft = draft
        selectedNodeID = node.id
        note = "\(node.label) added. Connect it to complete the workflow."
    }

    func removeSelectedNode() {
        guard var draft, let id = selectedNodeID,
              let node = draft.definition.nodes.first(where: { $0.id == id }),
              PulseWorkflowNodeTemplate.template(for: node.type)?.required == false else { return }
        draft.definition.nodes.removeAll { $0.id == id }
        draft.definition.edges.removeAll { $0.from == id || $0.to == id }
        draft.definition.positions[id] = nil
        if !draft.definition.hasVisualNodes,
           let cover = draft.definition.nodes.first(where: { $0.type == "pulse.cover_art" }),
           let publish = draft.definition.nodes.first(where: { $0.type == "pulse.inject" }) {
            draft.definition.edges.removeAll { $0.from == cover.id || $0.to == publish.id }
            draft.definition.edges.append(PulseWorkflowEdge(from: cover.id, to: publish.id))
        }
        self.draft = draft
        selectedNodeID = draft.definition.nodes.first?.id
        connectionSourceID = nil
        note = "\(node.label) removed."
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

    func removeEdge(_ edge: PulseWorkflowEdge) {
        guard var draft else { return }
        draft.definition.edges.removeAll { $0 == edge }
        self.draft = draft
        connectionSourceID = nil
        note = "Connection removed."
    }

    func startConnection(from nodeID: String) {
        guard isEditing else { return }
        connectionSourceID = nodeID
        note = "Choose an input port to connect this node."
    }

    func completeConnection(to nodeID: String) {
        guard let source = connectionSourceID, source != nodeID else { return }
        guard let draft,
              let sourceNode = draft.definition.nodes.first(where: { $0.id == source }),
              let targetNode = draft.definition.nodes.first(where: { $0.id == nodeID }),
              approvedConnection(from: sourceNode.type, to: targetNode.type, in: draft.definition) else {
            note = "That connection is not valid for Pulse."
            return
        }
        var next = draft
        next.definition.edges.removeAll { $0.from == source || $0.to == nodeID }
        next.definition.edges.append(PulseWorkflowEdge(from: source, to: nodeID))
        next.definition.normalizeEdgeOrder()
        self.draft = next
        connectionSourceID = nil
        note = "Connection added."
    }

    func save() async {
        guard let client, let draft else { return }
        guard draft.definition.validationMessage == nil else {
            note = draft.definition.validationMessage
            return
        }
        busy = true
        defer { busy = false }
        guard let saved = await client.save(draft) else { note = "The server rejected this graph."; return }
        self.draft = saved
        note = "Draft saved. Promote it when you want Pulse to use it."
    }

    func promote() async {
        guard let client, let draft else { return }
        guard draft.definition.validationMessage == nil else {
            note = draft.definition.validationMessage
            return
        }
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

    private func approvedConnection(from: String, to: String, in definition: PulseWorkflowDefinition) -> Bool {
        if definition.hasVisualNodes, from == "pulse.cover_art", to == "pulse.inject" { return false }
        let pairs = [
            ("pulse.triage", "pulse.gates"),
            ("pulse.gates", "pulse.synthesis"),
            ("pulse.synthesis", "pulse.claim_audit"),
            ("pulse.claim_audit", "pulse.cover_art"),
            ("pulse.cover_art", "pulse.visual_review"),
            ("pulse.cover_art", "pulse.inject"),
            ("pulse.visual_review", "pulse.cover_retry"),
            ("pulse.cover_retry", "pulse.inject")
        ]
        return pairs.contains { $0.0 == from && $0.1 == to }
    }

    static func fixture(editing: Bool = false) -> PulseWorkflowStore {
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
                                                 positions: [
                                                     "triage": PulseWorkflowPoint(x: 110, y: 140),
                                                     "gates": PulseWorkflowPoint(x: 320, y: 140),
                                                     "synthesis": PulseWorkflowPoint(x: 530, y: 140),
                                                     "claim_audit": PulseWorkflowPoint(x: 110, y: 290),
                                                     "cover_art": PulseWorkflowPoint(x: 320, y: 290),
                                                     "visual_review": PulseWorkflowPoint(x: 530, y: 290),
                                                     "cover_retry": PulseWorkflowPoint(x: 215, y: 440),
                                                     "inject": PulseWorkflowPoint(x: 425, y: 440)
                                                 ])
        store.active = PulseWorkflowVersion(id: "fixture", number: 2, state: "active", definition: definition)
        if editing { store.draft = store.active }
        store.selectedNodeID = "visual_review"
        store.phase = .ready
        return store
    }
}

struct PulseWorkflowEditor: View {
    @ObservedObject var store: PulseWorkflowStore
    var snapshot = false
    @State private var searchText = ""

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
                HStack(alignment: .top, spacing: 0) {
                    palette
                    Divider().overlay(Theme.hairline)
                    canvas
                    Divider().overlay(Theme.hairline)
                    inspector
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(Theme.bg)
    }

    private var toolbar: some View {
        HStack(spacing: 12) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.accent)
                .frame(width: 32, height: 32).background(Theme.accent.opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 1) {
                Text("Pulse workflow").font(.system(size: 17, weight: .semibold))
                Text("Build and configure the published pipeline").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
            }
            if store.displayed != nil {
                Text(store.isEditing ? "Draft" : "Active")
                    .font(.system(size: 10.5, weight: .semibold)).foregroundStyle(store.isEditing ? Theme.accent : Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 4).background((store.isEditing ? Theme.accent : Theme.textSecondary).opacity(0.1))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            Spacer()
            if store.isEditing {
                if let message = store.validationMessage {
                    Label(message, systemImage: "circle.dashed")
                        .font(.system(size: 10.5, weight: .medium)).foregroundStyle(.orange).lineLimit(1)
                } else {
                    Label("Ready to save", systemImage: "checkmark.circle.fill")
                        .font(.system(size: 10.5, weight: .medium)).foregroundStyle(.green)
                }
                Button("Discard") { store.discardDraft() }.buttonStyle(.plain).foregroundStyle(Theme.textSecondary)
                Button("Save draft") { Task { await store.save() } }.buttonStyle(.bordered).disabled(!store.canSave)
                Button("Promote") { Task { await store.promote() } }.buttonStyle(.borderedProminent).disabled(!store.canPromote)
            } else {
                Button("Edit workflow") { Task { await store.beginDraft() } }
                    .buttonStyle(.borderedProminent).disabled(store.busy)
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 11)
    }

    private var palette: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Nodes").font(.system(size: 15, weight: .semibold))
                Text("Drag a node onto the canvas").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
            }
            searchField
            if snapshot { paletteList } else { ScrollView { paletteList } }
            Text("Installed nodes can be dragged to reposition them.")
                .font(.system(size: 9.5)).foregroundStyle(Theme.textSecondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(14).frame(width: 246).frame(maxHeight: .infinity, alignment: .topLeading).background(Theme.bg)
    }

    private var paletteGroups: [String] {
        PulseWorkflowNodeTemplate.all.reduce(into: [String]()) { result, template in
            if !result.contains(template.group) { result.append(template.group) }
        }
    }

    @ViewBuilder private var searchField: some View {
        HStack(spacing: 7) {
            Image(systemName: "magnifyingglass").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
            if snapshot {
                Text("Search nodes").font(.system(size: 11.5)).foregroundStyle(Theme.textSecondary)
            } else {
                TextField("Search nodes", text: $searchText).textFieldStyle(.plain).font(.system(size: 11.5))
                if !searchText.isEmpty {
                    Button { searchText = "" } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.plain).foregroundStyle(Theme.textSecondary)
                }
            }
        }
        .padding(.horizontal, 10).frame(height: 32).background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.hairline, lineWidth: 1))
    }

    private var paletteList: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(paletteGroups, id: \.self) { group in
                let templates = visibleTemplates.filter { $0.group == group }
                if !templates.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(group.uppercased()).font(.system(size: 9, weight: .bold))
                            .tracking(0.7).foregroundStyle(Theme.textSecondary.opacity(0.68))
                        ForEach(templates) { template in paletteNode(template) }
                    }
                }
            }
        }
        .padding(.bottom, 12)
    }

    private var visibleTemplates: [PulseWorkflowNodeTemplate] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return PulseWorkflowNodeTemplate.all }
        return PulseWorkflowNodeTemplate.all.filter {
            $0.label.localizedCaseInsensitiveContains(query) || $0.detail.localizedCaseInsensitiveContains(query)
        }
    }

    private func paletteNode(_ template: PulseWorkflowNodeTemplate) -> some View {
        let installed = store.displayed?.definition.nodes.contains { $0.type == template.type } ?? false
        return Group {
            if snapshot {
                paletteNodeLabel(template, installed: installed)
            } else {
                Button {
                    if let node = store.displayed?.definition.nodes.first(where: { $0.type == template.type }) {
                        store.selectedNodeID = node.id
                    } else {
                        Task { await store.placeNode(template.type, at: CGPoint(x: 220, y: 220)) }
                    }
                } label: {
                    paletteNodeLabel(template, installed: installed)
                }
                .buttonStyle(.plain)
                .onDrag { NSItemProvider(object: template.type as NSString) }
                .help(installed ? "Drag to reposition" : "Drag to add")
            }
        }
    }

    private func paletteNodeLabel(_ template: PulseWorkflowNodeTemplate, installed: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: template.icon).font(.system(size: 13, weight: .semibold))
                .foregroundStyle(graphTint(template.tint)).frame(width: 32, height: 32)
                .background(graphTint(template.tint).opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 2) {
                Text(template.label).font(.system(size: 11.5, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                Text(template.detail).font(.system(size: 9.5)).foregroundStyle(Theme.textSecondary).lineLimit(1)
            }
            Spacer(minLength: 0)
            Image(systemName: installed ? "scope" : "plus")
                .font(.system(size: 9, weight: .bold)).foregroundStyle(installed ? Theme.textSecondary : Theme.accent)
        }
        .padding(.horizontal, 9).frame(height: 48).contentShape(Rectangle())
        .background(Theme.surface.opacity(0.82)).clipShape(RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline, lineWidth: 1))
    }

    @ViewBuilder private var canvas: some View {
        if snapshot {
            snapshotCanvas
        } else {
            GeometryReader { viewport in
            ScrollView([.horizontal, .vertical]) {
                if let workflow = store.displayed {
                    let width = max(viewport.size.width, (workflow.definition.positions.values.map(\.x).max() ?? 0) + 180)
                    let height = max(viewport.size.height, (workflow.definition.positions.values.map(\.y).max() ?? 0) + 130)
                    ZStack(alignment: .topLeading) {
                        DotGrid()
                        Canvas { context, _ in
                            for edge in workflow.definition.edges {
                                guard let from = workflow.definition.positions[edge.from],
                                      let to = workflow.definition.positions[edge.to] else { continue }
                                context.stroke(edgePath(CGPoint(x: from.x + 82, y: from.y), CGPoint(x: to.x - 82, y: to.y)),
                                               with: .color(Theme.textSecondary.opacity(0.52)), lineWidth: 1.5)
                            }
                        }
                        ForEach(workflow.definition.nodes) { node in
                            if let point = workflow.definition.positions[node.id] {
                                WorkflowNodeCard(node: node, selected: store.selectedNodeID == node.id,
                                                 connecting: store.connectionSourceID == node.id,
                                                 onInput: store.isEditing ? { store.completeConnection(to: node.id) } : nil,
                                                 onOutput: store.isEditing ? { store.startConnection(from: node.id) } : nil)
                                .position(x: point.x, y: point.y)
                                .onTapGesture { store.selectedNodeID = node.id }
                                .gesture(store.isEditing ? DragGesture().onEnded { value in store.moveNode(node.id, by: value.translation) } : nil)
                            }
                        }
                        Label(store.isEditing ? "Drag nodes to arrange. Click ports to connect." : "Create a draft to move or connect nodes.", systemImage: "cursorarrow.motionlines")
                            .font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                            .padding(.horizontal, 10).padding(.vertical, 7).background(Theme.bg.opacity(0.88))
                            .clipShape(RoundedRectangle(cornerRadius: 8)).padding(14).allowsHitTesting(false)
                    }
                    .frame(width: width, height: height)
                    .dropDestination(for: String.self) { items, location in
                        guard let type = items.first, PulseWorkflowNodeTemplate.template(for: type) != nil else { return false }
                        Task { await store.placeNode(type, at: location) }
                        return true
                    }
                }
            }
            .background(Theme.bg)
        }
        }
    }

    @ViewBuilder private var snapshotCanvas: some View {
        if let workflow = store.displayed {
            workflowCanvas(workflow)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.bg)
        }
    }

    private func workflowCanvas(_ workflow: PulseWorkflowVersion) -> some View {
        ZStack(alignment: .topLeading) {
            DotGrid()
            Canvas { context, _ in
                for edge in workflow.definition.edges {
                    guard let from = workflow.definition.positions[edge.from],
                          let to = workflow.definition.positions[edge.to] else { continue }
                    context.stroke(edgePath(CGPoint(x: from.x + 82, y: from.y), CGPoint(x: to.x - 82, y: to.y)),
                                   with: .color(Theme.textSecondary.opacity(0.52)), lineWidth: 1.5)
                }
            }
            ForEach(workflow.definition.nodes) { node in
                if let point = workflow.definition.positions[node.id] {
                    WorkflowNodeCard(node: node, selected: store.selectedNodeID == node.id)
                        .position(x: point.x, y: point.y)
                }
            }
            Label("Drag nodes to arrange. Click ports to connect.", systemImage: "cursorarrow.motionlines")
                .font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 10).padding(.vertical, 7).background(Theme.bg.opacity(0.88))
                .clipShape(RoundedRectangle(cornerRadius: 8)).padding(14)
        }
    }

    @ViewBuilder private var inspector: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let selected = store.displayed?.definition.nodes.first(where: { $0.id == store.selectedNodeID }),
               let template = PulseWorkflowNodeTemplate.template(for: selected.type) {
                HStack(spacing: 10) {
                    Image(systemName: template.icon).font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(graphTint(template.tint)).frame(width: 34, height: 34)
                        .background(graphTint(template.tint).opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 9))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(selected.label).font(.system(size: 14, weight: .semibold))
                        Text(template.detail).font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                    }
                }
                .padding(16)
                Divider().overlay(Theme.hairline)
                Group {
                    if snapshot {
                        inspectorContent(selected: selected, template: template)
                    } else {
                        ScrollView { inspectorContent(selected: selected, template: template) }
                    }
                }
            } else {
                VStack(alignment: .leading, spacing: 7) {
                    Image(systemName: "cursorarrow.click.2").font(.system(size: 18)).foregroundStyle(Theme.textSecondary)
                    Text("Select a node").font(.system(size: 13, weight: .semibold))
                    Text("Its parameters and connections will appear here.").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                }
                .padding(16)
                Spacer()
            }
        }
        .frame(width: 286).frame(maxHeight: .infinity, alignment: .topLeading).background(Theme.bg)
    }

    private func inspectorContent(selected: PulseWorkflowNode, template: PulseWorkflowNodeTemplate) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            inspectorSection("Parameters") {
                if store.isEditing {
                    controls(for: selected)
                } else {
                    Text("Create a draft to configure this node.")
                        .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                }
            }
            inspectorSection("Connections") { connectionList(for: selected) }
            if let note = store.note {
                Text(note).font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if store.isEditing, !template.required {
                Divider().overlay(Theme.hairline)
                Button(role: .destructive) { store.removeSelectedNode() } label: {
                    Label("Delete node", systemImage: "trash")
                }
                .buttonStyle(.plain).font(.system(size: 11.5, weight: .medium))
            }
        }
        .padding(16)
    }

    private func inspectorSection(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(title.uppercased()).font(.system(size: 9, weight: .bold)).tracking(0.7)
                .foregroundStyle(Theme.textSecondary.opacity(0.72))
            content()
        }
    }

    @ViewBuilder private func connectionList(for node: PulseWorkflowNode) -> some View {
        let edges = store.displayed?.definition.edges.filter { $0.from == node.id || $0.to == node.id } ?? []
        if edges.isEmpty {
            Text("No connections").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
        } else {
            VStack(spacing: 6) {
                ForEach(Array(edges.enumerated()), id: \.offset) { _, edge in
                    let peerID = edge.from == node.id ? edge.to : edge.from
                    let peer = store.displayed?.definition.nodes.first { $0.id == peerID }
                    HStack(spacing: 7) {
                        Image(systemName: edge.from == node.id ? "arrow.right" : "arrow.left")
                            .font(.system(size: 9, weight: .bold)).foregroundStyle(Theme.textSecondary)
                        Text(peer?.label ?? peerID).font(.system(size: 10.5, weight: .medium))
                        Spacer()
                        if store.isEditing {
                            Button { store.removeEdge(edge) } label: { Image(systemName: "xmark") }
                                .buttonStyle(.plain).foregroundStyle(Theme.textSecondary).help("Remove connection")
                        }
                    }
                    .padding(.horizontal, 9).frame(height: 30).background(Theme.surface.opacity(0.72))
                    .clipShape(RoundedRectangle(cornerRadius: 7))
                }
            }
        }
    }

    @ViewBuilder private func controls(for node: PulseWorkflowNode) -> some View {
        switch node.type {
        case "pulse.cover_art":
            VStack(alignment: .leading, spacing: 6) {
                Text("Style profile").font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                Picker("", selection: Binding(get: { node.config["style"] ?? "rotating" }, set: { value in store.setStyle(value) })) {
                    Text("Rotating").tag("rotating")
                    Text("Photographic").tag("photographic")
                    Text("Illustrated").tag("illustrated")
                    Text("Editorial").tag("editorial")
                }.labelsHidden().pickerStyle(.menu).frame(maxWidth: .infinity, alignment: .leading)
            }
        case "pulse.visual_review":
            let threshold = Double(node.config["threshold"] ?? "0.8") ?? 0.8
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Review threshold").font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    Spacer()
                    Text(threshold.formatted(.number.precision(.fractionLength(1))))
                        .font(.system(size: 10.5, weight: .semibold).monospacedDigit())
                }
                if snapshot {
                    GeometryReader { proxy in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Theme.surface)
                            Capsule().fill(Theme.accent).frame(width: proxy.size.width * threshold)
                            Circle().fill(Theme.textPrimary).frame(width: 12, height: 12)
                                .offset(x: max(0, proxy.size.width * threshold - 6))
                        }
                    }
                    .frame(height: 12)
                } else {
                    Slider(value: Binding(get: { threshold }, set: { value in store.setThreshold(value) }), in: 0...1, step: 0.1)
                }
            }
        case "pulse.cover_retry":
            let attempts = Int(node.config["max_attempts"] ?? "1") ?? 1
            Toggle("Allow one retry", isOn: Binding(get: { attempts == 1 }, set: { store.setAttempts($0 ? 1 : 0) }))
                .toggleStyle(.switch).font(.system(size: 11))
        default:
            Text("This node has no editable parameters.").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
        }
    }
}

struct WorkflowNodeCard: View {
    let node: PulseWorkflowNode
    var selected: Bool
    var connecting: Bool = false
    var onInput: (() -> Void)? = nil
    var onOutput: (() -> Void)? = nil

    var body: some View {
        let template = PulseWorkflowNodeTemplate.template(for: node.type)
        HStack(spacing: 10) {
            Image(systemName: node.icon).font(.system(size: 14, weight: .semibold)).foregroundStyle(graphTint(node.tint))
                .frame(width: 34, height: 34).background(graphTint(node.tint).opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 2) {
                Text(node.label).font(.system(size: 11.5, weight: .semibold)).lineLimit(1)
                Text(template?.group ?? "Node").font(.system(size: 9.5)).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 11).frame(width: 164, height: 66)
        .background(selected ? Theme.surface.opacity(1) : Theme.surface.opacity(0.9))
        .clipShape(RoundedRectangle(cornerRadius: 11))
        .overlay(RoundedRectangle(cornerRadius: 11).stroke(connecting || selected ? Theme.accent : Theme.hairline, lineWidth: connecting || selected ? 1.5 : 1))
        .shadow(color: Theme.bg.opacity(0.35), radius: selected ? 8 : 3, y: 2)
        .overlay(alignment: .leading) { connectionPort(action: onInput).offset(x: -4) }
        .overlay(alignment: .trailing) { connectionPort(action: onOutput).offset(x: 4) }
    }

    @ViewBuilder private func connectionPort(action: (() -> Void)?) -> some View {
        if let action {
            Button(action: action) { PortDot() }.buttonStyle(.plain).help("Connect")
        } else {
            PortDot()
        }
    }
}

struct PulseWorkflowEditorShot: View {
    @StateObject private var store = PulseWorkflowStore.fixture(editing: true)

    var body: some View {
        PulseWorkflowEditor(store: store, snapshot: true)
    }
}
