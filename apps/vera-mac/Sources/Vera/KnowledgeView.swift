import SwiftUI

struct KnowledgeView: View {
    @EnvironmentObject var config: ConfigStore
    @StateObject private var knowledge = KnowledgeStore()
    @State private var showCreate = false
    @State private var selected: KnowledgeCollection?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                if let e = knowledge.error { errorRow(e) }
                content
            }
            .frame(maxWidth: 940)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 30)
            .padding(.vertical, 24)
        }
        .background(Theme.bg)
        .sheet(isPresented: $showCreate) { KnowledgeCreateSheet(knowledge: knowledge) }
        .sheet(item: $selected) { KnowledgeCollectionDetail(knowledge: knowledge, collectionID: $0.id) }
        .task {
            knowledge.configure(base: config.veraAPIBase)
            await knowledge.refresh()
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 5) {
                Text("Knowledge").font(.system(size: 26, weight: .bold))
                Text("Reference collections Vera can ground chat answers in, with citations.")
                    .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            Spacer()
            if case .ready = knowledge.phase {
                Text("\(knowledge.collections.count)")
                    .font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Theme.surface, in: Capsule())
                Button {
                    showCreate = true
                } label: {
                    Label("New collection", systemImage: "plus")
                        .font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(.plain).foregroundStyle(Theme.accent)
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch knowledge.phase {
        case .loading:
            ProgressView().frame(maxWidth: .infinity, alignment: .center).padding(.vertical, 40)
        case .unconfigured:
            statusCard(icon: "books.vertical",
                       title: "Knowledge lights up with vera-api",
                       note: "Connect a vera-api URL in Settings and your document collections appear here, ready to ground chat answers.")
        case .unreachable:
            statusCard(icon: "exclamationmark.triangle",
                       title: "Couldn't reach vera-api",
                       note: "Couldn't load collections from \(knowledge.baseDescription).", retry: true)
        case .unsupported:
            statusCard(icon: "shippingbox",
                       title: "This vera-api doesn't serve documents yet",
                       note: "Update vera-api to a build with the documents capability, then retry.", retry: true)
        case .ready:
            if !knowledge.embeddingsConfigured {
                statusCard(icon: "clock",
                           title: "Indexing is paused",
                           note: "Collections and files are manageable, but indexing and retrieval wait for an embeddings endpoint on vera-api.")
            }
            if knowledge.collections.isEmpty {
                statusCard(icon: "books.vertical",
                           title: "No collections yet",
                           note: "Create a collection and add txt, md, pdf, docx, or html files. Chats can then ground answers in them.")
            } else {
                grid
            }
        }
    }

    private var grid: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 380), spacing: 14)], spacing: 14) {
            ForEach(knowledge.collections) { col in
                Button {
                    selected = col
                } label: {
                    KnowledgeCollectionCard(collection: col)
                }
                .buttonStyle(.plain)
                .pointerCursor()
            }
        }
    }

    private func errorRow(_ e: String) -> some View {
        HStack {
            Text(e).font(.system(size: 12)).foregroundStyle(.red)
            Spacer()
            Button { knowledge.error = nil } label: {
                Image(systemName: "xmark").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
            }.buttonStyle(.plain)
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func statusCard(icon: String, title: String, note: String, retry: Bool = false) -> some View {
        RowCard {
            Image(systemName: icon).font(.system(size: 16)).foregroundStyle(Theme.textSecondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(note).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
            if retry {
                Button("Retry") { Task { await knowledge.refresh() } }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.accent)
            }
        }
    }
}

struct KnowledgeCollectionCard: View {
    let collection: KnowledgeCollection

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(collection.name).font(.system(size: 15, weight: .semibold)).lineLimit(1)
                Spacer()
                KnowledgeStateChip(state: collection.indexState)
            }
            if !collection.description.isEmpty {
                Text(collection.description)
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary).lineLimit(2)
            }
            Text("\(collection.fileCount) \(collection.fileCount == 1 ? "file" : "files") · updated \(collection.updatedAt.formatted(date: .abbreviated, time: .omitted))")
                .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }
}

struct KnowledgeStateChip: View {
    let state: String

    private var tint: Color {
        switch state {
        case "ready": Color(red: 0.36, green: 0.78, blue: 0.5)
        case "failed": Color(red: 0.92, green: 0.42, blue: 0.38)
        case "stale", "pending", "indexing": .orange
        default: Theme.textSecondary
        }
    }

    var body: some View {
        Label(KnowledgeStateBadge.label(state), systemImage: KnowledgeStateBadge.icon(state))
            .font(.system(size: 10, weight: .semibold))
            .foregroundStyle(tint)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(tint.opacity(0.12), in: Capsule())
    }
}

struct KnowledgeCreateSheet: View {
    @ObservedObject var knowledge: KnowledgeStore
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var desc = ""
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("New collection").font(.system(size: 16, weight: .semibold))
            TextField("Name", text: $name).textFieldStyle(.roundedBorder)
            TextField("Description (optional)", text: $desc).textFieldStyle(.roundedBorder)
            if let error {
                Text(error).font(.system(size: 12)).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Create") {
                    saving = true
                    Task {
                        if let detail = await knowledge.client?.createCollection(
                            name: name.trimmingCharacters(in: .whitespaces), description: desc) {
                            error = detail
                            saving = false
                        } else {
                            await knowledge.refresh()
                            dismiss()
                        }
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || saving)
            }
        }
        .padding(20).frame(width: 420)
    }
}

struct KnowledgeCollectionDetail: View {
    @ObservedObject var knowledge: KnowledgeStore
    let collectionID: String
    @Environment(\.dismiss) private var dismiss
    enum FileLoadPhase { case loading, failed(String), loaded }

    @State private var files: [KnowledgeFile] = []
    @State private var filePhase = FileLoadPhase.loading
    @State private var search = ""
    @State private var sort: KnowledgeFileSort = .name
    @State private var name = ""
    @State private var desc = ""
    @State private var editing = false
    @State private var busy = false
    @State private var error: String?
    @State private var showDelete = false

    private var collection: KnowledgeCollection? {
        knowledge.collections.first { $0.id == collectionID }
    }

    private var visible: [KnowledgeFile] {
        KnowledgeFiltering.apply(files, search: search, sort: sort)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            if editing { editFields }
            if let error { errorRow(error) }
            controls
            fileList
            Spacer(minLength: 0)
        }
        .padding(20).frame(width: 640, height: 560)
        .background(Theme.bg)
        .confirmationDialog("Delete this collection?", isPresented: $showDelete, titleVisibility: .visible) {
            Button("Delete collection", role: .destructive) {
                Task {
                    await knowledge.run(collectionID) {
                        await knowledge.client?.deleteCollection(id: collectionID)
                    }
                    dismiss()
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Its files and their index are removed from vera-api.")
        }
        .task { await reload() }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(collection?.name ?? "Collection").font(.system(size: 18, weight: .bold))
                if let d = collection?.description, !d.isEmpty, !editing {
                    Text(d).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
            }
            if let state = collection?.indexState {
                KnowledgeStateChip(state: state)
            }
            Spacer()
            Menu {
                Button("Rename or describe") {
                    name = collection?.name ?? ""
                    desc = collection?.description ?? ""
                    editing = true
                }
                Button("Reindex all files") { runReindexAll() }
                Divider()
                Button("Delete collection", role: .destructive) { showDelete = true }
            } label: {
                Image(systemName: "ellipsis.circle").font(.system(size: 15))
                    .foregroundStyle(Theme.textSecondary)
            }
            .menuStyle(.borderlessButton).frame(width: 28)
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
            }.buttonStyle(.plain)
        }
    }

    private var editFields: some View {
        HStack(spacing: 8) {
            TextField("Name", text: $name).textFieldStyle(.roundedBorder)
            TextField("Description", text: $desc).textFieldStyle(.roundedBorder)
            Button("Save") {
                Task {
                    await knowledge.run(collectionID) {
                        await knowledge.client?.updateCollection(
                            id: collectionID,
                            name: name.trimmingCharacters(in: .whitespaces),
                            description: desc)
                    }
                    editing = false
                }
            }
            .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || busy)
            Button("Cancel") { editing = false }
        }
    }

    private var controls: some View {
        HStack(spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").font(.system(size: 11))
                    .foregroundStyle(Theme.textSecondary)
                TextField("Search files", text: $search).textFieldStyle(.plain)
                    .font(.system(size: 12))
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Theme.surface, in: Capsule())
            .overlay(Capsule().stroke(Theme.hairline))
            Picker("Sort", selection: $sort) {
                ForEach(KnowledgeFileSort.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.menu).fixedSize()
            Spacer()
            if busy { ProgressView().controlSize(.small) }
            Button {
                addFiles()
            } label: {
                Label("Add files", systemImage: "plus")
                    .font(.system(size: 12, weight: .medium))
            }
            .buttonStyle(.plain).foregroundStyle(Theme.accent)
            .disabled(busy)
        }
    }

    private var fileList: some View {
        ScrollView {
            LazyVStack(spacing: 8) {
                switch filePhase {
                case .loading:
                    Text("Loading files…")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 12)
                case .failed(let detail):
                    HStack(spacing: 8) {
                        Text("The file list couldn't load: \(detail)")
                            .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        Button("Try again") {
                            filePhase = .loading
                            Task { await reload() }
                        }
                        .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Theme.accent)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 12)
                case .loaded:
                    if visible.isEmpty {
                    Text(files.isEmpty ? "No files yet. Add txt, md, pdf, docx, or html files."
                                       : "No files match the search.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 12)
                    } else {
                        ForEach(visible) { file in
                            KnowledgeFileRow(file: file,
                                             reindex: { runFile(file.id) { await knowledge.client?.reindexFile(id: file.id) } },
                                             remove: { runFile(file.id) { await knowledge.client?.deleteFile(id: file.id) } })
                        }
                    }
                }
            }
        }
    }

    private func errorRow(_ e: String) -> some View {
        HStack {
            Text(e).font(.system(size: 12)).foregroundStyle(.red)
            Spacer()
            Button { error = nil } label: {
                Image(systemName: "xmark").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
            }.buttonStyle(.plain)
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func reload() async {
        guard let client = knowledge.client else { return }
        switch await client.files(collection: collectionID) {
        case .ok(let fetched):
            files = fetched
            filePhase = .loaded
        case .failed(let detail):
            if case .loaded = filePhase {
                error = "Couldn't refresh the file list: \(detail)"
            } else {
                filePhase = .failed(detail)
            }
        }
        await knowledge.refresh()
    }

    private func addFiles() {
        FilePicker.pick { urls in
            guard !urls.isEmpty, let client = knowledge.client else { return }
            busy = true
            Task {
                var failures: [String] = []
                for url in urls {
                    guard let data = try? Data(contentsOf: url) else {
                        failures.append("\(url.lastPathComponent): unreadable")
                        continue
                    }
                    if let detail = await client.upload(collection: collectionID,
                                                        name: url.lastPathComponent, data: data) {
                        failures.append("\(url.lastPathComponent): \(detail)")
                    }
                }
                if !failures.isEmpty { error = failures.joined(separator: "\n") }
                await reload()
                busy = false
            }
        }
    }

    private func runReindexAll() {
        busy = true
        Task {
            if let detail = await knowledge.client?.reindexCollection(id: collectionID) {
                error = detail
            }
            await reload()
            busy = false
        }
    }

    private func runFile(_ id: String, _ op: @escaping () async -> String?) {
        busy = true
        Task {
            if let detail = await op() { error = detail }
            await reload()
            busy = false
        }
    }
}

struct KnowledgeFileRow: View {
    let file: KnowledgeFile
    let reindex: () -> Void
    let remove: () -> Void
    var interactive = true

    private var meta: String {
        let size = ByteCountFormatter.string(fromByteCount: Int64(file.size), countStyle: .file)
        return "\(size) · \(file.updatedAt.formatted(date: .abbreviated, time: .omitted))"
    }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "doc.text").font(.system(size: 13))
                .foregroundStyle(Theme.textSecondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(file.name).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Text(meta).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                if file.state == "failed", let reason = file.error, !reason.isEmpty {
                    Text(reason).font(.system(size: 11)).foregroundStyle(.red).lineLimit(2)
                }
            }
            Spacer()
            KnowledgeStateChip(state: file.state)
            if interactive {
                Menu {
                    Button("Reindex") { reindex() }
                    Divider()
                    Button("Remove", role: .destructive) { remove() }
                } label: {
                    Image(systemName: "ellipsis.circle").font(.system(size: 13))
                        .foregroundStyle(Theme.textSecondary)
                }
                .menuStyle(.borderlessButton).frame(width: 26)
            } else {
                Image(systemName: "ellipsis.circle").font(.system(size: 13))
                    .foregroundStyle(Theme.textSecondary).frame(width: 26)
            }
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.hairline, lineWidth: 1))
    }
}

struct KnowledgeGroundingChip: View {
    @EnvironmentObject var store: ChatStore
    @EnvironmentObject var config: ConfigStore
    let conversation: Conversation
    @State private var showing = false
    @State private var available: [KnowledgeCollection] = []
    @State private var loadState = LoadState.idle

    enum LoadState { case idle, loading, loaded, failed }

    private var selectedCount: Int { conversation.grounding.count }

    private var chipLabel: String {
        if let status = store.groundingStatus[conversation.id], selectedCount > 0 {
            return status.label
        }
        return selectedCount == 0 ? "Knowledge"
            : "\(selectedCount) \(selectedCount == 1 ? "collection" : "collections")"
    }

    private var chipIcon: String {
        switch store.groundingStatus[conversation.id] {
        case .retrieving: "magnifyingglass"
        case .error, .unconfigured: "exclamationmark.triangle"
        default: selectedCount > 0 ? "books.vertical.fill" : "books.vertical"
        }
    }

    var body: some View {
        Button {
            showing = true
            Task { await load() }
        } label: {
            Label(chipLabel, systemImage: chipIcon)
                .font(.system(size: 11))
                .foregroundStyle(selectedCount > 0 ? Theme.accent : Theme.textSecondary)
        }
        .buttonStyle(.plain)
        .help("Ground this conversation's answers in selected document collections")
        .popover(isPresented: $showing, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Ground on knowledge").font(.system(size: 12, weight: .semibold))
                Text("Each reply searches the selected collections and cites the passages it uses.")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                switch loadState {
                case .idle, .loading:
                    ProgressView().controlSize(.small).frame(maxWidth: .infinity, alignment: .center)
                case .failed:
                    Text("Couldn't load collections from vera-api.")
                        .font(.system(size: 11)).foregroundStyle(.red)
                case .loaded:
                    if available.isEmpty {
                        Text("No collections yet. Create one in the Knowledge area.")
                            .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    } else {
                        ForEach(available) { col in
                            Toggle(isOn: binding(col.id)) {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(col.name).font(.system(size: 12))
                                    Text("\(col.fileCount) \(col.fileCount == 1 ? "file" : "files")")
                                        .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                                }
                            }
                            .toggleStyle(.checkbox)
                        }
                    }
                }
                if case .error(let detail) = store.groundingStatus[conversation.id], !detail.isEmpty {
                    Text(detail).font(.system(size: 11)).foregroundStyle(.red)
                }
            }
            .padding(12).frame(width: 300)
        }
    }

    private func binding(_ id: String) -> Binding<Bool> {
        Binding(
            get: { conversation.grounding.contains(id) },
            set: { on in
                var ids = conversation.grounding.filter { $0 != id }
                if on { ids.append(id) }
                store.setConversationGrounding(conversation.id, ids)
            })
    }

    private func load() async {
        guard loadState != .loaded else { return }
        loadState = .loading
        guard let base = config.veraAPIBase else { loadState = .failed; return }
        switch await KnowledgeClient(base: base).fetch() {
        case .ok(let cols):
            available = cols
            loadState = .loaded
            let ids = Set(cols.map(\.id))
            let pruned = conversation.grounding.filter { ids.contains($0) }
            if pruned != conversation.grounding {
                store.setConversationGrounding(conversation.id, pruned)
            }
        case .unsupported, .unreachable:
            loadState = .failed
        }
    }
}

struct KnowledgeShotSurface: View {
    let variant: String

    static let fixtureCollections: [KnowledgeCollection] = [
        KnowledgeCollection(id: "c1", name: "Home appliance manuals",
                            description: "Every manual for the house, from the heat pump to the dishwasher.",
                            fileCount: 12, indexState: "ready",
                            updatedAt: Date(timeIntervalSince1970: 1_754_200_000)),
        KnowledgeCollection(id: "c2", name: "Research papers",
                            description: "Interpretability and local inference reading list.",
                            fileCount: 34, indexState: "pending",
                            updatedAt: Date(timeIntervalSince1970: 1_754_100_000)),
        KnowledgeCollection(id: "c3", name: "Recipes",
                            description: "",
                            fileCount: 8, indexState: "failed",
                            updatedAt: Date(timeIntervalSince1970: 1_753_900_000)),
        KnowledgeCollection(id: "c4", name: "Insurance and warranty",
                            description: "Policies, claims, and warranty terms.",
                            fileCount: 5, indexState: "stale",
                            updatedAt: Date(timeIntervalSince1970: 1_753_800_000)),
    ]

    static let fixtureFiles: [KnowledgeFile] = [
        KnowledgeFile(id: "f1", name: "heat-pump-installation.pdf", format: "pdf", size: 4_820_000,
                      state: "ready", error: nil,
                      updatedAt: Date(timeIntervalSince1970: 1_754_200_000)),
        KnowledgeFile(id: "f2", name: "dishwasher-quick-start.pdf", format: "pdf", size: 812_000,
                      state: "ready", error: nil,
                      updatedAt: Date(timeIntervalSince1970: 1_754_150_000)),
        KnowledgeFile(id: "f3", name: "thermostat-schedule-notes.md", format: "md", size: 6_100,
                      state: "pending", error: nil,
                      updatedAt: Date(timeIntervalSince1970: 1_754_120_000)),
        KnowledgeFile(id: "f4", name: "scanned-warranty-card.pdf", format: "pdf", size: 2_400_000,
                      state: "failed", error: "no extractable text",
                      updatedAt: Date(timeIntervalSince1970: 1_754_000_000)),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Knowledge").font(.system(size: 26, weight: .bold))
                    Text("Reference collections Vera can ground chat answers in, with citations.")
                        .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                }
                Spacer()
                if variant == "knowledge" {
                    Text("\(Self.fixtureCollections.count)")
                        .font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 10).padding(.vertical, 5)
                        .background(Theme.surface, in: Capsule())
                    Label("New collection", systemImage: "plus")
                        .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.accent)
                }
            }
            switch variant {
            case "knowledge-unconfigured":
                shotStatusCard(icon: "books.vertical",
                               title: "Knowledge lights up with vera-api",
                               note: "Connect a vera-api URL in Settings and your document collections appear here, ready to ground chat answers.")
            case "knowledge-empty":
                shotStatusCard(icon: "books.vertical",
                               title: "No collections yet",
                               note: "Create a collection and add txt, md, pdf, docx, or html files. Chats can then ground answers in them.")
            case "knowledge-detail":
                detail
            default:
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 380), spacing: 14)], spacing: 14) {
                    ForEach(Self.fixtureCollections) { KnowledgeCollectionCard(collection: $0) }
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: 940)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .padding(.horizontal, 30)
        .padding(.vertical, 24)
        .background(Theme.bg)
    }

    private var detail: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Home appliance manuals").font(.system(size: 18, weight: .bold))
                    Text("Every manual for the house, from the heat pump to the dishwasher.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
                KnowledgeStateChip(state: "ready")
                Spacer()
                Image(systemName: "ellipsis.circle").font(.system(size: 15))
                    .foregroundStyle(Theme.textSecondary)
            }
            HStack(spacing: 10) {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass").font(.system(size: 11))
                        .foregroundStyle(Theme.textSecondary)
                    Text("Search files").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    Spacer()
                }
                .padding(.horizontal, 10).padding(.vertical, 6)
                .frame(width: 240)
                .background(Theme.surface, in: Capsule())
                .overlay(Capsule().stroke(Theme.hairline))
                Spacer()
                Label("Add files", systemImage: "plus")
                    .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.accent)
            }
            LazyVStack(spacing: 8) {
                ForEach(Self.fixtureFiles) { file in
                    KnowledgeFileRow(file: file, reindex: {}, remove: {}, interactive: false)
                }
            }
        }
        .padding(14)
        .background(Theme.surface.opacity(0.4))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
    }

    private func shotStatusCard(icon: String, title: String, note: String) -> some View {
        RowCard {
            Image(systemName: icon).font(.system(size: 16)).foregroundStyle(Theme.textSecondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(note).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
        }
    }
}
