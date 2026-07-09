import SwiftUI

/// Shared width of the Pulse content column. Header, the ambient-vein chip row, and the card feed
/// all align to this same centered column so nothing floats off to the page edge.
let pulseFeedWidth: CGFloat = 620

/// Heartbeat-found cards carry a quiet "Noticed this for you" byline (same depth as the
/// scheduled feed — just a whisper of origin). One flip to disable.
let showProvenanceByline = true

/// The Pulse surface — a single-column feed of ChatGPT-style briefing cards: generated cover art
/// on top, a tinted panel (color from the image's dominant hue) with title/preview/actions.
/// Tapping a card opens it in the chat view. The grid is reused (render-safe) for screenshots.
struct PulseView: View {
    @EnvironmentObject var store: ChatStore
    // Detail/vein selection lives in the store so the sidebar Pulse button can dismiss it.
    private var detail: PulseCard? { store.pulseDetail }
    private var veinDetail: PulseVein? { store.pulseVeinDetail }
    // Veins configure this feed, so the manager opens from here as a sheet, not the sidebar.
    @State private var showVeins = false

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    Text("Today's pulse").font(.title2.bold())
                    Text("\(store.feedCards.count)").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Theme.surface).clipShape(Capsule())
                    Spacer()
                    Button { showVeins = true } label: {
                        Image(systemName: "rectangle.split.3x1").font(.system(size: 14))
                            .foregroundStyle(Theme.textSecondary)
                            .frame(width: 28, height: 28)
                            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain).help("Veins (ambient watches pinned above the feed)")
                }
                .frame(maxWidth: pulseFeedWidth, alignment: .leading).frame(maxWidth: .infinity)
                .padding(.horizontal, 28).padding(.top, 12).padding(.bottom, 8)

                // Pinned ambient veins — quiet chips above the feed; tap a lit one for its cards.
                if !store.pulseVeins.isEmpty {
                    VeinChipRow(veins: store.pulseVeins, cards: store.pulseCards, onTap: { store.pulseVeinDetail = $0 })
                        .frame(maxWidth: pulseFeedWidth, alignment: .leading).frame(maxWidth: .infinity)
                        .padding(.horizontal, 28).padding(.bottom, 8)
                } else if store.isLive {
                    // No veins enabled — a quiet affordance instead of dead space.
                    Button { showVeins = true } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "plus.circle").font(.system(size: 11))
                            Text("Add veins to pin ambient watches above the feed")
                                .font(.system(size: 12))
                        }
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 12).padding(.vertical, 6)
                        .background(Theme.surface).clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: pulseFeedWidth, alignment: .leading).frame(maxWidth: .infinity)
                    .padding(.horizontal, 28).padding(.bottom, 8)
                }

                ScrollView {
                    if store.feedCards.isEmpty {
                        Text(store.isLive ? "No briefings yet. Pulse runs each morning."
                                          : "Not connected. Pulse briefings appear here.")
                            .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                            .frame(maxWidth: .infinity).padding(.top, 48)
                    } else {
                        PulseGrid(cards: store.feedCards, token: store.apiToken, onTap: { store.pulseDetail = $0 })
                            .padding(.horizontal, 28).padding(.vertical, 12)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.bg)

            if let c = detail {
                PulseDetailView(card: c, token: store.apiToken,
                                onClose: { store.pulseDetail = nil },
                                onContinue: { store.pulseDetail = nil; store.openPulseInChat(c) })
                    .transition(.opacity)
                    .zIndex(1)
            }
            if let vein = veinDetail {
                PulseVeinView(vein: vein, cards: store.veinCards(vein.kind),
                              onClose: { store.pulseVeinDetail = nil })
                    .transition(.opacity)
                    .zIndex(2)
            }
        }
        .animation(.easeInOut(duration: 0.18), value: detail)
        .animation(.easeInOut(duration: 0.18), value: veinDetail)
        .task { await store.refreshPulse() }   // pull the latest feed + veins whenever Pulse opens
        .sheet(isPresented: $showVeins) { VeinsSheet() }
    }
}

/// The veins manager presented over Pulse — the existing `VeinsView` with a Done bar and a frame
/// sized for its card grid. Veins configure the Pulse feed, so this is where they are managed.
struct VeinsSheet: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 4)
            VeinsView()
        }
        .frame(width: 900, height: 680)
        .background(Theme.bg)
    }
}

/// Single-column feed of cards (render-safe so ImageRenderer captures it for screenshots).
struct PulseGrid: View {
    let cards: [PulseCard]
    var token: String? = nil
    var onTap: ((PulseCard) -> Void)? = nil

    var body: some View {
        VStack(spacing: 18) {
            ForEach(cards) { PulseCardTile(card: $0, token: token, onTap: onTap) }
        }
        .frame(maxWidth: pulseFeedWidth)
        .frame(maxWidth: .infinity)
    }
}

struct PulseCardTile: View {
    @EnvironmentObject var store: ChatStore
    let card: PulseCard
    var token: String? = nil
    var onTap: ((PulseCard) -> Void)? = nil

    private var panel: Color { Color(hex: card.tint) ?? Theme.surface }

    var body: some View { tile }

    private var tile: some View {
        VStack(spacing: 0) {
            if let img = card.imageURL, !img.isEmpty {
                AuthedAsyncImage(url: img, token: token)
                    .frame(height: 200).frame(maxWidth: .infinity).clipped()
            }
            VStack(alignment: .leading, spacing: 8) {
                if showProvenanceByline, card.noticedForYou {
                    Label("Noticed this for you", systemImage: "sparkle")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.6))
                }
                Text(card.title).font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white).lineLimit(2)
                Text(card.preview).font(.system(size: 13))
                    .foregroundStyle(.white.opacity(0.78))
                    .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 18) {
                    cardAction("hand.thumbsup", on: store.pulseRatings[card.id] == "up") { store.ratePulse(card, "up") }
                    cardAction("hand.thumbsdown", on: store.pulseRatings[card.id] == "down") { store.ratePulse(card, "down") }
                    cardAction("bookmark", on: store.bookmarkedPulseIDs.contains(card.id)) { store.bookmarkPulse(card) }
                }
                .padding(.top, 2)
                if let action = card.action { actionAffordance(action) }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(ZStack { panel; Color.black.opacity(0.32) })
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
        .onTapGesture { onTap?(card) }
    }

    /// A card action glyph. Uses a high-priority tap so it beats the card's open-tap.
    private func cardAction(_ icon: String, on: Bool, _ go: @escaping () -> Void) -> some View {
        Image(systemName: on ? icon + ".fill" : icon)
            .font(.system(size: 13))
            .foregroundStyle(on ? Theme.accent : .white.opacity(0.7))
            .frame(width: 22, height: 18)
            .contentShape(Rectangle())
            .highPriorityGesture(TapGesture().onEnded(go))
            .pointerCursor()
    }

    /// The confirm-able action affordance — preview + Confirm/Dismiss, or the outcome once acted.
    @ViewBuilder private func actionAffordance(_ action: PulseAction) -> some View {
        let state = store.actionState[card.id]
        VStack(alignment: .leading, spacing: 8) {
            Rectangle().fill(Color.white.opacity(0.15)).frame(height: 1).padding(.top, 4)
            Text(action.preview).font(.system(size: 12))
                .foregroundStyle(.white.opacity(0.85))
                .fixedSize(horizontal: false, vertical: true)
            if let state {
                HStack(spacing: 6) {
                    Image(systemName: actionStateIcon(state))
                    Text(actionStateLabel(state))
                }
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.white.opacity(0.82))
            } else {
                HStack(spacing: 10) {
                    actionButton("checkmark", "Confirm", filled: true) { store.confirmAction(card) }
                    actionButton("xmark", "Dismiss", filled: false) { store.dismissAction(card) }
                    Spacer(minLength: 0)
                    if !action.reversible {
                        Text("not reversible").font(.system(size: 10, weight: .medium))
                            .foregroundStyle(.orange.opacity(0.9))
                    }
                }
            }
        }
        .padding(.top, 2)
    }

    private func actionStateIcon(_ s: String) -> String {
        if s == "done" { return "checkmark.circle.fill" }
        if s == "dismissed" { return "xmark.circle" }
        if s == "running" { return "clock" }
        return "exclamationmark.triangle"
    }

    private func actionStateLabel(_ s: String) -> String {
        if s == "done" { return "Done" }
        if s == "dismissed" { return "Dismissed" }
        if s == "running" { return "Running…" }
        return s   // "failed: …"
    }

    private func actionButton(_ icon: String, _ label: String, filled: Bool, _ go: @escaping () -> Void) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 11, weight: .bold))
            Text(label).font(.system(size: 12, weight: .semibold))
        }
        .foregroundStyle(filled ? Color.black.opacity(0.85) : .white.opacity(0.9))
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(filled ? Color.white.opacity(0.92) : Color.white.opacity(0.12))
        .clipShape(Capsule())
        .contentShape(Capsule())
        .highPriorityGesture(TapGesture().onEnded(go))
        .pointerCursor()
    }
}

// MARK: - Ambient veins (pinned chips + vein overlay)

/// Map an ambient-card severity to a rank/color. `notice`/nil read as neutral (accent), `alert`
/// amber, `critical` red — the chip dot and status-tile marker use these.
func pulseSeverityRank(_ s: String?) -> Int {
    switch s { case "critical": return 3; case "alert": return 2; case "notice": return 1; default: return 0 }
}

func pulseSeverityColor(_ s: String?) -> Color {
    switch s {
    case "critical": return .red
    case "alert": return Theme.accentGlow
    default: return Theme.accent
    }
}

/// The highest severity among a vein's cards (drives the chip dot color), or nil if none.
func pulseMaxSeverity(_ cards: [PulseCard]) -> String? {
    cards.max(by: { pulseSeverityRank($0.severity) < pulseSeverityRank($1.severity) })?.severity
}

/// The pinned ambient-vein chips above the research feed. A vein is quiet ("nominal") until it has
/// active cards, then it shows a count + severity-colored dot and becomes tappable. Render-safe (no
/// ScrollView) so the screenshot harness captures it.
struct VeinChipRow: View {
    let veins: [PulseVein]
    let cards: [PulseCard]
    var onTap: ((PulseVein) -> Void)? = nil

    var body: some View {
        HStack(spacing: 10) {
            ForEach(veins) { vein in
                VeinChip(vein: vein, cards: cards.filter { $0.kind == vein.kind }, onTap: onTap)
            }
            Spacer(minLength: 0)
        }
    }
}

struct VeinChip: View {
    let vein: PulseVein
    let cards: [PulseCard]
    var onTap: ((PulseVein) -> Void)? = nil

    // "Lit" = has UNREAD events (dot + count). Always tappable — the vein view reports
    // "Nothing to report" honestly when empty. Dot color comes from the max UNREAD severity.
    private var unread: Bool { vein.unread > 0 }
    private var dotColor: Color { pulseSeverityColor(vein.maxSeverity) }

    private func clipped(_ text: String, max: Int = 24) -> String {
        text.count > max ? String(text.prefix(max - 1)).trimmingCharacters(in: .whitespaces) + "…" : text
    }

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: vein.icon).font(.system(size: 13, weight: .medium))
                .foregroundStyle(unread ? Theme.textPrimary : Theme.textSecondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(clipped(vein.label)).font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(unread ? Theme.textPrimary : Theme.textSecondary)
                    .lineLimit(1)
                Text(clipped(unread ? "\(vein.unread) unread" : vein.nominalLabel))
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1)
            }
            if unread { Circle().fill(dotColor).frame(width: 8, height: 8) }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12)
            .stroke(unread ? dotColor.opacity(0.5) : Theme.hairline, lineWidth: 1))
        .contentShape(RoundedRectangle(cornerRadius: 12))
        .onTapGesture { onTap?(vein) }
        .pointerCursor()
    }
}

/// A text-forward status card tile (icon + title + summary, no cover art) for the vein overlay.
/// Carries the Confirm/Dismiss action affordance when the card proposes one; tap opens the full detail.
struct StatusCardTile: View {
    @EnvironmentObject var store: ChatStore
    let card: PulseCard
    var onTap: ((PulseCard) -> Void)? = nil

    // A read event reads quieter — hollowed dot, secondary title — until a new one arrives.
    private var isRead: Bool { store.readPulseIDs.contains(card.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Circle().fill(pulseSeverityColor(card.severity).opacity(isRead ? 0.3 : 1))
                    .frame(width: 8, height: 8).padding(.top, 6)
                VStack(alignment: .leading, spacing: 4) {
                    Text(card.title).font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(isRead ? Theme.textSecondary : Theme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(card.preview).font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            if let action = card.action { StatusActionAffordance(card: card, action: action) }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .onTapGesture { onTap?(card) }
        .pointerCursor()
    }
}

/// Confirm/Dismiss affordance styled for a status tile (theme colors on a surface bg, vs
/// PulseCardTile's white-on-tinted-panel variant). Same store-backed behavior.
struct StatusActionAffordance: View {
    @EnvironmentObject var store: ChatStore
    let card: PulseCard
    let action: PulseAction

    var body: some View {
        let state = store.actionState[card.id]
        VStack(alignment: .leading, spacing: 8) {
            Rectangle().fill(Theme.hairline).frame(height: 1).padding(.top, 2)
            Text(action.preview).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            if let state {
                HStack(spacing: 6) {
                    Image(systemName: stateIcon(state)); Text(stateLabel(state))
                }
                .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
            } else {
                HStack(spacing: 10) {
                    btn("checkmark", "Confirm", filled: true) { store.confirmAction(card) }
                    btn("xmark", "Dismiss", filled: false) { store.dismissAction(card) }
                    Spacer(minLength: 0)
                    if !action.reversible {
                        Text("not reversible").font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
                    }
                }
            }
        }
    }

    private func stateIcon(_ s: String) -> String {
        if s == "done" { return "checkmark.circle.fill" }
        if s == "dismissed" { return "xmark.circle" }
        if s == "running" { return "clock" }
        return "exclamationmark.triangle"
    }

    private func stateLabel(_ s: String) -> String {
        if s == "done" { return "Done" }
        if s == "dismissed" { return "Dismissed" }
        if s == "running" { return "Running…" }
        return s
    }

    private func btn(_ icon: String, _ label: String, filled: Bool, _ go: @escaping () -> Void) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 11, weight: .bold))
            Text(label).font(.system(size: 12, weight: .semibold))
        }
        .foregroundStyle(filled ? Color.white : Theme.textPrimary)
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(filled ? AnyShapeStyle(Theme.accent) : AnyShapeStyle(.quaternary), in: Capsule())
        .contentShape(Capsule())
        .highPriorityGesture(TapGesture().onEnded(go))
        .pointerCursor()
    }
}

/// Full-surface overlay listing one ambient vein's status cards (text-forward), reusing the
/// detail-overlay chrome. Tapping a card opens its PulseDetailView.
struct PulseVeinView: View {
    @EnvironmentObject var store: ChatStore
    let vein: PulseVein
    let cards: [PulseCard]
    var onClose: () -> Void = {}
    @State private var detail: PulseCard?

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Theme.bg.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 10) {
                        Image(systemName: vein.icon).font(.system(size: 18, weight: .semibold))
                        Text(vein.label).font(.system(size: 22, weight: .bold))
                    }
                    .foregroundStyle(Theme.textPrimary).padding(.bottom, 2)
                    if cards.isEmpty {
                        Text("Nothing to report.").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                    } else if vein.kind == "status" {
                        // The System vein groups by category (Vera / Infra / Health / Updates).
                        ForEach(store.veinCardsByCategory(vein.kind), id: \.0) { (cat, group) in
                            HStack(spacing: 8) {
                                Image(systemName: cat.icon).font(.system(size: 13, weight: .semibold))
                                Text(cat.title).font(.system(size: 14, weight: .semibold))
                            }
                            .foregroundStyle(Theme.textSecondary).padding(.top, 6)
                            ForEach(group) { StatusCardTile(card: $0, onTap: { detail = $0 }) }
                        }
                    } else {
                        ForEach(cards) { StatusCardTile(card: $0, onTap: { detail = $0 }) }
                    }
                }
                .padding(.horizontal, 24).padding(.bottom, 24).padding(.top, 16)
                .frame(maxWidth: 720, alignment: .leading).frame(maxWidth: .infinity)
            }
            Button(action: onClose) {
                Image(systemName: "xmark").font(.system(size: 13, weight: .semibold)).foregroundStyle(.white)
                    .frame(width: 30, height: 30).background(Color.black.opacity(0.5)).clipShape(Circle())
            }
            .buttonStyle(.plain).padding(.top, 16).padding(.horizontal, 16).padding(.bottom, 16)

            if let c = detail {
                PulseDetailView(card: c, token: store.apiToken,
                                onClose: { detail = nil },
                                onContinue: { detail = nil; store.openPulseInChat(c) })
                    .transition(.opacity).zIndex(1)
            }
        }
        .animation(.easeInOut(duration: 0.18), value: detail)
    }
}
