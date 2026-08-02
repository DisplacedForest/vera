import SwiftUI

struct MemoryView: View {
    @EnvironmentObject var store: ChatStore
    @EnvironmentObject var config: ConfigStore
    @State private var search = ""
    @State private var selectedGroup: NativeMemoryGroup?
    @State private var selected: NativeMemoryRecord?
    @State private var changeRequest = ""
    @State private var showClear = false

    private var visible: [NativeMemoryRecord] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return store.memoryRecords.filter {
            $0.status == .approved
                && (selectedGroup == nil || $0.group == selectedGroup)
                && (query.isEmpty || $0.title.lowercased().contains(query)
                    || $0.summary.lowercased().contains(query)
                    || $0.details.joined(separator: " ").lowercased().contains(query))
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                statusCard
                controls
                changeComposer
                if !store.memoryProposals.isEmpty { reviewQueue }
                library
            }
            .frame(maxWidth: 940)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 30)
            .padding(.vertical, 24)
        }
        .background(Theme.bg)
        .sheet(item: $selected) { MemoryDetailView(memory: $0) }
        .confirmationDialog(
            "Clear all approved memory?", isPresented: $showClear,
            titleVisibility: .visible
        ) {
            Button("Clear approved memory", role: .destructive) { store.clearNativeMemory() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes approved memory from later chat requests. Conversation history is not changed.")
        }
        .task { store.reloadNativeMemory() }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 5) {
                Text("Memory").font(.system(size: 26, weight: .bold))
                Text("A local, reviewable library of what Vera may remember about you.")
                    .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
            Text("\(visible.count) approved")
                .font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(Theme.surface, in: Capsule())
        }
    }

    private var statusCard: some View {
        HStack(spacing: 12) {
            Image(systemName: statusIcon)
                .font(.system(size: 18, weight: .semibold)).foregroundStyle(statusColor)
                .frame(width: 34, height: 34).background(statusColor.opacity(0.12), in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(store.memoryServiceState.label).font(.system(size: 13, weight: .semibold))
                Text(statusDetail).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
            SettingsLink { Text("Memory settings") }
        }
        .padding(14).background(Theme.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline))
    }

    private var statusIcon: String {
        switch store.memoryServiceState {
        case .ready: "checkmark.circle.fill"
        case .off: "pause.circle.fill"
        case .indexing: "arrow.triangle.2.circlepath"
        case .pendingReview, .maintenanceNeeded: "tray.full.fill"
        case .setupRequired: "gearshape.fill"
        case .retrievalUnavailable, .failed: "exclamationmark.triangle.fill"
        }
    }

    private var statusColor: Color {
        switch store.memoryServiceState {
        case .ready: .green
        case .pendingReview, .maintenanceNeeded: .orange
        case .retrievalUnavailable, .failed: .red
        default: Theme.accent
        }
    }

    private var statusDetail: String {
        switch store.memoryServiceState {
        case .off: "Nothing is retrieved, generated, maintained, or added to chat while Memory is off."
        case .setupRequired: "Choose compatible embeddings and extraction models. Ordinary chat still works."
        case .ready: "Only approved, relevant, unexpired items can be added to native chat requests."
        case .indexing: "Vera is preparing approved items for semantic recall."
        case .pendingReview: "Nothing in the queue changes approved memory until you accept it."
        case .maintenanceNeeded: "Review capacity, expiry, or consolidation suggestions. Nothing is removed automatically."
        case .retrievalUnavailable(let detail), .failed(let detail): "\(detail) Ordinary chat continues without memory context."
        }
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Build your library").font(.system(size: 16, weight: .semibold))
            HStack(spacing: 12) {
                MemoryActionCard(
                    icon: "text.magnifyingglass",
                    title: config.nativeSettings.memory.searchPastChats ? "Search past chats again" : "Search past chats",
                    detail: "Scans up to 12 completed local turns and creates proposals for review.",
                    action: searchPastChats)
                MemoryActionCard(
                    icon: "sparkles",
                    title: config.nativeSettings.memory.generateFromChats ? "Chat generation is on" : "Generate from chats",
                    detail: "Creates proposals after eligible completed turns. It never writes approved memory directly.",
                    action: { toggle(\.generateFromChats) })
            }
        }
    }

    private var changeComposer: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ask Memory to change").font(.system(size: 16, weight: .semibold))
            Text("Describe what to remember, correct, merge, suppress, expire, or forget. Vera turns the request into a proposal for review.")
                .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            HStack(spacing: 10) {
                TextField("For example, remember that I prefer morning meetings", text: $changeRequest)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { submitChange() }
                Button("Create proposal") { submitChange() }
                    .disabled(changeRequest.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(16).background(Theme.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline))
    }

    private var reviewQueue: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Review").font(.system(size: 16, weight: .semibold))
                Text("\(store.memoryProposals.count)")
                    .font(.system(size: 11, weight: .bold)).padding(.horizontal, 7).padding(.vertical, 2)
                    .background(.orange.opacity(0.16), in: Capsule())
            }
            ForEach(store.memoryProposals) { proposal in
                MemoryProposalRow(proposal: proposal)
            }
        }
    }

    private var library: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Library").font(.system(size: 16, weight: .semibold))
                Spacer()
                TextField("Search memory", text: $search).textFieldStyle(.roundedBorder).frame(width: 220)
                Menu("Group") {
                    Button("All groups") { selectedGroup = nil }
                    ForEach(NativeMemoryGroup.allCases) { group in
                        Button(group.rawValue) { selectedGroup = group }
                    }
                }
                Menu {
                    Button("Clear approved memory", role: .destructive) { showClear = true }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
            ForEach(NativeMemoryGroup.allCases) { group in
                let items = visible.filter { $0.group == group }
                if selectedGroup == nil || selectedGroup == group {
                    MemoryGroupSection(group: group, items: items, selected: $selected)
                }
            }
        }
    }

    private func submitChange() {
        let request = changeRequest
        changeRequest = ""
        store.requestMemoryChange(request)
    }

    private func toggle(_ keyPath: WritableKeyPath<NativeMemorySettings, Bool>) {
        config.updateMemory { $0[keyPath: keyPath].toggle() }
        try? config.save()
        store.updateNativeMemory(
            settings: config.nativeSettings.memory,
            service: config.nativeMemoryService)
    }

    private func searchPastChats() {
        config.updateMemory { $0.searchPastChats = true }
        try? config.save()
        store.updateNativeMemory(
            settings: config.nativeSettings.memory,
            service: config.nativeMemoryService)
        store.searchPastChatsForMemory()
    }

}

private struct MemoryActionCard: View {
    let icon: String
    let title: String
    let detail: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: icon).foregroundStyle(Theme.accent)
                Text(title).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                Text(detail).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, minHeight: 100, alignment: .topLeading)
            .padding(14).background(Theme.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline))
        }.buttonStyle(.plain)
    }
}

private struct MemoryGroupSection: View {
    @EnvironmentObject var store: ChatStore
    let group: NativeMemoryGroup
    let items: [NativeMemoryRecord]
    @Binding var selected: NativeMemoryRecord?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(group.rawValue).font(.system(size: 14, weight: .semibold))
                Text("\(items.count)").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            if items.isEmpty {
                Text("No approved memory in this section.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    .padding(.vertical, 8)
            } else {
                VStack(spacing: 0) {
                    ForEach(items) { memory in
                        HStack(spacing: 12) {
                            Button { selected = memory } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(memory.title).font(.system(size: 13, weight: .semibold))
                                    Text(memory.summary).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                                        .lineLimit(1)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(Rectangle())
                            }.buttonStyle(.plain)
                            Text(memory.updatedAt.formatted(date: .abbreviated, time: .omitted))
                                .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                            Menu {
                                Button("Open details") { selected = memory }
                                Button("Delete", role: .destructive) { store.deleteNativeMemory(memory) }
                            } label: {
                                Image(systemName: "ellipsis").foregroundStyle(Theme.textSecondary)
                            }.menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                        }
                        .padding(.horizontal, 14).padding(.vertical, 11)
                        if memory.id != items.last?.id { Divider().overlay(Theme.hairline) }
                    }
                }
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline))
            }
        }
    }
}

private struct MemoryProposalRow: View {
    @EnvironmentObject var store: ChatStore
    let proposal: NativeMemoryProposal
    @State private var showReview = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: proposalIcon).foregroundStyle(.orange)
                .frame(width: 30, height: 30).background(.orange.opacity(0.12), in: Circle())
            VStack(alignment: .leading, spacing: 4) {
                Text(proposal.title).font(.system(size: 13, weight: .semibold))
                Text(proposal.summary).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                Text(proposal.kind.rawValue.capitalized).font(.system(size: 10, weight: .semibold)).foregroundStyle(.orange)
            }
            Spacer()
            Button("Dismiss") { store.dismissMemoryProposal(proposal) }
            Button("Review") { showReview = true }.buttonStyle(.borderedProminent)
        }
        .padding(14).background(Theme.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline))
        .sheet(isPresented: $showReview) { MemoryProposalReviewView(proposal: proposal) }
    }

    private var proposalIcon: String {
        switch proposal.kind {
        case .create: "plus"
        case .update: "pencil"
        case .merge, .consolidate: "arrow.triangle.merge"
        case .suppress: "eye.slash"
        case .expire: "calendar.badge.clock"
        case .delete, .cleanup: "trash"
        }
    }
}

private struct MemoryProposalReviewView: View {
    @EnvironmentObject var store: ChatStore
    @Environment(\.dismiss) var dismiss
    let proposal: NativeMemoryProposal
    @State private var details: String

    init(proposal: NativeMemoryProposal) {
        self.proposal = proposal
        _details = State(initialValue: proposal.details.joined(separator: "\n"))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(proposal.title).font(.system(size: 18, weight: .semibold))
                Spacer()
                Text(proposal.kind.rawValue.capitalized)
                    .font(.system(size: 10, weight: .semibold)).foregroundStyle(.orange)
            }
            Text(proposal.summary).font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            Text("Proposed details").font(.system(size: 12, weight: .semibold))
            TextEditor(text: $details).font(.system(size: 13)).frame(minHeight: 180)
                .padding(6).background(Theme.surface, in: RoundedRectangle(cornerRadius: 8))
            HStack {
                if let source = proposal.sourceConversationID {
                    Button("Open source conversation") {
                        store.openMemorySource(conversationID: source)
                        dismiss()
                    }
                } else {
                    Text("Requested in Memory")
                        .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                }
                Spacer()
                Button("Dismiss") { store.dismissMemoryProposal(proposal); dismiss() }
                Button("Accept edited proposal") {
                    store.acceptMemoryProposal(
                        proposal,
                        editedDetails: details.split(whereSeparator: \.isNewline).map(String.init))
                    dismiss()
                }.buttonStyle(.borderedProminent)
            }
        }.padding(24).frame(width: 620, height: 430)
    }
}

private struct MemoryDetailView: View {
    @EnvironmentObject var store: ChatStore
    @Environment(\.dismiss) var dismiss
    let memory: NativeMemoryRecord
    @State private var title: String
    @State private var summary: String
    @State private var details: String
    @State private var confirmDelete = false

    init(memory: NativeMemoryRecord) {
        self.memory = memory
        _title = State(initialValue: memory.title)
        _summary = State(initialValue: memory.summary)
        _details = State(initialValue: memory.details.joined(separator: "\n"))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Memory detail").font(.system(size: 18, weight: .semibold))
                Spacer()
                Button("Done") { dismiss() }
            }
            TextField("Name", text: $title).textFieldStyle(.roundedBorder)
            TextField("Summary", text: $summary).textFieldStyle(.roundedBorder)
            Text("Durable details").font(.system(size: 12, weight: .semibold))
            TextEditor(text: $details).font(.system(size: 13)).frame(minHeight: 180)
                .padding(6).background(Theme.surface, in: RoundedRectangle(cornerRadius: 8))
            HStack {
                if let source = memory.sourceConversationID {
                    Button("Open source conversation") {
                        store.openMemorySource(conversationID: source)
                        dismiss()
                    }
                } else {
                    Text("Created in Memory")
                        .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                }
                Spacer()
                Button("Delete", role: .destructive) { confirmDelete = true }
                Button("Save") {
                    store.editNativeMemory(
                        memory, title: title, summary: summary,
                        details: details.split(whereSeparator: \.isNewline).map(String.init))
                    dismiss()
                }.buttonStyle(.borderedProminent)
            }
        }
        .padding(24).frame(width: 620, height: 500)
        .confirmationDialog("Delete this memory?", isPresented: $confirmDelete) {
            Button("Delete", role: .destructive) { store.deleteNativeMemory(memory); dismiss() }
            Button("Cancel", role: .cancel) {}
        }
    }

}

struct NativeMemoryShotSurface: View {
    let variant: String
    private let rows = NativeMemoryRecord.shotLibrary

    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 14) {
                HStack { VeraMark(size: 20); Text("Vera").font(.system(size: 15, weight: .semibold)) }
                Label("Pulse", systemImage: "newspaper")
                Label("Journal", systemImage: "book.closed")
                Label("Memory", systemImage: "tray.full").foregroundStyle(Theme.accent)
                Divider().overlay(Theme.hairline)
                Text("CHATS").font(.system(size: 10, weight: .bold)).foregroundStyle(Theme.textSecondary)
                Text("New chat").font(.system(size: 12))
                Spacer()
            }
            .padding(18).frame(width: 238).frame(maxHeight: .infinity, alignment: .topLeading)
            .background(Theme.sidebar)
            Rectangle().fill(Theme.hairline).frame(width: 1)
            ZStack {
                VStack(alignment: .leading, spacing: 16) {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Memory").font(.system(size: 25, weight: .bold))
                            Text("A local, reviewable library of what Vera may remember about you.")
                                .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        }
                        Spacer()
                        Text("4 approved").font(.system(size: 11, weight: .semibold))
                            .padding(.horizontal, 9).padding(.vertical, 4)
                            .background(Theme.surface, in: Capsule())
                    }
                    stateCard
                    if variant == "memory-change" { changeCard }
                    if ["memory-review", "memory-duplicate", "memory-expiry"].contains(variant) { proposalCard }
                    library
                    Spacer()
                }
                .padding(26)
                .blur(radius: overlayVariant ? 1.5 : 0)
                if overlayVariant { Color.black.opacity(0.34) }
                overlay
            }
            .background(Theme.bg)
        }
        .foregroundStyle(Theme.textPrimary)
    }

    private var stateCard: some View {
        let state: (String, String, String, Color) = switch variant {
        case "memory-off": ("Memory is off", "Nothing is retrieved, generated, maintained, or added to chat.", "pause.circle.fill", Theme.accent)
        case "memory-setup": ("Set up embeddings to use memory in chat", "Ordinary chat still works without memory context.", "gearshape.fill", Theme.accent)
        case "memory-unavailable": ("Memory recall is unavailable", "The embeddings endpoint could not be reached. Ordinary chat continues.", "exclamationmark.triangle.fill", .red)
        default: ("Ready", "Only approved, relevant, unexpired items can be added to native chat requests.", "checkmark.circle.fill", .green)
        }
        return HStack(spacing: 10) {
            Image(systemName: state.2).foregroundStyle(state.3)
            VStack(alignment: .leading, spacing: 2) {
                Text(state.0).font(.system(size: 12, weight: .semibold))
                Text(state.1).font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
            Text("Memory settings").font(.system(size: 11)).foregroundStyle(Theme.accent)
        }
        .padding(12).background(Theme.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.hairline))
    }

    private var changeCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask Memory to change").font(.system(size: 13, weight: .semibold))
            HStack {
                Text("Remember that I prefer morning meetings")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.bg, in: RoundedRectangle(cornerRadius: 6))
                Text("Create proposal").font(.system(size: 11, weight: .semibold)).foregroundStyle(Theme.accent)
            }
            Text("This request becomes a reviewable proposal. It does not write approved memory.")
                .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
        }
        .padding(12).background(Theme.surface, in: RoundedRectangle(cornerRadius: 10))
    }

    private var proposalCard: some View {
        let content: (String, String, String) = switch variant {
        case "memory-duplicate": ("Review similar memory", "A similar response preference already exists.", "Merge")
        case "memory-expiry": ("Conference plan expired", "This episodic item stopped appearing in recall on Aug 1, 2026.", "Delete")
        default: ("Meeting preference", "Prefers morning meetings when possible.", "Accept")
        }
        return HStack {
            Image(systemName: "tray.full.fill").foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text(content.0).font(.system(size: 12, weight: .semibold))
                Text(content.1).font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
            Text("Dismiss").font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
            Text(content.2).font(.system(size: 10, weight: .semibold)).foregroundStyle(Theme.accent)
        }
        .padding(12).background(Theme.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(.orange.opacity(0.35)))
    }

    private var library: some View {
        HStack(alignment: .top, spacing: 12) {
            ForEach(NativeMemoryGroup.allCases) { group in
                VStack(alignment: .leading, spacing: 7) {
                    Text(group.rawValue).font(.system(size: 13, weight: .semibold))
                    ForEach(rows.filter { $0.group == group }) { memory in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(memory.title).font(.system(size: 11, weight: .semibold))
                            Text(memory.summary).font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                                .lineLimit(2)
                            Text(memory.updatedAt.formatted(date: .abbreviated, time: .omitted))
                                .font(.system(size: 9)).foregroundStyle(Theme.textSecondary)
                        }
                        .padding(10).frame(maxWidth: .infinity, minHeight: 82, alignment: .topLeading)
                        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 9))
                        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline))
                    }
                }.frame(maxWidth: .infinity)
            }
        }
    }

    private var overlayVariant: Bool {
        ["memory-detail", "memory-clear"].contains(variant)
    }

    @ViewBuilder private var overlay: some View {
        switch variant {
        case "memory-detail":
            VStack(alignment: .leading, spacing: 14) {
                HStack { Text("Response style").font(.system(size: 18, weight: .semibold)); Spacer(); Text("Done").foregroundStyle(Theme.accent) }
                Text("Prefers direct practical answers").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                Text("Durable details").font(.system(size: 11, weight: .semibold))
                Text("Prefers direct practical answers with clear tradeoffs")
                    .font(.system(size: 12)).padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.bg, in: RoundedRectangle(cornerRadius: 8))
                HStack { Text("Open source conversation").font(.system(size: 10)).foregroundStyle(Theme.accent); Spacer(); Text("Delete").foregroundStyle(.red); Text("Save").foregroundStyle(Theme.accent) }
            }.padding(22).frame(width: 560).background(Theme.surface, in: RoundedRectangle(cornerRadius: 14))
        case "memory-clear":
            VStack(alignment: .leading, spacing: 14) {
                Text("Clear all approved memory?").font(.system(size: 18, weight: .semibold))
                Text("This removes approved memory from later chat requests. Conversation history is not changed.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                HStack { Spacer(); Text("Cancel"); Text("Clear approved memory").foregroundStyle(.red) }
            }.padding(22).frame(width: 500).background(Theme.surface, in: RoundedRectangle(cornerRadius: 14))
        default: EmptyView()
        }
    }
}
