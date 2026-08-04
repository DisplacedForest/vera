import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class ChatStore: ObservableObject {
    @Published var conversations: [Conversation] = []
    @Published var selectedID: String?
    @Published var draft: String = ""
    @Published var section: AppSection = .chat
    @Published var settingsTab: SettingsTab = .connection  // selected Settings tab; deep links set it before opening Settings
    @Published var agenticPane: AgenticPane = .canvas   // which Agentic surface the sidebar shows
    @Published var agenticFlowID: String?
    @Published var pulseCards: [PulseCard] = []
    @Published var pulseVeins: [PulseVein] = []   // pinned ambient veins, populated from vera-api
    @Published var memories: [MemoryItem] = []
    @Published var memoryRecords: [NativeMemoryRecord] = []
    @Published var memoryProposals: [NativeMemoryProposal] = []
    @Published var memoryServiceState: NativeMemoryServiceState = .off
    @Published var journalEntries: [JournalEntry] = []        // her standing commitments (read-only)
    @Published var journalArchive: [JournalArchiveMonth] = [] // recently resolved ones
    @Published var streamStatus: String?     // live tool/progress line while Vera is thinking
    @Published var generating = false        // true for the whole turn (drives the living flame mark)
    @Published var pulseRatings: [String: String] = [:]   // Pulse cardID → "up"/"down"
    @Published var messageRatings: [UUID: String] = [:]   // chat messageID → "up"/"down"
    @Published var bookmarkedPulseIDs: Set<String> = []   // Pulse cards also surfaced in the sidebar
    @Published var readPulseIDs: Set<String> = []         // cards whose detail this person has opened
    @Published var restoreState: [String: String] = [:]   // "<cardID>:<opIndex>" → running/done/failed
    @Published var actionState: [String: String] = [:]    // Pulse cardID → running/done/dismissed/failed
    @Published var digestItemState: [String: String] = [:] // "<cardID>:<itemID>" → pending/running/approved/skipped/failed
    @Published var pulseContinuation: [String: PulseContinuationState] = [:]
    @Published var pulseDetail: PulseCard? = nil    // open card detail (lifted here so the sidebar can dismiss it)
    @Published var pulseVeinDetail: PulseVein? = nil // open vein overlay (ditto)
    @Published var focusTick: Int = 0         // bump to move the cursor into the composer
    @Published var attachments: [Attachment] = []   // pending composer attachments (images/docs)
    @Published var attachmentError: String?
    @Published var pendingSendDecision: PendingSendDecision?
    @Published var activeArtifact: Artifact?  // the artifact shown in the Canvas panel
    @Published var showCanvas: Bool = false
    @Published var artifactLibrary: [Artifact] = []   // persisted across sessions
    @Published var chatConfigurationError: String?
    @Published private(set) var pendingToolConfirmation: NativeToolConfirmationRequest?

    private var config: OWUIConfig?
    private var client: OWUIClient?
    private var socket: VeraSocket?           // stream through OWUI's pipeline (tools + memory)
    private var nativeConfig: NativeChatConfig?
    private var nativeTransport: (any NativeChatTransport)?
    private var nativeSystemPrompt: String
    private var nativeOwnerName: String?
    private var nativeEnabledToolIDs: Set<String>
    private var nativeCapabilityOverrides: [String: ModelCapabilityProfile]
    private var visionBridgeConfig: VisionBridgeConfig?
    private let visionBridgeTransportFactory: ((VisionBridgeConfig) -> any NativeChatTransport)?
    private var nativeMemorySettings: NativeMemorySettings
    private var nativeMemoryService: (any NativeMemoryServing)?
    private var nativeToolRegistry: NativeToolRegistry
    private let usesInjectedToolRegistry: Bool
    private var capabilityDeclarations: [NativeToolDeclaration]
    private var toolConfirmationContinuation: CheckedContinuation<Bool, Never>?
    private let repository: (any ChatRepository)?
    private let repositoryInitializationError: String?
    private let hasLegacyOWUI: Bool
    let attachmentStore: NativeAttachmentStore
    private let pulseFeedProvider: (any PulseFeedProviding)?
    var isLive: Bool { nativeTransport != nil && repository != nil }
    var canSubmitChat: Bool { !generating && repository != nil }
    var isPulseConfigured: Bool { config?.veraAPIBase != nil }
    var isLegacyConfigured: Bool { hasLegacyOWUI }
    var apiToken: String? { hasLegacyOWUI ? config?.apiKey : nil }   // for authed OWUI image loads (Pulse cover art)
    var mediaBase: String? { config?.veraAPIBase?.absoluteString }
    var currentConfig: OWUIConfig? { config }  // for Settings to diff against saved edits
    var currentNativeConfig: NativeChatConfig? { nativeConfig }

    init(config: OWUIConfig?, client: OWUIClient?, socket: VeraSocket?,
         nativeConfig: NativeChatConfig?, nativeTransport: (any NativeChatTransport)?,
         repository: (any ChatRepository)?, repositoryError: String? = nil, hasLegacyOWUI: Bool,
         nativeSystemPrompt: String = NativeChatSettings.defaultSystemPrompt,
         nativeOwnerName: String? = nil,
         nativeEnabledToolIDs: Set<String> = [],
         nativeCapabilityOverrides: [String: ModelCapabilityProfile] = [:],
         visionBridgeConfig: VisionBridgeConfig? = nil,
         visionBridgeTransportFactory: ((VisionBridgeConfig) -> any NativeChatTransport)? = nil,
         nativeMemorySettings: NativeMemorySettings = .fresh,
         nativeMemoryService: (any NativeMemoryServing)? = nil,
         nativeToolRegistry: NativeToolRegistry? = nil,
         capabilityDeclarations: [NativeToolDeclaration] = [],
         pulseFeed: (any PulseFeedProviding)? = nil,
         attachmentStore: NativeAttachmentStore = NativeAttachmentStore(),
         sweepOrphanedAttachments: Bool = false) {
        self.config = config
        self.client = client
        self.socket = socket
        self.nativeConfig = nativeConfig
        self.nativeTransport = nativeTransport
        self.nativeSystemPrompt = nativeSystemPrompt
        self.nativeOwnerName = nativeOwnerName
        self.nativeEnabledToolIDs = nativeEnabledToolIDs
        self.nativeCapabilityOverrides = nativeCapabilityOverrides
        self.visionBridgeConfig = visionBridgeConfig
        self.visionBridgeTransportFactory = visionBridgeTransportFactory
        self.nativeMemorySettings = nativeMemorySettings
        self.nativeMemoryService = nativeMemoryService
        self.capabilityDeclarations = capabilityDeclarations
        self.nativeToolRegistry = nativeToolRegistry
            ?? ChatStore.buildToolRegistry(capabilityDeclarations)
        usesInjectedToolRegistry = nativeToolRegistry != nil
        self.repository = repository
        self.repositoryInitializationError = repositoryError
        self.hasLegacyOWUI = hasLegacyOWUI
        self.pulseFeedProvider = pulseFeed
        self.attachmentStore = attachmentStore
        chatConfigurationError = repositoryError
        selectedID = conversations.first?.id
        loadArtifacts()
        if sweepOrphanedAttachments, let repository,
           let referenced = try? repository.referencedAttachmentFileNames() {
            let store = attachmentStore
            let candidates = store.orphanCandidates(keeping: referenced)
            if !candidates.isEmpty {
                Task.detached(priority: .utility) { store.removeOrphans(candidates) }
            }
        }
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
        let configStore = ConfigStore()
        let native = configStore.nativeResolved
        let legacy = OWUIConfig.load()
        let ambient = legacy ?? OWUIConfig.ambientOnly(native: native)
        self.init(
            config: ambient,
            client: ambient.map { OWUIClient(config: $0) },
            socket: legacy.map { VeraSocket(config: $0) },
            nativeConfig: native,
            nativeTransport: native.map { NativeChatClient(config: $0) },
            repository: try? LocalChatRepository(inMemory: true),
            hasLegacyOWUI: legacy != nil,
            nativeOwnerName: configStore.ownerName,
            nativeMemorySettings: .fresh)
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

    static func buildToolRegistry(_ declarations: [NativeToolDeclaration]) -> NativeToolRegistry {
        let base: @Sendable () -> URL? = { OWUIConfig.resolvedVeraAPIBase() }
        return NativeToolRegistry(
            definitions: NativeRemindersTools.definitions(service: RemindersBridge.shared)
                + NativeWebTools.definitions(base: base)
                + NativeCapabilityTools.definitions(declarations, base: base))
    }

    func reloadCapabilityTools(_ declarations: [NativeToolDeclaration]) {
        capabilityDeclarations = declarations
        guard !usesInjectedToolRegistry else { return }
        nativeToolRegistry = ChatStore.buildToolRegistry(declarations)
    }

    func awaitToolConfirmation(_ activity: NativeToolActivity) async -> Bool {
        resolveToolConfirmation(false)
        return await withCheckedContinuation { continuation in
            toolConfirmationContinuation = continuation
            pendingToolConfirmation = NativeToolConfirmationRequest(activity: activity)
        }
    }

    func resolveToolConfirmation(_ approved: Bool) {
        guard let continuation = toolConfirmationContinuation else { return }
        toolConfirmationContinuation = nil
        pendingToolConfirmation = nil
        continuation.resume(returning: approved)
    }

    func adoptNative(
        _ cfg: NativeChatConfig,
        systemPrompt: String = NativeChatSettings.defaultSystemPrompt,
        ownerName: String? = nil,
        enabledToolIDs: Set<String>? = nil,
        capabilityOverrides: [String: ModelCapabilityProfile]? = nil,
        visionBridge: VisionBridgeConfig? = nil,
        memorySettings: NativeMemorySettings? = nil,
        memoryService: (any NativeMemoryServing)? = nil,
        capabilityDeclarations: [NativeToolDeclaration]? = nil
    ) {
        nativeConfig = cfg
        nativeTransport = NativeChatClient(config: cfg)
        nativeSystemPrompt = systemPrompt
        nativeOwnerName = ownerName
        if let enabledToolIDs { nativeEnabledToolIDs = enabledToolIDs }
        if let capabilityOverrides { nativeCapabilityOverrides = capabilityOverrides }
        visionBridgeConfig = visionBridge
        if let memorySettings { nativeMemorySettings = memorySettings }
        nativeMemoryService = memoryService
        if let capabilityDeclarations { reloadCapabilityTools(capabilityDeclarations) }
        let ambient = OWUIConfig.load() ?? OWUIConfig.ambientOnly(native: cfg)
        config = ambient
        client = ambient.map { OWUIClient(config: $0) }
        chatConfigurationError = nil
        Task { await connect() }
    }

    func connect() async {
        if let repository {
            do {
                conversations = try repository.listConversations()
                chatConfigurationError = nil
            } catch {
                conversations = []
                chatConfigurationError = "Local history couldn't open: \(error.localizedDescription)"
            }
        } else {
            conversations = []
            chatConfigurationError = repositoryInitializationError ?? "Local history couldn't open"
        }
        newConversation()
        await refreshPulse()
        reloadNativeMemory()
        runNativeMemoryMaintenance()
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
        guard hasLegacyOWUI, let client, let mems = await client.memories() else { return }
        memories = mems
    }

    /// Re-fetch her journal (self-authored, rendered read-only). Pulled when the view opens.
    func refreshJournal() async {
        guard hasLegacyOWUI, let client else { return }
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
        pulseVeins = veins   // empty when vera-api is unconfigured or returns none
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
    private var veinedKinds: Set<String> { Set(pulseVeins.map { $0.kind }) }

    /// The research feed: cards whose kind has no pinned vein (research + any unknown kind).
    var feedCards: [PulseCard] { pulseCards.filter { !veinedKinds.contains($0.kind) } }

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

    private func reconcile(refreshingMemories: Bool = false) async {
        await refreshPulse()
        _ = refreshingMemories
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
            // A successful apply changes the system: re-check so the card reflects reality
            // (the pending count drops) without waiting for the daily scheduled check.
            if ok, approve, card.category == "update" {
                await client.checkUpdatesNow()
                await refreshPulse()
            }
        }
    }

    /// Force a fresh stack-updates check, then refresh the feed (the updates card's "Check now").
    func checkUpdatesNow() {
        guard let client else { return }
        Task {
            await client.checkUpdatesNow()
            await refreshPulse()
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
    }

    func select(_ id: String) {
        section = .chat
        selectedID = id
        guard let repository,
              let idx = conversations.firstIndex(where: { $0.id == id }),
              conversations[idx].messages.isEmpty else { return }
        do {
            let msgs = try repository.messages(conversationID: id)
            if let i = conversations.firstIndex(where: { $0.id == id }) {
                conversations[i].messages = msgs
            }
        } catch {
            chatConfigurationError = "This conversation couldn't load: \(error.localizedDescription)"
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
        guard hasLegacyOWUI, let client else { return }
        Task {
            if await client.deleteMemory(id: item.id) == false { memories = prev }
        }
    }

    func reloadNativeMemory() {
        guard let memoryRepository = repository as? any NativeMemoryRepository else { return }
        do {
            memoryRecords = try memoryRepository.allMemories().filter { $0.status != .deleted }
            memoryProposals = try memoryRepository.pendingProposals()
            if !nativeMemorySettings.enabled {
                memoryServiceState = .off
            } else if nativeMemorySettings.embeddingsModel.isEmpty {
                memoryServiceState = .setupRequired
            } else if !memoryProposals.isEmpty {
                memoryServiceState = .pendingReview(memoryProposals.count)
            } else {
                memoryServiceState = .ready
            }
        } catch {
            memoryServiceState = .failed("The local memory library could not open: \(error.localizedDescription)")
        }
    }

    func updateNativeMemory(
        settings: NativeMemorySettings, service: (any NativeMemoryServing)?
    ) {
        nativeMemorySettings = settings
        nativeMemoryService = service
        reloadNativeMemory()
        runNativeMemoryMaintenance()
    }

    func requestMemoryChange(_ request: String) {
        let trimmed = request.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !NativeMemorySafety.containsSensitiveData(trimmed) else {
            memoryServiceState = .failed("Memory skipped text that looks like a credential or secret.")
            return
        }
        guard nativeMemorySettings.enabled, !trimmed.isEmpty,
              let service = nativeMemoryService,
              let memoryRepository = repository as? any NativeMemoryRepository else {
            memoryServiceState = nativeMemorySettings.enabled ? .setupRequired : .off
            return
        }
        memoryServiceState = .indexing
        Task {
            do {
                let proposals = try await service.proposal(
                    request: trimmed, existing: try memoryRepository.approvedMemories(), now: Date())
                try await saveMemoryProposals(
                    proposals, service: service, repository: memoryRepository)
                reloadNativeMemory()
            } catch {
                memoryServiceState = .retrievalUnavailable(error.localizedDescription)
            }
        }
    }

    func searchPastChatsForMemory() {
        guard nativeMemorySettings.enabled, nativeMemorySettings.searchPastChats,
              let service = nativeMemoryService,
              let memoryRepository = repository as? any NativeMemoryRepository else {
            memoryServiceState = nativeMemorySettings.enabled ? .setupRequired : .off
            return
        }
        let candidates = Array(conversations.lazy.filter { !$0.memoryExcluded }.prefix(12))
        memoryServiceState = .indexing
        Task {
            do {
                var reviewedTurns = 0
                var reviewedBytes = 0
                search: for conversation in candidates where reviewedTurns < 12 {
                    let messages = conversation.messages.isEmpty
                        ? try repository?.recentMessages(conversationID: conversation.id, limit: 48) ?? []
                        : Array(conversation.messages.suffix(48))
                    for index in messages.indices where reviewedTurns < 12 {
                        guard messages[index].role == .user,
                              messages.indices.contains(index + 1),
                              messages[index + 1].role == .assistant,
                              NativeMemoryExtractionPolicy.disposition(
                                user: messages[index].text, assistant: messages[index + 1],
                                conversationExcluded: conversation.memoryExcluded) == .eligible else { continue }
                        let turnBytes = NativeMemorySafety.boundedUTF8(
                            messages[index].text, maximumBytes: 4_000).utf8.count
                            + NativeMemorySafety.boundedUTF8(
                                messages[index + 1].text, maximumBytes: 4_000).utf8.count
                        guard reviewedBytes + turnBytes <= 48_000 else { break search }
                        let proposals = try await service.proposals(
                            user: messages[index].text, assistant: messages[index + 1].text,
                            sourceConversationID: conversation.id,
                            sourceMessageID: messages[index + 1].id.uuidString,
                            existing: try memoryRepository.approvedMemories(), now: Date())
                        try await saveMemoryProposals(
                            proposals, service: service, repository: memoryRepository)
                        reviewedTurns += 1
                        reviewedBytes += turnBytes
                    }
                }
                reloadNativeMemory()
            } catch {
                memoryServiceState = .retrievalUnavailable(error.localizedDescription)
            }
        }
    }

    func openMemorySource(conversationID: String?) {
        guard let conversationID,
              conversations.contains(where: { $0.id == conversationID }) else {
            memoryServiceState = .failed("The source conversation is no longer available on this Mac.")
            return
        }
        select(conversationID)
    }

    func acceptMemoryProposal(_ proposal: NativeMemoryProposal, editedDetails: [String]? = nil) {
        guard let memoryRepository = repository as? any NativeMemoryRepository else { return }
        do {
            switch proposal.kind {
            case .create:
                let memory = NativeMemoryRecord(
                    id: UUID().uuidString, title: proposal.title, summary: proposal.summary,
                    details: editedDetails ?? proposal.details, group: proposal.group,
                    category: proposal.category, bank: proposal.bank,
                    durability: proposal.durability, expiry: proposal.expiry, status: .approved,
                    embedding: nil, sourceConversationID: proposal.sourceConversationID,
                    sourceMessageID: proposal.sourceMessageID, createdAt: Date(), updatedAt: Date())
                try memoryRepository.saveMemory(
                    memory, decision: editedDetails == nil ? .accepted : .edited,
                    note: "Approved memory proposal")
            case .update, .merge, .consolidate:
                guard var target = memoryRecords.first(where: { proposal.targetIDs.contains($0.id) }) else { return }
                target.title = proposal.title
                target.summary = proposal.summary
                target.details = editedDetails ?? proposal.details
                target.group = proposal.group
                target.category = proposal.category
                target.bank = proposal.bank
                target.durability = proposal.durability
                target.expiry = proposal.expiry
                target.embedding = nil
                target.updatedAt = Date()
                try memoryRepository.saveMemory(
                    target, decision: proposal.kind == .update ? .edited : .merged,
                    note: "Approved memory proposal")
                if proposal.kind == .merge || proposal.kind == .consolidate {
                    for id in proposal.targetIDs where id != target.id { try memoryRepository.deleteMemory(id) }
                }
            case .suppress:
                guard var target = memoryRecords.first(where: { proposal.targetIDs.contains($0.id) }) else { return }
                target.status = .suppressed
                target.embedding = nil
                target.updatedAt = Date()
                try memoryRepository.saveMemory(target, decision: .suppressed, note: "Approved suppression")
            case .expire, .delete, .cleanup:
                for id in proposal.targetIDs { try memoryRepository.deleteMemory(id) }
            }
            try memoryRepository.decideProposal(proposal.id, status: .accepted)
            reloadNativeMemory()
            indexApprovedMemoryIfNeeded()
        } catch {
            memoryServiceState = .failed("The proposal could not be applied: \(error.localizedDescription)")
        }
    }

    func dismissMemoryProposal(_ proposal: NativeMemoryProposal) {
        guard let memoryRepository = repository as? any NativeMemoryRepository else { return }
        do {
            try memoryRepository.decideProposal(proposal.id, status: .dismissed)
            reloadNativeMemory()
        } catch {
            memoryServiceState = .failed("The proposal could not be dismissed: \(error.localizedDescription)")
        }
    }

    func deleteNativeMemory(_ memory: NativeMemoryRecord) {
        guard let memoryRepository = repository as? any NativeMemoryRepository else { return }
        do {
            try memoryRepository.deleteMemory(memory.id)
            reloadNativeMemory()
        } catch {
            memoryServiceState = .failed("The memory could not be deleted: \(error.localizedDescription)")
        }
    }

    func editNativeMemory(
        _ memory: NativeMemoryRecord, title: String, summary: String, details: [String]
    ) {
        guard let memoryRepository = repository as? any NativeMemoryRepository else { return }
        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanSummary = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanDetails = details.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        guard !cleanTitle.isEmpty, cleanTitle.count <= 80,
              !cleanSummary.isEmpty, cleanSummary.count <= 180,
              !cleanDetails.isEmpty, cleanDetails.count <= 8,
              cleanDetails.allSatisfy({ $0.count <= 600 }) else {
            memoryServiceState = .failed("Keep the name, summary, and details concise before saving.")
            return
        }
        do {
            var updated = memory
            updated.title = cleanTitle
            updated.summary = cleanSummary
            updated.details = cleanDetails
            updated.embedding = nil
            updated.updatedAt = Date()
            try memoryRepository.saveMemory(updated, decision: .edited, note: "Edited by the user")
            reloadNativeMemory()
            indexApprovedMemoryIfNeeded()
        } catch {
            memoryServiceState = .failed("The memory could not be saved: \(error.localizedDescription)")
        }
    }

    func clearNativeMemory() {
        guard let memoryRepository = repository as? any NativeMemoryRepository else { return }
        do {
            for memory in try memoryRepository.approvedMemories() {
                try memoryRepository.deleteMemory(memory.id)
            }
            reloadNativeMemory()
        } catch {
            memoryServiceState = .failed("The memory library could not be cleared: \(error.localizedDescription)")
        }
    }

    private func runNativeMemoryMaintenance() {
        guard nativeMemorySettings.enabled,
              let memoryRepository = repository as? any NativeMemoryRepository else { return }
        do {
            let records = try memoryRepository.allMemories()
            let pending = try memoryRepository.pendingProposals()
            for proposal in NativeMemoryMaintenance.proposals(records: records, existing: pending) {
                try memoryRepository.saveProposal(proposal)
            }
            reloadNativeMemory()
        } catch {
            memoryServiceState = .failed("Memory maintenance could not finish: \(error.localizedDescription)")
        }
    }

    private func indexApprovedMemoryIfNeeded() {
        guard nativeMemorySettings.enabled, let service = nativeMemoryService,
              let memoryRepository = repository as? any NativeMemoryRepository else { return }
        let missing = memoryRecords.filter {
            $0.status == .approved && $0.embedding == nil && $0.eligible(at: Date())
                && NativeMemorySafety.recordIsSafe($0)
        }
        guard !missing.isEmpty else { return }
        memoryServiceState = .indexing
        Task {
            for var memory in missing {
                do {
                    memory.embedding = try await service.embed(memory.details.joined(separator: "\n"))
                    memory.updatedAt = Date()
                    try memoryRepository.saveMemory(memory, decision: .edited, note: "Indexed approved memory")
                } catch {
                    memoryServiceState = .retrievalUnavailable(error.localizedDescription)
                    return
                }
            }
            reloadNativeMemory()
        }
    }

    private func saveMemoryProposals(
        _ proposals: [NativeMemoryProposal], service: any NativeMemoryServing,
        repository: any NativeMemoryRepository
    ) async throws {
        let existing = try repository.approvedMemories()
        for proposal in proposals where NativeMemorySafety.proposalIsSafe(proposal) {
            var reconciled = proposal
            if proposal.kind == .create,
               existing.contains(where: { $0.embedding != nil }),
               let embedding = try? await service.embed(proposal.details.joined(separator: "\n")) {
                reconciled = NativeMemoryReconciliation.reconcile(
                    proposal, candidateEmbedding: embedding, existing: existing)
            }
            try repository.saveProposal(reconciled)
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
        conversations[i].updatedAt = Date()
        do { try repository?.saveConversation(conversations[i]) }
        catch { chatConfigurationError = "Pin state couldn't save: \(error.localizedDescription)" }
    }

    func toggleMemoryExclusion(_ id: String) {
        guard let index = conversations.firstIndex(where: { $0.id == id }) else { return }
        conversations[index].memoryExcluded.toggle()
        conversations[index].updatedAt = Date()
        do { try repository?.saveConversation(conversations[index]) }
        catch { chatConfigurationError = "The conversation memory setting could not save: \(error.localizedDescription)" }
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

    func deleteConversation(_ id: String) {
        let idx = conversations.firstIndex { $0.id == id }
        let stored = (try? repository?.messages(conversationID: id)) ?? []
        conversations.removeAll { $0.id == id }
        if selectedID == id {
            let next = idx.flatMap { conversations.indices.contains($0) ? conversations[$0] : conversations.last }
            selectedID = next?.id ?? conversations.first?.id
        }
        do {
            try repository?.deleteConversation(id)
            for message in stored {
                for attachment in message.attachments { attachmentStore.remove(attachment) }
            }
        } catch {
            chatConfigurationError = "The conversation couldn't be deleted: \(error.localizedDescription)"
        }
        if selectedID == nil { newConversation() }
    }

    enum PulseContinuationState: Equatable {
        case opening
        case failed(String)
    }

    static let pulseContinuationDatabaseFailure = "Vera couldn't create the local conversation. Try again."

    func openPulseInChat(_ card: PulseCard, onOpened: (() -> Void)? = nil) {
        if pulseContinuation[card.id] == .opening { return }
        guard let repository else {
            pulseContinuation[card.id] = .failed(Self.pulseContinuationDatabaseFailure)
            return
        }
        do {
            if let existing = try repository.conversation(originType: PulseSeed.originType, originID: card.id) {
                pulseContinuation[card.id] = nil
                openContinuation(existing, onOpened: onOpened)
                return
            }
        } catch {
            pulseContinuation[card.id] = .failed(Self.pulseContinuationDatabaseFailure)
            return
        }
        guard let provider = pulseFeedProvider ?? (client as (any PulseFeedProviding)?) else {
            pulseContinuation[card.id] = .failed("Pulse isn't connected. Set up Vera API to continue this card.")
            return
        }
        pulseContinuation[card.id] = .opening
        Task {
            let result = await provider.pulseFeed()
            finishContinuation(card: card, result: result, repository: repository, onOpened: onOpened)
        }
    }

    private func finishContinuation(
        card: PulseCard, result: PulseFeedResult, repository: any ChatRepository,
        onOpened: (() -> Void)?
    ) {
        switch result {
        case .unconfigured:
            pulseContinuation[card.id] = .failed("Pulse isn't connected. Set up Vera API to continue this card.")
        case .transport:
            pulseContinuation[card.id] = .failed("Pulse couldn't be reached. Try again.")
        case .malformed:
            pulseContinuation[card.id] = .failed("This Pulse card couldn't be opened.")
        case .success(let cards, let rawIDs):
            guard let fresh = cards.first(where: { $0.id == card.id }) else {
                pulseContinuation[card.id] = .failed(rawIDs.contains(card.id)
                    ? "This Pulse card couldn't be opened."
                    : "This Pulse card is no longer available.")
                return
            }
            let now = Date()
            let seed = PulseSeed.message(for: fresh, at: now)
            let conversation = Conversation(
                id: UUID().uuidString, title: fresh.title, messages: [seed],
                createdAt: now, updatedAt: now, isPersisted: true,
                originType: PulseSeed.originType, originID: fresh.id)
            do {
                try repository.createOriginConversation(conversation, seed: seed)
            } catch {
                if let existing = try? repository.conversation(
                    originType: PulseSeed.originType, originID: card.id) {
                    pulseContinuation[card.id] = nil
                    openContinuation(existing, onOpened: onOpened)
                } else {
                    pulseContinuation[card.id] = .failed(Self.pulseContinuationDatabaseFailure)
                }
                return
            }
            pulseContinuation[card.id] = nil
            conversations.insert(conversation, at: 0)
            pulseDetail = nil
            onOpened?()
            section = .chat
            selectedID = conversation.id
            focusTick &+= 1
        }
    }

    private func openContinuation(_ conversation: Conversation, onOpened: (() -> Void)?) {
        if !conversations.contains(where: { $0.id == conversation.id }) {
            conversations.insert(conversation, at: 0)
        }
        pulseDetail = nil
        onOpened?()
        select(conversation.id)
        focusTick &+= 1
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

    func addFiles(_ urls: [URL]) {
        attachmentError = nil
        for url in urls {
            do {
                let record = try attachmentStore.save(url: url)
                attachments.append(pendingAttachment(for: record, sourceURL: url))
            } catch {
                attachmentError = error.localizedDescription
            }
        }
    }

    func addPastedImages(_ images: [Data]) {
        attachmentError = nil
        for data in images {
            do {
                let record = try attachmentStore.save(data: data, preferredName: "Pasted image")
                attachments.append(pendingAttachment(for: record, sourceURL: nil))
            } catch {
                attachmentError = error.localizedDescription
            }
        }
    }

    private func pendingAttachment(for record: MessageAttachment, sourceURL: URL?) -> Attachment {
        let fileURL = record.fileName.flatMap { attachmentStore.url(for: $0) }
        let att = Attachment(url: sourceURL ?? fileURL ?? URL(fileURLWithPath: record.name))
        att.nativeRecord = record
        if let fileURL, let image = NSImage(contentsOf: fileURL) {
            att.thumbnail = ImageEncoder.downscale(image, maxDim: 96)
        }
        att.status = .ready
        return att
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

    func removeAttachment(_ id: UUID) {
        attachmentError = nil
        for att in attachments where att.id == id {
            if let record = att.nativeRecord { attachmentStore.remove(record) }
        }
        attachments.removeAll { $0.id == id }
    }

    struct PendingSendDecision: Identifiable, Equatable {
        let id = UUID()
        let text: String
        let modelID: String
        let imageCount: Int
        let imageLimit: Int?
    }

    var activeCapabilityResolution: ModelCapabilityCatalog.Resolution? {
        guard let nativeConfig else { return nil }
        return ModelCapabilityCatalog.resolve(
            model: nativeConfig.model, overrides: nativeCapabilityOverrides)
    }

    var effectiveBridgeConfig: VisionBridgeConfig? {
        guard let bridge = visionBridgeConfig else { return nil }
        if let nativeConfig, bridge.isSameEndpoint(as: nativeConfig) {
            return nil
        }
        return bridge
    }

    func attachmentRoute(imageCount: Int) -> AttachmentRoute? {
        guard imageCount > 0 else { return nil }
        guard let resolution = activeCapabilityResolution else { return nil }
        return AttachmentPreflight.route(
            profile: resolution.profile, imageCount: imageCount,
            bridgeConfigured: effectiveBridgeConfig != nil)
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let atts = attachments
        guard !text.isEmpty || !atts.isEmpty else { return }
        guard !generating else { return }
        guard repository != nil else {
            chatConfigurationError = repositoryInitializationError ?? "Local history couldn't open"
            return
        }
        let imageCount = atts.filter { $0.nativeRecord?.isImage == true }.count
        let route = attachmentRoute(imageCount: imageCount)
        if route == .needsDecision, let model = nativeConfig?.model {
            let profile = activeCapabilityResolution?.profile
            pendingSendDecision = PendingSendDecision(
                text: text, modelID: model, imageCount: imageCount,
                imageLimit: profile?.acceptsImages == true
                    ? max(profile?.maxImagesPerRequest ?? 1, 1) : nil)
            return
        }
        draft = ""
        attachments = []
        let note: MessageRouteNote?
        switch route {
        case .direct: note = MessageRouteNote(route: .direct)
        case .bridged: note = MessageRouteNote(route: .bridged, bridgeModel: effectiveBridgeConfig?.model)
        default: note = nil
        }
        sendText(text, attachments: atts, routeNote: note)
    }

    func confirmSendWithoutAttachments() {
        guard pendingSendDecision != nil else { return }
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let atts = attachments
        pendingSendDecision = nil
        guard !text.isEmpty else {
            attachmentError = "There is no message text to send without the attachment. Add text or remove the attachment."
            return
        }
        draft = ""
        attachments = []
        sendText(text, attachments: atts, routeNote: MessageRouteNote(route: .withheld))
    }

    func cancelPendingSend() {
        pendingSendDecision = nil
    }

    /// Record the user's answer to a Vera structured question and send it as their next turn.
    func submitAsk(messageID: UUID, selections: [String], other: String) {
        guard !generating else { return }
        guard repository != nil else {
            chatConfigurationError = repositoryInitializationError ?? "Local history couldn't open"
            return
        }
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

    func sendText(
        _ text: String, attachments atts: [Attachment] = [], routeNote: MessageRouteNote? = nil
    ) {
        guard !generating else { return }
        guard let repository else {
            chatConfigurationError = repositoryInitializationError ?? "Local history couldn't open"
            return
        }
        guard let id = selectedID,
              let idx = conversations.firstIndex(where: { $0.id == id }) else { return }
        let records = atts.compactMap(\.nativeRecord)
        let user = Message(role: .user, text: text, attachments: records, routeNote: routeNote)
        conversations[idx].messages.append(user)
        if conversations[idx].title == "New chat" {
            let title = text.isEmpty ? (records.first?.name ?? "New chat") : String(text.prefix(40))
            conversations[idx].title = title
        }
        conversations[idx].updatedAt = Date()
        conversations[idx].isPersisted = true

        do {
            try repository.saveConversation(conversations[idx])
            try repository.saveMessage(user, conversationID: id, ordinal: conversations[idx].messages.count - 1)
        } catch {
            conversations[idx].messages.removeLast()
            for record in records { attachmentStore.remove(record) }
            chatConfigurationError = "The message couldn't save: \(error.localizedDescription)"
            return
        }

        guard let nativeTransport, let nativeConfig else {
            let reply = Message(role: .assistant, text: "", state: .interrupted,
                                failure: "Configure a model endpoint ending in /v1 and select a model")
            conversations[idx].messages.append(reply)
            try? repository.saveMessage(reply, conversationID: id, ordinal: conversations[idx].messages.count - 1)
            return
        }

        let reply = Message(role: .assistant, text: "", state: .streaming, modelID: nativeConfig.model)
        conversations[idx].messages.append(reply)
        let replyIndex = conversations[idx].messages.count - 1
        let userIndex = conversations[idx].messages.count - 2
        try? repository.saveMessage(reply, conversationID: id, ordinal: replyIndex)
        streamStatus = "Thinking…"
        generating = true
        let toolLoop = NativeToolLoop(
            transport: nativeTransport, registry: nativeToolRegistry,
            confirm: { [weak self] activity in
                guard let self else { return false }
                return await self.awaitToolConfirmation(activity)
            })
        let capabilityProfile = ModelCapabilityCatalog.resolve(
            model: nativeConfig.model, overrides: nativeCapabilityOverrides).profile
        let enabledToolIDs = capabilityProfile.supportsTools ? nativeEnabledToolIDs : []
        let bridgeConfig = effectiveBridgeConfig
        let bridgeTransportFactory = visionBridgeTransportFactory
        let memorySettings = nativeMemorySettings
        let memoryService = nativeMemoryService
        let persona = nativeSystemPrompt
        let ownerName = nativeOwnerName ?? config?.ownerName
        let activeTools = nativeToolRegistry.active(enabledIDs: enabledToolIDs)
        Task {
            defer {
                generating = false
                resolveToolConfirmation(false)
            }
            var lastCheckpoint = Date.distantPast
            do {
                let attachmentStore = self.attachmentStore
                let imageLoader: (MessageAttachment) -> String? = {
                    attachmentStore.requestDataURL(for: $0)
                }
                if routeNote?.route == .bridged {
                    guard let bridgeConfig else {
                        throw NativeChatClient.ClientError.server(
                            "The vision bridge is no longer configured. Your message and attachment are saved; nothing was sent.")
                    }
                    streamStatus = "Describing the attachment…"
                    let bridge = VisionBridge(
                        config: bridgeConfig,
                        transport: bridgeTransportFactory.map { $0(bridgeConfig) })
                    var descriptions: [(name: String, text: String)] = []
                    do {
                        for record in records where record.isImage {
                            guard let dataURL = attachmentStore.requestDataURL(for: record) else {
                                throw VisionBridgeError.emptyDescription(record.name)
                            }
                            descriptions.append(
                                (record.name, try await bridge.describe(name: record.name, dataURL: dataURL)))
                        }
                    } catch {
                        throw NativeChatClient.ClientError.server(
                            "The vision bridge couldn't describe the attachment: \(error.localizedDescription). Your message and attachment are saved locally; nothing was sent to the model.")
                    }
                    let disclosure = VisionBridgeDisclosure.block(
                        descriptions: descriptions, bridgeModel: bridgeConfig.model)
                    if let i = conversations.firstIndex(where: { $0.id == id }),
                       userIndex >= 0, userIndex < conversations[i].messages.count {
                        conversations[i].messages[userIndex].routeNote = MessageRouteNote(
                            route: .bridged, bridgeModel: bridgeConfig.model, disclosure: disclosure)
                        try? repository.saveMessage(
                            conversations[i].messages[userIndex], conversationID: id, ordinal: userIndex)
                    }
                    streamStatus = "Thinking…"
                }
                let turnMessages = conversations.first(where: { $0.id == id })?.messages
                    ?? []
                var selectedMemories: [NativeMemoryRanked] = []
                if memorySettings.enabled {
                    if memorySettings.embeddingsModel.isEmpty || memoryService == nil {
                        memoryServiceState = .setupRequired
                    } else if NativeMemorySafety.containsSensitiveData(text) {
                        memoryServiceState = memoryProposals.isEmpty
                            ? .ready : .pendingReview(memoryProposals.count)
                    } else if let memoryRepository = repository as? any NativeMemoryRepository,
                              let memoryService {
                        do {
                            let query = try await memoryService.embed(text)
                            selectedMemories = NativeMemoryRecall.rank(
                                records: try memoryRepository.approvedMemories(), query: query,
                                bankScope: memorySettings.bankScope)
                            memoryServiceState = memoryProposals.isEmpty
                                ? .ready : .pendingReview(memoryProposals.count)
                        } catch {
                            memoryServiceState = .retrievalUnavailable(error.localizedDescription)
                        }
                    }
                }
                var contracts = NativePresentationContract.chatDefaults
                if activeTools.contains(where: { NativeWebTools.researchCapableIDs.contains($0.id) }) {
                    contracts.insert(.citations)
                }
                let assembled = NativeContextAssembler.assemble(NativeContextInput(
                    persona: persona, timestamp: Date(), timeZone: .current,
                    ownerName: ownerName, memories: selectedMemories,
                    capabilities: capabilityProfile, tools: activeTools,
                    contracts: contracts))
                let history = NativeChatHistoryBuilder.build(
                    messages: turnMessages, systemPrompt: assembled.prompt,
                    imageLoader: imageLoader, capabilities: capabilityProfile)
                for try await snapshot in toolLoop.stream(
                    messages: history, model: nativeConfig.model, enabledToolIDs: enabledToolIDs
                ) {
                    guard let i = conversations.firstIndex(where: { $0.id == id }),
                          replyIndex < conversations[i].messages.count else { continue }
                    let (afterArt, arts) = Artifact.parse(snapshot.content)
                    let (clean, ask) = VeraAsk.parse(afterArt)
                    conversations[i].messages[replyIndex].text = clean
                    conversations[i].messages[replyIndex].ask = ask
                    conversations[i].messages[replyIndex].artifacts = arts
                    conversations[i].messages[replyIndex].toolActivities = snapshot.activities
                    let lifted = NativeResearchSources.lift(snapshot.activities)
                    if !lifted.isEmpty { conversations[i].messages[replyIndex].sources = lifted }
                    if let latest = arts.last,
                       latest.id != activeArtifact?.id || latest.content != activeArtifact?.content {
                        openArtifact(latest)
                    }
                    streamStatus = snapshot.activities.last(where: { $0.state == .pending })
                        .map { "Using \($0.title)…" }
                    if Date().timeIntervalSince(lastCheckpoint) >= 0.25 {
                        try repository.saveMessage(
                            conversations[i].messages[replyIndex], conversationID: id, ordinal: replyIndex)
                        lastCheckpoint = Date()
                    }
                }
                streamStatus = nil
                if let i = conversations.firstIndex(where: { $0.id == id }),
                   replyIndex < conversations[i].messages.count {
                    conversations[i].messages[replyIndex].state = .complete
                    conversations[i].updatedAt = Date()
                    try repository.saveMessage(conversations[i].messages[replyIndex], conversationID: id, ordinal: replyIndex)
                    try repository.saveConversation(conversations[i])
                    if memorySettings.enabled, memorySettings.generateFromChats,
                       let memoryService,
                       let memoryRepository = repository as? any NativeMemoryRepository,
                       NativeMemoryExtractionPolicy.disposition(
                            user: text, assistant: conversations[i].messages[replyIndex],
                            conversationExcluded: conversations[i].memoryExcluded) == .eligible {
                        let completedAssistant = conversations[i].messages[replyIndex]
                        Task {
                            do {
                                let proposals = try await memoryService.proposals(
                                    user: text, assistant: completedAssistant.text,
                                    sourceConversationID: id,
                                    sourceMessageID: completedAssistant.id.uuidString,
                                    existing: try memoryRepository.approvedMemories(), now: Date())
                                try await self.saveMemoryProposals(
                                    proposals, service: memoryService, repository: memoryRepository)
                                reloadNativeMemory()
                            } catch {
                                memoryServiceState = .retrievalUnavailable(error.localizedDescription)
                            }
                        }
                    }
                }
            } catch {
                streamStatus = nil
                if let i = conversations.firstIndex(where: { $0.id == id }),
                   replyIndex < conversations[i].messages.count {
                    conversations[i].messages[replyIndex].state = .interrupted
                    conversations[i].messages[replyIndex].failure = error.localizedDescription
                    conversations[i].updatedAt = Date()
                    if userIndex >= 0, userIndex < conversations[i].messages.count,
                       conversations[i].messages[userIndex].routeNote?.route == .bridged,
                       conversations[i].messages[userIndex].routeNote?.disclosure == nil {
                        conversations[i].messages[userIndex].routeNote = nil
                        try? repository.saveMessage(
                            conversations[i].messages[userIndex], conversationID: id, ordinal: userIndex)
                    }
                    try? repository.saveMessage(conversations[i].messages[replyIndex], conversationID: id, ordinal: replyIndex)
                    try? repository.saveConversation(conversations[i])
                }
            }
        }
    }

}
