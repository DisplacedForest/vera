import Foundation

/// Inline "presentation tool" blocks Vera can emit when prose isn't enough. She decides
/// when to use them; most turns use none. Parsed out of a reply and rendered natively, interleaved
/// with prose, in both chat and Pulse.
///
///     ```vera:chart
///     {"type":"groupedBar","title":"...","series":[{"name":"Openda","points":[{"x":"23-24","y":14}]}]}
///     ```
///     ```vera:stats
///     {"cards":[{"value":"33","label":"goals","sub":"69 games"}]}
///     ```

struct ChartSpec: Hashable {
    enum Kind: String { case bar, line, groupedBar }
    struct Point: Hashable { let x: String; let y: Double }
    struct Series: Hashable { let name: String; let points: [Point] }
    let type: Kind
    let title: String
    let xLabel: String
    let yLabel: String
    let series: [Series]
}

struct StatCard: Hashable, Identifiable {
    let id = UUID()
    let value: String
    let label: String
    let sub: String?
}

/// An ordered piece of an assistant reply: prose (markdown) or a native block.
enum ReplySegment: Identifiable {
    case prose(id: Int, text: String)
    case chart(id: Int, spec: ChartSpec)
    case stats(id: Int, cards: [StatCard])
    var id: Int {
        switch self {
        case .prose(let i, _), .chart(let i, _), .stats(let i, _): return i
        }
    }
}

enum VeraBlocks {
    /// Split a reply into ordered prose / chart / stats segments, preserving position. Fenced
    /// ```vera:chart / ```vera:stats blocks become native views; an incomplete trailing block
    /// (still streaming) is hidden.
    static func segments(_ text: String) -> [ReplySegment] {
        let ns = text as NSString
        guard let re = try? NSRegularExpression(
            pattern: "(?m)^```vera:(chart|stats)[^\\n]*\\n([\\s\\S]*?)\\n```[ \\t]*$") else {
            return [.prose(id: 0, text: text)]
        }
        var segs: [ReplySegment] = []
        var idx = 0
        var cursor = 0
        func addProse(_ s: String) {
            var clean = s
            // Hide an unclosed trailing vera fence (mid-stream).
            if let openRe = try? NSRegularExpression(pattern: "(?m)^```vera:(chart|stats)[\\s\\S]*$") {
                clean = openRe.stringByReplacingMatches(
                    in: clean, range: NSRange(location: 0, length: (clean as NSString).length), withTemplate: "")
            }
            let t = clean.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { segs.append(.prose(id: idx, text: t)); idx += 1 }
        }
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            if m.range.location > cursor {
                addProse(ns.substring(with: NSRange(location: cursor, length: m.range.location - cursor)))
            }
            let kind = ns.substring(with: m.range(at: 1))
            let body = ns.substring(with: m.range(at: 2))
            if kind == "chart", let spec = parseChart(body) { segs.append(.chart(id: idx, spec: spec)); idx += 1 }
            else if kind == "stats", let cards = parseStats(body) { segs.append(.stats(id: idx, cards: cards)); idx += 1 }
            cursor = m.range.location + m.range.length
        }
        if cursor < ns.length { addProse(ns.substring(from: cursor)) }
        if segs.isEmpty { addProse(text) }
        return segs
    }

    /// True if the reply contains any native block (used to decide whether to segment at all).
    static func hasBlocks(_ text: String) -> Bool {
        text.range(of: "(?m)^```vera:(chart|stats)", options: .regularExpression) != nil
    }

    static func parseChart(_ json: String) -> ChartSpec? {
        guard let d = json.data(using: .utf8),
              let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return nil }
        let kind = ChartSpec.Kind(rawValue: (o["type"] as? String) ?? "bar") ?? .bar
        let series: [ChartSpec.Series] = ((o["series"] as? [[String: Any]]) ?? []).compactMap { s in
            guard let name = s["name"] as? String else { return nil }
            let points: [ChartSpec.Point] = ((s["points"] as? [[String: Any]]) ?? []).compactMap { p in
                guard let xv = p["x"] else { return nil }
                let y: Double? = (p["y"] as? NSNumber)?.doubleValue ?? Double("\(p["y"] ?? "")")
                guard let yv = y else { return nil }
                return ChartSpec.Point(x: "\(xv)", y: yv)
            }
            return points.isEmpty ? nil : ChartSpec.Series(name: name, points: points)
        }
        guard !series.isEmpty else { return nil }
        return ChartSpec(type: kind, title: (o["title"] as? String) ?? "",
                         xLabel: (o["xLabel"] as? String) ?? "", yLabel: (o["yLabel"] as? String) ?? "",
                         series: series)
    }

    static func parseStats(_ json: String) -> [StatCard]? {
        guard let d = json.data(using: .utf8),
              let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              let arr = o["cards"] as? [[String: Any]] else { return nil }
        let cards: [StatCard] = arr.compactMap { c in
            guard let v = c["value"], let label = c["label"] as? String else { return nil }
            return StatCard(value: "\(v)", label: label, sub: c["sub"] as? String)
        }
        return cards.isEmpty ? nil : cards
    }
}
