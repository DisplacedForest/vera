import SwiftUI
import UniformTypeIdentifiers

/// App state. Starts mock-backed; the OWUI client swaps in live data once connected.
@MainActor
final class ChatStore: ObservableObject {
    @Published var conversations: [Conversation] = []
    @Published var selectedID: String?
    @Published var draft: String = ""
    @Published var section: AppSection = .chat
    @Published var pulseCards: [PulseCard] = []
    @Published var pulseVeins: [PulseVein] = PulseVein.mock()   // pinned ambient veins
    @Published var memories: [MemoryItem] = []
    @Published var journalEntries: [JournalEntry] = []        // her standing commitments (read-only)
    @Published var journalArchive: [JournalArchiveMonth] = [] // recently resolved ones
    @Published var streamStatus: String?     // live tool/progress line while Vera is thinking
    @Published var generating = false        // true for the whole turn (drives the living flame mark)
    private var settleTask: Task<Void, Never>?  // settles `generating` after the last token if the stream never signals done
    @Published var pulseRatings: [String: String] = [:]   // Pulse cardID → "up"/"down"
    @Published var messageRatings: [UUID: String] = [:]   // chat messageID → "up"/"down"
    @Published var bookmarkedPulseIDs: Set<String> = []   // Pulse cards also surfaced in the sidebar
    @Published var readPulseIDs: Set<String> = []         // cards whose detail this person has opened
    @Published var restoreState: [String: String] = [:]   // "<cardID>:<opIndex>" → running/done/failed
    @Published var actionState: [String: String] = [:]    // Pulse cardID → running/done/dismissed/failed
    @Published var digestItemState: [String: String] = [:] // "<cardID>:<itemID>" → pending/running/approved/skipped/failed
    @Published var pulseDetail: PulseCard? = nil    // open card detail (lifted here so the sidebar can dismiss it)
    @Published var pulseVeinDetail: PulseVein? = nil // open vein overlay (ditto)
    @Published var focusTick: Int = 0         // bump to move the cursor into the composer
    @Published var attachments: [Attachment] = []   // pending composer attachments (images/docs)
    @Published var activeArtifact: Artifact?  // the artifact shown in the Canvas panel
    @Published var showCanvas: Bool = false
    @Published var artifactLibrary: [Artifact] = []   // persisted across sessions

    private var config: OWUIConfig?
    private var client: OWUIClient?
    private var socket: VeraSocket?           // stream through OWUI's pipeline (tools + memory)
    var isLive: Bool { client != nil }
    var apiToken: String? { config?.apiKey }   // for authed OWUI image loads (Pulse cover art)
    var currentConfig: OWUIConfig? { config }  // for Settings to diff against saved edits

    init(config: OWUIConfig?, client: OWUIClient?, socket: VeraSocket?) {
        self.config = config
        self.client = client
        self.socket = socket
        selectedID = conversations.first?.id
        loadArtifacts()
    }

    // MARK: - Canvas / artifacts

    private static var artifactsURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Vera", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("artifacts.json")
    }

    func loadArtifacts() {
        if let d = try? Data(contentsOf: Self.artifactsURL),
           let arr = try? JSONDecoder().decode([Artifact].self, from: d) {
            artifactLibrary = arr
        }
    }

    func openArtifact(_ a: Artifact) {
        activeArtifact = a
        showCanvas = true
        saveArtifact(a)
    }

    func closeCanvas() { showCanvas = false }

    /// Local edit of the open artifact's source — live-updates the preview and persists.
    func updateActiveArtifact(content: String) {
        guard var a = activeArtifact else { return }
        a.content = content
        a.updatedAt = Date()
        activeArtifact = a
        saveArtifact(a)
    }

    private func saveArtifact(_ a: Artifact) {
        if let i = artifactLibrary.firstIndex(where: { $0.id == a.id }) { artifactLibrary[i] = a }
        else { artifactLibrary.insert(a, at: 0) }
        try? JSONEncoder().encode(artifactLibrary).write(to: Self.artifactsURL)
    }

    /// Standalone constructor (screenshots / `Shot`): loads config and builds its own deps.
    convenience init() {
        let cfg = OWUIConfig.load()
        self.init(config: cfg,
                  client: cfg.map { OWUIClient(config: $0) },
                  socket: cfg.map { VeraSocket(config: $0) })
    }

    /// Wire up a config at runtime (first-run onboarding, or connection edits in Settings):
    /// rebuild the client and a fresh signed-in socket, then connect. Voice mode and the MCP
    /// board pick the change up on next launch.
    func adopt(_ cfg: OWUIConfig) {
        config = cfg
        client = OWUIClient(config: cfg)
        socket = VeraSocket(config: cfg)
        Task { await connect() }
    }

    /// Apply edits that don't touch the OWUI session (vera-api base, model, identity) live —
    /// the socket and its sign-in are left alone.
    func applyLight(_ cfg: OWUIConfig) {
        config = cfg
        client = OWUIClient(config: cfg)
    }

    /// Load the live chat list from OWUI (called on the live app's appear). Ordered so no
    /// half-state renders: list arrives, then the "New chat" placeholder, then selection — once.
    func connect() async {
        guard client != nil else { return }
        await reconcileChats()
        newConversation()   // open to a fresh chat (like ChatGPT/Claude), not the last one
        await refreshPulse()
        await refreshMemories()
        startReconcileLoop()
    }

    private var reconcilingChats = false
    /// Diff the store's conversations against OWUI's chat list — the server is the source of
    /// truth for everything persisted; local unpersisted drafts are never touched. One pass at
    /// a time: a focus-triggered pass and the 30s tick must not interleave their diffs.
    func reconcileChats() async {
        guard !reconcilingChats else { return }
        reconcilingChats = true
        defer { reconcilingChats = false }
        guard let client, let chats = try? await client.listChats() else { return }
        let pinned = await client.pinnedChatIDs()
        let serverIDs = Set(chats.map(\.id))

        for summary in chats {
            let serverStamp = summary.updated_at ?? 0
            guard let i = conversations.firstIndex(where: { $0.id == summary.id }) else {
                conversations.append(Conversation(
                    id: summary.id, title: summary.title, messages: [],
                    updatedAt: Date(timeIntervalSince1970: TimeInterval(serverStamp)),
                    isPersisted: true, serverUpdatedAt: serverStamp,
                    pinned: pinned.contains(summary.id)))
                continue
            }
            // Leave the open chat alone mid-stream; the next tick reconciles it once settled.
            if summary.id == selectedID && generating { continue }
            let changedElsewhere = serverStamp > conversations[i].serverUpdatedAt
            conversations[i].title = summary.title
            conversations[i].updatedAt = Date(timeIntervalSince1970: TimeInterval(serverStamp))
            conversations[i].serverUpdatedAt = serverStamp
            conversations[i].pinned = pinned.contains(summary.id)
            guard changedElsewhere, !conversations[i].messages.isEmpty else { continue }
            if summary.id == selectedID {
                // Turns taken elsewhere appear in the open chat without reselecting.
                let msgs = await client.loadMessages(chatID: summary.id)
                if let j = conversations.firstIndex(where: { $0.id == summary.id }), !msgs.isEmpty {
                    conversations[j].messages = msgs
                }
            } else {
                conversations[i].messages = []   // stale history; the next select re-fetches
            }
        }

        // Server-side deletions leave the sidebar; a deleted open chat closes gracefully.
        let selectedWasDeleted = conversations.contains {
            $0.id == selectedID && $0.isPersisted && !serverIDs.contains($0.id)
        }
        conversations.removeAll { $0.isPersisted && !serverIDs.contains($0.id) }
        if selectedWasDeleted { selectedID = nil; newConversation() }
    }

    /// Re-fetch memories from OWUI. A failed fetch keeps the current list; an empty result is
    /// real (memories deleted elsewhere) and applies.
    func refreshMemories() async {
        guard let client, let mems = await client.memories() else { return }
        memories = mems
    }

    /// Re-fetch her journal (self-authored, rendered read-only). Pulled when the view opens.
    func refreshJournal() async {
        guard let client else { return }
        let (entries, archive) = await client.fetchJournal()
        journalEntries = entries
        journalArchive = archive
    }

    /// Re-fetch the Pulse feed from vera-api (its standalone store). Reflects adds/deletes,
    /// and restores persisted bookmark state.
    func refreshPulse() async {
        guard let client else { return }
        async let cardsTask = client.fetchPulseCards()
        async let veinsTask = client.fetchPulseVeins()
        let (cards, veins) = await (cardsTask, veinsTask)
        pulseCards = cards
        if !veins.isEmpty { pulseVeins = veins }   // keep mock veins if the backend returns none
        bookmarkedPulseIDs = Set(cards.filter { $0.status == "bookmarked" }.map { $0.id })
        readPulseIDs = Set(cards.filter { $0.read }.map { $0.id })   // per-row read state
    }

    /// Mark a card read the moment its detail opens. Optimistic (insert + decrement the
    /// chip's unread now), then persist to vera-api and reconcile the counts on refresh. Idempotent.
    func markPulseRead(_ card: PulseCard) {
        guard !readPulseIDs.contains(card.id) else { return }
        readPulseIDs.insert(card.id)
        if let i = pulseVeins.firstIndex(where: { $0.kind == card.kind }), pulseVeins[i].unread > 0 {
            pulseVeins[i].unread -= 1
        }
        guard let client else { return }
        Task {
            await client.markPulseRead(id: card.id)
            await refreshPulse()   // reconcile unread counts + max severity from the server
        }
    }

    // MARK: - Pulse tiers: pinned ambient veins vs the research feed

    /// Kinds that have a pinned vein — every other kind falls into the research feed.
    private var veindKinds: Set<String> { Set(pulseVeins.map { $0.kind }) }

    /// The research feed: cards whose kind has no pinned vein (research + any unknown kind).
    var feedCards: [PulseCard] { pulseCards.filter { !veindKinds.contains($0.kind) } }

    /// Active cards for one vein (matched by kind), newest first as the store returns them.
    func veinCards(_ kind: String) -> [PulseCard] { pulseCards.filter { $0.kind == kind } }

    /// A vein's cards grouped by category in fixed order (Vera/Infra/Health/Updates),
    /// newest-first within each group (store order is preserved). Empty groups are dropped.
    func veinCardsByCategory(_ kind: String) -> [(PulseCategory, [PulseCard])] {
        let cards = veinCards(kind)
        return PulseCategory.allCases.compactMap { cat in
            let group = cards.filter { PulseCategory.of($0) == cat }
            return group.isEmpty ? nil : (cat, group)
        }
    }

    /// Restore or reject one grooming op, routed to the correct store by `op.store`.
    /// Restore = one-time undo; Reject = undo + don't-redo (suppressed next run). Optimistic state,
    /// then reconcile the feed. A "stale" result means a later run already changed the target.
    func restoreMemoryOp(_ card: PulseCard, _ op: GroomOp) { decideGroomOp(card, op, mode: "restore") }
    func rejectOp(_ card: PulseCard, _ op: GroomOp) { decideGroomOp(card, op, mode: "reject") }

    private func decideGroomOp(_ card: PulseCard, _ op: GroomOp, mode: String) {
        guard let client else { return }
        let key = "\(card.id):\(op.index)"
        restoreState[key] = "running"
        Task {
            let state = await client.decideGroomOp(store: op.store, mode: mode,
                                                   cardID: card.id, opIndex: op.index)
            restoreState[key] = (state == "done") ? (mode == "reject" ? "rejected" : "done") : state
            await refreshPulse()
        }
    }

    /// Approve/Reject a flagged-for-review proposal (a digest item) inline — reuses the
    /// digest decision path; Approve commits the staged action, Reject suppresses it.
    func decideProposal(_ card: PulseCard, _ item: PulseDigestItem, approve: Bool) {
        decideDigestItem(card, item, approve: approve)
    }

    private var reconcileStarted = false
    private var reconcileTick = 0
    /// The app's single sync heartbeat: every 30 seconds (and instantly on window focus) the
    /// store reconciles against the server. Per-surface fetches run independently so one
    /// failing endpoint never blocks the others; memories ride a slower multiple.
    private func startReconcileLoop() {
        guard !reconcileStarted else { return }
        reconcileStarted = true
        NotificationCenter.default.addObserver(
            forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in await self?.reconcile() }
        }
        Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
                guard let self else { return }
                self.reconcileTick += 1
                await self.reconcile(refreshingMemories: self.reconcileTick % 5 == 0)
            }
        }
    }

    /// One reconcile pass over every live surface, fetched concurrently.
    private func reconcile(refreshingMemories: Bool = false) async {
        async let pulse: Void = refreshPulse()
        async let chats: Void = reconcileChats()
        if refreshingMemories { await refreshMemories() }
        _ = await (pulse, chats)
    }

    // MARK: - Bookmark + feedback

    /// Bookmark a Pulse card → server-persisted (vera-api), surfaced in the chat sidebar as a real
    /// chat, while it stays in the Pulse feed until expiry. Toggle off removes the untouched entry.
    func bookmarkPulse(_ card: PulseCard) {
        let turningOn = !bookmarkedPulseIDs.contains(card.id)
        if turningOn { bookmarkedPulseIDs.insert(card.id) } else { bookmarkedPulseIDs.remove(card.id) }
        guard let client else { return }
        Task {
            let chatID = await client.setPulseBookmark(id: card.id, on: turningOn)
            if turningOn, let chatID, !conversations.contains(where: { $0.id == chatID }) {
                conversations.insert(Conversation(id: chatID, title: card.title,
                    messages: [Message(role: .assistant, text: card.body, pulse: card)],
                    updatedAt: Date(), isPersisted: true), at: 0)
            } else if !turningOn {
                conversations.removeAll { $0.messages.count == 1 && $0.messages.first?.pulse?.id == card.id }
            }
        }
    }

    /// Thumbs-up/down a Pulse card. Tapping the active thumb again clears it (no record on clear).
    func ratePulse(_ card: PulseCard, _ sentiment: String) {
        if pulseRatings[card.id] == sentiment { pulseRatings[card.id] = nil; return }
        pulseRatings[card.id] = sentiment
        guard let client else { return }
        Task {
            await client.postFeedback([
                "kind": "pulse", "sentiment": sentiment, "topic": card.title, "title": card.title,
                "content": card.body, "chat_id": card.id, "model": config?.model ?? "",
            ])
        }
    }

    /// Confirm a Pulse action card → execute it server-side, then reflect the outcome.
    func confirmAction(_ card: PulseCard) {
        guard let action = card.action, let client else { return }
        actionState[card.id] = "running"
        Task {
            let out = await client.commitAction(token: action.token)
            if let out, (out["ok"] as? Bool) == true {
                actionState[card.id] = "done"
            } else {
                actionState[card.id] = "failed: " + ((out?["error"] as? String) ?? "error")
            }
        }
    }

    /// Dismiss a Pulse action card → drop the staged action; nothing runs.
    func dismissAction(_ card: PulseCard) {
        actionState[card.id] = "dismissed"
        guard let action = card.action, let client else { return }
        Task { await client.dismissAction(token: action.token) }
    }

    /// The live state of one digest item (local optimistic state overrides the stored state).
    func digestState(_ card: PulseCard, _ item: PulseDigestItem) -> String {
        digestItemState["\(card.id):\(item.itemID)"] ?? item.state
    }

    /// Approve (request) or skip one item of a digest card.
    func decideDigestItem(_ card: PulseCard, _ item: PulseDigestItem, approve: Bool) {
        guard let client else { return }
        let key = "\(card.id):\(item.itemID)"
        digestItemState[key] = "running"
        Task {
            let ok = await client.decideDigestItem(cardID: card.id, itemID: item.itemID, approve: approve)
            digestItemState[key] = ok ? (approve ? "approved" : "skipped") : "failed"
        }
    }

    /// Go to the Pulse feed, dismissing any open card/vein detail. Lets the sidebar Pulse
    /// button act as a "back to feed" from anywhere inside Pulse.
    func goToPulse() {
        section = .pulse
        pulseDetail = nil
        pulseVeinDetail = nil
    }

    /// Approve-all / skip-all the still-pending items of a digest card.
    func decideDigestAll(_ card: PulseCard, approve: Bool) {
        guard let client else { return }
        for item in card.items where digestState(card, item) == "pending" {
            digestItemState["\(card.id):\(item.itemID)"] = "running"
        }
        Task {
            await client.decideDigestAll(cardID: card.id, approve: approve)
            for item in card.items where digestItemState["\(card.id):\(item.itemID)"] == "running" {
                digestItemState["\(card.id):\(item.itemID)"] = approve ? "approved" : "skipped"
            }
        }
    }

    /// Thumbs-up/down a chat response → preference data for later post-training (RLHF/DPO).
    func rateMessage(_ message: Message, in convo: Conversation, _ sentiment: String) {
        if messageRatings[message.id] == sentiment { messageRatings[message.id] = nil; return }
        messageRatings[message.id] = sentiment
        guard let client else { return }
        Task {
            await client.postFeedback([
                "kind": "chat", "sentiment": sentiment, "title": convo.title, "content": message.text,
                "chat_id": convo.id, "message_id": message.id.uuidString,
                "model": config?.model ?? "",
            ])
        }
    }

    /// Select a conversation and lazily load its history from OWUI.
    func select(_ id: String) {
        section = .chat
        selectedID = id
        guard let client,
              let idx = conversations.firstIndex(where: { $0.id == id }),
              conversations[idx].messages.isEmpty else { return }
        Task {
            let msgs = await client.loadMessages(chatID: id)
            if let i = conversations.firstIndex(where: { $0.id == id }) {
                conversations[i].messages = msgs
            }
        }
    }

    var selected: Conversation? {
        guard let id = selectedID else { return nil }
        return conversations.first { $0.id == id }
    }

    // MARK: - Memory actions

    /// Forget a memory: optimistically remove it, then delete it in OWUI — revert on failure.
    func deleteMemory(_ item: MemoryItem) {
        let prev = memories
        memories.removeAll { $0.id == item.id }
        guard let client else { return }
        Task {
            if await client.deleteMemory(id: item.id) == false { memories = prev }
        }
    }

    func newConversation() {
        section = .chat
        draft = ""
        // Reuse an existing empty "New chat" instead of stacking duplicates.
        if let existing = conversations.first(where: { $0.title == "New chat" && $0.messages.isEmpty }) {
            selectedID = existing.id
        } else {
            let convo = Conversation(id: UUID().uuidString, title: "New chat", messages: [], updatedAt: Date())
            conversations.insert(convo, at: 0)
            selectedID = convo.id
        }
        focusTick &+= 1
    }

    /// Pin/unpin a conversation (local + persisted to OWUI).
    func togglePin(_ id: String) {
        guard let i = conversations.firstIndex(where: { $0.id == id }) else { return }
        conversations[i].pinned.toggle()
        if let client, conversations[i].isPersisted { Task { await client.togglePin(id: id) } }
    }

    // id must be STABLE across renders (title is) — a fresh UUID each compute breaks SwiftUI's
    // LazyVStack identity and leaks the selected-row background onto a recycled slot.
    struct SidebarGroup: Identifiable { var id: String { title }; let title: String; let convos: [Conversation] }

    /// Conversations grouped for the sidebar: Pinned, then Recents bucketed by recency.
    func sidebarGroups(search: String) -> [SidebarGroup] {
        let q = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var seenIDs = Set<String>()
        let visible = conversations.filter {
            guard seenIDs.insert($0.id).inserted else { return false }   // defensive: one row per id
            return (!$0.messages.isEmpty || $0.isPersisted) && (q.isEmpty || $0.title.lowercased().contains(q))
        }
        let pinned = visible.filter { $0.pinned }.sorted { $0.updatedAt > $1.updatedAt }
        let rest = visible.filter { !$0.pinned }.sorted { $0.updatedAt > $1.updatedAt }
        var groups: [SidebarGroup] = []
        if !pinned.isEmpty { groups.append(.init(title: "Pinned", convos: pinned)) }
        let cal = Calendar.current, now = Date()
        func bucket(_ d: Date) -> String {
            if cal.isDateInToday(d) { return "Today" }
            if cal.isDateInYesterday(d) { return "Yesterday" }
            let days = cal.dateComponents([.day], from: d, to: now).day ?? 0
            return days < 7 ? "Previous 7 Days" : (days < 30 ? "Previous 30 Days" : "Older")
        }
        var byBucket: [String: [Conversation]] = [:]
        for c in rest { byBucket[bucket(c.updatedAt), default: []].append(c) }
        for b in ["Today", "Yesterday", "Previous 7 Days", "Previous 30 Days", "Older"] {
            if let cs = byBucket[b], !cs.isEmpty { groups.append(.init(title: b, convos: cs)) }
        }
        return groups
    }

    /// Remove a conversation (locally and in OWUI) and reselect a neighbour.
    func deleteConversation(_ id: String) {
        let idx = conversations.firstIndex { $0.id == id }
        let persisted = conversations.first { $0.id == id }?.isPersisted ?? false
        conversations.removeAll { $0.id == id }
        if selectedID == id {
            let next = idx.flatMap { conversations.indices.contains($0) ? conversations[$0] : conversations.last }
            selectedID = next?.id ?? conversations.first?.id
        }
        if let client, persisted { Task { await client.deleteChat(id: id) } }
    }

    /// Open a Pulse briefing inside the chat view as a normal conversation (no popup).
    /// Continue a Pulse in chat → promote it (vera-api creates a real OWUI chat), then open that
    /// chat seeded with the rich briefing as its first message.
    func openPulseInChat(_ card: PulseCard) {
        section = .chat
        guard let client else { return }
        Task {
            guard let chatID = await client.promotePulse(id: card.id) else { return }
            if conversations.contains(where: { $0.id == chatID }) {
                selectedID = chatID
                return
            }
            let owuiMsgs = await client.loadMessages(chatID: chatID)
            // First turn IS the briefing — render it as the rich article; keep any later replies.
            var msgs: [Message] = [Message(role: .assistant, text: card.body, pulse: card)]
            if owuiMsgs.count > 1 { msgs.append(contentsOf: owuiMsgs.dropFirst()) }
            conversations.insert(Conversation(id: chatID, title: card.title, messages: msgs,
                                              updatedAt: Date(), isPersisted: true), at: 0)
            selectedID = chatID
        }
    }

    // MARK: - Voice mode

    /// Start a voice turn: ensure a conversation exists, append the user's transcript + an empty
    /// assistant placeholder, and return the chat id plus API message history (excluding the
    /// placeholder). `VoiceSession` streams the reply itself (to feed TTS) and calls
    /// `updateVoiceReply` as tokens arrive.
    func beginVoiceTurn(userText: String) -> (chatID: String, messages: [[String: Any]])? {
        section = .chat
        if selectedID == nil || conversations.firstIndex(where: { $0.id == selectedID }) == nil {
            let convo = Conversation(id: UUID().uuidString, title: "New chat", messages: [], updatedAt: Date())
            conversations.insert(convo, at: 0)
            selectedID = convo.id
        }
        guard let id = selectedID, let idx = conversations.firstIndex(where: { $0.id == id }) else { return nil }
        conversations[idx].messages.append(.init(role: .user, text: userText))
        if conversations[idx].title == "New chat" {
            conversations[idx].title = String(userText.prefix(40))
        }
        conversations[idx].updatedAt = Date()
        let history: [[String: Any]] = conversations[idx].messages.map { ["role": $0.role.rawValue, "content": $0.text] }
        conversations[idx].messages.append(.init(role: .assistant, text: ""))
        return (id, history)
    }

    /// Live-update the streaming assistant reply for a voice turn (the trailing assistant message).
    func updateVoiceReply(chatID: String, text: String) {
        guard let idx = conversations.firstIndex(where: { $0.id == chatID }),
              let last = conversations[idx].messages.indices.last,
              conversations[idx].messages[last].role == .assistant else { return }
        conversations[idx].messages[last].text = text
    }

    // MARK: - Attachments (composer)

    /// Add picked/dropped files: images are downscaled to an inline data URL (read by `see_image`);
    /// documents are uploaded to OWUI and referenced via the completion's `files`.
    func addFiles(_ urls: [URL]) {
        for url in urls {
            let att = Attachment(url: url)
            attachments.append(att)
            Task { await process(att) }
        }
        focusTick &+= 1
    }

    private func process(_ att: Attachment) async {
        if att.kind == .image {
            if let r = ImageEncoder.dataURL(from: att.url) {
                att.dataURL = r.dataURL
                att.thumbnail = r.thumb
                att.status = .ready
            } else { att.status = .failed }
            return
        }
        // Document → upload to OWUI for RAG-style grounding.
        guard let client, let data = try? Data(contentsOf: att.url) else { att.status = .failed; return }
        let mime = (UTType(filenameExtension: att.url.pathExtension)?.preferredMIMEType) ?? "application/octet-stream"
        if let obj = await client.uploadFile(name: att.name, data: data, mime: mime) {
            att.owuiFile = obj; att.status = .ready
        } else { att.status = .failed }
    }

    func removeAttachment(_ id: UUID) { attachments.removeAll { $0.id == id } }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let atts = attachments
        guard !text.isEmpty || !atts.isEmpty else { return }
        draft = ""
        attachments = []
        sendText(text, attachments: atts)
    }

    /// Record the user's answer to a Vera structured question and send it as their next turn.
    func submitAsk(messageID: UUID, selections: [String], other: String) {
        guard let id = selectedID,
              let ci = conversations.firstIndex(where: { $0.id == id }),
              let mi = conversations[ci].messages.firstIndex(where: { $0.id == messageID }) else { return }
        var parts = selections
        let o = other.trimmingCharacters(in: .whitespacesAndNewlines)
        if !o.isEmpty { parts.append(o) }
        let answer = parts.joined(separator: ", ")
        guard !answer.isEmpty else { return }
        conversations[ci].messages[mi].answered = true
        conversations[ci].messages[mi].answerText = answer
        sendText(answer)
    }

    /// Append a user turn and stream Vera's reply (shared by send() and submitAsk()).
    func sendText(_ text: String, attachments atts: [Attachment] = []) {
        guard let id = selectedID,
              let idx = conversations.firstIndex(where: { $0.id == id }) else { return }
        let snaps = atts.map {
            MessageAttachment(name: $0.name, ext: $0.ext, isImage: $0.kind == .image,
                              thumbnailData: $0.thumbnail?.pngData)
        }
        conversations[idx].messages.append(.init(role: .user, text: text, attachments: snaps))
        if conversations[idx].title == "New chat" {
            conversations[idx].title = String((text.isEmpty ? (atts.first?.name ?? "New chat") : text).prefix(40))
        }
        conversations[idx].updatedAt = Date()

        guard let socket else {
            conversations[idx].messages.append(.init(role: .assistant, text: "(OWUI not configured — set ~/.vera/config.json. Shell echo.)"))
            return
        }

        // OWUI expects STRING content + attachments in the top-level `files` array. Sending an
        // OpenAI-style multimodal content list breaks its pipeline ("'list' object has no attribute
        // 'strip'"). OWUI injects these `files` into the messages so `see_image` can read the image.
        let history: [[String: Any]] = conversations[idx].messages.map { ["role": $0.role.rawValue, "content": $0.text] }
        var files: [[String: Any]] = []
        for u in atts.filter({ $0.kind == .image }).compactMap({ $0.dataURL }) {
            files.append(["type": "image", "url": u])
        }
        for f in atts.compactMap({ $0.owuiFile }) {
            files.append(["type": "file", "id": (f["id"] as? String) ?? "", "file": f])
        }
        conversations[idx].messages.append(.init(role: .assistant, text: ""))
        let replyIndex = conversations[idx].messages.count - 1
        let messageID = UUID().uuidString
        let chatID = id   // the OWUI id once persisted; a local UUID only for a brand-new chat
        streamStatus = "Thinking…"
        generating = true
        // Stream through OWUI's pipeline; `.content` is cumulative. Parse out any vera:ask block live.
        Task { [socket] in
            defer { settleTask?.cancel(); generating = false }
            do {
                for try await event in socket.streamReply(chatID: chatID, messageID: messageID, messages: history, files: files.isEmpty ? nil : files) {
                    guard let i = conversations.firstIndex(where: { $0.id == id }),
                          replyIndex < conversations[i].messages.count else { continue }
                    switch event {
                    case .content(let raw):
                        let (afterArt, arts) = Artifact.parse(raw)
                        let (clean, ask) = VeraAsk.parse(afterArt)
                        conversations[i].messages[replyIndex].text = clean
                        conversations[i].messages[replyIndex].ask = ask
                        conversations[i].messages[replyIndex].artifacts = arts
                        if let latest = arts.last,
                           latest.id != activeArtifact?.id || latest.content != activeArtifact?.content {
                            openArtifact(latest)   // auto-open Canvas when an artifact completes
                        }
                        streamStatus = nil
                        scheduleSettle()   // typing started; settle if tokens stop for a while
                    case .status(let s):
                        streamStatus = s
                        scheduleSettle()   // tool activity counts as "still working"
                    case .sources(let srcs):
                        conversations[i].messages[replyIndex].sources = srcs
                    case .done:
                        streamStatus = nil
                        settleTask?.cancel()
                        generating = false
                    }
                }
                streamStatus = nil
                await persistChat(localID: id)
            } catch {
                streamStatus = nil
                if let i = conversations.firstIndex(where: { $0.id == id }),
                   replyIndex < conversations[i].messages.count {
                    conversations[i].messages[replyIndex].text = "⚠️ \(error.localizedDescription)"
                }
            }
        }
    }

    /// After the last token, settle `generating` (stops the flame) even if the socket never sends a
    /// clean `done`. Re-armed on every content tick; `.done`/stream-end cancel it.
    private func scheduleSettle() {
        settleTask?.cancel()
        settleTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            guard !Task.isCancelled else { return }
            self?.generating = false
            self?.streamStatus = nil
        }
    }

    /// Persist a conversation to OWUI after a turn so new chats survive relaunch
    /// (create-or-update). A first-time create re-keys the conversation to the OWUI id in one
    /// step — id, selection, everything — so there is exactly one identity from then on. The
    /// server's updated_at stamp is recorded so reconciliation never reads our own save as an
    /// external change.
    private func persistChat(localID: String) async {
        guard let client, let i = conversations.firstIndex(where: { $0.id == localID }) else { return }
        let convo = conversations[i]
        let turns = convo.messages.filter { !$0.text.isEmpty }.map { ($0.role.rawValue, $0.text) }
        guard !turns.isEmpty else { return }
        if convo.isPersisted {
            if let stamp = await client.saveChat(id: convo.id, title: convo.title, turns: turns),
               let j = conversations.firstIndex(where: { $0.id == localID }) {
                conversations[j].serverUpdatedAt = stamp
            }
        } else if let created = await client.createChat(title: convo.title, turns: turns),
                  let j = conversations.firstIndex(where: { $0.id == localID }) {
            var rekeyed = Conversation(id: created.id, title: conversations[j].title,
                                       messages: conversations[j].messages,
                                       updatedAt: conversations[j].updatedAt,
                                       isPersisted: true, serverUpdatedAt: created.updatedAt,
                                       pinned: conversations[j].pinned)
            // A reconcile pass may have already fetched the new chat from the server — keep one row.
            if let dup = conversations.firstIndex(where: { $0.id == created.id }), dup != j {
                rekeyed.pinned = conversations[dup].pinned
                conversations.remove(at: dup)
            }
            if let k = conversations.firstIndex(where: { $0.id == localID }) {
                conversations[k] = rekeyed
            }
            if selectedID == localID { selectedID = created.id }
        }
    }
}
