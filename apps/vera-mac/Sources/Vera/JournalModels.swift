import Foundation

/// One standing commitment from the journal (GET /journal), a read-only view the app
/// renders. `body` is the entry's markdown without its heading line (the heading renders
/// as the card title).
struct JournalEntry: Identifiable, Hashable {
    let id: String          // slug
    let heading: String
    let body: String
    let nextCheck: Date?
    let requested: Bool     // origin classed "requested" (the owner asked for this watch)

    static func parse(_ obj: [String: Any]) -> JournalEntry? {
        guard let heading = obj["heading"] as? String,
              let slug = obj["slug"] as? String,
              let text = obj["text"] as? String else { return nil }
        let body = text.split(separator: "\n", omittingEmptySubsequences: false)
            .dropFirst().joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let nc = (obj["next_check"] as? Double).map { Date(timeIntervalSince1970: $0) }
        return JournalEntry(id: slug, heading: heading, body: body, nextCheck: nc,
                            requested: (obj["origin"] as? String) == "requested")
    }
}

/// One month of resolved commitments (cold storage rendered below the active entries).
struct JournalArchiveMonth: Identifiable, Hashable {
    let id: String          // "YYYY-MM"
    let text: String
}

extension JournalEntry {
    /// Screenshot fixtures for `--shot journal`.
    static func mock() -> [JournalEntry] {
        [JournalEntry(id: "strait-shipping-disruption", heading: "Strait shipping disruption",
                      body: """
                      Origin: signals
                      Why: tanker traffic through the strait is near a standstill, which moves fuel, \
                      fertilizer, and food prices for the household.
                      Resolve when: commercial traffic resumes.
                      - 2026-06-10: traffic near standstill; diesel up 4% week over week.
                      - 2026-06-11: ceasefire talks announced, no traffic change yet.
                      """,
                      nextCheck: Date().addingTimeInterval(86_400), requested: false),
         JournalEntry(id: "lumber-prices", heading: "Lumber prices",
                      body: """
                      Origin: you asked (2026-06-11)
                      Watching for a material move before the deck project.
                      - 2026-06-11: baseline noted, futures flat this week.
                      """,
                      nextCheck: Date().addingTimeInterval(2 * 86_400), requested: true)]
    }
}
