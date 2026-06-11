import SwiftUI

struct ContentView: View {
    @EnvironmentObject var store: ChatStore
    @EnvironmentObject var tools: ToolsStore
    @EnvironmentObject var voice: VoiceSession
    @EnvironmentObject var config: ConfigStore

    var body: some View {
        ZStack {
            HStack(spacing: 0) {
                Sidebar().frame(width: 268).background(Theme.sidebar)
                Rectangle().fill(Theme.hairline).frame(width: 1)
                Group {
                    switch store.section {
                    case .chat: ChatPane().background(Theme.bg)
                    case .pulse: PulseView()
                    case .veins: VeinsView()
                    case .journal: JournalView()
                    case .memory: MemoryView()
                    case .plugins: PluginsView()
                    case .mcp: MCPView()
                    case .agentic: AgenticView()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if store.showCanvas {
                    Rectangle().fill(Theme.hairline).frame(width: 1)
                    CanvasPanel().frame(width: 480)
                }
            }
            .ignoresSafeArea()
            .background(WindowConfigurator())
            .foregroundStyle(Theme.textPrimary)
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
    @State private var search = ""

    private var agenticTab: Bool { store.section == .agentic }

    var body: some View {
        VStack(spacing: 0) {
            // Brand + modality tabs
            VStack(spacing: 10) {
                HStack(spacing: 8) {
                    VeraMark(size: 18)
                    Text("Vera").font(.system(size: 14, weight: .semibold))
                    Spacer()
                }
                ModalityTabs(agenticActive: agenticTab,
                             onChat: { store.section = .chat },
                             onAgentic: { store.section = .agentic })
            }
            .padding(.horizontal, 12).padding(.top, 36).padding(.bottom, 10)

            if agenticTab {
                agenticNav
            } else {
                chatNav
            }

            Spacer(minLength: 0)
            UpdateBanner()
            AccountRow()
        }
    }

    private var chatNav: some View {
        VStack(spacing: 0) {
            VStack(spacing: 1) {
                navRow("New chat", "square.and.pencil", active: false, action: store.newConversation)
                navRow("Pulse", "newspaper", active: store.section == .pulse) { store.goToPulse() }
                navRow("Veins", "rectangle.split.3x1", active: store.section == .veins) { store.section = .veins }
                navRow("Journal", "book.closed", active: store.section == .journal) { store.section = .journal }
                navRow("Memory", "tray.full", active: store.section == .memory) { store.section = .memory }
                navRow("Plugins", "shippingbox", active: store.section == .plugins) { store.section = .plugins }
                navRow("MCP", "puzzlepiece.extension", active: store.section == .mcp) { store.section = .mcp }
            }
            .padding(.horizontal, 8)

            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                TextField("Search", text: $search).textFieldStyle(.plain).font(.system(size: 13))
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
            .padding(.horizontal, 8).padding(.top, 8).padding(.bottom, 2)

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 1) {
                    ForEach(store.sidebarGroups(search: search)) { group in
                        Text(group.title.uppercased())
                            .font(.system(size: 10, weight: .semibold)).tracking(0.5)
                            .foregroundStyle(Theme.textSecondary.opacity(0.7))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 10).padding(.top, 14).padding(.bottom, 4)
                        ForEach(group.convos) { convo in
                            SidebarRow(convo: convo,
                                       selected: store.section == .chat && convo.id == store.selectedID,
                                       onSelect: { store.select(convo.id) },
                                       onPin: { store.togglePin(convo.id) },
                                       onDelete: { store.deleteConversation(convo.id) })
                        }
                    }
                }
                .padding(.horizontal, 8).padding(.bottom, 10)
            }
        }
    }

    private var agenticNav: some View {
        VStack(spacing: 0) {
            VStack(spacing: 1) {
                navRow("Default Schedules", "clock.arrow.2.circlepath", active: true, action: {})
            }
            .padding(.horizontal, 8)
            Spacer()
        }
    }

    private func navRow(_ title: String, _ icon: String, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: icon).font(.system(size: 14)).frame(width: 18)
                Text(title).font(.system(size: 13, weight: .medium))
                Spacer()
            }
            .foregroundStyle(active ? Theme.textPrimary : Theme.textPrimary.opacity(0.82))
            .padding(.horizontal, 10).padding(.vertical, 7)
            .background(active ? Theme.surfaceHover : Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Top-of-sidebar modality tabs (Chat / Agentic), built to take a 3rd later.
private struct ModalityTabs: View {
    let agenticActive: Bool
    var onChat: () -> Void
    var onAgentic: () -> Void
    var body: some View {
        HStack(spacing: 3) {
            tab("Chat", "message", active: !agenticActive, action: onChat)
            tab("Agentic", "slider.horizontal.3", active: agenticActive, action: onAgentic)
        }
        .padding(3)
        .background(Theme.bg)
        .clipShape(RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline, lineWidth: 1))
    }
    private func tab(_ title: String, _ icon: String, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: icon).font(.system(size: 12, weight: .medium))
                Text(title).font(.system(size: 12, weight: .medium))
            }
            .foregroundStyle(active ? Theme.textPrimary : Theme.textSecondary)
            .frame(maxWidth: .infinity).padding(.vertical, 5)
            .background(active ? Theme.surfaceHover : Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 7))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Bottom-of-sidebar account row — name from config, with the Settings affordance.
private struct AccountRow: View {
    @EnvironmentObject var config: ConfigStore
    var body: some View {
        let name = config.ownerName
        HStack(spacing: 8) {
            Circle().fill(Theme.accent).frame(width: 22, height: 22)
                .overlay {
                    if let initial = name?.first {
                        Text(String(initial).uppercased())
                            .font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
                    } else {
                        Image(systemName: "person.fill")
                            .font(.system(size: 10)).foregroundStyle(.white)
                    }
                }
            Text(name ?? "Account").font(.system(size: 13, weight: .medium))
            Spacer()
            SettingsLink {
                Image(systemName: "gearshape").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            .buttonStyle(.plain).help("Settings (⌘,)")
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .overlay(Rectangle().fill(Theme.hairline).frame(height: 1), alignment: .top)
    }
}

struct SidebarRow: View {
    let convo: Conversation
    let selected: Bool
    var onSelect: () -> Void = {}
    var onPin: () -> Void = {}
    var onDelete: () -> Void = {}
    @State private var hovering = false
    var body: some View {
        HStack(spacing: 8) {
            Text(convo.title).font(.system(size: 13)).lineLimit(1).foregroundStyle(Theme.textPrimary)
            Spacer(minLength: 0)
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
        .padding(.horizontal, 10).padding(.vertical, 7)
        // Only the active chat gets a fill, and it's accent-tinted so it clearly reads as selected
        // (a faint grey was easy to miss). Hover only reveals the ⋯ menu — no hover background, since
        // macOS drops onHover-exit events and would leave rows stuck looking selected.
        .background(selected ? Theme.accent.opacity(0.20) : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
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
                        // 36 top clearance — the thread scrolls under the hidden title bar,
                        // which otherwise clips the first message.
                        .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 26)
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
        .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 26)
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
                        Label("Vera isn't connected — set up the connection", systemImage: "bolt.horizontal")
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
