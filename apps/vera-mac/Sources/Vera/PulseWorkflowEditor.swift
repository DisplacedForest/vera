import SwiftUI
import UniformTypeIdentifiers

extension UTType {
    static let workflowPaletteNode = UTType(exportedAs: "app.vera.workflow-node")
}

struct PulseWorkflowNode: Identifiable, Hashable {
    var id: String
    var type: String
    var config: [String: WorkflowConfigValue]

    var jsonConfig: [String: Any] {
        config.mapValues(\.jsonValue)
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

    mutating func normalizeEdgeOrder() {
        let order = Dictionary(uniqueKeysWithValues: nodes.enumerated().map { ($0.element.id, $0.offset) })
        edges.sort {
            let left = order[$0.from] ?? Int.max
            let right = order[$1.from] ?? Int.max
            return left == right ? (order[$0.to] ?? Int.max) < (order[$1.to] ?? Int.max) : left < right
        }
    }

    func node(withID id: String) -> PulseWorkflowNode? {
        nodes.first { $0.id == id }
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
            let config = (raw["config"] as? [String: Any] ?? [:]).mapValues(WorkflowConfigValue.parse)
            return PulseWorkflowNode(id: id, type: type, config: config)
        }
        let edges = rawEdges.compactMap { raw -> PulseWorkflowEdge? in
            guard let from = raw["from"] as? String, let to = raw["to"] as? String else { return nil }
            return PulseWorkflowEdge(from: from, to: to)
        }
        let savedPositions = (object["positions"] as? [String: Any] ?? [:]).compactMapValues(PulseWorkflowPoint.parse)
        let positions = nodes.enumerated().reduce(into: savedPositions) { result, item in
            if result[item.element.id] == nil {
                result[item.element.id] = PulseWorkflowPoint(x: 110 + CGFloat(item.offset) * WorkflowCardGeometry.placementPitch, y: 210)
            }
        }
        return nodes.isEmpty ? nil : PulseWorkflowDefinition(id: id, nodes: nodes, edges: edges, positions: positions)
    }
}

extension WorkflowCatalog {
    func validationMessage(for definition: PulseWorkflowDefinition) -> String? {
        var counts: [String: Int] = [:]
        for node in definition.nodes { counts[node.type, default: 0] += 1 }
        for stage in profile.spine {
            guard let count = counts[stage] else { return "Add the required \(label(for: stage)) node." }
            if count > 1 { return "Only one \(label(for: stage)) node is allowed." }
        }
        for pair in profile.pairs {
            let present = pair.types.filter { counts[$0] != nil }
            if !present.isEmpty, present.count != pair.types.count {
                let missing = pair.types.filter { counts[$0] == nil }.map { label(for: $0) }.joined(separator: " and ")
                return "Add \(missing) to complete this path."
            }
            for type in pair.types where counts[type, default: 0] > 1 {
                return "Only one \(label(for: type)) node is allowed."
            }
        }
        for entry in definition.nodes {
            if profile.spine.contains(entry.type) || profile.pairTypes.contains(entry.type) { continue }
            guard let spec = node(for: entry.type), spec.insertable,
                  profile.insertableCategories.contains(spec.category) else {
                return "\(label(for: entry.type)) cannot be added to this workflow."
            }
        }
        guard !profile.spine.isEmpty else {
            return isAcyclic(definition) ? nil : "Remove the cycle from this workflow."
        }
        guard let path = singlePath(definition) else { return "Connect every node into a single path." }
        let order = path.compactMap { id in definition.node(withID: id)?.type }
        guard order.first == profile.spine.first, order.last == profile.spine.last else {
            return "The workflow must run from \(label(for: profile.spine.first ?? "")) to \(label(for: profile.spine.last ?? ""))."
        }
        guard order.filter({ profile.spine.contains($0) }) == profile.spine else {
            return "Connect the core stages in order."
        }
        for pair in profile.pairs {
            guard let firstType = pair.types.first, let start = order.firstIndex(of: firstType) else { continue }
            let run = Array(order.dropFirst(start).prefix(pair.types.count))
            guard run == pair.types else {
                return "\(label(for: pair.types.last ?? "")) must directly follow \(label(for: firstType))."
            }
            guard let anchor = order.firstIndex(of: pair.after), let bound = order.firstIndex(of: pair.before),
                  anchor < start, start + pair.types.count - 1 < bound else {
                return "\(label(for: firstType)) belongs between \(label(for: pair.after)) and \(label(for: pair.before))."
            }
        }
        return nil
    }

    func allowsConnection(from source: PulseWorkflowNode, to target: PulseWorkflowNode, in definition: PulseWorkflowDefinition) -> Bool {
        guard source.id != target.id else { return false }
        guard let sourceIndex = canonicalIndex(of: source.type),
              let targetIndex = canonicalIndex(of: target.type) else { return true }
        guard sourceIndex < targetIndex else { return false }
        let installedBetween = definition.nodes.contains { node in
            guard let index = canonicalIndex(of: node.type) else { return false }
            return index > sourceIndex && index < targetIndex
        }
        return !installedBetween
    }

    private func singlePath(_ definition: PulseWorkflowDefinition) -> [String]? {
        var outgoing: [String: String] = [:]
        var indegree = Dictionary(uniqueKeysWithValues: definition.nodes.map { ($0.id, 0) })
        var seen = Set<PulseWorkflowEdge>()
        for edge in definition.edges {
            guard !seen.contains(edge), outgoing[edge.from] == nil, indegree[edge.to] != nil else { return nil }
            seen.insert(edge)
            outgoing[edge.from] = edge.to
            indegree[edge.to]! += 1
        }
        let starts = indegree.filter { $0.value == 0 }.map(\.key)
        guard starts.count == 1, !indegree.values.contains(where: { $0 > 1 }) else { return nil }
        var path = [starts[0]]
        while let next = outgoing[path[path.count - 1]] {
            path.append(next)
            if path.count > definition.nodes.count { return nil }
        }
        return path.count == definition.nodes.count ? path : nil
    }

    private func isAcyclic(_ definition: PulseWorkflowDefinition) -> Bool {
        var indegree = Dictionary(uniqueKeysWithValues: definition.nodes.map { ($0.id, 0) })
        var downstream: [String: [String]] = [:]
        for edge in definition.edges {
            downstream[edge.from, default: []].append(edge.to)
            indegree[edge.to, default: 0] += 1
        }
        var ready = indegree.filter { $0.value == 0 }.map(\.key)
        var visited = 0
        while let current = ready.popLast() {
            visited += 1
            for target in downstream[current] ?? [] {
                indegree[target]! -= 1
                if indegree[target] == 0 { ready.append(target) }
            }
        }
        return visited == definition.nodes.count
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

enum PulseWorkflowOutcome {
    case success(PulseWorkflowVersion)
    case rejected(String)
    case failed
}

@MainActor
struct PulseWorkflowClient {
    let base: URL

    private func fetch(path: String, method: String = "GET", body: [String: Any]? = nil) async -> (Data, Int)? {
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 8
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let status = (response as? HTTPURLResponse)?.statusCode else { return nil }
        return (data, status)
    }

    private func request(path: String, method: String = "GET", body: [String: Any]? = nil) async -> PulseWorkflowOutcome {
        guard let (data, status) = await fetch(path: path, method: method, body: body) else { return .failed }
        if (200..<300).contains(status),
           let object = try? JSONSerialization.jsonObject(with: data),
           let wrapped = object as? [String: Any],
           let version = PulseWorkflowVersion.parse(wrapped["workflow"] as Any) {
            return .success(version)
        }
        if (400..<500).contains(status), let message = Self.rejectionMessage(from: data) {
            return .rejected(message)
        }
        return .failed
    }

    static func rejectionMessage(from data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let detail = object["detail"] as? String, !detail.isEmpty else { return nil }
        return detail
    }

    func catalog() async -> WorkflowCatalog? {
        guard let (data, status) = await fetch(path: "/agentic/workflows/pulse/catalog"),
              (200..<300).contains(status),
              let object = try? JSONSerialization.jsonObject(with: data) else { return nil }
        return WorkflowCatalog.parse(object)
    }

    func overview() async -> (version: PulseWorkflowVersion, latestRun: PulseWorkflowRun?, runUnreadable: Bool)? {
        guard let (data, status) = await fetch(path: "/agentic/workflows/pulse"),
              (200..<300).contains(status),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let version = PulseWorkflowVersion.parse(object["workflow"] as Any) else { return nil }
        let run = PulseWorkflowRun.classify(object["latest_run"])
        return (version, run.run, run.unreadable)
    }

    func createDraft() async -> PulseWorkflowVersion? {
        if case .success(let version) = await request(path: "/agentic/workflows/pulse/drafts", method: "POST") { return version }
        return nil
    }

    func save(_ version: PulseWorkflowVersion) async -> PulseWorkflowOutcome {
        await request(path: "/agentic/workflow-drafts/\(version.id)", method: "PUT", body: ["definition": version.definition.jsonObject()])
    }

    func promote(_ version: PulseWorkflowVersion) async -> PulseWorkflowOutcome {
        await request(path: "/agentic/workflow-drafts/\(version.id)/promote", method: "POST")
    }
}

@MainActor
final class PulseWorkflowStore: ObservableObject {
    enum Phase { case loading, unavailable, ready }
    enum Mode { case edit, run }
    @Published var phase: Phase = .loading
    @Published var mode: Mode = .edit
    @Published var catalog: WorkflowCatalog?
    @Published var active: PulseWorkflowVersion?
    @Published var draft: PulseWorkflowVersion?
    @Published var latestRun: PulseWorkflowRun?
    @Published var runUnreadable = false
    @Published var runStale = false
    @Published var selectedNodeID: String?
    @Published var connectionSourceID: String?
    @Published var busy = false
    @Published var note: String?
    var paletteDragType: String?

    private var client: PulseWorkflowClient?

    var displayed: PulseWorkflowVersion? { draft ?? active }
    var isEditing: Bool { draft != nil }
    var changed: Bool { draft?.definition != active?.definition }
    var validationMessage: String? {
        guard let catalog, let definition = displayed?.definition else { return nil }
        return catalog.validationMessage(for: definition)
    }
    var canSave: Bool { isEditing && changed && validationMessage == nil && !busy }
    var canPromote: Bool { isEditing && validationMessage == nil && !busy }

    func configure(base: URL?) {
        client = base.map { PulseWorkflowClient(base: $0) }
        if client == nil { phase = .unavailable }
    }

    func refresh() async {
        guard let client else { phase = .unavailable; return }
        guard let served = await client.catalog() else { phase = .unavailable; return }
        catalog = served
        guard let overview = await client.overview() else { phase = .unavailable; return }
        active = overview.version
        latestRun = overview.latestRun
        runUnreadable = overview.runUnreadable
        runStale = false
        if draft == nil { selectedNodeID = overview.version.definition.nodes.first?.id }
        phase = .ready
    }

    var runVersion: PulseWorkflowVersion? { latestRun?.version ?? active }

    func setMode(_ next: Mode) {
        guard mode != next else { return }
        mode = next
        connectionSourceID = nil
        if next == .run {
            Task { await refreshRun() }
        }
    }

    func refreshRun() async {
        guard let client else { return }
        guard let overview = await client.overview() else {
            runStale = true
            return
        }
        active = overview.version
        latestRun = overview.latestRun
        runUnreadable = overview.runUnreadable
        runStale = false
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
        guard mode == .edit else { return }
        if !isEditing { await beginDraft() }
        placeNodeInDraft(type, at: point)
    }

    func placeNodeInDraft(_ type: String, at point: CGPoint, into requestedEdge: PulseWorkflowEdge? = nil) {
        guard var draft, let catalog, let spec = catalog.node(for: type) else { return }
        let position = PulseWorkflowPoint(x: max(90, point.x), y: max(70, point.y))
        let singleInstance = catalog.canonicalIndex(of: type) != nil
        if singleInstance, let existing = draft.definition.nodes.first(where: { $0.type == type }) {
            draft.definition.positions[existing.id] = position
            self.draft = draft
            selectedNodeID = existing.id
            note = "\(spec.label) moved."
            return
        }
        let node = PulseWorkflowNode(id: nodeID(for: type, in: draft.definition), type: type, config: spec.defaultConfig)
        let index = draft.definition.nodes.firstIndex { existing in
            guard let existingOrder = catalog.canonicalIndex(of: existing.type),
                  let newOrder = catalog.canonicalIndex(of: type) else { return false }
            return existingOrder > newOrder
        } ?? draft.definition.nodes.endIndex
        draft.definition.nodes.insert(node, at: index)
        draft.definition.positions[node.id] = position
        let edge = requestedEdge.flatMap { draft.definition.edges.contains($0) ? $0 : nil }
            ?? nearestWorkflowEdge(to: point, in: draft.definition)
        if let edge {
            draft.definition.edges.removeAll { $0 == edge }
            draft.definition.edges.append(PulseWorkflowEdge(from: edge.from, to: node.id))
            draft.definition.edges.append(PulseWorkflowEdge(from: node.id, to: edge.to))
            draft.definition.normalizeEdgeOrder()
            note = "\(spec.label) inserted into the path."
        } else {
            note = "\(spec.label) added. Connect it to complete the workflow."
        }
        self.draft = draft
        selectedNodeID = node.id
    }

    func removeSelectedNode() {
        guard var draft, let catalog, let id = selectedNodeID,
              let node = draft.definition.node(withID: id),
              !catalog.isRequired(node.type) else { return }
        let incoming = draft.definition.edges.filter { $0.to == id }
        let outgoing = draft.definition.edges.filter { $0.from == id }
        draft.definition.nodes.removeAll { $0.id == id }
        draft.definition.edges.removeAll { $0.from == id || $0.to == id }
        draft.definition.positions[id] = nil
        if let source = incoming.first?.from, let target = outgoing.first?.to,
           incoming.count == 1, outgoing.count == 1 {
            let bridge = PulseWorkflowEdge(from: source, to: target)
            if !draft.definition.edges.contains(bridge) {
                draft.definition.edges.append(bridge)
                draft.definition.normalizeEdgeOrder()
            }
        }
        self.draft = draft
        selectedNodeID = draft.definition.nodes.first?.id
        connectionSourceID = nil
        note = "\(catalog.label(for: node.type)) removed."
    }

    func setConfigValue(_ key: String, _ value: WorkflowConfigValue) {
        mutateSelected { node in node.config[key] = value }
    }

    func completeNodeDrag(_ id: String, by translation: CGSize, splicing edge: PulseWorkflowEdge?) {
        guard var draft, var point = draft.definition.positions[id] else { return }
        point.x = max(75, point.x + translation.width)
        point.y = max(75, point.y + translation.height)
        draft.definition.positions[id] = point
        if let edge, applySplice(of: id, into: edge, in: &draft.definition) {
            let type = draft.definition.node(withID: id)?.type
            note = "\(type.map { catalog?.label(for: $0) ?? workflowDisplayName($0) } ?? id) inserted into the path."
        }
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
        connect(from: source, to: nodeID)
    }

    func connect(from source: String, to target: String) {
        guard source != target else { return }
        guard let draft, let catalog,
              let sourceNode = draft.definition.node(withID: source),
              let targetNode = draft.definition.node(withID: target),
              catalog.allowsConnection(from: sourceNode, to: targetNode, in: draft.definition) else {
            note = "That connection is not valid for this workflow."
            return
        }
        var next = draft
        next.definition.edges.removeAll { $0.from == source || $0.to == target }
        next.definition.edges.append(PulseWorkflowEdge(from: source, to: target))
        next.definition.normalizeEdgeOrder()
        self.draft = next
        connectionSourceID = nil
        note = "Connection added."
    }

    func spliceNode(_ id: String, into edge: PulseWorkflowEdge) {
        guard var draft, let node = draft.definition.node(withID: id),
              applySplice(of: id, into: edge, in: &draft.definition) else { return }
        self.draft = draft
        note = "\(catalog?.label(for: node.type) ?? workflowDisplayName(node.type)) inserted into the path."
    }

    private func applySplice(of id: String, into edge: PulseWorkflowEdge,
                             in definition: inout PulseWorkflowDefinition) -> Bool {
        guard definition.node(withID: id) != nil,
              edge.from != id, edge.to != id,
              definition.edges.contains(edge),
              !definition.edges.contains(where: { $0.from == id || $0.to == id }) else { return false }
        definition.edges.removeAll { $0 == edge }
        definition.edges.append(PulseWorkflowEdge(from: edge.from, to: id))
        definition.edges.append(PulseWorkflowEdge(from: id, to: edge.to))
        definition.normalizeEdgeOrder()
        return true
    }

    func save() async {
        guard let client, let draft else { return }
        if let message = validationMessage {
            note = message
            return
        }
        busy = true
        defer { busy = false }
        switch await client.save(draft) {
        case .success(let saved):
            self.draft = saved
            note = "Draft saved. Promote it when you want Pulse to use it."
        case .rejected(let message):
            note = message
        case .failed:
            note = "Couldn’t reach the server to save this draft."
        }
    }

    func promote() async {
        guard let client, let draft else { return }
        if let message = validationMessage {
            note = message
            return
        }
        busy = true
        defer { busy = false }
        switch await client.save(draft) {
        case .success(let saved):
            switch await client.promote(saved) {
            case .success(let promoted):
                active = promoted
                self.draft = nil
                note = "This workflow is active for future Pulse runs."
            case .rejected(let message):
                note = message
            case .failed:
                note = "Couldn’t promote this draft."
            }
        case .rejected(let message):
            note = message
        case .failed:
            note = "Couldn’t reach the server to save this draft."
        }
    }

    private func mutateSelected(_ change: (inout PulseWorkflowNode) -> Void) {
        guard var draft, let id = selectedNodeID,
              let index = draft.definition.nodes.firstIndex(where: { $0.id == id }) else { return }
        change(&draft.definition.nodes[index])
        self.draft = draft
    }

    private func nodeID(for type: String, in definition: PulseWorkflowDefinition) -> String {
        let base = type.split(separator: ".").last.map(String.init) ?? type
        if definition.node(withID: base) == nil { return base }
        var counter = 2
        while definition.node(withID: "\(base)-\(counter)") != nil { counter += 1 }
        return "\(base)-\(counter)"
    }

    static func fixture(editing: Bool = false) -> PulseWorkflowStore {
        let store = PulseWorkflowStore()
        store.catalog = WorkflowCatalog.fixture()
        var nodes = [
            PulseWorkflowNode(id: "triage", type: "pulse.triage", config: [:]),
            PulseWorkflowNode(id: "gates", type: "pulse.gates", config: [:]),
            PulseWorkflowNode(id: "synthesis", type: "pulse.synthesis", config: [:]),
            PulseWorkflowNode(id: "claim_audit", type: "pulse.claim_audit", config: [:]),
            PulseWorkflowNode(id: "cover_art", type: "pulse.cover_art", config: ["style": .string("editorial")]),
            PulseWorkflowNode(id: "visual_review", type: "pulse.visual_review", config: ["threshold": .double(0.8)]),
            PulseWorkflowNode(id: "cover_retry", type: "pulse.cover_retry", config: ["max_attempts": .int(1)]),
            PulseWorkflowNode(id: "inject", type: "pulse.inject", config: [:])
        ]
        if editing {
            nodes.insert(PulseWorkflowNode(id: "filter", type: "flow.filter",
                                           config: ["field": .string("title"), "operator": .string("contains"),
                                                    "value": .string("frost"), "action": .string("drop")]),
                         at: 3)
        }
        let definition = PulseWorkflowDefinition(id: "pulse", nodes: nodes,
                                                 edges: Array(zip(nodes, nodes.dropFirst())).map { PulseWorkflowEdge(from: $0.id, to: $1.id) },
                                                 positions: Dictionary(uniqueKeysWithValues: nodes.enumerated().map {
                                                     ($0.element.id, PulseWorkflowPoint(x: 105 + CGFloat($0.offset) * WorkflowCardGeometry.placementPitch, y: 310))
                                                 }))
        store.active = PulseWorkflowVersion(id: "fixture", number: 2, state: "active", definition: definition)
        if editing { store.draft = store.active }
        store.selectedNodeID = editing ? "filter" : "visual_review"
        store.phase = .ready
        return store
    }
}

extension WorkflowCatalog {
    static func fixture() -> WorkflowCatalog? {
        let json = """
        {"nodes":[
          {"type":"pulse.triage","label":"Triage","description":"Pulls in this run's fresh candidate stories and signals so the rest of the pipeline has something to judge.","icon":"globe","tint":"accent","category":"core","config_schema":{},"insertable":false},
          {"type":"pulse.gates","label":"Gates","description":"Checks each candidate against your interest and quality gates and drops the ones that fall short.","icon":"line.3.horizontal.decrease.circle","tint":"orange","category":"core","config_schema":{},"insertable":false},
          {"type":"pulse.synthesis","label":"Synthesis","description":"Writes each surviving candidate into a readable card with a headline and summary.","icon":"sparkles","tint":"purple","category":"core","config_schema":{},"insertable":false},
          {"type":"pulse.claim_audit","label":"Claim audit","description":"Rechecks the factual claims in every drafted card and pulls the ones that don't hold up.","icon":"checkmark.shield","tint":"cyan","category":"core","config_schema":{},"insertable":false},
          {"type":"pulse.cover_art","label":"Cover art","description":"Generates a cover image for each card in the visual style you pick.","icon":"photo","tint":"purple","category":"core",
           "config_schema":{"style":{"type":"choice","default":"rotating","options":["rotating","photographic","illustrated","editorial"]}},"insertable":false},
          {"type":"pulse.visual_review","label":"Visual review","description":"Scores each generated cover and rejects the ones below your quality threshold.","icon":"eye","tint":"cyan","category":"visual",
           "config_schema":{"threshold":{"type":"number","min":0,"max":1,"default":0.8}},"insertable":false},
          {"type":"pulse.cover_retry","label":"One retry","description":"Regenerates a rejected cover one more time before the card ships without it.","icon":"arrow.clockwise","tint":"orange","category":"visual",
           "config_schema":{"max_attempts":{"type":"choice","options":[0,1],"default":1}},"insertable":false},
          {"type":"pulse.inject","label":"Inject","description":"Publishes the finished cards into your feed.","icon":"arrow.down.to.line","tint":"green","category":"core","config_schema":{},"insertable":false},
          {"type":"flow.filter","label":"Filter","description":"Keeps or drops cards by comparing one of their fields against a value you choose.","icon":"line.3.horizontal.decrease","tint":"orange","category":"transform",
           "config_schema":{"field":{"type":"text","default":"title"},
                            "operator":{"type":"choice","default":"contains","options":["contains","not_contains","equals","not_equals","present","missing"]},
                            "value":{"type":"text","default":""},
                            "action":{"type":"choice","default":"keep","options":["keep","drop"]}},"insertable":true}
        ],
        "profile":{"id":"pulse",
          "spine":["pulse.triage","pulse.gates","pulse.synthesis","pulse.claim_audit","pulse.cover_art","pulse.inject"],
          "insertable_categories":["transform","enrich","notify"],
          "pairs":[{"types":["pulse.visual_review","pulse.cover_retry"],"after":"pulse.cover_art","before":"pulse.inject"}]}}
        """
        guard let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)) else { return nil }
        return WorkflowCatalog.parse(object)
    }
}

struct PulseWorkflowPalette: View {
    @ObservedObject var store: PulseWorkflowStore
    @Binding var searchText: String
    var snapshot = false
    var onBack: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onBack) {
                Label("All workflows", systemImage: "chevron.left")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 14).padding(.vertical, 10)
            Divider().overlay(Theme.hairline)
            VStack(alignment: .leading, spacing: 3) {
                Text("Nodes").font(.system(size: 15, weight: .semibold))
                Text("Drag onto the workflow canvas").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 10)
            searchField.padding(.horizontal, 12).padding(.bottom, 10)
            if snapshot {
                paletteList.padding(.horizontal, 10)
            } else {
                ScrollView {
                    paletteList.padding(.horizontal, 10)
                }
            }
            Text(store.mode == .run ? "Run mode is read only. Switch to Edit to change the workflow."
                 : store.isEditing ? "Drop to add or reposition a node." : "Dropping a node creates a draft first.")
                .font(.system(size: 9.5)).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(12)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.sidebar)
        .onAppear { searchText = "" }
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
            ForEach(store.catalog?.paletteCategories ?? [], id: \.self) { category in
                let entries = visibleEntries.filter { $0.category == category }
                if !entries.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(category.uppercased()).font(.system(size: 9, weight: .bold))
                            .tracking(0.7).foregroundStyle(Theme.textSecondary.opacity(0.68))
                        ForEach(entries) { entry in paletteNode(entry) }
                    }
                }
            }
        }
        .padding(.bottom, 12)
    }

    private var visibleEntries: [WorkflowCatalogNode] {
        let all = store.catalog?.paletteNodes ?? []
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return all }
        return all.filter {
            $0.label.localizedCaseInsensitiveContains(query)
                || $0.description.localizedCaseInsensitiveContains(query)
                || $0.type.localizedCaseInsensitiveContains(query)
        }
    }

    private func paletteNode(_ entry: WorkflowCatalogNode) -> some View {
        let installed = store.displayed?.definition.nodes.contains { $0.type == entry.type } ?? false
        return Group {
            if snapshot {
                paletteNodeLabel(entry, installed: installed)
            } else {
                paletteNodeLabel(entry, installed: installed)
                .onTapGesture {
                    if installed, let node = store.displayed?.definition.nodes.first(where: { $0.type == entry.type }) {
                        store.selectedNodeID = node.id
                    } else {
                        Task { await store.placeNode(entry.type, at: CGPoint(x: 360, y: 320)) }
                    }
                }
                .onDrag {
                    store.paletteDragType = entry.type
                    let provider = NSItemProvider(object: entry.type as NSString)
                    provider.registerDataRepresentation(forTypeIdentifier: UTType.workflowPaletteNode.identifier,
                                                        visibility: .ownProcess) { completion in
                        completion(Data(entry.type.utf8), nil)
                        return nil
                    }
                    return provider
                } preview: {
                    WorkflowNodeCardPreview(spec: entry)
                }
                .help(installed ? "Drag to reposition" : "Drag to add")
                .opacity(store.phase == .ready && !store.busy ? 1 : 0.5)
                .allowsHitTesting(store.phase == .ready && !store.busy)
            }
        }
    }

    private func paletteNodeLabel(_ entry: WorkflowCatalogNode, installed: Bool) -> some View {
        HStack(spacing: 9) {
            Image(systemName: entry.icon).font(.system(size: 12, weight: .semibold))
                .foregroundStyle(graphTint(entry.tint)).frame(width: 30, height: 30)
                .background(graphTint(entry.tint).opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.label).font(.system(size: 11.5, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                Text(entry.description).font(.system(size: 9.5)).foregroundStyle(Theme.textSecondary).lineLimit(2)
            }
            Spacer(minLength: 0)
            Image(systemName: installed ? "scope" : "plus")
                .font(.system(size: 9, weight: .bold)).foregroundStyle(installed ? Theme.textSecondary : Theme.accent)
        }
        .padding(.horizontal, 8).padding(.vertical, 7).frame(minHeight: 46).contentShape(Rectangle())
        .background(Theme.surface.opacity(0.76)).clipShape(RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline, lineWidth: 1))
    }
}

struct WorkflowCanvasDragState: Equatable {
    var nodeID: String
    var offset: CGSize
}

struct WorkflowWireDrag: Equatable {
    var sourceID: String
    var point: CGPoint
    var targetID: String?
}

struct WorkflowInsertRequest: Equatable {
    var point: CGPoint
    var edge: PulseWorkflowEdge?
    var connectFrom: String?
}

@MainActor
final class WorkflowCanvasNav: ObservableObject {
    @Published var transform = WorkflowCanvasTransform()
    var viewportGlobal: CGRect = .zero
    weak var window: NSWindow?
    private var monitor: Any?

    func startMonitoring() {
        guard monitor == nil else { return }
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.scrollWheel, .magnify]) { [weak self] event in
            guard let self else { return event }
            nonisolated(unsafe) let captured = event
            let consumed = MainActor.assumeIsolated { self.handle(captured) }
            return consumed ? nil : event
        }
    }

    func stopMonitoring() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
    }

    private func handle(_ event: NSEvent) -> Bool {
        guard let window = event.window, window === self.window,
              let content = window.contentView else { return false }
        var point = content.convert(event.locationInWindow, from: nil)
        if !content.isFlipped { point.y = content.bounds.height - point.y }
        guard viewportGlobal.contains(point) else { return false }
        let local = CGPoint(x: point.x - viewportGlobal.minX, y: point.y - viewportGlobal.minY)
        switch event.type {
        case .magnify:
            transform.zoom(by: 1 + event.magnification, around: local)
            return true
        case .scrollWheel:
            if event.modifierFlags.contains(.command) {
                transform.zoom(by: pow(1.004, event.scrollingDeltaY), around: local)
            } else {
                transform.pan(by: CGSize(width: event.scrollingDeltaX, height: event.scrollingDeltaY))
            }
            return true
        default:
            return false
        }
    }
}

struct PulseWorkflowEditor: View {
    @ObservedObject var store: PulseWorkflowStore
    var snapshot = false

    @StateObject private var nav = WorkflowCanvasNav()
    @State private var nodeDrag: WorkflowCanvasDragState?
    @State private var wireDrag: WorkflowWireDrag?
    @State private var spliceEdge: PulseWorkflowEdge?
    @State private var hoverEdge: PulseWorkflowEdge?
    @State private var hoveredNodeID: String?
    @State private var insertRequest: WorkflowInsertRequest?
    @State private var panAnchor: CGSize?
    @State private var magnifyLast: CGFloat?

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
                if store.mode == .run {
                    if store.runUnreadable {
                        WorkflowRunEmptyState(icon: "exclamationmark.triangle",
                                              title: "Run record unreadable",
                                              note: "The server returned a run this app couldn't read. Update vera-api or the app so the two match.")
                    } else if store.latestRun == nil {
                        WorkflowRunEmptyState()
                    } else {
                        HStack(alignment: .top, spacing: 0) {
                            runCanvas
                            Divider().overlay(Theme.hairline)
                            WorkflowRunInspector(store: store, snapshot: snapshot)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                } else {
                    HStack(alignment: .top, spacing: 0) {
                        canvas
                        Divider().overlay(Theme.hairline)
                        inspector
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
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
            if store.mode == .run {
                Text(store.latestRun?.version.map { "Latest run · v\($0.number)" } ?? "Latest run")
                    .font(.system(size: 10.5, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 4).background(Theme.textSecondary.opacity(0.1))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            } else if store.displayed != nil {
                Text(store.isEditing ? "Draft" : "Active")
                    .font(.system(size: 10.5, weight: .semibold)).foregroundStyle(store.isEditing ? Theme.accent : Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 4).background((store.isEditing ? Theme.accent : Theme.textSecondary).opacity(0.1))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            if store.phase == .ready {
                if snapshot {
                    HStack(spacing: 2) {
                        modeChip("Edit", active: store.mode == .edit)
                        modeChip("Run", active: store.mode == .run)
                    }
                    .padding(2).background(Theme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 7))
                } else {
                    Picker("", selection: Binding(get: { store.mode }, set: { store.setMode($0) })) {
                        Text("Edit").tag(PulseWorkflowStore.Mode.edit)
                        Text("Run").tag(PulseWorkflowStore.Mode.run)
                    }
                    .pickerStyle(.segmented).labelsHidden().fixedSize()
                }
            }
            Spacer()
            if store.mode == .run {
                if store.runStale {
                    Label("Couldn't refresh the run record", systemImage: "exclamationmark.triangle")
                        .font(.system(size: 10.5, weight: .medium)).foregroundStyle(.orange)
                }
                if let run = store.latestRun {
                    HStack(spacing: 7) {
                        Circle().fill(runStateColor(run.state)).frame(width: 7, height: 7)
                        Text("\(runStateLabel(run.state)) · started \(run.startedAt.formatted(date: .abbreviated, time: .shortened))")
                            .font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    }
                } else if !store.runStale, !store.runUnreadable {
                    Text("No recorded runs").font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                }
            } else if store.isEditing {
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

    private func modeChip(_ label: String, active: Bool) -> some View {
        Text(label).font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(active ? Theme.textPrimary : Theme.textSecondary)
            .padding(.horizontal, 10).padding(.vertical, 3)
            .background(active ? Theme.bg : .clear)
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    @ViewBuilder private var canvas: some View {
        if snapshot {
            snapshotCanvas
        } else {
            interactiveCanvas
        }
    }

    private var interactiveCanvas: some View {
        GeometryReader { viewport in
            ZStack(alignment: .topLeading) {
                WorkflowCanvasGrid(transform: nav.transform)
                if let workflow = store.displayed {
                    editCanvasContent(workflow, viewport: viewport.size)
                        .scaleEffect(nav.transform.scale, anchor: .topLeading)
                        .offset(nav.transform.offset)
                }
            }
            .frame(width: viewport.size.width, height: viewport.size.height, alignment: .topLeading)
            .clipped()
            .contentShape(Rectangle())
            .coordinateSpace(name: "workflowViewport")
            .gesture(panGesture)
            .simultaneousGesture(magnifyGesture)
            .onContinuousHover { phase in handleHover(phase) }
            .onDrop(of: [.workflowPaletteNode, .plainText], delegate: WorkflowCanvasDropDelegate(
                update: { location in updateDropHighlight(location) },
                exit: { spliceEdge = nil },
                perform: { location, info in performCanvasDrop(location, info: info) }))
            .overlay(alignment: .topLeading) {
                Label(store.isEditing ? "Drag nodes to arrange. Drag a port to connect. Drag empty space to pan."
                      : "Create a draft to move or connect nodes.", systemImage: "cursorarrow.motionlines")
                    .font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 10).padding(.vertical, 7).background(Theme.bg.opacity(0.88))
                    .clipShape(RoundedRectangle(cornerRadius: 8)).padding(14).allowsHitTesting(false)
            }
            .overlay(alignment: .bottomTrailing) { zoomControls(viewport.size) }
            .overlay { insertPickerOverlay(viewport.size) }
            .onAppear {
                nav.startMonitoring()
                store.paletteDragType = nil
            }
            .onDisappear { nav.stopMonitoring() }
            .background(GeometryReader { probe in
                Color.clear
                    .onAppear { nav.viewportGlobal = probe.frame(in: .global) }
                    .onChange(of: probe.frame(in: .global)) { _, frame in nav.viewportGlobal = frame }
            })
            .background(WorkflowCanvasWindowReader { window in nav.window = window })
        }
        .background(Theme.bg)
    }

    private func editCanvasContent(_ workflow: PulseWorkflowVersion, viewport: CGSize) -> some View {
        let definition = workflow.definition
        let offsets: [String: CGSize] = nodeDrag.map { [$0.nodeID: $0.offset] } ?? [:]
        let size = contentSize(definition, viewport: viewport)
        return ZStack(alignment: .topLeading) {
            wireLayer(definition, offsets: offsets)
            ForEach(definition.nodes) { node in
                if let point = WorkflowCardGeometry.position(of: node.id, in: definition, offsets: offsets) {
                    WorkflowNodeCard(node: node, spec: store.catalog?.node(for: node.type),
                                     selected: store.selectedNodeID == node.id,
                                     connecting: store.connectionSourceID == node.id,
                                     inputHighlighted: wireDrag?.targetID == node.id,
                                     portHitDiameter: 2 * WorkflowCardGeometry.portHitRadius / nav.transform.scale,
                                     onInput: store.isEditing ? { store.completeConnection(to: node.id) } : nil,
                                     onOutput: store.isEditing ? { store.startConnection(from: node.id) } : nil,
                                     onOutputDragChanged: store.isEditing ? { location in updateWireDrag(from: node.id, viewportPoint: location) } : nil,
                                     onOutputDragEnded: store.isEditing ? { location in finishWireDrag(viewportPoint: location) } : nil)
                    .position(x: point.x, y: point.y)
                    .zIndex(hoveredNodeID == node.id ? 4 : (store.selectedNodeID == node.id ? 3 : 1))
                    .onTapGesture { store.selectedNodeID = node.id }
                    .gesture(store.isEditing ? nodeDragGesture(node) : nil)
                    .onHover { hovering in
                        if hovering {
                            hoveredNodeID = node.id
                        } else if hoveredNodeID == node.id {
                            hoveredNodeID = nil
                        }
                    }
                }
            }
            if store.isEditing, wireDrag == nil, nodeDrag == nil, let hoverEdge,
               let midpoint = workflowWireMidpoint(hoverEdge, in: definition) {
                Button {
                    insertRequest = WorkflowInsertRequest(point: midpoint, edge: hoverEdge, connectFrom: nil)
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 10, weight: .bold)).foregroundStyle(Theme.accent)
                        .frame(width: 22, height: 22).background(Theme.bg)
                        .clipShape(Circle())
                        .overlay(Circle().stroke(Theme.accent.opacity(0.7), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .position(x: midpoint.x, y: midpoint.y)
                .zIndex(6)
                .help("Insert a node here")
            }
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
    }

    private func wireLayer(_ definition: PulseWorkflowDefinition, offsets: [String: CGSize]) -> some View {
        Canvas { context, _ in
            for edge in definition.edges {
                guard let start = WorkflowCardGeometry.outputPort(for: edge.from, in: definition, offsets: offsets),
                      let end = WorkflowCardGeometry.inputPort(for: edge.to, in: definition, offsets: offsets) else { continue }
                let emphasized = edge == spliceEdge || edge == hoverEdge
                context.stroke(edgePath(start, end),
                               with: .color(emphasized ? Theme.accent.opacity(0.85) : Theme.textSecondary.opacity(0.52)),
                               lineWidth: emphasized ? 2.5 : 1.5)
            }
            if let wireDrag,
               let start = WorkflowCardGeometry.outputPort(for: wireDrag.sourceID, in: definition, offsets: offsets) {
                let end = wireDrag.targetID.flatMap { WorkflowCardGeometry.inputPort(for: $0, in: definition, offsets: offsets) } ?? wireDrag.point
                context.stroke(edgePath(start, end), with: .color(Theme.accent.opacity(0.9)),
                               style: StrokeStyle(lineWidth: 2, lineCap: .round,
                                                  dash: wireDrag.targetID == nil ? [6, 5] : []))
            }
        }
    }

    private func contentSize(_ definition: PulseWorkflowDefinition, viewport: CGSize) -> CGSize {
        let visible = nav.transform.toCanvas(CGPoint(x: viewport.width, y: viewport.height))
        let width = max((definition.positions.values.map(\.x).max() ?? 0) + 600, visible.x + 200)
        let height = max((definition.positions.values.map(\.y).max() ?? 0) + 500, visible.y + 200)
        return CGSize(width: width, height: height)
    }

    private var panGesture: some Gesture {
        DragGesture(minimumDistance: 2, coordinateSpace: .named("workflowViewport"))
            .onChanged { value in
                let anchor = panAnchor ?? nav.transform.offset
                panAnchor = anchor
                nav.transform.offset = CGSize(width: anchor.width + value.translation.width,
                                              height: anchor.height + value.translation.height)
            }
            .onEnded { _ in panAnchor = nil }
    }

    private var magnifyGesture: some Gesture {
        MagnifyGesture()
            .onChanged { value in
                let last = magnifyLast ?? 1
                magnifyLast = value.magnification
                nav.transform.zoom(by: value.magnification / last, around: value.startLocation)
            }
            .onEnded { _ in magnifyLast = nil }
    }

    private func nodeDragGesture(_ node: PulseWorkflowNode) -> some Gesture {
        DragGesture(minimumDistance: 1, coordinateSpace: .named("workflowViewport"))
            .onChanged { value in
                let translation = CGSize(width: value.translation.width / nav.transform.scale,
                                         height: value.translation.height / nav.transform.scale)
                nodeDrag = WorkflowCanvasDragState(nodeID: node.id, offset: translation)
                guard let definition = store.displayed?.definition,
                      isUnconnected(node.id, in: definition),
                      let position = WorkflowCardGeometry.position(of: node.id, in: definition,
                                                                   offsets: [node.id: translation]) else {
                    spliceEdge = nil
                    return
                }
                spliceEdge = nearestWorkflowEdge(to: position, in: definition)
            }
            .onEnded { value in
                let translation = CGSize(width: value.translation.width / nav.transform.scale,
                                         height: value.translation.height / nav.transform.scale)
                store.completeNodeDrag(node.id, by: translation, splicing: spliceEdge)
                nodeDrag = nil
                spliceEdge = nil
            }
    }

    private func isUnconnected(_ id: String, in definition: PulseWorkflowDefinition) -> Bool {
        !definition.edges.contains { $0.from == id || $0.to == id }
    }

    private func cardID(at point: CGPoint, in definition: PulseWorkflowDefinition) -> String? {
        for node in definition.nodes.reversed() {
            guard let center = WorkflowCardGeometry.position(of: node.id, in: definition) else { continue }
            if abs(point.x - center.x) <= WorkflowCardGeometry.size.width / 2,
               abs(point.y - center.y) <= WorkflowCardGeometry.size.height / 2 {
                return node.id
            }
        }
        return nil
    }

    private func updateWireDrag(from sourceID: String, viewportPoint: CGPoint) {
        guard let definition = store.displayed?.definition else { return }
        let point = nav.transform.toCanvas(viewportPoint)
        let target = WorkflowCardGeometry.nearestInputPort(to: point, in: definition, excluding: sourceID,
                                                           within: WorkflowCardGeometry.portHitRadius / nav.transform.scale)
        wireDrag = WorkflowWireDrag(sourceID: sourceID, point: point, targetID: target)
        hoverEdge = nil
    }

    private func finishWireDrag(viewportPoint: CGPoint) {
        guard let drag = wireDrag else { return }
        wireDrag = nil
        guard let definition = store.displayed?.definition else { return }
        let point = nav.transform.toCanvas(viewportPoint)
        if let target = WorkflowCardGeometry.nearestInputPort(to: point, in: definition, excluding: drag.sourceID,
                                                              within: WorkflowCardGeometry.portHitRadius / nav.transform.scale) {
            store.connect(from: drag.sourceID, to: target)
        } else if let card = cardID(at: point, in: definition), card != drag.sourceID {
            store.connect(from: drag.sourceID, to: card)
        } else if !insertableEntries.isEmpty {
            insertRequest = WorkflowInsertRequest(point: point, edge: nil, connectFrom: drag.sourceID)
        }
    }

    private func handleHover(_ phase: HoverPhase) {
        switch phase {
        case .active(let location):
            guard store.isEditing, wireDrag == nil, nodeDrag == nil, insertRequest == nil,
                  let definition = store.displayed?.definition else {
                hoverEdge = nil
                return
            }
            let point = nav.transform.toCanvas(location)
            guard cardID(at: point, in: definition) == nil else {
                hoverEdge = nil
                return
            }
            hoverEdge = nearestWorkflowEdge(to: point, in: definition,
                                            within: WorkflowCardGeometry.wireHoverRange / nav.transform.scale)
        case .ended:
            hoverEdge = nil
        }
    }

    private func updateDropHighlight(_ viewportPoint: CGPoint) {
        guard store.mode == .edit, let definition = store.displayed?.definition else { return }
        if let type = store.paletteDragType, let catalog = store.catalog,
           catalog.canonicalIndex(of: type) != nil,
           definition.nodes.contains(where: { $0.type == type }) {
            spliceEdge = nil
            return
        }
        spliceEdge = nearestWorkflowEdge(to: nav.transform.toCanvas(viewportPoint), in: definition)
    }

    private func performCanvasDrop(_ viewportPoint: CGPoint, info: DropInfo) -> Bool {
        let point = nav.transform.toCanvas(viewportPoint)
        spliceEdge = nil
        if info.hasItemsConforming(to: [.workflowPaletteNode]) {
            if let type = store.paletteDragType {
                store.paletteDragType = nil
                guard store.catalog?.node(for: type) != nil else { return false }
                Task { await store.placeNode(type, at: point) }
                return true
            }
            guard let provider = info.itemProviders(for: [.workflowPaletteNode]).first else { return false }
            provider.loadDataRepresentation(forTypeIdentifier: UTType.workflowPaletteNode.identifier) { data, _ in
                guard let data, let type = String(data: data, encoding: .utf8) else { return }
                Task { @MainActor in
                    guard store.catalog?.node(for: type) != nil else { return }
                    await store.placeNode(type, at: point)
                }
            }
            return true
        }
        guard let provider = info.itemProviders(for: [.plainText]).first else { return false }
        provider.loadObject(ofClass: NSString.self) { object, _ in
            guard let type = object as? String else { return }
            Task { @MainActor in
                guard store.catalog?.node(for: type) != nil else { return }
                await store.placeNode(type, at: point)
            }
        }
        return true
    }

    private var insertableEntries: [WorkflowCatalogNode] {
        guard let catalog = store.catalog else { return [] }
        return catalog.nodes.filter { $0.insertable && catalog.profile.insertableCategories.contains($0.category) }
    }

    private func zoomControls(_ viewport: CGSize) -> some View {
        let center = CGPoint(x: viewport.width / 2, y: viewport.height / 2)
        return HStack(spacing: 0) {
            Button { nav.transform.zoom(by: 1 / 1.2, around: center) } label: {
                Image(systemName: "minus").frame(width: 26, height: 24).contentShape(Rectangle())
            }
            Button { nav.transform = WorkflowCanvasTransform() } label: {
                Text("\(Int((nav.transform.scale * 100).rounded()))%")
                    .font(.system(size: 10, weight: .semibold).monospacedDigit())
                    .frame(width: 40, height: 24).contentShape(Rectangle())
            }
            Button { nav.transform.zoom(by: 1.2, around: center) } label: {
                Image(systemName: "plus").frame(width: 26, height: 24).contentShape(Rectangle())
            }
        }
        .buttonStyle(.plain)
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(Theme.textSecondary)
        .background(Theme.surface.opacity(0.92))
        .clipShape(RoundedRectangle(cornerRadius: 7))
        .overlay(RoundedRectangle(cornerRadius: 7).stroke(Theme.hairline, lineWidth: 1))
        .padding(12)
    }

    @ViewBuilder private func insertPickerOverlay(_ viewport: CGSize) -> some View {
        if let request = insertRequest {
            let entries = insertableEntries
            let screen = nav.transform.toScreen(request.point)
            let height = CGFloat(entries.count) * 40 + 46
            let x = min(max(screen.x + 8, 8), max(8, viewport.width - 236))
            let y = min(max(screen.y + 8, 8), max(8, viewport.height - height - 8))
            ZStack(alignment: .topLeading) {
                Color.black.opacity(0.001)
                    .contentShape(Rectangle())
                    .onTapGesture { insertRequest = nil }
                VStack(alignment: .leading, spacing: 4) {
                    Text("INSERT NODE").font(.system(size: 9, weight: .bold)).tracking(0.7)
                        .foregroundStyle(Theme.textSecondary.opacity(0.72))
                        .padding(.bottom, 3)
                    if entries.isEmpty {
                        Text("No insertable nodes available.")
                            .font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                    }
                    ForEach(entries) { entry in
                        Button {
                            performInsert(entry.type, for: request)
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: entry.icon).font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(graphTint(entry.tint)).frame(width: 24, height: 24)
                                    .background(graphTint(entry.tint).opacity(0.12))
                                    .clipShape(RoundedRectangle(cornerRadius: 6))
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(entry.label).font(.system(size: 11, weight: .semibold))
                                        .foregroundStyle(Theme.textPrimary)
                                    Text(entry.description).font(.system(size: 9)).foregroundStyle(Theme.textSecondary)
                                        .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                                }
                                Spacer(minLength: 0)
                            }
                            .padding(.horizontal, 6).padding(.vertical, 5).frame(minHeight: 36).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(10).frame(width: 228)
                .background(Theme.surface)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.hairline, lineWidth: 1))
                .shadow(color: Theme.bg.opacity(0.5), radius: 14, y: 4)
                .offset(x: x, y: y)
            }
        }
    }

    private func performInsert(_ type: String, for request: WorkflowInsertRequest) {
        insertRequest = nil
        hoverEdge = nil
        guard store.isEditing else { return }
        store.placeNodeInDraft(type, at: request.point, into: request.edge)
        if let source = request.connectFrom, let placed = store.selectedNodeID, placed != source {
            store.connect(from: source, to: placed)
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
                    guard let start = WorkflowCardGeometry.outputPort(for: edge.from, in: workflow.definition),
                          let end = WorkflowCardGeometry.inputPort(for: edge.to, in: workflow.definition) else { continue }
                    context.stroke(edgePath(start, end),
                                   with: .color(Theme.textSecondary.opacity(0.52)), lineWidth: 1.5)
                }
            }
            ForEach(workflow.definition.nodes) { node in
                if let point = workflow.definition.positions[node.id] {
                    WorkflowNodeCard(node: node, spec: store.catalog?.node(for: node.type),
                                     selected: store.selectedNodeID == node.id)
                        .position(x: point.x, y: point.y)
                }
            }
            Label("Drag nodes to arrange. Drag a port to connect.", systemImage: "cursorarrow.motionlines")
                .font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 10).padding(.vertical, 7).background(Theme.bg.opacity(0.88))
                .clipShape(RoundedRectangle(cornerRadius: 8)).padding(14)
        }
    }

    @ViewBuilder private var runCanvas: some View {
        if let workflow = store.runVersion, let run = store.latestRun {
            if snapshot {
                runGraph(workflow, run: run)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Theme.bg)
            } else {
                GeometryReader { viewport in
                    ScrollView([.horizontal, .vertical]) {
                        let width = max(viewport.size.width, (workflow.definition.positions.values.map(\.x).max() ?? 0) + 180)
                        let height = max(viewport.size.height, (workflow.definition.positions.values.map(\.y).max() ?? 0) + 130)
                        runGraph(workflow, run: run)
                            .frame(width: width, height: height)
                    }
                    .background(Theme.bg)
                }
            }
        }
    }

    private func runGraph(_ workflow: PulseWorkflowVersion, run: PulseWorkflowRun) -> some View {
        ZStack(alignment: .topLeading) {
            DotGrid()
            Canvas { context, _ in
                for edge in workflow.definition.edges {
                    guard let start = WorkflowCardGeometry.outputPort(for: edge.from, in: workflow.definition),
                          let end = WorkflowCardGeometry.inputPort(for: edge.to, in: workflow.definition) else { continue }
                    context.stroke(edgePath(start, end),
                                   with: .color(Theme.textSecondary.opacity(0.52)), lineWidth: 1.5)
                }
            }
            ForEach(workflow.definition.nodes) { node in
                if let point = workflow.definition.positions[node.id] {
                    WorkflowRunNodeCard(node: node, spec: store.catalog?.node(for: node.type),
                                        run: run.nodeRun(node.id),
                                        selected: store.selectedNodeID == node.id)
                        .position(x: point.x, y: point.y)
                        .onTapGesture { store.selectedNodeID = node.id }
                }
            }
        }
    }

    @ViewBuilder private var inspector: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let selected = store.displayed?.definition.nodes.first(where: { $0.id == store.selectedNodeID }) {
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
                Group {
                    if snapshot {
                        inspectorContent(selected: selected, spec: spec)
                    } else {
                        ScrollView { inspectorContent(selected: selected, spec: spec) }
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

    private func inspectorContent(selected: PulseWorkflowNode, spec: WorkflowCatalogNode?) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            inspectorSection("Parameters") {
                if store.isEditing {
                    controls(for: selected, spec: spec)
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
            if store.isEditing, store.catalog?.isRequired(selected.type) == false {
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
                    let peer = store.displayed?.definition.node(withID: peerID)
                    HStack(spacing: 7) {
                        Image(systemName: edge.from == node.id ? "arrow.right" : "arrow.left")
                            .font(.system(size: 9, weight: .bold)).foregroundStyle(Theme.textSecondary)
                        Text(peer.map { store.catalog?.label(for: $0.type) ?? workflowDisplayName($0.type) } ?? peerID)
                            .font(.system(size: 10.5, weight: .medium))
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

    @ViewBuilder private func controls(for node: PulseWorkflowNode, spec: WorkflowCatalogNode?) -> some View {
        if let fields = spec?.fields, !fields.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                ForEach(fields) { field in
                    fieldControl(field, node: node)
                }
            }
        } else {
            Text("This node has no editable parameters.").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
        }
    }

    @ViewBuilder private func fieldControl(_ field: WorkflowSchemaField, node: PulseWorkflowNode) -> some View {
        let current = node.config[field.key] ?? field.defaultValue
        switch field.kind {
        case .choice(let options):
            VStack(alignment: .leading, spacing: 6) {
                Text(field.label).font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                if snapshot {
                    Text((current ?? options.first)?.display ?? "Not set")
                        .font(.system(size: 11, weight: .semibold))
                } else {
                    let picker = Picker("", selection: Binding(get: { current ?? options[0] }, set: { value in store.setConfigValue(field.key, value) })) {
                        ForEach(options, id: \.self) { option in
                            Text(option.display).tag(option)
                        }
                    }
                    .labelsHidden()
                    if options.count <= 3 {
                        picker.pickerStyle(.segmented).frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        picker.pickerStyle(.menu).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        case .number(let low, let high):
            let value = current?.doubleValue ?? low ?? high ?? 0
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text(field.label).font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    Spacer()
                    if let low, let high, low < high {
                        let step = high - low <= 2 ? 0.1 : 1
                        Text(value.formatted(.number.precision(.fractionLength(step < 1 ? 1 : 0))))
                            .font(.system(size: 10.5, weight: .semibold).monospacedDigit())
                    }
                }
                if let low, let high, low < high {
                    let step = high - low <= 2 ? 0.1 : 1
                    if snapshot {
                        GeometryReader { proxy in
                            let fraction = (value - low) / (high - low)
                            ZStack(alignment: .leading) {
                                Capsule().fill(Theme.surface)
                                Capsule().fill(Theme.accent).frame(width: proxy.size.width * fraction)
                                Circle().fill(Theme.textPrimary).frame(width: 12, height: 12)
                                    .offset(x: max(0, proxy.size.width * fraction - 6))
                            }
                        }
                        .frame(height: 12)
                    } else {
                        Slider(value: Binding(get: { min(max(value, low), high) }, set: { newValue in
                            store.setConfigValue(field.key, step < 1 ? .double((newValue * 10).rounded() / 10) : .double(newValue.rounded()))
                        }), in: low...high, step: step)
                    }
                } else if snapshot {
                    Text(value.formatted(.number))
                        .font(.system(size: 10.5, weight: .semibold).monospacedDigit())
                } else {
                    TextField("", value: Binding(get: { value }, set: { newValue in
                        var bounded = newValue
                        if let low { bounded = max(low, bounded) }
                        if let high { bounded = min(high, bounded) }
                        store.setConfigValue(field.key, .double(bounded))
                    }), format: .number)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 11))
                }
            }
        case .bool:
            let flag: Bool = {
                if case .bool(let value) = current { return value }
                return false
            }()
            if snapshot {
                VStack(alignment: .leading, spacing: 6) {
                    Text(field.label).font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    Text(flag ? "On" : "Off").font(.system(size: 11, weight: .semibold))
                }
            } else {
                Toggle(field.label, isOn: Binding(get: { flag },
                                                  set: { value in store.setConfigValue(field.key, .bool(value)) }))
                .toggleStyle(.switch).font(.system(size: 11))
            }
        case .text:
            let value: String = {
                if case .string(let text) = current { return text }
                return ""
            }()
            VStack(alignment: .leading, spacing: 6) {
                Text(field.label).font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                if snapshot {
                    Text(value.isEmpty ? "Not set" : value)
                        .font(.system(size: 11))
                        .foregroundStyle(value.isEmpty ? Theme.textSecondary : Theme.textPrimary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    TextField("", text: Binding(get: { value }, set: { newValue in
                        store.setConfigValue(field.key, .string(newValue))
                    }), axis: .vertical)
                    .lineLimit(1...6)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 11))
                }
            }
        }
    }
}

struct WorkflowNodeCard: View {
    let node: PulseWorkflowNode
    var spec: WorkflowCatalogNode?
    var selected: Bool
    var connecting: Bool = false
    var inputHighlighted: Bool = false
    var portHitDiameter: CGFloat = 2 * WorkflowCardGeometry.portHitRadius
    var onInput: (() -> Void)? = nil
    var onOutput: (() -> Void)? = nil
    var onOutputDragChanged: ((CGPoint) -> Void)? = nil
    var onOutputDragEnded: ((CGPoint) -> Void)? = nil

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: spec?.icon ?? "puzzlepiece").font(.system(size: 14, weight: .semibold))
                .foregroundStyle(graphTint(spec?.tint ?? "accent"))
                .frame(width: 34, height: 34).background(graphTint(spec?.tint ?? "accent").opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 2) {
                Text(spec?.label ?? workflowDisplayName(node.type)).font(.system(size: 11.5, weight: .semibold)).lineLimit(1)
                Text(spec?.categoryLabel ?? "Node").font(.system(size: 9.5)).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 11)
        .frame(width: WorkflowCardGeometry.size.width, height: WorkflowCardGeometry.size.height)
        .background(selected ? Theme.surface.opacity(1) : Theme.surface.opacity(0.9))
        .clipShape(RoundedRectangle(cornerRadius: 11))
        .overlay(RoundedRectangle(cornerRadius: 11).stroke(connecting || selected ? Theme.accent : Theme.hairline, lineWidth: connecting || selected ? 1.5 : 1))
        .shadow(color: Theme.bg.opacity(0.35), radius: selected ? 8 : 3, y: 2)
        .overlay(alignment: .leading) { inputPort.offset(x: onInput != nil ? -portHitDiameter / 2 : -4) }
        .overlay(alignment: .trailing) { outputPort.offset(x: onOutput != nil ? portHitDiameter / 2 : 4) }
    }

    @ViewBuilder private var inputPort: some View {
        if let onInput {
            WorkflowEditorPort(highlighted: inputHighlighted, hitDiameter: portHitDiameter, onTap: onInput)
        } else {
            PortDot()
        }
    }

    @ViewBuilder private var outputPort: some View {
        if let onOutput {
            WorkflowEditorPort(highlighted: connecting, hitDiameter: portHitDiameter, onTap: onOutput,
                               dragChanged: onOutputDragChanged, dragEnded: onOutputDragEnded)
        } else {
            PortDot()
        }
    }
}

struct WorkflowEditorPort: View {
    var highlighted: Bool
    var hitDiameter: CGFloat
    var onTap: () -> Void
    var dragChanged: ((CGPoint) -> Void)? = nil
    var dragEnded: ((CGPoint) -> Void)? = nil
    @State private var hovering = false

    private var emphasized: Bool { highlighted || hovering }

    var body: some View {
        Circle().fill(Theme.bg)
            .overlay(Circle().stroke(emphasized ? Theme.accent : .white.opacity(0.22),
                                     lineWidth: emphasized ? 1.5 : 1))
            .frame(width: emphasized ? 11 : 7, height: emphasized ? 11 : 7)
            .frame(width: hitDiameter, height: hitDiameter)
            .contentShape(Circle())
            .onHover { hovering = $0 }
            .gesture(dragChanged != nil && dragEnded != nil
                     ? DragGesture(minimumDistance: 3, coordinateSpace: .named("workflowViewport"))
                         .onChanged { value in dragChanged?(value.location) }
                         .onEnded { value in dragEnded?(value.location) }
                     : nil)
            .onTapGesture(perform: onTap)
            .help("Connect")
    }
}

struct WorkflowCanvasGrid: View {
    var transform: WorkflowCanvasTransform

    var body: some View {
        Canvas { context, size in
            let step = 24 * transform.scale
            guard step >= 7 else { return }
            let dot = max(1.2, 2 * transform.scale)
            let phaseX = (transform.offset.width + 12 * transform.scale).truncatingRemainder(dividingBy: step)
            let phaseY = (transform.offset.height + 12 * transform.scale).truncatingRemainder(dividingBy: step)
            var y = phaseY - step
            while y < size.height {
                var x = phaseX - step
                while x < size.width {
                    context.fill(Path(ellipseIn: CGRect(x: x, y: y, width: dot, height: dot)),
                                 with: .color(.white.opacity(0.05)))
                    x += step
                }
                y += step
            }
        }
    }
}

struct WorkflowCanvasDropDelegate: DropDelegate {
    let update: (CGPoint) -> Void
    let exit: () -> Void
    let perform: (CGPoint, DropInfo) -> Bool

    func validateDrop(info: DropInfo) -> Bool {
        info.hasItemsConforming(to: [.workflowPaletteNode, .plainText])
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        update(info.location)
        return DropProposal(operation: .copy)
    }

    func dropExited(info: DropInfo) {
        exit()
    }

    func performDrop(info: DropInfo) -> Bool {
        perform(info.location, info)
    }
}

private final class WorkflowCanvasWindowProbe: NSView {
    var onWindow: ((NSWindow?) -> Void)?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        onWindow?(window)
    }
}

struct WorkflowCanvasWindowReader: NSViewRepresentable {
    var onWindow: (NSWindow?) -> Void

    func makeNSView(context: Context) -> NSView {
        let view = WorkflowCanvasWindowProbe()
        view.onWindow = onWindow
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {}
}

struct WorkflowNodeCardPreview: View {
    let spec: WorkflowCatalogNode

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: spec.icon).font(.system(size: 14, weight: .semibold))
                .foregroundStyle(graphTint(spec.tint))
                .frame(width: 34, height: 34).background(graphTint(spec.tint).opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 9))
            VStack(alignment: .leading, spacing: 2) {
                Text(spec.label).font(.system(size: 11.5, weight: .semibold)).lineLimit(1)
                Text(spec.categoryLabel).font(.system(size: 9.5)).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 11)
        .frame(width: WorkflowCardGeometry.size.width, height: WorkflowCardGeometry.size.height)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 11))
        .overlay(RoundedRectangle(cornerRadius: 11).stroke(Theme.accent, lineWidth: 1.5))
    }
}

struct PulseWorkflowEditorShot: View {
    @StateObject private var store = PulseWorkflowStore.fixture(editing: true)
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
                PulseWorkflowPalette(store: store, searchText: $searchText, snapshot: true)
            }
            .frame(width: 248).background(Theme.sidebar)
            Divider().overlay(Theme.hairline)
            PulseWorkflowEditor(store: store, snapshot: true)
        }
    }
}
