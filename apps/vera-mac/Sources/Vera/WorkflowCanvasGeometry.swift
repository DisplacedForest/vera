import SwiftUI

struct WorkflowCanvasTransform: Equatable {
    var scale: CGFloat = 1
    var offset: CGSize = .zero

    static let minScale: CGFloat = 0.35
    static let maxScale: CGFloat = 2.5

    func toCanvas(_ point: CGPoint) -> CGPoint {
        CGPoint(x: (point.x - offset.width) / scale, y: (point.y - offset.height) / scale)
    }

    func toScreen(_ point: CGPoint) -> CGPoint {
        CGPoint(x: point.x * scale + offset.width, y: point.y * scale + offset.height)
    }

    func toCanvas(_ size: CGSize) -> CGSize {
        CGSize(width: size.width / scale, height: size.height / scale)
    }

    mutating func pan(by delta: CGSize) {
        offset.width += delta.width
        offset.height += delta.height
    }

    mutating func zoom(by factor: CGFloat, around screenPoint: CGPoint) {
        let anchor = toCanvas(screenPoint)
        scale = min(max(scale * factor, Self.minScale), Self.maxScale)
        offset = CGSize(width: screenPoint.x - anchor.x * scale,
                        height: screenPoint.y - anchor.y * scale)
    }

    static func fitting(_ bounds: CGRect, in viewport: CGSize, padding: CGFloat = 70) -> WorkflowCanvasTransform {
        guard bounds.width > 0, bounds.height > 0, viewport.width > padding * 2, viewport.height > padding * 2 else {
            return WorkflowCanvasTransform()
        }
        let fit = min((viewport.width - padding * 2) / bounds.width,
                      (viewport.height - padding * 2) / bounds.height)
        let scale = min(max(fit, minScale), 1)
        let offset = CGSize(width: (viewport.width - bounds.width * scale) / 2 - bounds.minX * scale,
                            height: (viewport.height - bounds.height * scale) / 2 - bounds.minY * scale)
        return WorkflowCanvasTransform(scale: scale, offset: offset)
    }
}

enum WorkflowCardGeometry {
    static let size = CGSize(width: 92, height: 92)
    static let cornerRadius: CGFloat = 16
    static let labelClearance: CGFloat = 30
    static let placementPitch: CGFloat = size.width * 2
    static let portHitRadius: CGFloat = 20
    static let spliceRange: CGFloat = 46
    static let wireHoverRange: CGFloat = 14

    static func bounds(of definition: PulseWorkflowDefinition) -> CGRect? {
        let rects = definition.nodes.compactMap { node in
            definition.positions[node.id].map { point in
                CGRect(x: point.x - size.width / 2, y: point.y - size.height / 2,
                       width: size.width, height: size.height + labelClearance)
            }
        }
        guard let first = rects.first else { return nil }
        return rects.dropFirst().reduce(first) { $0.union($1) }
    }

    static func inputPort(_ center: CGPoint) -> CGPoint {
        CGPoint(x: center.x - size.width / 2, y: center.y)
    }

    static func outputPort(_ center: CGPoint) -> CGPoint {
        CGPoint(x: center.x + size.width / 2, y: center.y)
    }

    static func inputPort(for id: String, in definition: PulseWorkflowDefinition,
                          offsets: [String: CGSize] = [:]) -> CGPoint? {
        position(of: id, in: definition, offsets: offsets).map(inputPort)
    }

    static func outputPort(for id: String, in definition: PulseWorkflowDefinition,
                           offsets: [String: CGSize] = [:]) -> CGPoint? {
        position(of: id, in: definition, offsets: offsets).map(outputPort)
    }

    static func position(of id: String, in definition: PulseWorkflowDefinition,
                         offsets: [String: CGSize] = [:]) -> CGPoint? {
        guard let point = definition.positions[id] else { return nil }
        let shift = offsets[id] ?? .zero
        return CGPoint(x: point.x + shift.width, y: point.y + shift.height)
    }

    static func nearestInputPort(to point: CGPoint, in definition: PulseWorkflowDefinition,
                                 excluding sourceID: String,
                                 within radius: CGFloat = portHitRadius,
                                 blocked: Set<String> = []) -> String? {
        var best: (id: String, distance: CGFloat)?
        for node in definition.nodes where node.id != sourceID && !blocked.contains(node.id) {
            guard let port = inputPort(for: node.id, in: definition) else { continue }
            let distance = hypot(point.x - port.x, point.y - port.y)
            if distance <= radius, distance < (best?.distance ?? .greatestFiniteMagnitude) {
                best = (node.id, distance)
            }
        }
        return best?.id
    }
}

func nearestWorkflowEdge(to point: CGPoint, in definition: PulseWorkflowDefinition,
                         within range: CGFloat = WorkflowCardGeometry.spliceRange) -> PulseWorkflowEdge? {
    var best: (edge: PulseWorkflowEdge, distance: CGFloat)?
    for edge in definition.edges {
        guard let start = WorkflowCardGeometry.outputPort(for: edge.from, in: definition),
              let end = WorkflowCardGeometry.inputPort(for: edge.to, in: definition) else { continue }
        let distance = workflowWireDistance(point, start, end)
        if distance < range, distance < (best?.distance ?? .greatestFiniteMagnitude) {
            best = (edge, distance)
        }
    }
    return best?.edge
}

func workflowWireDistance(_ point: CGPoint, _ start: CGPoint, _ end: CGPoint) -> CGFloat {
    var best = CGFloat.greatestFiniteMagnitude
    for sample in 0...24 {
        let t = CGFloat(sample) / 24
        let sampled = workflowWirePoint(start, end, t: t)
        best = min(best, hypot(point.x - sampled.x, point.y - sampled.y))
    }
    return best
}

func workflowWirePoint(_ start: CGPoint, _ end: CGPoint, t: CGFloat) -> CGPoint {
    edgePoint(start, end, t: t)
}

func workflowWireMidpoint(_ edge: PulseWorkflowEdge, in definition: PulseWorkflowDefinition) -> CGPoint? {
    guard let start = WorkflowCardGeometry.outputPort(for: edge.from, in: definition),
          let end = WorkflowCardGeometry.inputPort(for: edge.to, in: definition) else { return nil }
    return workflowWirePoint(start, end, t: 0.5)
}
