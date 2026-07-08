import SwiftUI

/// The Memory surface — browse/curate what Vera knows. Live view scrolls; the list
/// itself (render-safe VStack) is reused for screenshots.
struct MemoryView: View {
    @EnvironmentObject var store: ChatStore

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Memory").font(.title2.bold())
                Text("\(store.memories.count)").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.surface).clipShape(Capsule())
                Spacer()
                Text("what Vera knows about you").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 28).padding(.top, 12).padding(.bottom, 8)
            ScrollView {
                if store.memories.isEmpty {
                    Text(store.isLive ? "No memories yet. What Vera learns about you appears here."
                                      : "Not connected. Memories appear here once Vera is online.")
                        .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity).padding(.top, 48)
                } else {
                    MemoryList(items: store.memories, onDelete: { store.deleteMemory($0) })
                        .padding(.horizontal, 28).padding(.vertical, 12)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
        .task { await store.refreshMemories() }   // always current when the view opens
    }
}

/// Render-safe memory list.
struct MemoryList: View {
    let items: [MemoryItem]
    var onDelete: ((MemoryItem) -> Void)? = nil
    var body: some View {
        VStack(spacing: 10) {
            ForEach(items) { MemoryRow(item: $0, onDelete: onDelete) }
        }
        .frame(maxWidth: 820)
        .frame(maxWidth: .infinity)
    }
}

struct MemoryRow: View {
    let item: MemoryItem
    var onDelete: ((MemoryItem) -> Void)? = nil
    @State private var confirming = false
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                Text(item.text).font(.system(size: 14)).foregroundStyle(Theme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    ForEach(item.tags, id: \.self) { tag in
                        Text(tag).font(.system(size: 10, weight: .medium)).foregroundStyle(Theme.textSecondary)
                            .padding(.horizontal, 7).padding(.vertical, 2)
                            .background(.quaternary, in: Capsule())
                    }
                    Text(item.bank).font(.system(size: 10, weight: .semibold)).foregroundStyle(Theme.accent)
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(Theme.accent.opacity(0.15)).clipShape(Capsule())
                }
            }
            Spacer(minLength: 8)
            if onDelete != nil {
                Button { confirming = true } label: {
                    Image(systemName: "trash").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                }
                .buttonStyle(.plain)
                .help("Forget this memory")
                .confirmationDialog("Forget this memory?", isPresented: $confirming) {
                    Button("Forget", role: .destructive) { onDelete?(item) }
                    Button("Cancel", role: .cancel) {}
                } message: { Text(item.text) }
            } else {
                Image(systemName: "trash").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
    }
}
