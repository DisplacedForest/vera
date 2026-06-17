import SwiftUI
import AppKit
import MarkdownUI

/// ChatGPT-style Pulse article detail: hero cover, first-person body rendered paragraph-by-paragraph
/// with per-paragraph citation chips, real inline photos, and a tap-to-expand Sources list.
struct PulseDetailView: View {
    @EnvironmentObject var store: ChatStore
    let card: PulseCard
    var token: String? = nil
    var onClose: () -> Void = {}
    var onContinue: () -> Void = {}

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Theme.bg.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let img = card.imageURL, !img.isEmpty {
                        AuthedAsyncImage(url: img, token: token)
                            .frame(height: 300).frame(maxWidth: .infinity).clipped()
                    }
                    VStack(alignment: .leading, spacing: 16) {
                        PulseArticleView(card: card, token: token, includeHero: false)
                        if !card.changeSet.isEmpty {
                            GroomChangeSetView(card: card)   // grooming diff (renders its own flagged items)
                        } else if !card.items.isEmpty {      // multi-item digest (media picks / stack updates)
                            if card.category == "update" { UpdatesDigestView(card: card) }
                            else { MediaDigestView(card: card) }
                        }
                        HStack(spacing: 14) {
                            Button(action: onContinue) {
                                HStack(spacing: 8) {
                                    Image(systemName: "bubble.left")
                                    Text("Continue in chat")
                                }
                                .font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.textPrimary)
                                .padding(.horizontal, 14).padding(.vertical, 9)
                                .background(Theme.surface).clipShape(Capsule())
                                .overlay(Capsule().stroke(Theme.hairline, lineWidth: 1))
                            }
                            .buttonStyle(.plain)
                            detailThumb("hand.thumbsup", on: store.pulseRatings[card.id] == "up") { store.ratePulse(card, "up") }
                            detailThumb("hand.thumbsdown", on: store.pulseRatings[card.id] == "down") { store.ratePulse(card, "down") }
                        }
                        .padding(.top, 4)
                    }
                    // Without a full-bleed hero the title is the topmost element — give it
                    // title-bar clearance (the overlay sits under the hidden title bar).
                    .padding(.horizontal, 24).padding(.bottom, 24)
                    .padding(.top, (card.imageURL?.isEmpty == false) ? 24 : 36)
                    .frame(maxWidth: 720, alignment: .leading)
                    .frame(maxWidth: .infinity)
                }
            }
            HStack(spacing: 10) {
                detailBtn(store.bookmarkedPulseIDs.contains(card.id) ? "bookmark.fill" : "bookmark") {
                    store.bookmarkPulse(card)
                }
                detailBtn("xmark", action: onClose)
            }
            // 36 top — the overlay sits under the hidden title bar, which clips anything closer.
            .padding(.top, 36).padding(.horizontal, 16).padding(.bottom, 16)
        }
        .onAppear { store.markPulseRead(card) }   // opening the detail IS the read event
    }

    private func detailBtn(_ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 13, weight: .semibold)).foregroundStyle(.white)
                .frame(width: 30, height: 30).background(Color.black.opacity(0.5)).clipShape(Circle())
        }
        .buttonStyle(.plain)
    }

    private func detailThumb(_ icon: String, on: Bool, _ go: @escaping () -> Void) -> some View {
        Button(action: go) {
            Image(systemName: on ? icon + ".fill" : icon)
                .font(.system(size: 14)).foregroundStyle(on ? Theme.accent : Theme.textSecondary)
                .frame(width: 30, height: 30)
        }
        .buttonStyle(.plain).pointerCursor()
    }
}

/// The article itself (hero + title + cited paragraphs + inline photos + sources), reusable as the
/// detail body AND as a rich first message when a Pulse is continued in chat. `includeHero` is off
/// in the detail view (which draws its own full-bleed hero above).
struct PulseArticleView: View {
    let card: PulseCard
    var token: String? = nil
    var includeHero: Bool = true
    var heroHeight: CGFloat = 200
    var titleSize: CGFloat = 24

    private var blocks: [PulseBlock] {
        pulseBlocks(PulseMarkers.stripSourcesSection(card.body), images: card.inlineImages)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if includeHero, let img = card.imageURL, !img.isEmpty {
                AuthedAsyncImage(url: img, token: token)
                    .frame(height: heroHeight).frame(maxWidth: .infinity).clipped()
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            Text(card.title).font(.system(size: titleSize, weight: .bold)).foregroundStyle(Theme.textPrimary)
            ForEach(blocks) { block in
                switch block {
                case .paragraph(_, let text, let refs):
                    PulseParagraph(text: text, refs: refs, sources: card.sourceList)
                case .image(let im):
                    PulseInlineImageView(image: im, token: token)
                case .chart(_, let spec):
                    ChartBlockView(spec: spec)
                case .stats(_, let cards):
                    StatCardsView(cards: cards)
                }
            }
            if !card.sourceList.isEmpty { SourcesRow(sources: card.sourceList) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Body block model

/// A rendered unit of a Pulse body: a paragraph (with trailing citation refs), an inline image, or a
/// native presentation block (chart / stat cards) Vera emitted in the briefing.
enum PulseBlock: Identifiable {
    case paragraph(id: Int, text: String, refs: [Int])
    case image(PulseInlineImage)
    case chart(id: Int, spec: ChartSpec)
    case stats(id: Int, cards: [StatCard])
    var id: String {
        switch self {
        case .paragraph(let i, _, _): return "p\(i)"
        case .image(let im): return "img\(im.n)"
        case .chart(let i, _): return "c\(i)"
        case .stats(let i, _): return "s\(i)"
        }
    }
}

/// Split a marker-stripped body into ordered blocks: native chart/stat-card blocks (vera:chart /
/// vera:stats fences) first, then within each prose run, `[[img:n]]` image slots and paragraphs
/// (each paragraph's `[n,m]` citation refs pulled out of the prose).
func pulseBlocks(_ body: String, images: [PulseInlineImage]) -> [PulseBlock] {
    let imgByN = Dictionary(images.map { ($0.n, $0) }, uniquingKeysWith: { a, _ in a })
    var blocks: [PulseBlock] = []
    var seq = 0
    func emitProse(_ s: String) {
        let ns = s as NSString
        func addParas(_ str: String) {
            for para in str.components(separatedBy: "\n\n") {
                let t = para.trimmingCharacters(in: .whitespacesAndNewlines)
                if t.isEmpty { continue }
                let (clean, refs) = extractRefs(t)
                if clean.isEmpty && refs.isEmpty { continue }
                blocks.append(.paragraph(id: seq, text: clean, refs: refs)); seq += 1
            }
        }
        guard let re = try? NSRegularExpression(pattern: "\\[\\[img:(\\d+)\\]\\]") else { addParas(s); return }
        var cursor = 0
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            if m.range.location > cursor {
                addParas(ns.substring(with: NSRange(location: cursor, length: m.range.location - cursor)))
            }
            if let n = Int(ns.substring(with: m.range(at: 1))), let im = imgByN[n] { blocks.append(.image(im)) }
            cursor = m.range.location + m.range.length
        }
        if cursor < ns.length { addParas(ns.substring(from: cursor)) }
    }
    for seg in VeraBlocks.segments(body) {
        switch seg {
        case .prose(_, let text): emitProse(text)
        case .chart(_, let spec): blocks.append(.chart(id: seq, spec: spec)); seq += 1
        case .stats(_, let cards): blocks.append(.stats(id: seq, cards: cards)); seq += 1
        }
    }
    return blocks
}

// MARK: - Block views

/// One body paragraph + its citation chips.
struct PulseParagraph: View {
    let text: String
    let refs: [Int]
    let sources: [PulseSource]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Markdown(SourceFormatter.apply(text))
                .markdownTextStyle { ForegroundColor(Theme.textPrimary); FontSize(15) }
                .markdownTextStyle(\.code) { FontFamilyVariant(.monospaced); BackgroundColor(Theme.surfaceHover) }
                .textSelection(.enabled)
            let chips = refs.compactMap { n in sources.first { $0.n == n } }
            if !chips.isEmpty {
                HStack(spacing: 6) { ForEach(chips) { CitationChip(source: $0) } }
            }
        }
    }
}

/// Open a URL string in the default browser.
func openExternal(_ s: String) {
    guard let u = URL(string: s) else { return }
    NSWorkspace.shared.open(u)
}

extension View {
    /// Show the pointing-hand cursor while hovering (affordance for tappable rows/chips).
    func pointerCursor() -> some View {
        onHover { inside in
            if inside { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }
}

/// A real photo woven into the article body, with caption.
struct PulseInlineImageView: View {
    let image: PulseInlineImage
    var token: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Natural aspect ratio, width-driven, capped height — shows the whole photo (no face/edge crop).
            AuthedAsyncImage(url: image.url, token: token, natural: true, placeholderHeight: 200)
                .frame(maxWidth: .infinity)
                .frame(maxHeight: 360)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
            if !image.caption.isEmpty {
                Text(image.caption).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            }
        }
    }
}

/// The real diff behind the nightly grooming digest — every applied op rendered
/// with concrete detail (which belief / entity / type, the migrated entities, the merged pair),
/// grouped by store (World-model / Knowledge store), each expandable and Restore/Reject-able, plus a
/// Flagged-for-review section for proposals that need a human Approve/Reject.
/// Render-safe (plain VStack/Text), so the screenshot harness can capture it directly.
struct GroomChangeSetView: View {
    @EnvironmentObject var store: ChatStore
    let card: PulseCard
    @State private var expanded: Set<Int> = []

    private var memoryOps: [GroomOp] { card.changeSet.filter { $0.store == "memory" } }
    private var knowledgeOps: [GroomOp] { card.changeSet.filter { $0.store == "knowledge" } }
    private var flagged: [PulseDigestItem] { card.items.filter { ($0.group ?? "") == "Flagged for review" } }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Rectangle().fill(Theme.hairline).frame(height: 1)
            Text("What I changed").font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.textSecondary)
            section("World-model", memoryOps)
            section("Knowledge store", knowledgeOps)
            if !flagged.isEmpty { flaggedSection }
        }
    }

    @ViewBuilder private func section(_ title: String, _ ops: [GroomOp]) -> some View {
        if !ops.isEmpty {
            Text(title.uppercased()).font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textSecondary).tracking(0.5).padding(.top, 4)
            ForEach(ops) { op in opRow(op) }
        }
    }

    private func opRow(_ op: GroomOp) -> some View {
        let isOpen = expanded.contains(op.index)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: opIcon(op.type)).font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                Text(opTitle(op)).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 0)
                restoreControl(op)
            }
            ForEach(Array(primaryLines(op).enumerated()), id: \.offset) { _, line in
                Text(line).font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let detail = detailLines(op), !detail.isEmpty {
                Button(action: { if isOpen { expanded.remove(op.index) } else { expanded.insert(op.index) } }) {
                    HStack(spacing: 4) {
                        Image(systemName: isOpen ? "chevron.down" : "chevron.right").font(.system(size: 9, weight: .semibold))
                        Text(isOpen ? "hide detail" : "show detail").font(.system(size: 11, weight: .medium))
                    }.foregroundStyle(Theme.textSecondary)
                }.buttonStyle(.plain).pointerCursor()
                if isOpen {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(detail.enumerated()), id: \.offset) { _, d in
                            Text(d).font(.system(size: 12)).foregroundStyle(Theme.textSecondary.opacity(0.9))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }.padding(.leading, 10)
                }
            }
            if !op.reason.isEmpty {
                Text(op.reason).font(.system(size: 11)).foregroundStyle(Theme.textSecondary.opacity(0.75))
            }
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    /// The concrete summary line(s) shown un-expanded: the result for promote/archive/codify, the
    /// before→after pair for a merge, the affected snapshot for gc/forget.
    private func primaryLines(_ op: GroomOp) -> [String] {
        switch op.type {
        case "merge":
            return op.before.map { $0.line } + (op.after.map { ["→ \($0.line)"] } ?? [])
        case "promote", "archive", "codify":
            return [(op.after ?? op.before.first)?.line ?? ""]
        default:  // gc, forget
            return op.before.map { $0.line }
        }
    }

    /// Expanded before/after detail — present when a snapshot carries more than its one-line summary
    /// (entity attrs, a codified type's fields + migrated entities, or a merge's source records).
    private func detailLines(_ op: GroomOp) -> [String]? {
        var out: [String] = []
        if op.type == "merge" { out += op.before.flatMap { ["from \($0.line)"] } }
        for snap in ([op.after].compactMap { $0 }) where snap.kind == "type" || snap.kind == "entity" {
            if snap.detail.count > 1 || (snap.kind == "type" && !snap.migrated.isEmpty) {
                out += snap.detail
            }
        }
        return out.isEmpty ? nil : out
    }

    @ViewBuilder private func restoreControl(_ op: GroomOp) -> some View {
        if op.reversible {
            let state = store.restoreState["\(card.id):\(op.index)"]
            switch state {
            case "done":
                Label("Restored", systemImage: "checkmark").font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Theme.textSecondary)
            case "rejected":
                Label("Rejected", systemImage: "hand.thumbsdown").font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Theme.textSecondary)
            case "running":
                Image(systemName: "clock").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            case "stale":
                Text("changed since, review").font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
            default:
                HStack(spacing: 6) {
                    if state == "failed" {
                        Text("failed").font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
                    }
                    pill(op.type == "merge" ? "Undo merge" : "Restore", filled: false) { store.restoreMemoryOp(card, op) }
                    pill("Reject", filled: false) { store.rejectOp(card, op) }
                }
            }
        }
    }

    /// The Flagged-for-review section — proposals Vera left for a human. Pending rows offer
    /// Approve/Reject; info rows (e.g. lossy merges that need reconciliation) are read-only.
    private var flaggedSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("FLAGGED FOR REVIEW").font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textSecondary).tracking(0.5).padding(.top, 4)
            ForEach(flagged) { item in flaggedRow(item) }
        }
    }

    private func flaggedRow(_ item: PulseDigestItem) -> some View {
        let state = store.digestState(card, item)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "questionmark.circle").font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                Text(item.title).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 0)
                flaggedControls(item, state)
            }
            if !item.subtitle.isEmpty {
                Text(item.subtitle).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder private func flaggedControls(_ item: PulseDigestItem, _ state: String) -> some View {
        switch state {
        case "approved": Label("Approved", systemImage: "checkmark").font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.textSecondary)
        case "skipped": Label("Rejected", systemImage: "hand.thumbsdown").font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.textSecondary)
        case "running": Image(systemName: "clock").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        case "info": Image(systemName: "bubble.left").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        default:
            HStack(spacing: 6) {
                if state == "failed" { Text("failed").font(.system(size: 10, weight: .medium)).foregroundStyle(.orange) }
                pill("Approve", filled: true) { store.decideProposal(card, item, approve: true) }
                pill("Reject", filled: false) { store.decideProposal(card, item, approve: false) }
            }
        }
    }

    private func pill(_ label: String, filled: Bool, _ go: @escaping () -> Void) -> some View {
        Button(action: go) {
            Text(label).font(.system(size: 11, weight: .semibold))
                .foregroundStyle(filled ? Color.white : Theme.textPrimary)
                .padding(.horizontal, 10).padding(.vertical, 4)
                .background(filled ? Theme.accent : Theme.surfaceHover).clipShape(Capsule())
        }
        .buttonStyle(.plain).pointerCursor()
    }

    private func opIcon(_ t: String) -> String {
        switch t {
        case "merge": "arrow.triangle.merge"
        case "forget": "trash"
        case "gc": "trash"
        case "promote": "arrow.up.circle"
        case "codify": "checkmark.seal"
        case "archive": "archivebox"
        default: "circle"
        }
    }

    private func opTitle(_ op: GroomOp) -> String {
        switch op.type {
        case "merge": return op.store == "knowledge" ? "Merged \(op.before.count) records" : "Merged \(op.before.count) → 1"
        case "forget": return "Let go: \(op.before.first?.topic ?? op.before.first?.content ?? "")"
        case "gc": return "GC'd \(op.before.first?.name ?? op.before.first?.id ?? "")"
        case "promote": return op.store == "knowledge" ? "Promoted to core: \(op.after?.type ?? "")" : "Promoted to core"
        case "codify": return "Codified \(op.after?.type ?? "")"
        case "archive": return "Moved to archive"
        default: return op.type
        }
    }
}

/// The multi-item digest body — one row per pick with its own Add/Skip, plus Add all/Skip all.
/// A skip persists server-side so it never resurfaces. Render-safe (plain VStack), for the shot harness.
struct MediaDigestView: View {
    @EnvironmentObject var store: ChatStore
    let card: PulseCard

    private var hasPending: Bool { card.items.contains { store.digestState(card, $0) == "pending" } }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Rectangle().fill(Theme.hairline).frame(height: 1)
            HStack {
                Text("This week").font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                Spacer(minLength: 0)
                if hasPending {
                    bulkButton("Add all", "plus") { store.decideDigestAll(card, approve: true) }
                    bulkButton("Skip all", "xmark") { store.decideDigestAll(card, approve: false) }
                }
            }
            ForEach(card.items) { item in itemRow(item) }
        }
    }

    private func itemRow(_ item: PulseDigestItem) -> some View {
        let state = store.digestState(card, item)
        return HStack(alignment: .center, spacing: 12) {
            poster(item)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    Text(item.title).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                    if item.link != nil {
                        Image(systemName: "arrow.up.right.square").font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                    }
                }
                if !item.subtitle.isEmpty {
                    Text(item.subtitle).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { if let l = item.link { openExternal(l) } }
            .pointerCursor()
            Spacer(minLength: 8)
            controls(item, state)
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder private func poster(_ item: PulseDigestItem) -> some View {
        ZStack {
            if let p = item.poster, !p.isEmpty {
                AuthedAsyncImage(url: p)
            } else {
                Image(systemName: item.mediaType == "tv" ? "tv" : "film")
                    .font(.system(size: 16)).foregroundStyle(Theme.textSecondary)
            }
        }
        .frame(width: 46, height: 69)
        .background(Theme.surfaceHover)
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    @ViewBuilder private func controls(_ item: PulseDigestItem, _ state: String) -> some View {
        switch state {
        case "approved":
            Label("Added", systemImage: "checkmark").font(.system(size: 11, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
        case "skipped":
            Label("Skipped", systemImage: "xmark").font(.system(size: 11, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
        case "running":
            Image(systemName: "clock").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        default:
            HStack(spacing: 6) {
                if state == "failed" {
                    Text("failed").font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
                }
                rowButton("Add", "plus", filled: true) { store.decideDigestItem(card, item, approve: true) }
                rowButton("Skip", "xmark", filled: false) { store.decideDigestItem(card, item, approve: false) }
            }
        }
    }

    private func rowButton(_ label: String, _ icon: String, filled: Bool, _ go: @escaping () -> Void) -> some View {
        Button(action: go) {
            HStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 10, weight: .semibold))
                Text(label).font(.system(size: 11, weight: .semibold))
            }
            .foregroundStyle(filled ? Color.white : Theme.textPrimary)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(filled ? Theme.accent : Theme.surfaceHover).clipShape(Capsule())
        }
        .buttonStyle(.plain).pointerCursor()
    }

    private func bulkButton(_ label: String, _ icon: String, _ go: @escaping () -> Void) -> some View {
        Button(action: go) {
            HStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 10, weight: .semibold))
                Text(label).font(.system(size: 11, weight: .medium))
            }
            .foregroundStyle(Theme.textSecondary)
            .padding(.horizontal, 8).padding(.vertical, 3)
        }
        .buttonStyle(.plain).pointerCursor()
    }
}

/// The available-stack-updates body — one row per component, grouped by source, each
/// actionable row carrying a Confirm-to-apply ("Update") button. Flag-only rows (state "info",
/// updates that can't be applied remotely) render without a button. Render-safe (plain VStack)
/// for the shot harness.
struct UpdatesDigestView: View {
    @EnvironmentObject var store: ChatStore
    let card: PulseCard

    // Rows armed for apply: the first tap arms (docker.update is non-reversible), the second
    // applies. Single-element so arming one row disarms any other.
    @State private var armed: Set<String> = []

    // Group names and their order come entirely from the server's digest payload (items
    // arrive pre-ordered by source) — no infrastructure taxonomy lives in the app.
    private var groups: [(String, [PulseDigestItem])] {
        var order: [String] = []
        var by: [String: [PulseDigestItem]] = [:]
        for item in card.items {
            let g = item.group ?? ""
            if by[g] == nil { order.append(g) }
            by[g, default: []].append(item)
        }
        return order.compactMap { g in by[g].map { (g, $0) } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Rectangle().fill(Theme.hairline).frame(height: 1)
            HStack {
                Spacer()
                Button { store.checkUpdatesNow() } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.clockwise").font(.system(size: 10, weight: .semibold))
                        Text("Check now").font(.system(size: 11, weight: .medium))
                    }
                    .foregroundStyle(Theme.textSecondary)
                }
                .buttonStyle(.plain).pointerCursor()
                .help("Re-check the stack for available updates now")
            }
            ForEach(groups, id: \.0) { (group, items) in
                VStack(alignment: .leading, spacing: 6) {
                    Text(group.uppercased()).font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary).tracking(0.5)
                    ForEach(items) { item in itemRow(item) }
                }
            }
        }
        // Re-read the feed when the card opens so an out-of-band apply is reflected.
        .task { await store.refreshPulse() }
    }

    private func itemRow(_ item: PulseDigestItem) -> some View {
        let state = store.digestState(card, item)
        return HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                if !item.subtitle.isEmpty {
                    Text(item.subtitle).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                }
            }
            Spacer(minLength: 8)
            controls(item, state)
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder private func controls(_ item: PulseDigestItem, _ state: String) -> some View {
        switch state {
        case "info":  // flag-only (Unraid OS) — no apply button
            Image(systemName: "exclamationmark.arrow.triangle.2.circlepath")
                .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
        case "approved":
            Label("Updated", systemImage: "checkmark").font(.system(size: 11, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
        case "running":
            Image(systemName: "clock").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        default:
            HStack(spacing: 6) {
                if state == "failed" {
                    Text("failed").font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
                }
                if armed.contains(item.itemID) {
                    Button { armed.remove(item.itemID) } label: {
                        Image(systemName: "xmark").font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                            .padding(.horizontal, 8).padding(.vertical, 4)
                            .background(Theme.surface).clipShape(Capsule())
                    }
                    .buttonStyle(.plain).pointerCursor().help("Cancel")
                    applyButton(item, label: "Confirm", icon: "checkmark", tint: .orange) {
                        armed.remove(item.itemID)
                        store.decideDigestItem(card, item, approve: true)
                    }
                } else {
                    applyButton(item, label: "Update", icon: "arrow.down.circle", tint: Theme.accent) {
                        armed = [item.itemID]   // arm; a second tap confirms (non-reversible)
                    }
                }
            }
        }
    }

    private func applyButton(_ item: PulseDigestItem, label: String, icon: String,
                             tint: Color, _ go: @escaping () -> Void) -> some View {
        Button(action: go) {
            HStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 10, weight: .semibold))
                Text(label).font(.system(size: 11, weight: .semibold))
            }
            .foregroundStyle(Color.white)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(tint).clipShape(Capsule())
        }
        .buttonStyle(.plain).pointerCursor()
    }
}
