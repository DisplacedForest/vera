import SwiftUI
import AppKit
import MarkdownUI

/// Renders an assistant message: tool-call chips (parsed out of OWUI's `<details type="tool_calls">`
/// HTML) + the prose as real markdown + any structured question / artifact chips.
struct AssistantBody: View {
    @EnvironmentObject var store: ChatStore
    let message: Message
    var onAnswer: ((UUID, [String], String) -> Void)? = nil
    var onOpenArtifact: ((Artifact) -> Void)? = nil

    var body: some View {
        // A continued Pulse briefing renders as the full rich article (hero, cited paragraphs, photos).
        if let card = message.pulse {
            PulseArticleView(card: card, token: store.apiToken)
        } else {
            prose
        }
    }

    private var prose: some View {
        let parsed = ToolCallParser.parse(message.text)
        // Cited replies drop their trailing plain-text "Sources" list — the SourcesRow below
        // renders the real thing (no-op when the section is absent).
        let display = message.sources.isEmpty ? parsed.clean : PulseMarkers.stripSourcesSection(parsed.clean)
        return VStack(alignment: .leading, spacing: 10) {
            ForEach(ToolCallParser.group(parsed.calls)) { ToolGroupChip(group: $0) }
            // Interleave prose (markdown — incl. tables) with native chart / stat-card blocks.
            ForEach(VeraBlocks.segments(display)) { seg in
                switch seg {
                case .prose(_, let t):
                    // Cited replies get the Pulse treatment: refs stripped, chips beneath.
                    if message.sources.isEmpty { ProseMarkdown(text: t) }
                    else { CitedProse(text: t, sources: message.sources) }
                case .chart(_, let spec): ChartBlockView(spec: spec)
                case .stats(_, let cards): StatCardsView(cards: cards)
                }
            }
            if message.ask != nil {
                VeraAskCard(message: message, onAnswer: onAnswer)
            }
            ForEach(message.artifacts) { art in
                Button { onOpenArtifact?(art) } label: { ArtifactChip(artifact: art) }.buttonStyle(.plain)
            }
            // Pulse parity: the expandable sources row at the bottom of a cited reply.
            if !message.sources.isEmpty {
                SourcesRow(sources: message.sources)
            }
            // Response actions — copy + thumbs up/down (ratings feed the preference log).
            if !parsed.clean.isEmpty {
                HStack(spacing: 16) {
                    msgAction("doc.on.doc") { copyText(parsed.clean) }
                    msgAction("hand.thumbsup", on: store.messageRatings[message.id] == "up") { rate("up") }
                    msgAction("hand.thumbsdown", on: store.messageRatings[message.id] == "down") { rate("down") }
                }
                .padding(.top, 2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func rate(_ sentiment: String) {
        if let convo = store.selected { store.rateMessage(message, in: convo, sentiment) }
    }

    private func copyText(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    private func msgAction(_ icon: String, on: Bool = false, _ go: @escaping () -> Void) -> some View {
        Button(action: go) {
            Image(systemName: on ? icon + ".fill" : icon)
                .font(.system(size: 13)).foregroundStyle(on ? Theme.accent : Theme.textSecondary)
                .frame(width: 24, height: 20)
        }
        .buttonStyle(.plain).pointerCursor()
    }
}

/// A prose segment rendered as themed markdown (tidies bare-URL source walls first).
struct ProseMarkdown: View {
    let text: String
    var body: some View {
        Markdown(SourceFormatter.apply(text))
            .markdownTextStyle { ForegroundColor(Theme.textPrimary); FontSize(14) }
            .markdownTextStyle(\.code) { FontFamilyVariant(.monospaced); BackgroundColor(Theme.surfaceHover) }
            .textSelection(.enabled)
    }
}

struct ToolCall: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let detail: String
}

/// Consecutive calls of the same tool, collapsed into one row (avoids a stack of identical chips).
struct ToolCallGroup: Identifiable {
    let id = UUID()
    let name: String
    let calls: [ToolCall]
}

/// Pulls OWUI's `<details type="tool_calls" … name="X">…</details>` blocks out of a reply.
enum ToolCallParser {
    static func parse(_ text: String) -> (clean: String, calls: [ToolCall]) {
        let ns = text as NSString
        var calls: [ToolCall] = []
        if let re = try? NSRegularExpression(
            pattern: "<details type=\"tool_calls\"[^>]*?name=\"([^\"]+)\"[^>]*>([\\s\\S]*?)</details>", options: []) {
            for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
                let name = ns.substring(with: m.range(at: 1))
                var inner = ns.substring(with: m.range(at: 2))
                inner = inner.replacingOccurrences(of: "<summary>[\\s\\S]*?</summary>", with: "", options: .regularExpression)
                calls.append(ToolCall(name: name, detail: decode(inner).trimmingCharacters(in: .whitespacesAndNewlines)))
            }
        }
        var clean = text
        // Reasoning blocks are model thinking, never part of the reply — stripped entirely.
        if let re = try? NSRegularExpression(
            pattern: "<details type=\"(?:tool_calls|reasoning)\"[\\s\\S]*?</details>", options: []) {
            clean = re.stringByReplacingMatches(in: clean, range: NSRange(location: 0, length: (clean as NSString).length), withTemplate: "")
        }
        // Strip an incomplete trailing block while streaming.
        if let re = try? NSRegularExpression(pattern: "<details type=\"(?:tool_calls|reasoning)\"[\\s\\S]*$", options: []) {
            let cns = clean as NSString
            if let mm = re.firstMatch(in: clean, range: NSRange(location: 0, length: cns.length)) {
                clean = cns.replacingCharacters(in: mm.range, with: "")
            }
        }
        return (clean.trimmingCharacters(in: .whitespacesAndNewlines), calls)
    }

    /// Collapse runs of the same tool name into groups (preserving interleaving with other tools).
    static func group(_ calls: [ToolCall]) -> [ToolCallGroup] {
        var groups: [ToolCallGroup] = []
        for c in calls {
            if let last = groups.last, last.name == c.name {
                groups[groups.count - 1] = ToolCallGroup(name: last.name, calls: last.calls + [c])
            } else {
                groups.append(ToolCallGroup(name: c.name, calls: [c]))
            }
        }
        return groups
    }

    static func decode(_ s: String) -> String {
        s.replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&#x27;", with: "'")
            .replacingOccurrences(of: "&#x2F;", with: "/")
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "\\n", with: "\n")
    }
}

/// A collapsible "used <tool>" chip representing one or more consecutive calls of the same tool.
/// Shows a ×N count when repeated; expands to each call's raw detail.
struct ToolGroupChip: View {
    let group: ToolCallGroup
    @State private var open = false
    private var details: [ToolCall] { group.calls.filter { !$0.detail.isEmpty } }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { if !details.isEmpty { open.toggle() } } label: {
                HStack(spacing: 6) {
                    Image(systemName: "wrench.and.screwdriver.fill").font(.system(size: 9))
                    Text("used \(group.name)").font(.system(size: 11, weight: .medium))
                    if group.calls.count > 1 {
                        Text("\(group.calls.count)×").font(.system(size: 10, weight: .semibold))
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Theme.surface).clipShape(Capsule())
                    }
                    if !details.isEmpty {
                        Image(systemName: open ? "chevron.down" : "chevron.right").font(.system(size: 8))
                    }
                }
                .foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 9).padding(.vertical, 4)
                .background(Theme.surfaceHover).clipShape(Capsule())
            }
            .buttonStyle(.plain)
            if open {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(details.enumerated()), id: \.offset) { _, call in
                        Text(call.detail)
                            .font(.system(size: 11, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(8)
                            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            }
        }
    }
}
