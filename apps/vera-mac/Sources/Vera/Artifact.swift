import Foundation

enum ArtifactType: String, Codable { case markdown, code, html, svg, mermaid, unknown }

/// A Canvas artifact Vera emits via a directive block in her reply:
///
///     :::vera-artifact id="landing" title="Landing page" type="html"
///     <raw content — may span many lines, contain ``` etc.>
///     :::
///
/// Raw body (not JSON) so large HTML/code survives without escaping. The app parses these
/// out of the streamed reply, hides the block, and renders the artifact in the Canvas panel.
struct Artifact: Identifiable, Hashable, Codable {
    var id: String
    var title: String
    var type: ArtifactType
    var language: String      // for code artifacts (e.g. "swift")
    var content: String
    var updatedAt: Date = Date()

    /// Extract all `:::vera-artifact ... :::` blocks from assistant text.
    /// Returns the text with blocks removed + the parsed artifacts (most recent last).
    /// A mid-stream incomplete block (open directive, no close yet) is hidden, no artifact yet.
    static func parse(_ text: String) -> (clean: String, artifacts: [Artifact]) {
        let ns = text as NSString
        var artifacts: [Artifact] = []
        guard let re = try? NSRegularExpression(
            pattern: "(?m)^:::vera-artifact([^\\n]*)\\n([\\s\\S]*?)\\n:::\\s*$", options: []) else {
            return (text, [])
        }
        let matches = re.matches(in: text, range: NSRange(location: 0, length: ns.length))
        for m in matches {
            let attrs = ns.substring(with: m.range(at: 1))
            let body = ns.substring(with: m.range(at: 2))
            artifacts.append(make(attrs: attrs, body: body))
        }
        var clean = re.stringByReplacingMatches(in: text, range: NSRange(location: 0, length: ns.length), withTemplate: "")
        // Hide an incomplete trailing block while streaming.
        if let openRe = try? NSRegularExpression(pattern: "(?m)^:::vera-artifact[\\s\\S]*$", options: []) {
            let cns = clean as NSString
            if let mm = openRe.firstMatch(in: clean, range: NSRange(location: 0, length: cns.length)) {
                clean = cns.replacingCharacters(in: mm.range, with: "")
            }
        }
        return (clean.trimmingCharacters(in: .whitespacesAndNewlines), artifacts)
    }

    private static func make(attrs: String, body: String) -> Artifact {
        let a = parseAttrs(attrs)
        let lang = a["language"] ?? a["lang"] ?? ""
        let type: ArtifactType = {
            switch (a["type"] ?? "").lowercased() {
            case "markdown", "md": return .markdown
            case "html": return .html
            case "svg": return .svg
            case "mermaid": return .mermaid
            case "code": return .code
            default:
                if lang.lowercased() == "html" { return .html }
                if lang.lowercased() == "svg" { return .svg }
                if lang.lowercased() == "mermaid" { return .mermaid }
                return lang.isEmpty ? .markdown : .code
            }
        }()
        let title = a["title"] ?? "Untitled"
        let id = a["id"] ?? title.lowercased().replacingOccurrences(of: " ", with: "-")
        return Artifact(id: id, title: title, type: type, language: lang, content: body)
    }

    /// Parse `key="value"` / `key=value` pairs from the directive header.
    private static func parseAttrs(_ s: String) -> [String: String] {
        var out: [String: String] = [:]
        guard let re = try? NSRegularExpression(pattern: "(\\w+)\\s*=\\s*\"([^\"]*)\"|(\\w+)\\s*=\\s*(\\S+)") else { return out }
        let ns = s as NSString
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            if m.range(at: 1).location != NSNotFound {
                out[ns.substring(with: m.range(at: 1))] = ns.substring(with: m.range(at: 2))
            } else if m.range(at: 3).location != NSNotFound {
                out[ns.substring(with: m.range(at: 3))] = ns.substring(with: m.range(at: 4))
            }
        }
        return out
    }

    static func mock() -> Artifact {
        Artifact(id: "demo-svg", title: "Vera mark sketch", type: .svg, language: "",
                 content: "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"120\" height=\"120\"><circle cx=\"60\" cy=\"60\" r=\"50\" fill=\"#E8923B\"/></svg>")
    }
}
