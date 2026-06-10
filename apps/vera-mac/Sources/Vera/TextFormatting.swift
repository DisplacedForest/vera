import Foundation

extension String {
    /// A clean single-paragraph plain-text snippet from markdown — for list/card previews.
    /// Drops a leading heading (it usually repeats the title) and strips inline markdown.
    func strippedMarkdown(droppingTitle title: String = "") -> String {
        var lines = components(separatedBy: "\n")
        while let first = lines.first {
            let t = first.trimmingCharacters(in: .whitespaces)
            if t.isEmpty || t.hasPrefix("#") || (!title.isEmpty && t == title) { lines.removeFirst() } else { break }
        }
        var text = lines.joined(separator: " ")
        let subs: [(String, String)] = [
            ("`{1,3}", ""),                          // code ticks
            ("\\*\\*([^*]+)\\*\\*", "$1"),           // bold
            ("__([^_]+)__", "$1"),
            ("\\*([^*]+)\\*", "$1"),                 // italic
            ("\\[([^\\]]+)\\]\\([^)]*\\)", "$1"),    // links -> text
            ("[#>]", ""),                            // headings / quotes
            ("(^|\\s)[-*+]\\s+", " "),               // bullets
            ("\\s+", " "),                           // collapse whitespace
        ]
        for (p, r) in subs { text = text.replacingOccurrences(of: p, with: r, options: .regularExpression) }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// Parses the `<!--vera-*-->` markers the Pulse pipeline stamps on a card.
enum PulseMarkers {
    static func parse(_ raw: String) -> (image: String?, tint: String?, summary: String?,
                                         sources: [PulseSource], inlineImages: [PulseInlineImage], body: String) {
        let ns = raw as NSString
        let full = NSRange(location: 0, length: ns.length)
        func cap(_ pattern: String) -> String? {
            guard let re = try? NSRegularExpression(pattern: pattern) else { return nil }
            guard let m = re.firstMatch(in: raw, range: full), m.numberOfRanges > 1 else { return nil }
            return ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespaces)
        }
        func all(_ pattern: String) -> [[String]] {
            guard let re = try? NSRegularExpression(pattern: pattern) else { return [] }
            return re.matches(in: raw, range: full).map { m in
                (0..<m.numberOfRanges).map { i in
                    m.range(at: i).location == NSNotFound ? "" : ns.substring(with: m.range(at: i))
                }
            }
        }
        let image = cap("<!--vera-image (.+?)-->")
        let tint = cap("<!--vera-tint (.+?)-->")
        let summary = cap("<!--vera-summary (.+?)-->")
        // <!--vera-source N|title|url-->
        let sources: [PulseSource] = all("<!--vera-source (\\d+)\\|(.*?)\\|(.*?)-->").compactMap { g in
            guard g.count >= 4, let n = Int(g[1]) else { return nil }
            return PulseSource(n: n, title: g[2].trimmingCharacters(in: .whitespaces), url: g[3].trimmingCharacters(in: .whitespaces))
        }.sorted { $0.n < $1.n }
        // <!--vera-inline N|url|caption|srcN-->
        let inlineImages: [PulseInlineImage] = all("<!--vera-inline (\\d+)\\|(.*?)\\|(.*?)\\|(.*?)-->").compactMap { g in
            guard g.count >= 5, let n = Int(g[1]) else { return nil }
            let srcN = Int(g[4]); return PulseInlineImage(n: n, url: g[2].trimmingCharacters(in: .whitespaces),
                                                          caption: g[3].trimmingCharacters(in: .whitespaces),
                                                          sourceN: (srcN == 0 ? nil : srcN))
        }.sorted { $0.n < $1.n }
        var body = raw
        if let re = try? NSRegularExpression(pattern: "<!--vera-[a-z]+ .*?-->\\n?") {
            body = re.stringByReplacingMatches(in: body, range: NSRange(location: 0, length: (body as NSString).length), withTemplate: "")
        }
        return (image, tint, summary, sources, inlineImages, body.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// Strip vera markers (and inline-image tokens) from content — for a clean Pulse chat view.
    static func strip(_ raw: String) -> String {
        var body = parse(raw).body
        if let re = try? NSRegularExpression(pattern: "\\[\\[img:\\d+\\]\\]\\n?") {
            body = re.stringByReplacingMatches(in: body, range: NSRange(location: 0, length: (body as NSString).length), withTemplate: "")
        }
        return body.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Remove a trailing "Sources" heading + list from a card body (we render favicons instead).
    static func stripSourcesSection(_ body: String) -> String {
        guard let re = try? NSRegularExpression(
            pattern: "(?im)\\n+#{0,4}\\s*\\*{0,2}sources\\*{0,2}\\s*:?\\s*\\n[\\s\\S]*$") else { return body }
        let ns = body as NSString
        return re.stringByReplacingMatches(in: body, range: NSRange(location: 0, length: ns.length), withTemplate: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// Tidies the bare-URL "Sources" wall that web-search replies append: a run of ≥2 URLs becomes
/// a bulleted list, and every bare URL is shortened to a clickable `[host](url)` link.
enum SourceFormatter {
    static func apply(_ text: String) -> String { linkify(breakRuns(text)) }

    /// Break a run of 2+ space-separated bare URLs onto their own bullet lines.
    private static func breakRuns(_ text: String) -> String {
        guard let re = try? NSRegularExpression(pattern: "https?://[^\\s)>\\]]+(?:[ \\t]+https?://[^\\s)>\\]]+)+") else { return text }
        let original = text as NSString
        var s = text
        for m in re.matches(in: text, range: NSRange(location: 0, length: original.length)).reversed() {
            let urls = original.substring(with: m.range).split { $0 == " " || $0 == "\t" }.map(String.init)
            let bullets = "\n\n" + urls.map { "- \($0)" }.joined(separator: "\n")
            s = (s as NSString).replacingCharacters(in: m.range, with: bullets)
        }
        return s
    }

    /// Replace bare URLs (not already inside a markdown/autolink) with `[host](url)`.
    private static func linkify(_ text: String) -> String {
        guard let re = try? NSRegularExpression(pattern: "(?<![\\(<])https?://[^\\s)>\\]]+") else { return text }
        let ns = text as NSString
        var out = ""; var last = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            let url = ns.substring(with: m.range)
            out += "[\(host(url))](\(url))"
            last = m.range.location + m.range.length
        }
        out += ns.substring(from: last)
        return out
    }

    private static func host(_ url: String) -> String {
        guard let h = URL(string: url)?.host else { return url }
        return h.hasPrefix("www.") ? String(h.dropFirst(4)) : h
    }
}
