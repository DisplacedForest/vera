import SwiftUI

/// Render-safe mirror of the app for headless screenshots (ImageRenderer can't render
/// ScrollView contents or TextField). Renders the icon rail + the chosen surface.
struct ShotView: View {
    let store: ChatStore
    var section: AppSection = .chat
    var emptyChat: Bool = false
    var attachDemo: Bool = false
    // Veins and Plugins/MCP no longer have sidebar destinations — they render in their new homes.
    var chrome: ShotChrome = .window

    /// Which framing the shot renders: the main window, the Veins sheet over Pulse, or a Settings tab.
    enum ShotChrome: Equatable { case window, veinsSheet, settings(SettingsTab) }

    var body: some View {
        Group {
            switch chrome {
            case .window: windowBody
            case .veinsSheet: veinsSheetShot
            case .settings(let tab): settingsShot(tab)
            }
        }
        .frame(width: 1180, height: 760)
        .foregroundStyle(Theme.textPrimary)
    }

    private var windowBody: some View {
        HStack(spacing: 0) {
            sidebar.frame(width: 268).background(Theme.sidebar)
            Rectangle().fill(Theme.hairline).frame(width: 1)
            Group {
                switch section {
                case .pulse: pulse
                case .journal: journal
                case .memory: memory
                case .agentic: agenticBoard
                default: if emptyChat { emptyChatShot } else { chat }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.bg)
        }
    }

    /// The Veins manager as it now appears: a sheet card (Done bar + the veins board) over a dimmed Pulse.
    private var veinsSheetShot: some View {
        ZStack {
            windowBodyForPulse.opacity(0.5)
            Color.black.opacity(0.35)
            VStack(spacing: 0) {
                HStack {
                    Spacer()
                    Text("Done").font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.accent)
                }
                .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 4)
                veinsBoard
            }
            .frame(width: 900, height: 600)
            .background(Theme.bg)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    private var windowBodyForPulse: some View {
        HStack(spacing: 0) {
            sidebar.frame(width: 268).background(Theme.sidebar)
            Rectangle().fill(Theme.hairline).frame(width: 1)
            pulse.frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.bg)
        }
    }

    /// A Settings tab (Plugins or MCP) — the Settings window chrome (tab bar) over the moved board.
    private func settingsShot(_ tab: SettingsTab) -> some View {
        ZStack {
            Color.black.opacity(0.45)
            VStack(spacing: 0) {
                settingsTabBar(tab)
                Divider().overlay(Theme.hairline)
                Group { tab == .mcp ? AnyView(mcpBoard) : AnyView(pluginsBoard) }
            }
            .frame(width: 860, height: 640, alignment: .top)
            .background(Theme.bg)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    private func settingsTabBar(_ active: SettingsTab) -> some View {
        let tabs: [(SettingsTab, String, String)] = [
            (.connection, "Connection", "link"), (.model, "Model", "cpu"),
            (.services, "Services", "server.rack"), (.plugins, "Plugins", "shippingbox"),
            (.mcp, "MCP", "puzzlepiece.extension"), (.identity, "Identity", "person"),
            (.about, "About", "info.circle"),
        ]
        return HStack(spacing: 22) {
            ForEach(tabs, id: \.0) { t in
                VStack(spacing: 3) {
                    Image(systemName: t.2).font(.system(size: 16))
                    Text(t.1).font(.system(size: 11))
                }
                .foregroundStyle(t.0 == active ? Theme.accent : Theme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity).padding(.vertical, 10).background(Theme.surface)
    }

    /// Render-safe stand-in for a Toggle (ImageRenderer can't draw the live switch).
    fileprivate struct StatePill: View {
        let on: Bool
        var body: some View {
            Text(on ? "On" : "Off").font(.system(size: 11, weight: .semibold))
                .foregroundStyle(on ? Theme.accent : Theme.textSecondary)
                .padding(.horizontal, 9).padding(.vertical, 3)
                .background((on ? Theme.accent : Theme.textSecondary).opacity(0.16)).clipShape(Capsule())
        }
    }

    // Demo messages for the chat screenshot — includes a Vera structured-question card.
    private var shotMessages: [Message] {
        var m = store.selected?.messages ?? []
        m.append(Message(role: .assistant, text: """
        <details type="tool_calls" done="true" name="kitchen_status"><summary>Tool Executed</summary>"low: spaghetti, chicken nuggets, pork sausage"</details>
        You're low on a few **staples**:

        - Spaghetti
        - Chicken nuggets
        - Pork sausage

        With those back you could do a quick `Bolognese`. Want me to add them to the list?
        """))
        m.append(Message(role: .assistant, text: "Happy to line up the next batch. Which direction?", ask: VeraAsk.mock()))
        m.append(Message(role: .assistant, text: "Sketched the label mark. Open it in the Canvas to tweak.", artifacts: [Artifact.mock()]))
        m.append(Message.assistant(from: "The downtown farmers market kicks off its 2026 season today.\n\n**Sources** https://www.springfield-downtown.example/news_detail.php https://www.heraldpress.example/story/news/local/2026/06/03/events/90354139007/ https://www.localtv.example/news/downtown-market-2026-schedule.html\n\nLet me know if you want the full vendor list."))
        // A deep-research style cited reply — exercises chips, Sources-section stripping, and the row.
        m.append(Message.assistant(
            from: "Cover crops measurably improve vineyard soil health [1]. Clover mixes also fix nitrogen between rows [2].\n\n**Sources**\n1. Vineyard soil study\n2. Cover crop guide",
            sources: [PulseSource(n: 1, title: "Vineyard soil study", url: "https://viticulture.example/soil-health"),
                      PulseSource(n: 2, title: "Cover crop guide", url: "https://extension.example/cover-crops")]))
        return m
    }

    // Render-safe mirror of the empty-chat welcome (ImageRenderer can't draw the live TextField).
    private var emptyChatShot: some View {
        VStack(spacing: 0) {
            Spacer()
            VStack(spacing: 22) {
                HStack(spacing: 12) {
                    VeraMark(size: 30)
                    Text("Good afternoon, Jordan").font(.system(size: 28, weight: .semibold))
                }
                VStack(alignment: .leading, spacing: 8) {
                    if attachDemo {
                        HStack(spacing: 10) {
                            RoundedRectangle(cornerRadius: 12)
                                .fill(LinearGradient(colors: [Color(red: 0.85, green: 0.35, blue: 0.2),
                                                              Color(red: 0.96, green: 0.62, blue: 0.32)],
                                                     startPoint: .topLeading, endPoint: .bottomTrailing))
                                .frame(width: 76, height: 76)
                                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
                            shotFileCard(title: "Vendor Platform Proposal (Software and…", badge: "PPTX")
                            shotSkeletonCard
                        }
                    }
                    Text(attachDemo ? "What do you make of these?" : "Message Vera…")
                        .font(.system(size: 14)).foregroundStyle(attachDemo ? Theme.textPrimary : Theme.textSecondary)
                    HStack(spacing: 10) {
                        Image(systemName: "plus").font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.textSecondary)
                            .frame(width: 28, height: 28).background(Theme.surfaceHover).clipShape(Circle())
                        Spacer()
                        Image(systemName: "arrow.up.circle.fill").font(.system(size: 26))
                            .foregroundStyle(attachDemo ? Theme.accent : Theme.textSecondary)
                    }
                }
                .padding(.horizontal, 14).padding(.vertical, 11)
                .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 16))
                .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.hairline, lineWidth: 1))
                HStack(spacing: 8) {
                    ForEach(["What's on today?", "Check the kitchen", "What's the weather?", "Research a topic for me"], id: \.self) { s in
                        Text(s).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
                            .padding(.horizontal, 12).padding(.vertical, 7)
                            .background(Theme.surface).clipShape(Capsule())
                            .overlay(Capsule().stroke(Theme.hairline, lineWidth: 1))
                    }
                }
            }
            .frame(maxWidth: 640).padding(.horizontal, 28)
            Spacer(); Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func shotFileCard(title: String, badge: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textPrimary)
                .lineLimit(3).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            Text(badge).font(.system(size: 9, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 6).padding(.vertical, 2).background(Theme.surfaceHover).clipShape(RoundedRectangle(cornerRadius: 4))
        }
        .padding(10).frame(width: 152, height: 76, alignment: .topLeading)
        .background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }

    private var shotSkeletonCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            RoundedRectangle(cornerRadius: 6).fill(Theme.surfaceHover).frame(height: 11)
            RoundedRectangle(cornerRadius: 6).fill(Theme.surfaceHover).frame(width: 92, height: 11)
            Spacer(minLength: 0)
            RoundedRectangle(cornerRadius: 6).fill(Theme.surfaceHover).frame(width: 42, height: 14)
        }
        .padding(10).frame(width: 152, height: 76, alignment: .topLeading)
        .background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            VStack(spacing: 10) {
                HStack(spacing: 8) {
                    VeraMark(size: 18)
                    Text("Vera").font(.system(size: 14, weight: .semibold))
                    Spacer()
                }
                HStack(spacing: 3) {
                    shotTab("Chat", "message", active: section != .agentic)
                    shotTab("Agentic", "slider.horizontal.3", active: section == .agentic)
                }
                .padding(3).background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 9))
                .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline, lineWidth: 1))
            }
            .padding(.horizontal, 12).padding(.top, 16).padding(.bottom, 10)

            if section == .agentic {
                VStack(spacing: 1) {
                    shotNav("Canvas", "point.3.connected.trianglepath.dotted", active: true)
                    shotNav("Activity", "bolt", active: false)
                }
                .padding(.horizontal, 8)
            } else {
                chatSidebarBody
            }
            Spacer()
            HStack(spacing: 8) {
                Circle().fill(Theme.accent).frame(width: 22, height: 22)
                    .overlay(Text("J").font(.system(size: 11, weight: .bold)).foregroundStyle(.white))
                Text("Jordan").font(.system(size: 13, weight: .medium))
                Spacer()
                Image(systemName: "gearshape").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .overlay(Rectangle().fill(Theme.hairline).frame(height: 1), alignment: .top)
        }
    }

    @ViewBuilder
    private var chatSidebarBody: some View {
            VStack(spacing: 1) {
                shotNav("New chat", "square.and.pencil", active: false)
                shotNav("Pulse", "newspaper", active: section == .pulse)
                shotNav("Journal", "book.closed", active: section == .journal)
                shotNav("Memory", "tray.full", active: section == .memory)
            }
            .padding(.horizontal, 8)

            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                Text("Search").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                Spacer()
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
            .padding(.horizontal, 8).padding(.top, 8).padding(.bottom, 2)

            VStack(alignment: .leading, spacing: 1) {
                shotSection("Pinned")
                shotChatRow("Greenhouse irrigation plan", selected: false, pinned: true)
                shotSection("Today")
                shotChatRow("Weather watch", selected: true, pinned: false)
                shotChatRow("Local LLM + AMD GPU news", selected: false, pinned: false)
                shotSection("Previous 7 Days")
                shotChatRow("Sourdough starter notes", selected: false, pinned: false)
                shotChatRow("Server rebuild plan", selected: false, pinned: false)
            }
            .padding(.horizontal, 8).padding(.top, 2)
    }

    private func shotTab(_ title: String, _ icon: String, active: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 12, weight: .medium))
            Text(title).font(.system(size: 12, weight: .medium))
        }
        .foregroundStyle(active ? Theme.textPrimary : Theme.textSecondary)
        .frame(maxWidth: .infinity).padding(.vertical, 5)
        .background(active ? Theme.surfaceHover : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 7))
    }

    private func shotNav(_ title: String, _ icon: String, active: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon).font(.system(size: 14)).frame(width: 18)
            Text(title).font(.system(size: 13, weight: .medium))
            Spacer()
        }
        .foregroundStyle(active ? Theme.textPrimary : Theme.textPrimary.opacity(0.82))
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(active ? Theme.surfaceHover : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func shotSection(_ t: String) -> some View {
        Text(t.uppercased()).font(.system(size: 10, weight: .semibold)).tracking(0.5)
            .foregroundStyle(Theme.textSecondary.opacity(0.75))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12).padding(.top, 14).padding(.bottom, 4)
    }

    private func shotChatRow(_ title: String, selected: Bool, pinned: Bool) -> some View {
        HStack(spacing: 8) {
            Text(title).font(.system(size: 13)).lineLimit(1).foregroundStyle(Theme.textPrimary)
            Spacer(minLength: 0)
            if pinned {
                Image(systemName: "pin.fill").font(.system(size: 9))
                    .foregroundStyle(Theme.textSecondary).rotationEffect(.degrees(45))
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(selected ? Theme.surfaceHover : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var chat: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 22) {
                ForEach(shotMessages) { MessageRow(message: $0) }
            }
            .padding(.horizontal, 28).padding(.vertical, 26)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            HStack(alignment: .bottom, spacing: 10) {
                Text("Message Vera…").font(.system(size: 14)).foregroundStyle(Theme.textSecondary)
                Spacer()
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 26)).foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 14).padding(.vertical, 11)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.hairline, lineWidth: 1))
            .frame(maxWidth: 760)
            .padding(.horizontal, 28).padding(.bottom, 18).padding(.top, 6)
        }
    }

    private var pulse: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Pulse").font(.system(size: 22, weight: .bold))
                Text("\(store.feedCards.count)").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.surface).clipShape(Capsule())
                Spacer()
                Text("today").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            .frame(maxWidth: pulseFeedWidth, alignment: .leading).frame(maxWidth: .infinity)
            .padding(.horizontal, 28).padding(.top, 24).padding(.bottom, 8)
            if !store.pulseVeins.isEmpty {
                VeinChipRow(veins: store.pulseVeins, cards: store.pulseCards)
                    .frame(maxWidth: pulseFeedWidth, alignment: .leading).frame(maxWidth: .infinity)
                    .padding(.horizontal, 28).padding(.bottom, 8)
            }
            PulseGrid(cards: store.feedCards).padding(.horizontal, 28).padding(.vertical, 16)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    private var journal: some View {
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
            .padding(.horizontal, 28).padding(.top, 24).padding(.bottom, 8)
            JournalList(entries: store.journalEntries, archive: store.journalArchive)
                .padding(.horizontal, 28).padding(.vertical, 12)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    private var memory: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Memory").font(.system(size: 22, weight: .bold))
                Text("\(store.memories.count)").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.surface).clipShape(Capsule())
                Spacer()
                Text("what Vera knows about you").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 28).padding(.top, 24).padding(.bottom, 8)
            MemoryList(items: store.memories).padding(.horizontal, 28).padding(.vertical, 12)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    // Static Agentic board for screenshots — the organism map over the mock graph
    // (render-safe: no live controls, animations off, pulses frozen mid-edge).
    private var agenticBoard: some View {
        let graph = AgenticGraph.mock()
        let jobs = Dictionary(uniqueKeysWithValues: SchedulerJob.mock().map { ($0.id, $0) })
        let pulses = [
            CanvasPulse(id: "shot-weather", flowID: "weather", surfaceID: "veins",
                        startedAt: Date().addingTimeInterval(-CanvasPulse.duration / 2)),
            CanvasPulse(id: "shot-heartbeat", flowID: "heartbeat", surfaceID: "memory",
                        startedAt: Date().addingTimeInterval(-CanvasPulse.duration / 2)),
        ]
        return VStack(spacing: 0) {
            HStack {
                Text("Agentic").font(.system(size: 22, weight: .bold))
                InfoTip(text: "Everything Vera runs on her own, as one living system.", size: 13)
                Text("\(graph.flows.count) flows").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 9).padding(.vertical, 3)
                    .background(Theme.surface).clipShape(Capsule())
                Spacer()
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 14)
            // The live canvas scrolls; the shot scales the whole organism to fit its frame.
            let viewport = CGSize(width: 911, height: 666)
            let laid = OrganismLayout(graph: graph, viewport: viewport).size
            let scale = min(1, viewport.width / laid.width, viewport.height / laid.height)
            OrganismMap(graph: graph, jobs: jobs, size: viewport, pulses: pulses, animated: false)
                .scaleEffect(scale, anchor: .topLeading)
                .frame(width: viewport.width, height: viewport.height, alignment: .topLeading)
                .clipped()
                .overlay(Rectangle().fill(Theme.hairline).frame(height: 1), alignment: .top)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    // Static Veins board for screenshots — the vein catalog over mock entries
    // (render-safe: StatePill instead of live Toggles).
    private var veinsBoard: some View {
        let entries = VeinEntry.mock()
        return VStack(spacing: 0) {
            HStack {
                Text("Veins").font(.system(size: 22, weight: .bold))
                InfoTip(text: "The ambient watches pinned above the Pulse feed.", size: 13)
                Text("\(entries.filter(\.enabled).count)/6")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface).clipShape(Capsule())
                Spacer()
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 8)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 380), spacing: 14, alignment: .top)],
                      alignment: .leading, spacing: 14) {
                ForEach(entries) { e in shotVeinCard(e) }
            }
            .padding(.horizontal, 28).padding(.vertical, 18)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    private func shotVeinCard(_ e: VeinEntry) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                RoundedRectangle(cornerRadius: 9).fill(Theme.surfaceHover)
                    .overlay(Image(systemName: e.icon)
                        .font(.system(size: 17)).foregroundStyle(Theme.textPrimary))
                    .frame(width: 40, height: 40)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(e.label).font(.system(size: 15, weight: .semibold))
                        InfoTip(text: e.blurb)
                    }
                    HStack(spacing: 5) {
                        Circle().fill(e.enabled ? Color(red: 0.36, green: 0.78, blue: 0.5)
                                                : Theme.textSecondary.opacity(0.5))
                            .frame(width: 6, height: 6)
                        Text(e.enabled ? "On" : "Off")
                            .font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    }
                }
                Spacer(minLength: 8)
                if e.canEnable || e.enabled { StatePill(on: e.enabled) }
            }
            ForEach(e.requires.filter { !$0.met }, id: \.label) { req in
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle").font(.system(size: 10))
                    Text("Requires \(req.label): \(req.detail)").font(.system(size: 11, weight: .medium))
                        .lineLimit(1).truncationMode(.tail)
                }
                .foregroundStyle(.orange)
                .padding(.horizontal, 9).padding(.vertical, 4)
                .background(Color.orange.opacity(0.12)).clipShape(Capsule())
            }
            Spacer(minLength: 0)
            HStack(spacing: 10) {
                Text("Configure").font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(Theme.surfaceHover).clipShape(Capsule())
                if let job = e.jobs.first, e.enabled {
                    Text(cronSummary(job.cron)).font(.system(size: 11))
                        .foregroundStyle(Theme.textSecondary)
                }
                Spacer()
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: 148)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
    }

    // Static Plugins board for screenshots — the integration store over mock entries
    // (render-safe: StatePill instead of live Toggles).
    private var pluginsBoard: some View {
        let entries = PluginEntry.mock()
        return VStack(spacing: 0) {
            HStack {
                Text("Plugins").font(.system(size: 22, weight: .bold))
                InfoTip(text: "What Vera is connected to: each integration unlocks capabilities across the app.", size: 13)
                Text("\(entries.filter(\.enabled).count)/\(entries.count)")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface).clipShape(Capsule())
                Spacer()
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 8)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 380), spacing: 14, alignment: .top)],
                      alignment: .leading, spacing: 14) {
                ForEach(entries) { e in shotPluginCard(e) }
            }
            .padding(.horizontal, 28).padding(.vertical, 18)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    private func shotPluginCard(_ e: PluginEntry) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                PluginLogo(id: e.id)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(e.displayName).font(.system(size: 15, weight: .semibold))
                        InfoTip(text: e.unlocksLine)
                    }
                    HStack(spacing: 5) {
                        Circle().fill(shotStatusColor(e)).frame(width: 6, height: 6)
                        Text(shotStatusText(e)).font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
                Spacer(minLength: 8)
                if e.configured { StatePill(on: e.enabled) }
            }
            if let pairing = e.pairing, pairing.active {
                HStack(spacing: 6) {
                    Image(systemName: "link").font(.system(size: 10))
                    Text("Paired: \(pairing.label)").font(.system(size: 11, weight: .medium))
                        .lineLimit(1).truncationMode(.tail)
                }
                .foregroundStyle(Theme.accent)
                .padding(.horizontal, 9).padding(.vertical, 4)
                .background(Theme.accent.opacity(0.12)).clipShape(Capsule())
            }
            ForEach(e.features) { f in
                HStack(spacing: 8) {
                    Text(f.label).font(.system(size: 12, weight: .medium))
                    Text("EXPERIMENTAL").font(.system(size: 8, weight: .bold)).tracking(0.5)
                        .foregroundStyle(.orange)
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(Color.orange.opacity(0.15)).clipShape(Capsule())
                    Spacer(minLength: 8)
                    StatePill(on: f.enabled)
                }
                .padding(.horizontal, 10).padding(.vertical, 7)
                .background(Theme.bg.opacity(0.5)).clipShape(RoundedRectangle(cornerRadius: 8))
            }
            Spacer(minLength: 0)
            HStack {
                Text(e.configured ? "Configure" : "Add")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(e.configured ? Theme.textPrimary : .white)
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(e.configured ? Theme.surfaceHover : Theme.accent)
                    .clipShape(Capsule())
                Spacer()
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: 188)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
    }

    private func shotStatusColor(_ e: PluginEntry) -> Color {
        switch e.status {
        case "enabled": Color(red: 0.36, green: 0.78, blue: 0.5)
        case "error": Color(red: 0.92, green: 0.42, blue: 0.38)
        default: Theme.textSecondary
        }
    }

    private func shotStatusText(_ e: PluginEntry) -> String {
        switch e.status {
        case "enabled": "Connected"
        case "error": e.lastTestDetail.isEmpty ? "Error" : "Error: \(e.lastTestDetail)"
        case "configured": "Off"
        default: "Not connected"
        }
    }

    // Static MCP board for screenshots (mock data; no ToolsStore needed).
    private var mcpBoard: some View {
        let demoTools: [ToolEntry] = [
            .init(id: "web_search", name: "Web Search",
                  description: "Autonomous web search (SearXNG + Playwright).", availableToVera: true, lastUsed: Date()),
            .init(id: "home_assistant", name: "Home Assistant",
                  description: "Control and query Home Assistant devices.", availableToVera: false, lastUsed: nil),
        ]
        return VStack(spacing: 0) {
            HStack {
                Text("MCP").font(.system(size: 22, weight: .bold))
                InfoTip(text: "What Vera can use: the tools, functions, and servers available to her.", size: 13)
                Text("3").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface).clipShape(Capsule())
                Spacer()
            }
            .padding(.horizontal, 28).padding(.top, 24).padding(.bottom, 8)
            VStack(alignment: .leading, spacing: 22) {
                ActivitySection(invocations: [
                    .init(label: "knowledge_search", at: Date()), .init(label: "web_search", at: Date())])
                SectionBox(title: "Tools") {
                    ForEach(demoTools) { t in
                        RowCard {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(t.name).font(.system(size: 14, weight: .semibold))
                                Text(t.description).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                            }
                            Spacer(minLength: 12)
                            Image(systemName: "slider.horizontal.3").foregroundStyle(Theme.textSecondary)
                            StatePill(on: t.availableToVera)
                        }
                    }
                }
                SectionBox(title: "Functions") {
                    RowCard {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Adaptive Memory v3").font(.system(size: 14, weight: .semibold))
                            Text("filter").font(.system(size: 10, weight: .medium)).foregroundStyle(Theme.textSecondary)
                                .padding(.horizontal, 7).padding(.vertical, 2).background(Theme.surfaceHover).clipShape(Capsule())
                        }
                        Spacer(minLength: 12)
                        Image(systemName: "slider.horizontal.3").foregroundStyle(Theme.textSecondary)
                        StatePill(on: true)
                    }
                }
                SectionBox(title: "Tool Servers") {
                    Text("No external tool servers connected.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
            }
            .padding(.horizontal, 28).padding(.vertical, 18)
            .frame(maxWidth: 860, alignment: .leading)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }
}

/// Render-safe Agentic detail boards for screenshots: the pulse pipeline, the
/// heartbeat branch fan, and the organism map with the inspector open.
struct AgenticDetailShot: View {
    let variant: String

    var body: some View {
        let graph = AgenticGraph.mock()
        let jobs = Dictionary(uniqueKeysWithValues: SchedulerJob.mock().map { ($0.id, $0) })
        return HStack(spacing: 0) {
            VStack(spacing: 0) {
                header(graph)
                Group {
                    switch variant {
                    case "agentic-pulse":
                        if let flow = graph.flow("pulse") {
                            PulseDrill(flow: flow, graph: graph,
                                       viewport: CGSize(width: 1180, height: 640),
                                       detail: PulseRunClient.mock(), initialExpandedStage: "triage")
                        }
                    case "agentic-heartbeat":
                        if let flow = graph.flow("heartbeat") {
                            HeartbeatDrill(flow: flow, graph: graph,
                                           viewport: CGSize(width: 848, height: 640))
                        }
                    default:
                        let viewport = CGSize(width: 848, height: 666)
                        let laid = OrganismLayout(graph: graph, viewport: viewport).size
                        let scale = min(1, viewport.width / laid.width, viewport.height / laid.height)
                        OrganismMap(graph: graph, jobs: jobs, size: viewport,
                                    selected: "pulse", animated: false)
                            .scaleEffect(scale, anchor: .topLeading)
                            .frame(width: viewport.width, height: viewport.height, alignment: .topLeading)
                            .clipped()
                    }
                }
                .overlay(Rectangle().fill(Theme.hairline).frame(height: 1), alignment: .top)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            if variant.hasPrefix("agentic-inspector"), let flow = inspectorFlow(graph) {
                let events = Array(ActivityEvent.mock().prefix(4))
                InspectorContent(flow: flow, job: inspectorJob(jobs), sched: SchedulerStore(),
                                 events: events,
                                 onEditSchedule: { _ in }, onDrill: {}, onClose: {},
                                 liveControls: false,
                                 initialExpandedEventID: events.dropFirst().first?.id)
                    .frame(width: 332, alignment: .top)
                    .frame(maxHeight: .infinity, alignment: .top)
                    .background(Color(red: 0.118, green: 0.122, blue: 0.129))
                    .overlay(Rectangle().fill(Theme.hairline).frame(width: 1), alignment: .leading)
            }
        }
        .frame(width: 1180, height: 760)
        .background(Theme.bg)
        .foregroundStyle(Theme.textPrimary)
        .environment(\.colorScheme, .dark)
    }

    /// The pulse flow for the inspector shot; the `-long` variant seeds oversized run state
    /// (many gate kills, multi-sentence warnings) to prove sections lay out below long content.
    private func inspectorFlow(_ graph: AgenticGraph) -> GraphFlow? {
        guard var flow = graph.flow("pulse") else { return nil }
        guard variant == "agentic-inspector-long", var st = flow.pulseState else { return flow }
        st.warnings = [
            "Starved run: only 6 of the target 8 cards survived after 3 triage rounds, so the briefing shipped short.",
            "Signals corpus for the Strait of Hormuz watch grew past the fold threshold and was held for manual review rather than auto-merged.",
            "Cover art for 2 cards fell back to source photos because the image service was busy at synthesis time.",
        ]
        st.gates = ["dedup": 11, "freshness": 4, "coherence": 3, "empty": 2, "interest_cap": 5]
        flow.pulseState = st
        return flow
    }

    private func inspectorJob(_ jobs: [String: SchedulerJob]) -> SchedulerJob? {
        guard var job = jobs["pulse"] else { return nil }
        guard variant == "agentic-inspector-long" else { return job }
        job.lastRunDetail = "Signals check tripped 4 of 12 watched indicators this run. Brent crude held above its 90-day band for a third straight session, the Baltic Dry index broke its upper threshold, Taiwan Strait AIS density fell below the quiet-water floor, and the EUR/USD cross-currency basis widened past its alert line. Each trip carries its own multi-source corpus and a recommended follow-up; the longest ran to nine sources and was condensed to a single paragraph for the card."
        return job
    }

    @ViewBuilder
    private func header(_ graph: AgenticGraph) -> some View {
        if variant.hasPrefix("agentic-inspector") {
            HStack(spacing: 10) {
                Text("Agentic").font(.system(size: 22, weight: .bold))
                Text("\(graph.flows.count) flows").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 9).padding(.vertical, 3)
                    .background(Theme.surface).clipShape(Capsule())
                Spacer()
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 14)
        } else {
            let isPulse = variant == "agentic-pulse"
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 10) {
                    HStack(spacing: 5) {
                        Image(systemName: "chevron.left").font(.system(size: 10, weight: .bold))
                        Text("All flows").font(.system(size: 13, weight: .medium))
                    }
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 11).padding(.vertical, 5)
                    .background(Theme.surface).clipShape(Capsule())
                    .overlay(Capsule().stroke(Theme.hairline, lineWidth: 1))
                    Text(isPulse ? "Pulse briefing" : "Heartbeat").font(.system(size: 22, weight: .bold))
                    Text(isPulse ? "Daily 5:00 AM" : "Every 20 min")
                        .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 9).padding(.vertical, 3)
                        .background(Theme.surface).clipShape(Capsule())
                    Spacer()
                }
                HStack(spacing: 7) {
                    Image(systemName: isPulse ? "exclamationmark.triangle" : "clock")
                        .font(.system(size: 11))
                        .foregroundStyle(isPulse ? Color(red: 0.90, green: 0.62, blue: 0.30) : Theme.textSecondary)
                    Text(isPulse
                         ? "Last run injected 6 cards 7 hr ago. starved run: 6/8 cards after 3 triage round(s)."
                         : "Each tick reads HEARTBEAT.md and decides which branches to take.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
