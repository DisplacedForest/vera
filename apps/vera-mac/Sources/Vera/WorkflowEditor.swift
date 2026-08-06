import SwiftUI
import UniformTypeIdentifiers

extension UTType {
    static let workflowPaletteNode = UTType(exportedAs: "app.vera.workflow-node")
}

struct WorkflowNode: Identifiable, Hashable {
    var id: String
    var type: String
    var config: [String: WorkflowConfigValue]
    var rule: String? = nil

    var jsonConfig: [String: Any] {
        config.mapValues(\.jsonValue)
    }
}

struct WorkflowEdge: Hashable {
    var from: String
    var to: String
}

struct WorkflowPoint: Hashable {
    var x: CGFloat
    var y: CGFloat

    static func parse(_ value: Any) -> WorkflowPoint? {
        guard let point = value as? [String: Any],
              let x = point["x"] as? Double,
              let y = point["y"] as? Double else { return nil }
        return WorkflowPoint(x: x, y: y)
    }

    var jsonObject: [String: Double] { ["x": x, "y": y] }
}

struct WorkflowDefinition: Hashable {
    var id: String
    var nodes: [WorkflowNode]
    var edges: [WorkflowEdge]
    var positions: [String: WorkflowPoint]

    mutating func normalizeEdgeOrder() {
        let order = Dictionary(uniqueKeysWithValues: nodes.enumerated().map { ($0.element.id, $0.offset) })
        edges.sort {
            let left = order[$0.from] ?? Int.max
            let right = order[$1.from] ?? Int.max
            return left == right ? (order[$0.to] ?? Int.max) < (order[$1.to] ?? Int.max) : left < right
        }
    }

    func node(withID id: String) -> WorkflowNode? {
        nodes.first { $0.id == id }
    }

    func jsonObject() -> [String: Any] {
        ["id": id,
         "nodes": nodes.map { node -> [String: Any] in
             var raw: [String: Any] = ["id": node.id, "type": node.type, "config": node.jsonConfig]
             if let rule = node.rule { raw["rule"] = rule }
             return raw
         },
         "edges": edges.map { ["from": $0.from, "to": $0.to] },
         "positions": positions.mapValues(\.jsonObject)]
    }

    static func parse(_ value: Any) -> WorkflowDefinition? {
        guard let object = value as? [String: Any],
              let id = object["id"] as? String,
              let rawNodes = object["nodes"] as? [[String: Any]],
              let rawEdges = object["edges"] as? [[String: Any]] else { return nil }
        let nodes = rawNodes.compactMap { raw -> WorkflowNode? in
            guard let id = raw["id"] as? String, let type = raw["type"] as? String else { return nil }
            let config = (raw["config"] as? [String: Any] ?? [:]).mapValues(WorkflowConfigValue.parse)
            return WorkflowNode(id: id, type: type, config: config, rule: raw["rule"] as? String)
        }
        let edges = rawEdges.compactMap { raw -> WorkflowEdge? in
            guard let from = raw["from"] as? String, let to = raw["to"] as? String else { return nil }
            return WorkflowEdge(from: from, to: to)
        }
        let savedPositions = (object["positions"] as? [String: Any] ?? [:]).compactMapValues(WorkflowPoint.parse)
        let positions = nodes.enumerated().reduce(into: savedPositions) { result, item in
            if result[item.element.id] == nil {
                result[item.element.id] = WorkflowPoint(x: 110 + CGFloat(item.offset) * WorkflowCardGeometry.placementPitch, y: 210)
            }
        }
        return nodes.isEmpty ? nil : WorkflowDefinition(id: id, nodes: nodes, edges: edges, positions: positions)
    }
}

extension WorkflowCatalog {
    func validationMessage(for definition: WorkflowDefinition) -> String? {
        let triggerIDs = triggerIDs(in: definition)
        if triggerIDs.count > 1 { return "Only one trigger is allowed." }
        if definition.edges.contains(where: { triggerIDs.contains($0.to) }) {
            return "A trigger starts the workflow and cannot take an input."
        }
        var body = definition
        body.nodes.removeAll { triggerIDs.contains($0.id) }
        body.edges.removeAll { triggerIDs.contains($0.from) || triggerIDs.contains($0.to) }
        var counts: [String: Int] = [:]
        for node in body.nodes { counts[node.type, default: 0] += 1 }
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
        guard !profile.spine.isEmpty else {
            guard isAcyclic(definition) else { return "Remove the cycle from this workflow." }
            return triggerConnectionMessage(triggerIDs, in: definition, head: nil)
        }
        for entry in body.nodes {
            if profile.spine.contains(entry.type) || profile.pairTypes.contains(entry.type) { continue }
            guard let spec = node(for: entry.type), spec.insertable,
                  profile.insertableCategories.contains(spec.category) else {
                return "\(label(for: entry.type)) cannot be added to this workflow."
            }
        }
        guard !body.nodes.isEmpty else { return "Add the required \(label(for: profile.spine.first ?? "")) node." }
        guard let path = singlePath(body) else { return "Connect every node into a single path." }
        if let message = triggerConnectionMessage(triggerIDs, in: definition, head: path.first) {
            return message
        }
        let order = path.compactMap { id in body.node(withID: id)?.type }
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

    private func triggerConnectionMessage(_ triggerIDs: Set<String>, in definition: WorkflowDefinition,
                                          head: String?) -> String? {
        for id in triggerIDs {
            let outgoing = definition.edges.filter { $0.from == id }
            if outgoing.count != 1 || (head != nil && outgoing.first?.to != head) {
                return "Connect the trigger to the start of the workflow."
            }
        }
        return nil
    }

    func promotionMessage(for definition: WorkflowDefinition) -> String? {
        for type in profile.triggers where !definition.nodes.contains(where: { $0.type == type }) {
            return "Add a \(label(for: type)) trigger so this workflow can start on its own."
        }
        return nil
    }

    func allowsConnection(from source: WorkflowNode, to target: WorkflowNode, in definition: WorkflowDefinition) -> Bool {
        guard source.id != target.id else { return false }
        guard !isTriggerType(target.type) else { return false }
        guard let sourceIndex = canonicalIndex(of: source.type),
              let targetIndex = canonicalIndex(of: target.type) else { return true }
        guard sourceIndex < targetIndex else { return false }
        let installedBetween = definition.nodes.contains { node in
            guard let index = canonicalIndex(of: node.type) else { return false }
            return index > sourceIndex && index < targetIndex
        }
        return !installedBetween
    }

    private func singlePath(_ definition: WorkflowDefinition) -> [String]? {
        var outgoing: [String: String] = [:]
        var indegree = Dictionary(uniqueKeysWithValues: definition.nodes.map { ($0.id, 0) })
        var seen = Set<WorkflowEdge>()
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

    private func isAcyclic(_ definition: WorkflowDefinition) -> Bool {
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

struct WorkflowVersion: Hashable {
    var id: String
    var number: Int
    var state: String
    var definition: WorkflowDefinition

    static func parse(_ value: Any) -> WorkflowVersion? {
        guard let object = value as? [String: Any],
              let id = object["id"] as? String,
              let number = object["version"] as? Int,
              let state = object["state"] as? String,
              let definition = WorkflowDefinition.parse(object["definition"] as Any) else { return nil }
        return WorkflowVersion(id: id, number: number, state: state, definition: definition)
    }
}

enum WorkflowOutcome {
    case success(WorkflowVersion)
    case rejected(String)
    case failed
}

struct WorkflowFlowFace: Hashable {
    var label: String
    var description: String?

    static func parse(_ raw: Any?) -> WorkflowFlowFace? {
        guard let object = raw as? [String: Any], let label = object["label"] as? String else { return nil }
        return WorkflowFlowFace(label: label, description: object["description"] as? String)
    }
}

@MainActor
struct WorkflowClient {
    let base: URL
    let workflowID: String

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

    private func request(path: String, method: String = "GET", body: [String: Any]? = nil) async -> WorkflowOutcome {
        guard let (data, status) = await fetch(path: path, method: method, body: body) else { return .failed }
        if (200..<300).contains(status),
           let object = try? JSONSerialization.jsonObject(with: data),
           let wrapped = object as? [String: Any],
           let version = WorkflowVersion.parse(wrapped["workflow"] as Any) {
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
        guard let (data, status) = await fetch(path: "/agentic/workflows/\(workflowID)/catalog"),
              (200..<300).contains(status),
              let object = try? JSONSerialization.jsonObject(with: data) else { return nil }
        return WorkflowCatalog.parse(object)
    }

    func overview() async -> (version: WorkflowVersion, latestRun: WorkflowRun?, runUnreadable: Bool,
                              triggerJob: SchedulerJob?, face: WorkflowFlowFace?)? {
        guard let (data, status) = await fetch(path: "/agentic/workflows/\(workflowID)"),
              (200..<300).contains(status),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let version = WorkflowVersion.parse(object["workflow"] as Any) else { return nil }
        let run = WorkflowRun.classify(object["latest_run"])
        return (version, run.run, run.unreadable, Self.triggerJob(from: object["trigger_job"]),
                WorkflowFlowFace.parse(object["flow"]))
    }

    static func triggerJob(from raw: Any?) -> SchedulerJob? {
        guard let job = raw as? [String: Any] else { return nil }
        return SchedulerState.parse(["jobs": [job]])?.jobs.first
    }

    func createDraft() async -> WorkflowVersion? {
        if case .success(let version) = await request(path: "/agentic/workflows/\(workflowID)/drafts", method: "POST") { return version }
        return nil
    }

    func save(_ version: WorkflowVersion) async -> WorkflowOutcome {
        await request(path: "/agentic/workflow-drafts/\(version.id)", method: "PUT", body: ["definition": version.definition.jsonObject()])
    }

    func promote(_ version: WorkflowVersion) async -> WorkflowOutcome {
        await request(path: "/agentic/workflow-drafts/\(version.id)/promote", method: "POST")
    }
}

@MainActor
final class WorkflowEditorStore: ObservableObject {
    enum Phase { case loading, unavailable, ready }
    enum Mode { case edit, run }
    @Published var phase: Phase = .loading
    @Published var mode: Mode = .edit
    @Published var catalog: WorkflowCatalog?
    @Published var active: WorkflowVersion?
    @Published var draft: WorkflowVersion?
    @Published var latestRun: WorkflowRun?
    @Published var triggerJob: SchedulerJob?
    @Published var runUnreadable = false
    @Published var runStale = false
    @Published var selectedNodeID: String?
    @Published var connectionSourceID: String?
    @Published var busy = false
    @Published var note: String?
    @Published var flowID = "pulse"
    @Published var flowFace: WorkflowFlowFace?
    var paletteDragType: String?

    private var base: URL?
    private var client: WorkflowClient?

    var displayed: WorkflowVersion? { draft ?? active }
    var isEditing: Bool { draft != nil }
    var isLocked: Bool { catalog?.profile.locked == true }
    var allowsStructuralEditing: Bool { isEditing && !isLocked }
    var flowLabel: String { flowFace?.label ?? workflowDisplayName(flowID) }
    var flowDescription: String { flowFace?.description ?? "Build and configure this workflow" }
    var changed: Bool { draft?.definition != active?.definition }
    var validationMessage: String? {
        guard let catalog, let definition = displayed?.definition else { return nil }
        return catalog.validationMessage(for: definition)
    }
    var promotionMessage: String? {
        guard let catalog, let definition = displayed?.definition else { return nil }
        return catalog.promotionMessage(for: definition)
    }
    var canSave: Bool { isEditing && changed && validationMessage == nil && !busy }
    var canPromote: Bool { isEditing && validationMessage == nil && promotionMessage == nil && !busy }

    func configure(base: URL?) {
        self.base = base
        client = base.map { WorkflowClient(base: $0, workflowID: flowID) }
        if client == nil { phase = .unavailable }
    }

    func select(_ id: String) async {
        guard id != flowID else { return }
        flowID = id
        flowFace = nil
        client = base.map { WorkflowClient(base: $0, workflowID: id) }
        catalog = nil
        active = nil
        draft = nil
        latestRun = nil
        triggerJob = nil
        runUnreadable = false
        runStale = false
        selectedNodeID = nil
        connectionSourceID = nil
        note = nil
        mode = .edit
        phase = .loading
        await refresh()
    }

    func refresh() async {
        guard let client else { phase = .unavailable; return }
        guard let served = await client.catalog() else { phase = .unavailable; return }
        catalog = served
        guard let overview = await client.overview() else { phase = .unavailable; return }
        active = overview.version
        latestRun = overview.latestRun
        triggerJob = overview.triggerJob
        runUnreadable = overview.runUnreadable
        flowFace = overview.face ?? flowFace
        runStale = false
        if draft == nil { selectedNodeID = overview.version.definition.nodes.first?.id }
        phase = .ready
    }

    var runVersion: WorkflowVersion? { latestRun?.version ?? active }

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
        triggerJob = overview.triggerJob
        runUnreadable = overview.runUnreadable
        flowFace = overview.face ?? flowFace
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
        guard mode == .edit, !isLocked else { return }
        if !isEditing { await beginDraft() }
        placeNodeInDraft(type, at: point)
    }

    func placeNodeInDraft(_ type: String, at point: CGPoint, into requestedEdge: WorkflowEdge? = nil) {
        guard !isLocked, var draft, let catalog, let spec = catalog.node(for: type) else { return }
        let position = WorkflowPoint(x: max(90, point.x), y: max(70, point.y))
        let singleInstance = catalog.canonicalIndex(of: type) != nil
        if singleInstance, let existing = draft.definition.nodes.first(where: { $0.type == type }) {
            draft.definition.positions[existing.id] = position
            self.draft = draft
            selectedNodeID = existing.id
            note = "\(spec.label) moved."
            return
        }
        let node = WorkflowNode(id: nodeID(for: type, in: draft.definition), type: type, config: spec.defaultConfig)
        let index = draft.definition.nodes.firstIndex { existing in
            guard let existingOrder = catalog.canonicalIndex(of: existing.type),
                  let newOrder = catalog.canonicalIndex(of: type) else { return false }
            return existingOrder > newOrder
        } ?? draft.definition.nodes.endIndex
        draft.definition.nodes.insert(node, at: index)
        draft.definition.positions[node.id] = position
        let edge = catalog.isTriggerType(type) ? nil
            : requestedEdge.flatMap { draft.definition.edges.contains($0) ? $0 : nil }
                ?? nearestWorkflowEdge(to: point, in: draft.definition)
        if let edge {
            draft.definition.edges.removeAll { $0 == edge }
            draft.definition.edges.append(WorkflowEdge(from: edge.from, to: node.id))
            draft.definition.edges.append(WorkflowEdge(from: node.id, to: edge.to))
            draft.definition.normalizeEdgeOrder()
            note = "\(spec.label) inserted into the path."
        } else {
            note = "\(spec.label) added. Connect it to complete the workflow."
        }
        self.draft = draft
        selectedNodeID = node.id
    }

    func removeSelectedNode() {
        guard !isLocked, var draft, let catalog, let id = selectedNodeID,
              let node = draft.definition.node(withID: id),
              !catalog.isRequired(node.type) else { return }
        let incoming = draft.definition.edges.filter { $0.to == id }
        let outgoing = draft.definition.edges.filter { $0.from == id }
        draft.definition.nodes.removeAll { $0.id == id }
        draft.definition.edges.removeAll { $0.from == id || $0.to == id }
        draft.definition.positions[id] = nil
        if let source = incoming.first?.from, let target = outgoing.first?.to,
           incoming.count == 1, outgoing.count == 1 {
            let bridge = WorkflowEdge(from: source, to: target)
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

    func completeNodeDrag(_ id: String, by translation: CGSize, splicing edge: WorkflowEdge?) {
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

    func removeEdge(_ edge: WorkflowEdge) {
        guard !isLocked, var draft else { return }
        draft.definition.edges.removeAll { $0 == edge }
        self.draft = draft
        connectionSourceID = nil
        note = "Connection removed."
    }

    func startConnection(from nodeID: String) {
        guard allowsStructuralEditing else { return }
        connectionSourceID = nodeID
        note = "Choose an input port to connect this node."
    }

    func completeConnection(to nodeID: String) {
        guard let source = connectionSourceID, source != nodeID else { return }
        connect(from: source, to: nodeID)
    }

    func connect(from source: String, to target: String) {
        guard !isLocked, source != target else { return }
        guard let draft, let catalog,
              let sourceNode = draft.definition.node(withID: source),
              let targetNode = draft.definition.node(withID: target),
              catalog.allowsConnection(from: sourceNode, to: targetNode, in: draft.definition) else {
            note = "That connection is not valid for this workflow."
            return
        }
        var next = draft
        next.definition.edges.removeAll { $0.from == source || $0.to == target }
        next.definition.edges.append(WorkflowEdge(from: source, to: target))
        next.definition.normalizeEdgeOrder()
        self.draft = next
        connectionSourceID = nil
        note = "Connection added."
    }

    func spliceNode(_ id: String, into edge: WorkflowEdge) {
        guard var draft, let node = draft.definition.node(withID: id),
              applySplice(of: id, into: edge, in: &draft.definition) else { return }
        self.draft = draft
        note = "\(catalog?.label(for: node.type) ?? workflowDisplayName(node.type)) inserted into the path."
    }

    private func applySplice(of id: String, into edge: WorkflowEdge,
                             in definition: inout WorkflowDefinition) -> Bool {
        guard !isLocked, let node = definition.node(withID: id),
              catalog?.isTriggerType(node.type) != true,
              edge.from != id, edge.to != id,
              definition.edges.contains(edge),
              !definition.edges.contains(where: { $0.from == id || $0.to == id }) else { return false }
        definition.edges.removeAll { $0 == edge }
        definition.edges.append(WorkflowEdge(from: edge.from, to: id))
        definition.edges.append(WorkflowEdge(from: id, to: edge.to))
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
            note = "Draft saved. Promote it when you want this workflow to use it."
        case .rejected(let message):
            note = message
        case .failed:
            note = "Couldn’t reach the server to save this draft."
        }
    }

    func promote() async {
        guard let client, let draft else { return }
        if let message = validationMessage ?? promotionMessage {
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
                note = "This workflow is active for future runs."
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

    private func mutateSelected(_ change: (inout WorkflowNode) -> Void) {
        guard var draft, let id = selectedNodeID,
              let index = draft.definition.nodes.firstIndex(where: { $0.id == id }) else { return }
        change(&draft.definition.nodes[index])
        self.draft = draft
    }

    private func nodeID(for type: String, in definition: WorkflowDefinition) -> String {
        let base = type.split(separator: ".").last.map(String.init) ?? type
        if definition.node(withID: base) == nil { return base }
        var counter = 2
        while definition.node(withID: "\(base)-\(counter)") != nil { counter += 1 }
        return "\(base)-\(counter)"
    }

    static func fixture(editing: Bool = false) -> WorkflowEditorStore {
        let store = WorkflowEditorStore()
        store.catalog = WorkflowCatalog.fixture()
        var nodes = [
            WorkflowNode(id: "schedule", type: "trigger.schedule",
                              config: ["mode": .string("daily"), "time": .string("05:00")]),
            WorkflowNode(id: "triage", type: "pulse.triage", config: [:]),
            WorkflowNode(id: "gates", type: "pulse.gates", config: [:]),
            WorkflowNode(id: "synthesis", type: "pulse.synthesis", config: [:]),
            WorkflowNode(id: "claim_audit", type: "pulse.claim_audit", config: [:]),
            WorkflowNode(id: "cover_art", type: "pulse.cover_art", config: ["style": .string("editorial")]),
            WorkflowNode(id: "visual_review", type: "pulse.visual_review", config: ["threshold": .double(0.8)]),
            WorkflowNode(id: "cover_retry", type: "pulse.cover_retry", config: ["max_attempts": .int(1)]),
            WorkflowNode(id: "inject", type: "pulse.inject", config: [:])
        ]
        if editing {
            nodes.insert(WorkflowNode(id: "filter", type: "flow.filter",
                                           config: ["field": .string("title"), "operator": .string("contains"),
                                                    "value": .string("frost"), "action": .string("drop")]),
                         at: 4)
        }
        let definition = WorkflowDefinition(id: "pulse", nodes: nodes,
                                                 edges: Array(zip(nodes, nodes.dropFirst())).map { WorkflowEdge(from: $0.id, to: $1.id) },
                                                 positions: Dictionary(uniqueKeysWithValues: nodes.enumerated().map {
                                                     ($0.element.id, WorkflowPoint(x: 105 + CGFloat($0.offset) * WorkflowCardGeometry.placementPitch, y: 310))
                                                 }))
        store.active = WorkflowVersion(id: "fixture", number: 2, state: "active", definition: definition)
        store.flowFace = WorkflowFlowFace(label: "Pulse briefing",
                                          description: "Gathers fresh stories on its schedule, filters them by your interests, and publishes your briefing.")
        if editing { store.draft = store.active }
        store.selectedNodeID = editing ? "filter" : "visual_review"
        store.phase = .ready
        return store
    }

    static func lockedFixture() -> WorkflowEditorStore {
        let store = WorkflowEditorStore()
        store.flowID = "home_model"
        store.flowFace = WorkflowFlowFace(label: "Home model",
                                          description: "Refreshes the model of your home from recent activity so predictions stay current.")
        store.catalog = WorkflowCatalog.lockedFixture()
        let nodes = [
            WorkflowNode(id: "schedule", type: "trigger.schedule",
                              config: ["mode": .string("daily"), "time": .string("03:30")]),
            WorkflowNode(id: "run", type: "step.home_model", config: [:])
        ]
        store.active = WorkflowVersion(id: "projected-home_model", number: 0, state: "active",
                                            definition: fixtureDefinition(id: "home_model", nodes: nodes))
        store.selectedNodeID = "schedule"
        store.phase = .ready
        return store
    }

    static func veinFixture() -> WorkflowEditorStore {
        let store = WorkflowEditorStore()
        store.flowID = "vein_weather"
        store.flowFace = WorkflowFlowFace(label: "Weather",
                                          description: "Severe-weather pre-warnings for your area.")
        store.catalog = WorkflowCatalog.lockedFixture()
        let nodes = [
            WorkflowNode(id: "schedule", type: "trigger.schedule",
                              config: ["mode": .string("interval"), "every_minutes": .int(360)]),
            WorkflowNode(id: "step-1", type: "http_fetch",
                              config: ["url": .string("https://forecast.example/v1.json"),
                                       "extract": .string("gust")]),
            WorkflowNode(id: "step-2", type: "trip_band",
                              config: ["hi": .double(45), "severity": .string("alert")])
        ]
        store.active = WorkflowVersion(id: "projected-vein_weather", number: 0, state: "active",
                                            definition: fixtureDefinition(id: "vein_weather", nodes: nodes))
        store.selectedNodeID = "step-1"
        store.phase = .ready
        return store
    }

    private static func fixtureDefinition(id: String, nodes: [WorkflowNode]) -> WorkflowDefinition {
        WorkflowDefinition(id: id, nodes: nodes,
                                edges: Array(zip(nodes, nodes.dropFirst())).map { WorkflowEdge(from: $0.id, to: $1.id) },
                                positions: Dictionary(uniqueKeysWithValues: nodes.enumerated().map {
                                    ($0.element.id, WorkflowPoint(x: 105 + CGFloat($0.offset) * WorkflowCardGeometry.placementPitch, y: 310))
                                }))
    }
}

extension WorkflowCatalog {
    static func fixture() -> WorkflowCatalog? {
        let json = """
        {"nodes":[
          {"type":"trigger.schedule","label":"Schedule","description":"Starts this workflow automatically on the schedule you set.","icon":"clock","tint":"accent","category":"trigger",
           "config_schema":{"mode":{"type":"choice","default":"daily","options":["daily","weekly","interval"]},
                            "time":{"type":"text","default":"05:00"},
                            "weekday":{"type":"choice","default":"monday","options":["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]},
                            "every_minutes":{"type":"choice","default":60,"options":[5,10,15,30,60,120,240,360,720]}},"insertable":false},
          {"type":"pulse.triage","label":"Gather candidates","description":"Pulls in this run's fresh candidate stories and signals so the rest of the pipeline has something to judge.","icon":"globe","tint":"accent","category":"core","config_schema":{},"insertable":false},
          {"type":"pulse.gates","label":"Filter by interest","description":"Checks each candidate against your interest and quality gates and drops the ones that fall short.","icon":"line.3.horizontal.decrease.circle","tint":"orange","category":"core","config_schema":{},"insertable":false},
          {"type":"pulse.synthesis","label":"Write cards","description":"Writes each surviving candidate into a readable card with a headline and summary.","icon":"sparkles","tint":"purple","category":"core","config_schema":{},"insertable":false},
          {"type":"pulse.claim_audit","label":"Check the facts","description":"Rechecks the factual claims in every drafted card and pulls the ones that don't hold up.","icon":"checkmark.shield","tint":"cyan","category":"core","config_schema":{},"insertable":false},
          {"type":"pulse.cover_art","label":"Generate covers","description":"Generates a cover image for each card in the visual style you pick.","icon":"photo","tint":"purple","category":"core",
           "config_schema":{"style":{"type":"choice","default":"rotating","options":["rotating","photographic","illustrated","editorial"]}},"insertable":false},
          {"type":"pulse.visual_review","label":"Review covers","description":"Scores each generated cover and rejects the ones below your quality threshold.","icon":"eye","tint":"cyan","category":"visual",
           "config_schema":{"threshold":{"type":"number","min":0,"max":1,"default":0.8}},"insertable":false},
          {"type":"pulse.cover_retry","label":"Retry a cover","description":"Regenerates a rejected cover one more time before the card ships without it.","icon":"arrow.clockwise","tint":"orange","category":"visual",
           "config_schema":{"max_attempts":{"type":"choice","options":[0,1],"default":1}},"insertable":false},
          {"type":"pulse.inject","label":"Publish to feed","description":"Publishes the finished cards into your feed.","icon":"arrow.down.to.line","tint":"green","category":"core","config_schema":{},"insertable":false},
          {"type":"flow.filter","label":"Filter","description":"Keeps or drops cards by comparing one of their fields against a value you choose.","icon":"line.3.horizontal.decrease","tint":"orange","category":"transform",
           "config_schema":{"field":{"type":"text","default":"title"},
                            "operator":{"type":"choice","default":"contains","options":["contains","not_contains","equals","not_equals","present","missing"]},
                            "value":{"type":"text","default":""},
                            "action":{"type":"choice","default":"keep","options":["keep","drop"]}},"insertable":true}
        ],
        "profile":{"id":"pulse",
          "spine":["pulse.triage","pulse.gates","pulse.synthesis","pulse.claim_audit","pulse.cover_art","pulse.inject"],
          "insertable_categories":["transform","enrich","notify"],
          "pairs":[{"types":["pulse.visual_review","pulse.cover_retry"],"after":"pulse.cover_art","before":"pulse.inject"}],
          "triggers":["trigger.schedule"]}}
        """
        guard let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)) else { return nil }
        return WorkflowCatalog.parse(object)
    }

    static func lockedFixture() -> WorkflowCatalog? {
        let json = """
        {"nodes":[
          {"type":"trigger.schedule","label":"Schedule","description":"Starts this workflow automatically on the schedule you set.","icon":"clock","tint":"accent","category":"trigger",
           "config_schema":{"mode":{"type":"choice","default":"daily","options":["daily","weekly","interval"]},
                            "time":{"type":"text","default":"05:00"},
                            "weekday":{"type":"choice","default":"monday","options":["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]},
                            "every_minutes":{"type":"choice","default":60,"options":[5,10,15,30,60,120,240,360,720]}},"insertable":false},
          {"type":"step.home_model","label":"Refresh the home model","description":"Rebuilds the model of your home from the latest recorded activity.","icon":"house","tint":"cyan","category":"core","config_schema":{},"insertable":false},
          {"type":"http_fetch","label":"HTTP fetch","description":"Fetches a URL and turns the response into items.","icon":"arrow.down.circle","tint":"accent","category":"enrich",
           "config_schema":{"url":{"type":"text","default":""},
                            "extract":{"type":"text","default":""},
                            "label":{"type":"text","default":""}},"insertable":false},
          {"type":"trip_band","label":"Trip band","description":"Watches a numeric reading and emits an item when it crosses the band you set.","icon":"waveform.path","tint":"accent","category":"transform",
           "config_schema":{"hi":{"type":"number"},"lo":{"type":"number"},
                            "field":{"type":"text","default":"value"},
                            "severity":{"type":"choice","default":"alert","options":["notice","alert","critical"]}},"insertable":false}
        ],
        "profile":{"id":"locked","spine":[],"insertable_categories":[],"pairs":[],"triggers":["trigger.schedule"],"locked":true}}
        """
        guard let object = try? JSONSerialization.jsonObject(with: Data(json.utf8)) else { return nil }
        return WorkflowCatalog.parse(object)
    }
}

struct WorkflowPalette: View {
    @ObservedObject var store: WorkflowEditorStore
    @Binding var searchText: String
    var snapshot = false
    var onBack: () -> Void = {}
    @State private var hoveredType: String?

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
                Text(locked ? "The steps in this workflow" : "Drag onto the workflow canvas")
                    .font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
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
                 : locked ? "This workflow's steps are managed by the server. Select a node to adjust its settings."
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
        VStack(alignment: .leading, spacing: 16) {
            ForEach(orderedCategories, id: \.self) { category in
                let entries = visibleEntries.filter { $0.category == category }
                if !entries.isEmpty {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(sectionTitle(category).uppercased()).font(.system(size: 9, weight: .bold))
                            .tracking(0.7).foregroundStyle(Theme.textSecondary.opacity(0.68))
                            .padding(.horizontal, 8).padding(.bottom, 3)
                        ForEach(entries) { entry in paletteNode(entry) }
                    }
                }
            }
        }
        .padding(.bottom, 12)
    }

    private var orderedCategories: [String] {
        var all: [String] = []
        for entry in visibleEntries where !all.contains(entry.category) {
            all.append(entry.category)
        }
        return all.filter { $0 == "trigger" } + all.filter { $0 != "trigger" }
    }

    private func sectionTitle(_ category: String) -> String {
        category == "trigger" ? "Triggers" : category.prefix(1).uppercased() + category.dropFirst()
    }

    private var locked: Bool {
        store.catalog?.profile.locked == true
    }

    private var visibleEntries: [WorkflowCatalogNode] {
        let all: [WorkflowCatalogNode]
        if locked {
            var seen = Set<String>()
            all = (store.displayed?.definition.nodes ?? []).compactMap { node in
                guard seen.insert(node.type).inserted else { return nil }
                return store.catalog?.node(for: node.type)
            }
        } else {
            all = store.catalog?.paletteNodes ?? []
        }
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
            } else if locked {
                paletteNodeLabel(entry, installed: installed)
                .onTapGesture {
                    if let node = store.displayed?.definition.nodes.first(where: { $0.type == entry.type }) {
                        store.selectedNodeID = node.id
                    }
                }
                .onHover { hovering in
                    if hovering {
                        hoveredType = entry.type
                    } else if hoveredType == entry.type {
                        hoveredType = nil
                    }
                }
                .help("Select this step")
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
                .onHover { hovering in
                    if hovering {
                        hoveredType = entry.type
                    } else if hoveredType == entry.type {
                        hoveredType = nil
                    }
                }
                .help(installed ? "Drag to reposition" : "Drag to add")
                .opacity(store.phase == .ready && !store.busy ? 1 : 0.5)
                .allowsHitTesting(store.phase == .ready && !store.busy)
            }
        }
    }

    private func paletteNodeLabel(_ entry: WorkflowCatalogNode, installed: Bool) -> some View {
        let hovered = snapshot ? entry.type == store.displayed?.definition.node(withID: store.selectedNodeID ?? "")?.type
                               : hoveredType == entry.type
        return HStack(spacing: 9) {
            Image(systemName: entry.icon)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(graphTint(entry.tint)).frame(width: 28, height: 28)
                .background(graphTint(entry.tint).opacity(0.12))
                .clipShape(workflowNodeIsTrigger(entry)
                           ? UnevenRoundedRectangle(topLeadingRadius: 14, bottomLeadingRadius: 14,
                                                    bottomTrailingRadius: 8, topTrailingRadius: 8, style: .continuous)
                           : UnevenRoundedRectangle(topLeadingRadius: 8, bottomLeadingRadius: 8,
                                                    bottomTrailingRadius: 8, topTrailingRadius: 8, style: .continuous))
            VStack(alignment: .leading, spacing: 1) {
                Text(entry.label).font(.system(size: 11.5, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                Text(entry.description).font(.system(size: 9.5)).foregroundStyle(Theme.textSecondary).lineLimit(1)
            }
            Spacer(minLength: 0)
            Image(systemName: installed ? "scope" : "plus.circle.fill")
                .font(.system(size: installed ? 10 : 13, weight: .bold))
                .foregroundStyle(installed ? Theme.textSecondary : Theme.accent)
                .opacity(installed ? 0.7 : (hovered ? 1 : 0))
        }
        .padding(.horizontal, 8).padding(.vertical, 6).frame(minHeight: 40).contentShape(Rectangle())
        .background(hovered ? Theme.surfaceHover : .clear)
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
    var edge: WorkflowEdge?
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

struct WorkflowEditor: View {
    @ObservedObject var store: WorkflowEditorStore
    var snapshot = false

    @StateObject private var nav = WorkflowCanvasNav()
    @State private var nodeDrag: WorkflowCanvasDragState?
    @State private var wireDrag: WorkflowWireDrag?
    @State private var spliceEdge: WorkflowEdge?
    @State private var hoverEdge: WorkflowEdge?
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
                CanvasStatusCard(icon: "exclamationmark.triangle", title: "Workflow unavailable", note: "Connect vera-api to open this workflow.")
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
                HStack(spacing: 7) {
                    Text(store.flowLabel).font(.system(size: 17, weight: .semibold))
                    if store.isLocked {
                        Image(systemName: "gearshape.arrow.triangle.2.circlepath")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                            .help("This flow's steps are managed by the server. Its settings can still be edited.")
                    }
                }
                Text(store.flowDescription).font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1)
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
                        Text("Edit").tag(WorkflowEditorStore.Mode.edit)
                        Text("Run").tag(WorkflowEditorStore.Mode.run)
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
                } else if let message = store.promotionMessage {
                    Label(message, systemImage: "bolt.badge.clock")
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
            .overlay(alignment: .bottom) {
                Text(store.isEditing
                     ? (store.isLocked ? "Drag to arrange · select a node to adjust its settings"
                                       : "Drag to arrange · drag a port to connect · drag empty space to pan")
                     : "Viewing the active workflow · Edit workflow to make changes")
                    .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 11).padding(.vertical, 5)
                    .background(Capsule().fill(Theme.bg.opacity(0.85)))
                    .padding(.bottom, 13).allowsHitTesting(false)
            }
            .overlay(alignment: .bottomLeading) { zoomControls(viewport.size) }
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

    private func editCanvasContent(_ workflow: WorkflowVersion, viewport: CGSize) -> some View {
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
                                     hovered: hoveredNodeID == node.id && wireDrag == nil && nodeDrag == nil,
                                     canDelete: store.allowsStructuralEditing && store.catalog?.isRequired(node.type) == false,
                                     hasInput: store.catalog?.isTriggerType(node.type) != true,
                                     portHitDiameter: 2 * WorkflowCardGeometry.portHitRadius / nav.transform.scale,
                                     onInput: store.allowsStructuralEditing && store.catalog?.isTriggerType(node.type) != true
                                         ? { store.completeConnection(to: node.id) } : nil,
                                     onOutput: store.allowsStructuralEditing ? { store.startConnection(from: node.id) } : nil,
                                     onOutputDragChanged: store.allowsStructuralEditing ? { location in updateWireDrag(from: node.id, viewportPoint: location) } : nil,
                                     onOutputDragEnded: store.allowsStructuralEditing ? { location in finishWireDrag(viewportPoint: location) } : nil,
                                     onConfigure: store.isEditing ? { store.selectedNodeID = node.id } : nil,
                                     onDelete: store.allowsStructuralEditing ? {
                                         store.selectedNodeID = node.id
                                         store.removeSelectedNode()
                                     } : nil)
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
            if store.allowsStructuralEditing, wireDrag == nil, nodeDrag == nil, let hoverEdge,
               let midpoint = workflowWireMidpoint(hoverEdge, in: definition) {
                Button {
                    insertRequest = WorkflowInsertRequest(point: midpoint, edge: hoverEdge, connectFrom: nil)
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
                        .frame(width: 24, height: 24).background(Circle().fill(Theme.accent))
                        .overlay(Circle().stroke(Theme.bg.opacity(0.6), lineWidth: 1.5))
                        .shadow(color: Theme.bg.opacity(0.4), radius: 4, y: 1)
                }
                .buttonStyle(.plain)
                .position(x: midpoint.x, y: midpoint.y)
                .zIndex(6)
                .help("Insert a node here")
            }
        }
        .frame(width: size.width, height: size.height, alignment: .topLeading)
    }

    private func wireLayer(_ definition: WorkflowDefinition, offsets: [String: CGSize]) -> some View {
        Canvas { context, _ in
            for edge in definition.edges {
                guard let start = WorkflowCardGeometry.outputPort(for: edge.from, in: definition, offsets: offsets),
                      let end = WorkflowCardGeometry.inputPort(for: edge.to, in: definition, offsets: offsets) else { continue }
                let emphasized = edge == spliceEdge || edge == hoverEdge
                drawWorkflowWire(context, from: start, to: end,
                                 color: emphasized ? Theme.accent.opacity(0.9) : Theme.textSecondary.opacity(0.68),
                                 lineWidth: emphasized ? 3 : 2)
            }
            if let wireDrag,
               let start = WorkflowCardGeometry.outputPort(for: wireDrag.sourceID, in: definition, offsets: offsets) {
                let end = wireDrag.targetID.flatMap { WorkflowCardGeometry.inputPort(for: $0, in: definition, offsets: offsets) } ?? wireDrag.point
                drawWorkflowWire(context, from: start, to: end, color: Theme.accent.opacity(0.9),
                                 lineWidth: 2, dash: wireDrag.targetID == nil ? [6, 5] : [], arrow: false)
            }
        }
    }

    private func contentSize(_ definition: WorkflowDefinition, viewport: CGSize) -> CGSize {
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

    private func nodeDragGesture(_ node: WorkflowNode) -> some Gesture {
        DragGesture(minimumDistance: 1, coordinateSpace: .named("workflowViewport"))
            .onChanged { value in
                let translation = CGSize(width: value.translation.width / nav.transform.scale,
                                         height: value.translation.height / nav.transform.scale)
                nodeDrag = WorkflowCanvasDragState(nodeID: node.id, offset: translation)
                guard let definition = store.displayed?.definition,
                      store.catalog?.isTriggerType(node.type) != true,
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

    private func isUnconnected(_ id: String, in definition: WorkflowDefinition) -> Bool {
        !definition.edges.contains { $0.from == id || $0.to == id }
    }

    private func cardID(at point: CGPoint, in definition: WorkflowDefinition) -> String? {
        for node in definition.nodes.reversed() {
            guard let center = WorkflowCardGeometry.position(of: node.id, in: definition) else { continue }
            if abs(point.x - center.x) <= WorkflowCardGeometry.size.width / 2,
               abs(point.y - center.y) <= WorkflowCardGeometry.size.height / 2 {
                return node.id
            }
        }
        return nil
    }

    private var triggerNodeIDs: Set<String> {
        guard let catalog = store.catalog, let definition = store.displayed?.definition else { return [] }
        return catalog.triggerIDs(in: definition)
    }

    private func updateWireDrag(from sourceID: String, viewportPoint: CGPoint) {
        guard let definition = store.displayed?.definition else { return }
        let point = nav.transform.toCanvas(viewportPoint)
        let target = WorkflowCardGeometry.nearestInputPort(to: point, in: definition, excluding: sourceID,
                                                           within: WorkflowCardGeometry.portHitRadius / nav.transform.scale,
                                                           blocked: triggerNodeIDs)
        wireDrag = WorkflowWireDrag(sourceID: sourceID, point: point, targetID: target)
        hoverEdge = nil
    }

    private func finishWireDrag(viewportPoint: CGPoint) {
        guard let drag = wireDrag else { return }
        wireDrag = nil
        guard let definition = store.displayed?.definition else { return }
        let point = nav.transform.toCanvas(viewportPoint)
        if let target = WorkflowCardGeometry.nearestInputPort(to: point, in: definition, excluding: drag.sourceID,
                                                              within: WorkflowCardGeometry.portHitRadius / nav.transform.scale,
                                                              blocked: triggerNodeIDs) {
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
            guard store.allowsStructuralEditing, wireDrag == nil, nodeDrag == nil, insertRequest == nil,
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
           catalog.isTriggerType(type)
               || (catalog.canonicalIndex(of: type) != nil
                   && definition.nodes.contains(where: { $0.type == type })) {
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
            Button { fitView(viewport) } label: {
                Image(systemName: "viewfinder").frame(width: 26, height: 24).contentShape(Rectangle())
            }
            .help("Fit workflow")
            Divider().frame(height: 14).overlay(Theme.hairline)
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

    private func fitView(_ viewport: CGSize) {
        guard let definition = store.displayed?.definition,
              let bounds = WorkflowCardGeometry.bounds(of: definition) else { return }
        withAnimation(.easeInOut(duration: 0.2)) {
            nav.transform = WorkflowCanvasTransform.fitting(bounds, in: viewport)
        }
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
            ZStack(alignment: .topLeading) {
                WorkflowCanvasGrid(transform: WorkflowCanvasTransform())
                WorkflowFittedCanvas(definition: workflow.definition) {
                    workflowGraphContent(workflow)
                }
            }
            .overlay(alignment: .bottom) {
                Text("Drag to arrange · drag a port to connect")
                    .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 11).padding(.vertical, 5)
                    .background(Capsule().fill(Theme.bg.opacity(0.85)))
                    .padding(.bottom, 13)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.bg)
        }
    }

    private func workflowGraphContent(_ workflow: WorkflowVersion) -> some View {
        ZStack(alignment: .topLeading) {
            Canvas { context, _ in
                for edge in workflow.definition.edges {
                    guard let start = WorkflowCardGeometry.outputPort(for: edge.from, in: workflow.definition),
                          let end = WorkflowCardGeometry.inputPort(for: edge.to, in: workflow.definition) else { continue }
                    drawWorkflowWire(context, from: start, to: end,
                                     color: Theme.textSecondary.opacity(0.68), lineWidth: 2)
                }
            }
            ForEach(workflow.definition.nodes) { node in
                if let point = workflow.definition.positions[node.id] {
                    WorkflowNodeCard(node: node, spec: store.catalog?.node(for: node.type),
                                     selected: store.selectedNodeID == node.id,
                                     hasInput: store.catalog?.isTriggerType(node.type) != true)
                        .position(x: point.x, y: point.y)
                }
            }
        }
    }

    @ViewBuilder private var runCanvas: some View {
        if let workflow = store.runVersion, let run = store.latestRun {
            if snapshot {
                ZStack(alignment: .topLeading) {
                    WorkflowCanvasGrid(transform: WorkflowCanvasTransform())
                    WorkflowFittedCanvas(definition: workflow.definition) {
                        runGraphContent(workflow, run: run)
                    }
                }
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

    private func runGraph(_ workflow: WorkflowVersion, run: WorkflowRun) -> some View {
        ZStack(alignment: .topLeading) {
            WorkflowCanvasGrid(transform: WorkflowCanvasTransform())
            runGraphContent(workflow, run: run)
        }
    }

    private func runGraphContent(_ workflow: WorkflowVersion, run: WorkflowRun) -> some View {
        ZStack(alignment: .topLeading) {
            Canvas { context, _ in
                for edge in workflow.definition.edges {
                    guard let start = WorkflowCardGeometry.outputPort(for: edge.from, in: workflow.definition),
                          let end = WorkflowCardGeometry.inputPort(for: edge.to, in: workflow.definition) else { continue }
                    drawWorkflowWire(context, from: start, to: end,
                                     color: Theme.textSecondary.opacity(0.68), lineWidth: 2)
                    if let count = run.nodeRun(edge.from)?.itemCount {
                        let mid = workflowWirePoint(start, end, t: 0.5)
                        context.draw(Text("\(count) \(count == 1 ? "item" : "items")")
                            .font(.system(size: 9.5, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary),
                                     at: CGPoint(x: mid.x, y: mid.y - 13))
                    }
                }
            }
            ForEach(workflow.definition.nodes) { node in
                if let point = workflow.definition.positions[node.id] {
                    let isTrigger = store.catalog?.isTriggerType(node.type) == true
                    WorkflowRunNodeCard(node: node, spec: store.catalog?.node(for: node.type),
                                        run: run.nodeRun(node.id),
                                        selected: store.selectedNodeID == node.id,
                                        hasInput: !isTrigger,
                                        triggerJob: isTrigger ? store.triggerJob : nil)
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

    private func inspectorContent(selected: WorkflowNode, spec: WorkflowCatalogNode?) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            inspectorSection("Parameters") {
                VStack(alignment: .leading, spacing: 10) {
                    controls(for: selected, spec: spec)
                    if !store.isEditing, !snapshot, selected.rule == nil, spec?.fields.isEmpty == false {
                        Text("Create a draft to change these settings.")
                            .font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                    }
                }
            }
            inspectorSection("Connections") { connectionList(for: selected) }
            if let note = store.note {
                Text(note).font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if store.allowsStructuralEditing, store.catalog?.isRequired(selected.type) == false {
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

    @ViewBuilder private func connectionList(for node: WorkflowNode) -> some View {
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
                        if store.allowsStructuralEditing {
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

    @ViewBuilder private func controls(for node: WorkflowNode, spec: WorkflowCatalogNode?) -> some View {
        if let rule = node.rule {
            VStack(alignment: .leading, spacing: 4) {
                Text(cronSummary(rule)).font(.system(size: 11.5, weight: .semibold))
                Text("This schedule is set on the server and can't be edited here.")
                    .font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else if let fields = spec?.fields, !fields.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                ForEach(fields) { field in
                    fieldControl(field, node: node, readOnly: snapshot || !store.isEditing)
                }
            }
        } else {
            Text("This node has no editable parameters.").font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
        }
    }

    @ViewBuilder private func fieldControl(_ field: WorkflowSchemaField, node: WorkflowNode, readOnly: Bool) -> some View {
        let current = node.config[field.key] ?? field.defaultValue
        switch field.kind {
        case .choice(let options):
            VStack(alignment: .leading, spacing: 6) {
                Text(field.label).font(.system(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                if readOnly {
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
                    if readOnly {
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
                } else if readOnly {
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
            if readOnly {
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
                if readOnly {
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

func drawWorkflowWire(_ context: GraphicsContext, from start: CGPoint, to end: CGPoint,
                      color: Color, lineWidth: CGFloat, dash: [CGFloat] = [], arrow: Bool = true) {
    context.stroke(edgePath(start, end), with: .color(color),
                   style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, dash: dash))
    guard arrow else { return }
    let ahead = workflowWirePoint(start, end, t: 0.56)
    let behind = workflowWirePoint(start, end, t: 0.44)
    let angle = atan2(ahead.y - behind.y, ahead.x - behind.x)
    let mid = workflowWirePoint(start, end, t: 0.5)
    let length: CGFloat = 5.5
    var head = Path()
    head.move(to: CGPoint(x: mid.x + cos(angle) * length, y: mid.y + sin(angle) * length))
    head.addLine(to: CGPoint(x: mid.x + cos(angle + 2.4) * length, y: mid.y + sin(angle + 2.4) * length))
    head.addLine(to: CGPoint(x: mid.x + cos(angle - 2.4) * length, y: mid.y + sin(angle - 2.4) * length))
    head.closeSubpath()
    context.fill(head, with: .color(color))
}

func workflowCardShape(trigger: Bool) -> UnevenRoundedRectangle {
    let radius = WorkflowCardGeometry.cornerRadius
    let leading = trigger ? WorkflowCardGeometry.size.height / 2 : radius
    return UnevenRoundedRectangle(topLeadingRadius: leading, bottomLeadingRadius: leading,
                                  bottomTrailingRadius: radius, topTrailingRadius: radius,
                                  style: .continuous)
}

func workflowNodeIsTrigger(_ spec: WorkflowCatalogNode?) -> Bool {
    spec?.category == "trigger"
}

struct WorkflowCardFace: View {
    var spec: WorkflowCatalogNode?
    var fallbackType: String
    var stroke: Color
    var strokeWidth: CGFloat
    var fillOpacity: Double = 1

    var body: some View {
        let trigger = workflowNodeIsTrigger(spec)
        let shape = workflowCardShape(trigger: trigger)
        ZStack {
            shape.fill(Theme.surface.opacity(fillOpacity))
            Image(systemName: spec?.icon ?? "puzzlepiece")
                .font(.system(size: 30, weight: .medium))
                .foregroundStyle(graphTint(spec?.tint ?? "accent"))
        }
        .overlay(shape.stroke(stroke, lineWidth: strokeWidth))
        .overlay(alignment: .bottomLeading) {
            if trigger {
                Image(systemName: "bolt.fill").font(.system(size: 10, weight: .bold))
                    .foregroundStyle(Theme.accent)
                    .padding(.leading, 12).padding(.bottom, 10)
            }
        }
        .frame(width: WorkflowCardGeometry.size.width, height: WorkflowCardGeometry.size.height)
    }
}

struct WorkflowCardName: View {
    var text: String
    var muted: Bool = false
    var caption: String? = nil

    var body: some View {
        VStack(spacing: 1) {
            Text(text).font(.system(size: 11, weight: .semibold))
                .foregroundStyle(muted ? Theme.textSecondary : Theme.textPrimary)
                .lineLimit(1)
                .frame(maxWidth: 150)
                .fixedSize()
            if let caption {
                Text(caption).font(.system(size: 9))
                    .foregroundStyle(Theme.textSecondary)
                    .lineLimit(1)
                    .fixedSize()
            }
        }
        .offset(y: WorkflowCardGeometry.size.height / 2 + (caption == nil ? 16 : 22))
        .allowsHitTesting(false)
    }
}

struct WorkflowPortDot: View {
    var body: some View {
        Circle().fill(Theme.bg)
            .overlay(Circle().stroke(Theme.textSecondary.opacity(0.55), lineWidth: 1.5))
            .frame(width: 8, height: 8)
    }
}

struct WorkflowNodeCard: View {
    let node: WorkflowNode
    var spec: WorkflowCatalogNode?
    var selected: Bool
    var connecting: Bool = false
    var inputHighlighted: Bool = false
    var hovered: Bool = false
    var canDelete: Bool = false
    var hasInput: Bool = true
    var portHitDiameter: CGFloat = 2 * WorkflowCardGeometry.portHitRadius
    var onInput: (() -> Void)? = nil
    var onOutput: (() -> Void)? = nil
    var onOutputDragChanged: ((CGPoint) -> Void)? = nil
    var onOutputDragEnded: ((CGPoint) -> Void)? = nil
    var onConfigure: (() -> Void)? = nil
    var onDelete: (() -> Void)? = nil

    private var strokeColor: Color {
        if connecting || selected { return Theme.accent }
        if hovered { return Theme.accent.opacity(0.45) }
        return Theme.hairline
    }

    private var showsActions: Bool {
        hovered && (onConfigure != nil || (canDelete && onDelete != nil))
    }

    var body: some View {
        WorkflowCardFace(spec: spec, fallbackType: node.type,
                         stroke: strokeColor, strokeWidth: connecting || selected ? 2 : 1)
            .shadow(color: Theme.bg.opacity(0.35), radius: selected || hovered ? 9 : 4, y: 2)
            .overlay { WorkflowCardName(text: spec?.label ?? workflowDisplayName(node.type)) }
            .overlay(alignment: .leading) {
                if hasInput { inputPort.offset(x: onInput != nil ? -portHitDiameter / 2 : -4) }
            }
            .overlay(alignment: .trailing) { outputPort.offset(x: onOutput != nil ? portHitDiameter / 2 : 4) }
            .overlay(alignment: .top) {
                if showsActions { quickActions.offset(y: -27) }
            }
    }

    private var quickActions: some View {
        HStack(spacing: 2) {
            if let onConfigure {
                quickAction("slider.horizontal.3", help: "Configure", action: onConfigure)
            }
            if canDelete, let onDelete {
                quickAction("trash", help: "Delete", action: onDelete)
            }
        }
        .padding(3)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.hairline, lineWidth: 1))
        .shadow(color: Theme.bg.opacity(0.4), radius: 6, y: 2)
    }

    private func quickAction(_ icon: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 24, height: 21).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(help)
    }

    @ViewBuilder private var inputPort: some View {
        if let onInput {
            WorkflowEditorPort(highlighted: inputHighlighted, hitDiameter: portHitDiameter, onTap: onInput)
        } else {
            WorkflowPortDot()
        }
    }

    @ViewBuilder private var outputPort: some View {
        if let onOutput {
            WorkflowEditorPort(highlighted: connecting, hitDiameter: portHitDiameter, onTap: onOutput,
                               dragChanged: onOutputDragChanged, dragEnded: onOutputDragEnded)
        } else {
            WorkflowPortDot()
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
            .overlay(Circle().stroke(emphasized ? Theme.accent : Theme.textSecondary.opacity(0.55),
                                     lineWidth: 1.5))
            .frame(width: emphasized ? 13 : 8, height: emphasized ? 13 : 8)
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

struct WorkflowFittedCanvas<Content: View>: View {
    var definition: WorkflowDefinition
    @ViewBuilder var content: Content

    var body: some View {
        GeometryReader { viewport in
            let transform = WorkflowCardGeometry.bounds(of: definition)
                .map { WorkflowCanvasTransform.fitting($0, in: viewport.size) }
                ?? WorkflowCanvasTransform()
            content
                .scaleEffect(transform.scale, anchor: .topLeading)
                .offset(transform.offset)
        }
    }
}

struct WorkflowCanvasGrid: View {
    var transform: WorkflowCanvasTransform

    var body: some View {
        Canvas { context, size in
            let step = 24 * transform.scale
            guard step >= 7 else { return }
            let dot = max(1, 1.6 * transform.scale)
            let phaseX = (transform.offset.width + 12 * transform.scale).truncatingRemainder(dividingBy: step)
            let phaseY = (transform.offset.height + 12 * transform.scale).truncatingRemainder(dividingBy: step)
            var y = phaseY - step
            while y < size.height {
                var x = phaseX - step
                while x < size.width {
                    context.fill(Path(ellipseIn: CGRect(x: x, y: y, width: dot, height: dot)),
                                 with: .color(Theme.textSecondary.opacity(0.14)))
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
        VStack(spacing: 6) {
            WorkflowCardFace(spec: spec, fallbackType: spec.type, stroke: Theme.accent, strokeWidth: 1.5)
            Text(spec.label).font(.system(size: 11, weight: .semibold)).lineLimit(1)
        }
        .padding(4)
    }
}

struct WorkflowEditorShot: View {
    @StateObject private var store = WorkflowEditorStore.fixture(editing: true)
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

struct LockedWorkflowEditorShot: View {
    @StateObject var store: WorkflowEditorStore
    @State private var searchText = ""

    init(store: WorkflowEditorStore) {
        _store = StateObject(wrappedValue: store)
    }

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
