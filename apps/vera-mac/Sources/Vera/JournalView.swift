import MarkdownUI
import SwiftUI

/// The Journal surface — Vera's self-authored standing commitments, rendered read-only.
/// She writes, updates, and retires the entries herself; you steer it by talking to her.
struct JournalView: View {
    @EnvironmentObject var store: ChatStore

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Journal").font(.system(size: 22, weight: .bold))
                Text("\(store.journalEntries.count)").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.surface).clipShape(Capsule())
                Spacer()
                Text("what Vera has committed to keep an eye on")
                    .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 8)
            ScrollView {
                if store.journalEntries.isEmpty && store.journalArchive.isEmpty {
                    Text(store.isLive ? "Nothing on her journal right now. Commitments she takes on (from signals, or because you ask) appear here."
                                      : "Not connected. Her journal appears here once Vera is online.")
                        .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity).padding(.top, 48)
                } else {
                    JournalList(entries: store.journalEntries, archive: store.journalArchive)
                        .padding(.horizontal, 28).padding(.vertical, 12)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
        .task { await store.refreshJournal() }   // pull the latest document whenever the view opens
    }
}

/// Render-safe journal list (reused for screenshots).
struct JournalList: View {
    let entries: [JournalEntry]
    let archive: [JournalArchiveMonth]
    var body: some View {
        VStack(spacing: 10) {
            ForEach(entries) { JournalEntryCard(entry: $0) }
            if !archive.isEmpty {
                HStack {
                    Text("Recently resolved").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary)
                    Spacer()
                }
                .padding(.top, 18)
                ForEach(archive) { month in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(month.id).font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                        Markdown(month.text.replacingOccurrences(of: "\n", with: "  \n"))
                            .markdownTextStyle { ForegroundColor(Theme.textSecondary); FontSize(12) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(14)
                    .background(Theme.surface.opacity(0.6))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
        }
        .frame(maxWidth: 820)
        .frame(maxWidth: .infinity)
    }
}

struct JournalEntryCard: View {
    let entry: JournalEntry
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(entry.heading).font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                if entry.requested {
                    Text("you asked").font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Theme.accent)
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(Theme.accent.opacity(0.15)).clipShape(Capsule())
                }
                Spacer()
                if let next = entry.nextCheck {
                    Label(next.formatted(.dateTime.month(.abbreviated).day()),
                          systemImage: "clock.arrow.circlepath")
                        .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                        .help("Her next check on this")
                }
            }
            // Hard-break every line: the journal is a plain document where line structure
            // is meaning (Origin/Why/Next check each on its own line), not paragraph flow.
            Markdown(entry.body.replacingOccurrences(of: "\n", with: "  \n"))
                .markdownTextStyle { ForegroundColor(Theme.textPrimary); FontSize(13) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
