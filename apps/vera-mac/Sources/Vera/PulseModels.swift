import Foundation

/// App surfaces. `chat` and `agentic` are the top modality tabs; `pulse`/`memory`/`plugins`/`mcp`
/// are nav rows under the Chat tab.
enum AppSection: String, CaseIterable, Identifiable {
    case chat, pulse, veins, journal, memory, plugins, mcp, agentic
    var id: String { rawValue }
    var title: String {
        switch self {
        case .chat: "Chat"; case .pulse: "Pulse"; case .veins: "Veins"; case .journal: "Journal"
        case .memory: "Memory"; case .plugins: "Plugins"; case .mcp: "MCP"; case .agentic: "Agentic"
        }
    }
    var icon: String {
        switch self {
        case .chat: "message"
        case .pulse: "newspaper"
        case .veins: "rectangle.split.3x1"
        case .journal: "book.closed"
        case .memory: "tray.full"
        case .plugins: "shippingbox"
        case .mcp: "puzzlepiece.extension"
        case .agentic: "slider.horizontal.3"
        }
    }
    /// True when this surface lives under the Chat tab (vs the Agentic tab).
    var underChatTab: Bool { self != .agentic }
}

/// One numbered source backing a Pulse card (for per-paragraph chips + the expandable list).
struct PulseSource: Identifiable, Hashable {
    let n: Int
    let title: String
    let url: String
    var id: Int { n }
}

/// One real (retrieved + re-hosted) photo woven into a Pulse card's body.
struct PulseInlineImage: Identifiable, Hashable {
    let n: Int            // matches the body's [[img:n]] token
    let url: String       // OWUI file content URL
    let caption: String
    let sourceN: Int?     // numbered source it came from (nil/0 = image search)
    var id: Int { n }
}

/// A confirm-able action a Pulse card carries. Confirming runs it server-side via /actions/commit.
struct PulseAction: Hashable {
    let verb: String        // e.g. "ha.service", "knowledge.set"
    let preview: String     // human-readable description of what will happen
    let risk: String        // "none" | "low" | "medium" | "high"
    let reversible: Bool
    let token: String       // content-hash token to commit/dismiss
}

/// One row of a multi-item digest card (e.g. the weekly "worth adding" media list). Each
/// item carries its own staged action and is independently approve/skip-able.
struct PulseDigestItem: Identifiable, Hashable {
    let itemID: String
    let title: String
    let subtitle: String
    let mediaType: String?   // "movie" | "tv" (media digests)
    let tmdbID: Int?
    let token: String?       // staged action token (for reference; decisions go by item id)
    var state: String        // "pending" | "approved" | "skipped" | "info" (flag-only, no action)
    var poster: String? = nil // poster thumbnail URL
    var link: String? = nil   // IMDb (or TMDB) page
    var group: String? = nil  // source group for update rows (Containers/HACS/Network/…)
    var id: String { itemID }
}

/// One polymorphic snapshot inside a grooming change-set. `kind` selects which fields are
/// meaningful: a vera-memory `belief` (topic/content/tier), a knowledge-store `entity`
/// (type/name/attrs), or a codified `type` (schema fields + the entities it now governs).
struct GroomSnapshot: Hashable {
    var kind: String = "belief"   // belief | entity | type
    let id: String
    // belief
    var topic: String = ""
    var content: String = ""
    var tier: String = "archive"
    // entity
    var type: String = ""
    var name: String = ""
    var attrs: [String: String] = [:]
    // type (knowledge promotion)
    var schemaFields: [String] = []   // required fields of the codified schema
    var entityCount: Int = 0
    var migrated: [String] = []       // names of the entities the schema now governs

    /// One concrete summary line for the row (no blanks, no generic placeholders).
    var line: String {
        switch kind {
        case "entity":
            let a = attrs.isEmpty ? "" : " · " + attrs.map { "\($0.key): \($0.value)" }.sorted().joined(separator: ", ")
            let label = name.isEmpty ? id : name
            return "\(label)\(type.isEmpty ? "" : " (\(type))")\(a)"
        case "type":
            let f = schemaFields.isEmpty ? "" : " · " + schemaFields.joined(separator: ", ")
            return "\(type) · \(entityCount) entit\(entityCount == 1 ? "y" : "ies")\(f)"
        default:
            return topic.isEmpty ? content : "\(topic): \(content)"
        }
    }

    /// Expanded before/after detail (revealed by the row's disclosure).
    var detail: [String] {
        switch kind {
        case "entity":
            return attrs.isEmpty ? ["(no attributes)"] : attrs.map { "\($0.key): \($0.value)" }.sorted()
        case "type":
            return (schemaFields.isEmpty ? [] : ["fields: " + schemaFields.joined(separator: ", ")])
                 + migrated.map { "• \($0)" }
        default:
            return topic.isEmpty ? [content] : ["\(topic): \(content)"]
        }
    }
}

/// One reversible op Vera applied while grooming. `store` routes Restore/Reject to
/// the right backend (vera-memory vs the knowledge store).
struct GroomOp: Identifiable, Hashable {
    let index: Int
    let type: String          // merge | forget | promote | archive | gc | codify
    let store: String         // memory | knowledge
    let reason: String
    let before: [GroomSnapshot] // the pre-change snapshot(s) — what restore brings back
    let after: GroomSnapshot?   // the result (nil for forget/gc)
    var id: Int { index }
    var reversible: Bool { !before.isEmpty }   // undo is offered wherever a before-snapshot exists
}

/// System-vein sub-groups, fixed render order. `.vera` is the fallback for legacy/untagged cards.
enum PulseCategory: String, CaseIterable, Identifiable {
    case vera, infra, health, update
    var id: String { rawValue }
    var title: String {
        switch self { case .vera: "Vera"; case .infra: "Infra"; case .health: "Health"; case .update: "Updates" }
    }
    var icon: String {
        switch self {
        case .vera: "brain"
        case .infra: "server.rack"
        case .health: "heart.text.square"
        case .update: "arrow.triangle.2.circlepath"
        }
    }
    static func of(_ card: PulseCard) -> PulseCategory { PulseCategory(rawValue: card.category ?? "") ?? .vera }
}

/// A pinned ambient vein from the backend registry (`GET /pulse/veins`). Rendered as a
/// slim chip above the research feed; quiet ("nominal") until a card of its `kind` arrives, then it
/// lights up with a count + severity-colored dot.
struct PulseVein: Identifiable, Hashable {
    let kind: String
    let label: String
    let icon: String          // SF Symbol name
    let order: Int
    let nominalLabel: String  // chip text when the vein has no active card today
    var unread: Int = 0           // this person's unread count (drives the dot + count)
    var maxSeverity: String? = nil // max severity among UNREAD cards (drives the dot color)
    var id: String { kind }

    static func mock() -> [PulseVein] {
        [
            PulseVein(kind: "status", label: "System", icon: "gearshape", order: 0, nominalLabel: "nominal"),
            PulseVein(kind: "weather", label: "Weather", icon: "cloud.sun", order: 1, nominalLabel: "clear"),
            PulseVein(kind: "signals", label: "Signals", icon: "antenna.radiowaves.left.and.right", order: 2, nominalLabel: "quiet"),
        ]
    }
}

/// A Pulse briefing card (derived from a chat in the OWUI Pulse folder).
struct PulseCard: Identifiable, Hashable {
    let id: String
    var title: String      // "Pulse · " prefix stripped
    var preview: String
    var subtitle: String
    var imageURL: String? = nil   // OWUI file content URL for the generated cover art
    var tint: String? = nil       // "#rrggbb" panel tint from the image's dominant color
    var sources: [String] = []    // source URLs (for favicon row) — derived from sourceList
    var sourceList: [PulseSource] = []        // numbered sources (chips + expandable list)
    var inlineImages: [PulseInlineImage] = []  // real photos placed via [[img:n]] tokens
    var body: String = ""         // full markdown body (markers stripped) for the expanded view
    var status: String? = nil     // vera-api lifecycle: new|seen|bookmarked|promoted
    var kind: String = "research" // research feed vs an ambient vein (status/weather/signals/…)
    var severity: String? = nil   // ambient severity — notice|alert|critical (nil = neutral)
    var action: PulseAction? = nil // a confirm-able action this card proposes
    var provenance: String = "scheduled" // "scheduled" (morning run) | "heartbeat" (noticed for you)
    var read: Bool = false        // has this person opened this card's detail?
    var category: String? = nil   // System sub-group — vera|infra|health|update
    var changeSet: [GroomOp] = [] // reversible memory-tending diff (audit + restore)
    var items: [PulseDigestItem] = [] // multi-item digest rows (per-row approve/skip)

    /// Surfaced by the heartbeat noticing something for this person (vs the scheduled run).
    var noticedForYou: Bool { provenance == "heartbeat" }

    static func mock() -> [PulseCard] {
        // The feed is research-grade only. Weather/Signal watch live in chips now, not here.
        [
            .init(id: "p1", title: "Ashvale Rovers",
                  preview: "Rovers secured top-flight safety with a 3-1 win — a striker brace and a late third sealed it. Six points clear of the drop now.",
                  subtitle: "Pulse", tint: "#3a5a40"),
            .init(id: "p3", title: "Local LLM + AMD GPU",
                  preview: "MLX hit production maturity — FLUX image gen ~3.8x faster on M5 vs M4, and ROCm 6.x now treats PyTorch as a first-class backend.",
                  subtitle: "Pulse", tint: "#5b4636"),
            .init(id: "p5", title: "Winemaking",
                  preview: "Sur lie aging with weekly bâtonnage builds mid-palate weight without oak — worth trying on the next white batch.",
                  subtitle: "Pulse", tint: "#5a4a6b", provenance: "heartbeat"),
            .init(id: "p6", title: "Around town",
                  preview: "Two things worth a look this weekend downtown — a maker's market Saturday and live music at the amphitheater Sunday evening.",
                  subtitle: "Pulse", tint: "#4a5a6b", provenance: "heartbeat"),
        ]
    }

    /// Mock System-vein status cards for the lit-state + grouped-detail shots —
    /// text-forward, no cover art, spread across categories (Vera / Infra / Health / Updates).
    static func statusMock() -> [PulseCard] {
        [
            groomMock(),
            PulseCard(id: "st-infra", title: "vera-api restarted",
                      preview: "Container came back after a brief blip; all routers healthy.",
                      subtitle: "System", kind: "status", severity: "notice", category: "infra"),
            PulseCard(id: "st-health", title: "SearXNG degraded",
                      preview: "Search backend was slow to respond on the last two checks.",
                      subtitle: "System", kind: "status", severity: "alert", category: "health"),
            updateDigestMock(),
        ]
    }

    /// The available-stack-updates card — per-row Confirm-to-apply, grouped by source.
    /// Container + HA-domain rows are actionable; the Unraid OS row is flag-only ("info").
    static func updateDigestMock() -> PulseCard {
        func row(_ id: String, _ t: String, _ s: String, _ group: String, _ state: String = "pending") -> PulseDigestItem {
            PulseDigestItem(itemID: id, title: t, subtitle: s, mediaType: nil, tmdbID: nil,
                            token: state == "info" ? nil : id, state: state, group: group)
        }
        return PulseCard(
            id: "st-update", title: "4 updates available",
            preview: "Containers, HACS, and network gear have updates ready to apply.",
            subtitle: "System", body: "", kind: "status", severity: "notice", category: "update",
            items: [
                row("u1", "searxng", "new image available", "Containers"),
                row("u2", "unraid", "v2026.6.0 → v2026.6.1", "Unraid OS", "info"),
                row("u3", "bubble card", "v3.2.2 → v3.2.3", "HACS"),
                row("u4", "udmpro", "5.1.15 → 5.2.0", "Network"),
            ])
    }

    /// The unified nightly grooming digest — one card spanning both stores (World-model +
    /// Knowledge store) with a Flagged-for-review section, each applied op Restore/Reject-able.
    static func groomMock() -> PulseCard {
        func belief(_ id: String, _ topic: String, _ content: String, _ tier: String = "archive") -> GroomSnapshot {
            var s = GroomSnapshot(kind: "belief", id: id); s.topic = topic; s.content = content; s.tier = tier; return s
        }
        func entity(_ id: String, _ type: String, _ name: String, _ attrs: [String: String]) -> GroomSnapshot {
            var s = GroomSnapshot(kind: "entity", id: id); s.type = type; s.name = name; s.attrs = attrs; return s
        }
        func typeSnap(_ type: String, _ fields: [String], _ migrated: [String]) -> GroomSnapshot {
            var s = GroomSnapshot(kind: "type", id: "type:\(type)"); s.type = type
            s.schemaFields = fields; s.entityCount = migrated.count; s.migrated = migrated; return s
        }
        return PulseCard(
            id: "st-groom", title: "Last night I tended my knowledge · merged 1; promoted 1; GC'd 1",
            preview: "Overnight I tended my world-model and the home knowledge store.",
            subtitle: "System", body: "Everything I changed is reversible — restore or reject anything below.",
            kind: "status", severity: nil, category: "vera",
            changeSet: [
                GroomOp(index: 0, type: "merge", store: "memory", reason: "two notes about the same thing",
                        before: [belief("a1", "Network", "The home runs UniFi networking gear."),
                                 belief("a2", "Network", "UniFi gear is used across the house.")],
                        after: belief("m1", "Network", "The home runs UniFi networking gear throughout.")),
                GroomOp(index: 1, type: "promote", store: "memory", reason: "promoted into core",
                        before: [belief("p1", "Home", "Jordan lives in Springfield.")],
                        after: belief("p1", "Home", "Jordan lives in Springfield.", "core")),
                GroomOp(index: 2, type: "promote", store: "knowledge", reason: "codified a stabilized type's schema",
                        before: [typeSnap("appliance", [], [])],
                        after: typeSnap("appliance", ["brand", "model", "room"],
                                        ["Dishwasher", "Fridge", "Washer", "Dryer", "Oven", "Microwave"])),
                GroomOp(index: 3, type: "gc", store: "knowledge", reason: "removed orphan (no attributes left)",
                        before: [entity("sensor:hallway-old", "sensor", "hallway-old", [:])], after: nil),
            ],
            items: [
                PulseDigestItem(itemID: "review:promote:service", title: "Codify 'service' type?",
                                subtitle: "4 entities, 0.83 coverage — stabilizing but not a confident auto-promote.",
                                mediaType: nil, tmdbID: nil, token: "tok", state: "pending",
                                group: "Flagged for review"),
            ])
    }

    /// The weekly media-curation digest — a multi-item approve/skip card for the Media vein.
    static func mediaDigestMock() -> PulseCard {
        func item(_ id: String, _ t: String, _ s: String, _ mt: String, _ tmdb: Int, _ state: String = "pending") -> PulseDigestItem {
            PulseDigestItem(itemID: id, title: t, subtitle: s, mediaType: mt, tmdbID: tmdb, token: id, state: state,
                            poster: "https://image.tmdb.org/t/p/w185/\(id).jpg",
                            link: "https://www.imdb.com/title/tt\(tmdb)/")
        }
        return PulseCard(
            id: "media-digest", title: "Worth adding this week",
            preview: "8 picks for the library — add the ones you want.",
            subtitle: "Media", body: "This week I'd add these to the library. Tap add to grab each, or skip to pass.",
            kind: "media",
            items: [
                item("m1", "The Traitors", "2023 · TV · Cultural-moment reality competition", "tv", 135157),
                item("m2", "Dune: Prophecy", "2024 · TV · Acclaimed prestige sci-fi", "tv", 90228),
                item("m3", "Yellowstone", "2018 · TV · Flagship modern Western drama", "tv", 73586, "approved"),
                item("m4", "Alien", "1979 · Movie · Essential canon, missing from the shelf", "movie", 348),
                item("m5", "The Bear", "2022 · TV · Critical darling, high relevance", "tv", 136315, "skipped"),
            ])
    }

    /// A deep-research card for the detail-view shot: first-person body, citation refs, an inline image.
    static func deepMock() -> PulseCard {
        let body = """
        I'm surfacing this because Ashvale's entire summer hinges on one number, and it happens to be a club record. [1]

        ```vera:stats
        {"cards":[{"value":"£70m","label":"reported fee","sub":"club record"},{"value":"34","label":"league apps","sub":"this season"},{"value":"7","label":"goal contributions"}]}
        ```

        Joe Carter is being lined up as potentially the most expensive sale in the club's history [1], with reported interest pushing toward the £70m mark after a breakout campaign in midfield. [2]

        [[img:1]]

        That fee reshapes the rebuild. With the window closing on September 1, the manager is said to favour reinvesting in one deep-lying playmaker rather than spreading the budget across three squad pieces. [2,3]

        ```vera:chart
        {"type":"bar","title":"Midfield minutes, 2025-26","yLabel":"mins","series":[{"name":"mins","points":[{"x":"Carter","y":2890},{"x":"Okafor","y":2100},{"x":"Reyes","y":2750}]}]}
        ```

        So what: if the Carter sale completes, the tell will be the reinvestment — watch for a single marquee midfielder, not a scattering of depth signings. [3]
        """
        return PulseCard(
            id: "deep", title: "Ashvale's record-breaking summer", preview: "Carter sale could fund the rebuild.",
            subtitle: "Pulse", imageURL: "", tint: "#472f22",
            sourceList: [
                PulseSource(n: 1, title: "Ashvale eye club-record fee for Carter", url: "https://sport.example.com/ashvale-record-fee"),
                PulseSource(n: 2, title: "Inside Ashvale Rovers' transfer plan", url: "https://news.example.com/ashvale-transfer-plan"),
                PulseSource(n: 3, title: "The manager's midfield priority for the window", url: "https://local.example.com/midfield-priority"),
            ],
            inlineImages: [
                PulseInlineImage(n: 1, url: "", caption: "Joe Carter in midfield this season", sourceN: 2),
            ],
            body: body)
    }
}
