import SwiftUI

/// The sidebar's selectable destinations — nav surfaces, one conversation, or an Agentic pane.
enum SidebarItem: Hashable {
    case pulse, journal, memory
    case convo(String)
    case canvas, activity
}

struct ContentView: View {
    @EnvironmentObject var store: ChatStore
    @EnvironmentObject var tools: ToolsStore
    @EnvironmentObject var voice: VoiceSession
    @EnvironmentObject var config: ConfigStore
    @State private var search = ""

    /// The toolbar modality picker's view of `store.section`: any non-agentic surface reads
    /// as Chat; picking a mode routes to that mode's home surface.
    private var mode: Binding<AppSection> {
        Binding(get: { store.section == .agentic ? .agentic : .chat },
                set: { store.section = $0 == .agentic ? .agentic : .chat })
    }

    var body: some View {
        ZStack {
            NavigationSplitView {
                Sidebar(search: $search)
                    .navigationSplitViewColumnWidth(min: 210, ideal: 248, max: 320)
            } detail: {
                HStack(spacing: 0) {
                    Group {
                        switch store.section {
                        case .chat: ChatPane()
                        case .pulse: PulseView()
                        case .journal: JournalView()
                        case .memory: MemoryView()
                        case .agentic: AgenticView()
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    if store.showCanvas {
                        Divider()
                        CanvasPanel().frame(width: 480)
                    }
                }
                .background(Theme.ink)
                .toolbar {
                    ToolbarItem(placement: .navigation) {
                        Picker("Mode", selection: mode) {
                            Text("Chat").tag(AppSection.chat)
                            Text("Agentic").tag(AppSection.agentic)
                        }
                        .pickerStyle(.segmented)
                    }
                }
            }
            .task { await store.connect() }
            .task { await tools.load() }
            .task { await tools.start() }   // long-lived: consume the live tool-invocation feed

            if voice.isActive {
                VoiceView().transition(.opacity).zIndex(1)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: voice.isActive)
        .sheet(isPresented: $config.showOnboarding) { OnboardingSheet() }
    }
}

struct Placeholder: View {
    let title: String
    let note: String
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "hammer.fill").font(.system(size: 28)).foregroundStyle(Theme.textSecondary)
            Text(title).font(.system(size: 18, weight: .semibold))
            Text("coming in \(note)").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }
}

// MARK: - Sidebar

private struct Sidebar: View {
    @EnvironmentObject var store: ChatStore
    @Binding var search: String

    private var agenticTab: Bool { store.section == .agentic }

    /// Bridges native list selection to the store's section / conversation / pane state.
    private var selection: Binding<SidebarItem?> {
        Binding(
            get: {
                switch store.section {
                case .pulse: return .pulse
                case .journal: return .journal
                case .memory: return .memory
                case .agentic: return store.agenticPane == .canvas ? .canvas : .activity
                case .chat: return store.selectedID.map(SidebarItem.convo)
                }
            },
            set: { item in
                switch item {
                case .pulse: store.goToPulse()
                case .journal: store.section = .journal
                case .memory: store.section = .memory
                case .canvas: store.section = .agentic; store.agenticPane = .canvas
                case .activity: store.section = .agentic; store.agenticPane = .activity
                case .convo(let id): store.select(id)
                case nil: break
                }
            })
    }

    var body: some View {
        List(selection: selection) {
            if agenticTab {
                Section {
                    Label("Canvas", systemImage: "point.3.connected.trianglepath.dotted")
                        .tag(SidebarItem.canvas)
                    Label("Activity", systemImage: "bolt").tag(SidebarItem.activity)
                }
            } else {
                Section {
                    Label("Pulse", systemImage: "newspaper").tag(SidebarItem.pulse)
                    Label("Journal", systemImage: "book.closed").tag(SidebarItem.journal)
                    Label("Memory", systemImage: "tray.full").tag(SidebarItem.memory)
                }
                ForEach(store.sidebarGroups(search: search)) { group in
                    Section(group.title) {
                        ForEach(group.convos) { convo in
                            SidebarRow(convo: convo,
                                       onPin: { store.togglePin(convo.id) },
                                       onDelete: { store.deleteConversation(convo.id) })
                                .tag(SidebarItem.convo(convo.id))
                        }
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .searchable(text: $search, placement: .sidebar, prompt: "Search")
        .safeAreaInset(edge: .top, spacing: 0) {
            HStack(spacing: 8) {
                VeraMark(size: 18)
                Text("Vera").font(.system(size: 14, weight: .semibold))
                Spacer()
            }
            .padding(.horizontal, 16).padding(.top, 2).padding(.bottom, 6)
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { UpdateBanner() }
        .toolbar {
            ToolbarItem {
                Button(action: store.newConversation) {
                    Label("New chat", systemImage: "square.and.pencil")
                }
                .help("New chat")
            }
        }
    }
}

struct SidebarRow: View {
    let convo: Conversation
    var onPin: () -> Void = {}
    var onDelete: () -> Void = {}
    @State private var hovering = false
    var body: some View {
        HStack(spacing: 8) {
            Text(convo.title).lineLimit(1)
            Spacer(minLength: 0)
            // Hover only reveals the ⋯ menu — no hover background, since macOS drops
            // onHover-exit events and would leave rows stuck looking selected.
            if hovering {
                Menu {
                    Button(convo.pinned ? "Unpin" : "Pin", action: onPin)
                    Button("Delete", role: .destructive, action: onDelete)
                } label: {
                    Image(systemName: "ellipsis").font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary).frame(width: 22, height: 18).contentShape(Rectangle())
                }
                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
            } else if convo.pinned {
                Image(systemName: "pin.fill").font(.system(size: 9))
                    .foregroundStyle(Theme.textSecondary).rotationEffect(.degrees(45))
            }
        }
        .onHover { hovering = $0 }
        .contextMenu {
            Button(convo.pinned ? "Unpin" : "Pin", action: onPin)
            Button("Delete", role: .destructive, action: onDelete)
        }
    }
}

// MARK: - Chat pane

private struct ChatPane: View {
    @EnvironmentObject var store: ChatStore

    var body: some View {
        VStack(spacing: 0) {
            if let convo = store.selected, !convo.messages.isEmpty {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 22) {
                            ForEach(convo.messages) { msg in
                                MessageRow(message: msg,
                                           onAnswer: { id, sel, other in store.submitAsk(messageID: id, selections: sel, other: other) },
                                           onOpenArtifact: { store.openArtifact($0) })
                            }
                            // One mark per chat at the leading edge — below the last response; animates
                            // while Vera works, sits still when idle. (Like Claude's single mark.)
                            VeraMark(size: 27, animated: store.generating)
                                .frame(width: 27, height: 27)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.top, 2)
                            Color.clear.frame(height: 1).id("bottom")
                        }
                        .padding(.horizontal, 28).padding(.top, 12).padding(.bottom, 26)
                        .frame(maxWidth: 760, alignment: .leading)
                        .frame(maxWidth: .infinity)
                    }
                    // Follow the text as it streams in, and on new turns / chat switches.
                    .onChange(of: convo.messages.last?.text ?? "") { proxy.scrollTo("bottom", anchor: .bottom) }
                    .onChange(of: convo.messages.count) { withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("bottom", anchor: .bottom) } }
                    .onChange(of: store.selectedID) { proxy.scrollTo("bottom", anchor: .bottom) }
                    .onAppear { proxy.scrollTo("bottom", anchor: .bottom) }
                }
                if let status = store.streamStatus {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(status).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        Spacer()
                    }
                    .frame(maxWidth: 760)
                    .padding(.horizontal, 28).padding(.bottom, 2)
                }
                ComposerField().padding(.horizontal, 28).padding(.bottom, 18).padding(.top, 6)
                    .frame(maxWidth: 760)
            } else if let convo = store.selected, convo.isPersisted {
                // A real chat whose history is still loading — skeleton, not the welcome flash.
                ChatLoadingSkeleton()
                ComposerField().padding(.horizontal, 28).padding(.bottom, 18).padding(.top, 6)
                    .frame(maxWidth: 760)
            } else {
                EmptyChatView()
            }
        }
    }
}

/// Shimmering placeholder shown while a real chat's history loads (no welcome-screen flash).
private struct ChatLoadingSkeleton: View {
    @State private var on = false
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            ForEach(0..<3, id: \.self) { _ in
                VStack(alignment: .leading, spacing: 8) { bar(140); bar(nil); bar(360) }
            }
            Spacer()
        }
        .padding(.horizontal, 28).padding(.top, 12).padding(.bottom, 26)
        .frame(maxWidth: 760, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
        .opacity(on ? 0.45 : 0.9)
        .onAppear { withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { on = true } }
    }
    private func bar(_ w: CGFloat?) -> some View {
        RoundedRectangle(cornerRadius: 6).fill(Theme.surfaceHover).frame(height: 12)
            .frame(maxWidth: w ?? .infinity, alignment: .leading)
    }
}

/// Centered welcome shown for an empty chat — greeting, centered composer, and starter prompts.
/// When the app isn't connected, offers the setup sheet instead of pretending to work.
private struct EmptyChatView: View {
    @EnvironmentObject var store: ChatStore
    @EnvironmentObject var config: ConfigStore

    private var greeting: String {
        let h = Calendar.current.component(.hour, from: Date())
        let part = h < 12 ? "morning" : (h < 18 ? "afternoon" : "evening")
        guard let name = config.ownerName else { return "Good \(part)" }
        return "Good \(part), \(name)"
    }

    private let starters = [
        "What's on today?", "Check the kitchen", "What's the weather?", "Research a topic for me",
    ]

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            VStack(spacing: 22) {
                HStack(spacing: 12) {
                    VeraMark(size: 30)
                    Text(greeting).font(.system(size: 28, weight: .semibold))
                }
                ComposerField()
                if store.isLive {
                    FlowChips(items: starters) { s in store.draft = s; store.focusTick &+= 1 }
                } else {
                    Button { config.showOnboarding = true } label: {
                        Label("Vera isn't connected. Set up the connection", systemImage: "bolt.horizontal")
                            .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.accent)
                            .padding(.horizontal, 12).padding(.vertical, 7)
                            .background(Theme.surface).clipShape(Capsule())
                            .overlay(Capsule().stroke(Theme.hairline, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                }
            }
            .frame(maxWidth: 640)
            .padding(.horizontal, 28)
            Spacer()
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }
}

/// Wrapping row of tappable starter-prompt chips.
private struct FlowChips: View {
    let items: [String]
    let onTap: (String) -> Void
    var body: some View {
        HStack(spacing: 8) {
            ForEach(items, id: \.self) { s in
                Button { onTap(s) } label: {
                    Text(s).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .background(Theme.surface).clipShape(Capsule())
                        .overlay(Capsule().stroke(Theme.hairline, lineWidth: 1))
                }
                .buttonStyle(.plain)
            }
        }
    }
}

struct MessageRow: View {
    let message: Message
    var onAnswer: ((UUID, [String], String) -> Void)? = nil
    var onOpenArtifact: ((Artifact) -> Void)? = nil
    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            if message.role == .assistant {
                // No per-message avatar — the single living mark lives at the foot of the thread.
                AssistantBody(message: message, onAnswer: onAnswer, onOpenArtifact: onOpenArtifact)
            } else {
                Spacer(minLength: 60)
                VStack(alignment: .trailing, spacing: 8) {
                    if !message.attachments.isEmpty {
                        SentAttachmentsBar(attachments: message.attachments)
                    }
                    if !message.text.isEmpty {
                        Text(message.text)
                            .font(.system(size: 14)).textSelection(.enabled)
                            .padding(.horizontal, 14).padding(.vertical, 10)
                            .background(Theme.userBubble)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                }
            }
        }
    }
}

/// A tappable reference to an artifact, shown inline under Vera's message.
struct ArtifactChip: View {
    let artifact: Artifact
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "rectangle.on.rectangle.angled").font(.system(size: 13)).foregroundStyle(Theme.accent)
            VStack(alignment: .leading, spacing: 1) {
                Text(artifact.title).font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.textPrimary)
                Text("\(artifact.type.rawValue) · open in Canvas").font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 8)
            Image(systemName: "arrow.up.right.square").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
        }
        .padding(10).frame(maxWidth: 360, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.hairline, lineWidth: 1))
    }
}

// MARK: - Composer

/// The rounded chat input box (text field + send), reused by the bottom bar and the empty state.
/// Moves the cursor into the field whenever `store.focusTick` changes (new chat, starter tap).
struct ComposerField: View {
    @EnvironmentObject var store: ChatStore
    @EnvironmentObject var voice: VoiceSession
    @FocusState private var focused: Bool
    @State private var dropTargeted = false

    private var canSend: Bool {
        !store.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !store.attachments.isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !store.attachments.isEmpty {
                AttachmentsBar(attachments: store.attachments, onRemove: { store.removeAttachment($0) })
            }
            TextField("Message Vera…", text: $store.draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .lineLimit(1...8)
                .focused($focused)
                .onKeyPress(.return, phases: .down) { press in
                    // Shift+Return inserts a line break; plain Return sends.
                    // We must insert the newline ourselves and return .handled —
                    // the field editor maps any Return to submit, so .ignored sends.
                    if press.modifiers.contains(.shift) {
                        store.draft += "\n"
                        return .handled
                    }
                    store.send()
                    return .handled
                }
            HStack(spacing: 10) {
                Menu {
                    Button("Add files or photos") { pickFiles() }
                } label: {
                    Image(systemName: "plus").font(.system(size: 14, weight: .medium))
                        .foregroundStyle(Theme.textSecondary)
                        .frame(width: 28, height: 28)
                        .background(Theme.surfaceHover).clipShape(Circle())
                }
                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                .help("Add files or photos (⌘U)")
                Spacer()
                Button(action: { voice.start() }) {
                    Image(systemName: "waveform")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(Theme.textSecondary)
                        .frame(width: 28, height: 28)
                        .background(Theme.surfaceHover).clipShape(Circle())
                }
                .buttonStyle(.plain)
                .help("Voice mode")
                Button(action: store.send) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 26))
                        .foregroundStyle(canSend ? Theme.accent : Theme.textSecondary)
                }
                .buttonStyle(.plain)
                .disabled(!canSend)
            }
            // Hidden ⌘U shortcut for the file picker.
            Button("", action: pickFiles).keyboardShortcut("u", modifiers: .command)
                .frame(width: 0, height: 0).opacity(0).accessibilityHidden(true)
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16)
            .stroke(dropTargeted ? Theme.accent : Theme.hairline, lineWidth: dropTargeted ? 2 : 1))
        .onAppear { focused = true }
        .onChange(of: store.focusTick) { _, _ in focused = true }
        .dropDestination(for: URL.self) { urls, _ in
            store.addFiles(urls); return true
        } isTargeted: { dropTargeted = $0 }
    }

    private func pickFiles() {
        FilePicker.pick { urls in if !urls.isEmpty { store.addFiles(urls) } }
    }
}
