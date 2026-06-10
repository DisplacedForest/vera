import SwiftUI

/// The one citation language for the whole app: numbered `[n]` / `[n,m]` refs are pulled out
/// of prose and rendered as tappable favicon+host capsule chips beneath the paragraph that
/// cited them. Pulse detail and chat replies both consume these — never a second implementation.

/// Pull EVERY `[n]` / `[n,m]` citation group from a paragraph (Vera cites mid-sentence, not just at
/// the end) → (clean text with refs removed + spacing tidied, aggregated source numbers).
func extractRefs(_ text: String) -> (String, [Int]) {
    // Match a bracket group that starts with a digit (so it never eats an [[img:N]] token).
    guard let re = try? NSRegularExpression(pattern: "\\s*\\[[0-9][0-9,\\s]*\\]") else { return (text, []) }
    let ns = text as NSString
    let matches = re.matches(in: text, range: NSRange(location: 0, length: ns.length))
    guard !matches.isEmpty else { return (text, []) }
    var nums: [Int] = []
    for m in matches {
        nums += ns.substring(with: m.range).components(separatedBy: CharacterSet(charactersIn: "[], ")).compactMap { Int($0) }
    }
    var clean = re.stringByReplacingMatches(in: text, range: NSRange(location: 0, length: ns.length), withTemplate: "")
    // Tidy the gaps the refs left behind: space-before-punctuation, doubled spaces.
    clean = clean.replacingOccurrences(of: "\\s+([.,;:!?])", with: "$1", options: .regularExpression)
    clean = clean.replacingOccurrences(of: "[ \\t]{2,}", with: " ", options: .regularExpression)
    return (clean.trimmingCharacters(in: .whitespacesAndNewlines), Array(Set(nums)).sorted())
}

/// A tappable per-paragraph source pill (favicon + host) that opens the cited page.
struct CitationChip: View {
    let source: PulseSource
    var body: some View {
        HStack(spacing: 5) {
            Favicon(urlString: source.url)
            Text(sourceHost(source.url)).font(.system(size: 11, weight: .medium))
                .foregroundStyle(Theme.textSecondary).lineLimit(1)
        }
        .padding(.horizontal, 8).padding(.vertical, 4)
        .background(Theme.surface).clipShape(Capsule())
        .overlay(Capsule().stroke(Theme.hairline, lineWidth: 1))
        .contentShape(Capsule())
        .onTapGesture { openExternal(source.url) }
        .pointerCursor()
    }
}

/// Maps OWUI's per-message `sources` payload (RAG/tool citation entries) to numbered
/// PulseSources, 1-based in payload order — the same numbers the reply's `[n]` refs use.
enum OWUISources {
    static func parse(_ raw: [[String: Any]]) -> [PulseSource] {
        var out: [PulseSource] = []
        for (i, entry) in raw.enumerated() {
            let src = entry["source"] as? [String: Any] ?? [:]
            let metaURL = ((entry["metadata"] as? [[String: Any]])?.first?["source"] as? String) ?? ""
            let name = (src["name"] as? String) ?? ""
            let url = (src["url"] as? String)
                ?? (metaURL.hasPrefix("http") ? metaURL : nil)
                ?? (name.hasPrefix("http") ? name : nil)
            guard let url, !url.isEmpty else { continue }
            out.append(PulseSource(n: i + 1, title: name.isEmpty || name.hasPrefix("http") ? sourceHost(url) : name,
                                   url: url))
        }
        return out
    }
}

/// A chat prose segment with the Pulse citation treatment: paragraphs render with their `[n]`
/// refs stripped and the matching chips beneath. Segments containing fenced code render whole
/// (never split a fence), with the segment's chips aggregated below.
struct CitedProse: View {
    let text: String
    let sources: [PulseSource]

    var body: some View {
        if text.contains("```") {
            let (_, refs) = extractRefs(text)
            VStack(alignment: .leading, spacing: 8) {
                ProseMarkdown(text: text)
                chipRow(refs)
            }
        } else {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(text.components(separatedBy: "\n\n").enumerated()), id: \.offset) { _, para in
                    let t = para.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !t.isEmpty {
                        let (clean, refs) = extractRefs(t)
                        VStack(alignment: .leading, spacing: 8) {
                            ProseMarkdown(text: clean)
                            chipRow(refs)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func chipRow(_ refs: [Int]) -> some View {
        let chips = refs.compactMap { n in sources.first { $0.n == n } }
        if !chips.isEmpty {
            HStack(spacing: 6) { ForEach(chips) { CitationChip(source: $0) } }
        }
    }
}
