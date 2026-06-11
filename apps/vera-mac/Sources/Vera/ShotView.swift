import SwiftUI

/// Render-safe mirror of the app for headless screenshots (ImageRenderer can't render
/// ScrollView contents or TextField). Renders the icon rail + the chosen surface.
struct ShotView: View {
    let store: ChatStore
    var section: AppSection = .chat
    var emptyChat: Bool = false
    var attachDemo: Bool = false

    var body: some View {
        HStack(spacing: 0) {
            sidebar.frame(width: 268).background(Theme.sidebar)
            Rectangle().fill(Theme.hairline).frame(width: 1)
            Group {
                switch section {
                case .pulse: pulse
                case .veins: veinsBoard
                case .journal: journal
                case .memory: memory
                case .plugins: pluginsBoard
                case .mcp: mcpBoard
                case .agentic: agenticBoard
                default: if emptyChat { emptyChatShot } else { chat }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.bg)
        }
        .frame(width: 1180, height: 760)
        .foregroundStyle(Theme.textPrimary)
        .environment(\.colorScheme, .dark)
    }

    /// Render-safe stand-in for a Toggle (ImageRenderer can't draw the live switch).
    private struct StatePill: View {
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
        m.append(Message(role: .assistant, text: "Happy to line up the next batch — which direction?", ask: VeraAsk.mock()))
        m.append(Message(role: .assistant, text: "Sketched the label mark — open it in the Canvas to tweak.", artifacts: [Artifact.mock()]))
        m.append(Message.assistant(from: "The downtown farmers market kicks off its 2026 season today.\n\n**Sources** https://www.springfield-downtown.example/news_detail.php https://www.heraldpress.example/story/news/local/2026/06/03/events/90354139007/ https://www.localtv.example/news/downtown-market-2026-schedule.html\n\nLet me know if you want the full vendor list."))
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
                    shotTab("Chat", "message", active: true)
                    shotTab("Agentic", "slider.horizontal.3", active: false)
                }
                .padding(3).background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 9))
                .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline, lineWidth: 1))
            }
            .padding(.horizontal, 12).padding(.top, 16).padding(.bottom, 10)

            VStack(spacing: 1) {
                shotNav("New chat", "square.and.pencil", active: false)
                shotNav("Pulse", "newspaper", active: false)
                shotNav("Veins", "rectangle.split.3x1", active: section == .veins)
                shotNav("Journal", "book.closed", active: section == .journal)
                shotNav("Memory", "tray.full", active: false)
                shotNav("Plugins", "shippingbox", active: section == .plugins)
                shotNav("MCP", "puzzlepiece.extension", active: false)
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

    // Static Agentic board for screenshots — the Default Schedules section over mock jobs
    // (render-safe: StatePill instead of a live Toggle).
    private var agenticBoard: some View {
        let jobs = SchedulerJob.mock()
        return VStack(spacing: 0) {
            HStack {
                Text("Agentic").font(.system(size: 22, weight: .bold))
                Text("\(jobs.count)").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface).clipShape(Capsule())
                Spacer()
                Text("what Vera runs on her own").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 8)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)
            VStack(alignment: .leading, spacing: 22) {
                SectionBox(title: "Default Schedules") {
                    ForEach(jobs) { job in
                        RowCard {
                            Circle()
                                .fill(job.lastRunOK == nil ? Theme.textSecondary.opacity(0.5)
                                      : (job.lastRunOK == true ? Color(red: 0.36, green: 0.78, blue: 0.5)
                                                               : Color(red: 0.92, green: 0.42, blue: 0.38)))
                                .frame(width: 7, height: 7)
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 6) {
                                    Text(job.label).font(.system(size: 14, weight: .semibold))
                                    if job.envLocked {
                                        Image(systemName: "lock.fill").font(.system(size: 10))
                                            .foregroundStyle(Theme.textSecondary)
                                    }
                                }
                                Text(shotSubline(job)).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                            }
                            Spacer(minLength: 12)
                            Image(systemName: "play.circle").font(.system(size: 15)).foregroundStyle(Theme.textSecondary)
                            Image(systemName: "pencil").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                            StatePill(on: job.enabled)
                        }
                    }
                }
            }
            .padding(.horizontal, 28).padding(.vertical, 18)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    private func shotSubline(_ job: SchedulerJob) -> String {
        var parts = [cronSummary(job.cron)]
        if let last = job.lastRunAt {
            parts.append("last \(relativeTime(last)) · \(job.lastRunOK == false ? "failed" : "ok")")
        } else {
            parts.append("never run")
        }
        if job.enabled, let next = job.nextRun { parts.append("next \(relativeTime(next))") }
        return parts.joined(separator: "  ·  ")
    }

    // Static Veins board for screenshots — the vein catalog over mock entries
    // (render-safe: StatePill instead of live Toggles).
    private var veinsBoard: some View {
        let entries = VeinEntry.mock()
        return VStack(spacing: 0) {
            HStack {
                Text("Veins").font(.system(size: 22, weight: .bold))
                Text("\(entries.filter(\.enabled).count)/6")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface).clipShape(Capsule())
                Spacer()
                Text("the ambient watches pinned above the Pulse feed")
                    .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
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
                    Text(e.label).font(.system(size: 15, weight: .semibold))
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
            Text(e.blurb).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(e.requires.filter { !$0.met }, id: \.label) { req in
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle").font(.system(size: 10))
                    Text("Requires \(req.label) — \(req.detail)").font(.system(size: 11, weight: .medium))
                }
                .foregroundStyle(.orange)
                .padding(.horizontal, 9).padding(.vertical, 4)
                .background(Color.orange.opacity(0.12)).clipShape(Capsule())
            }
            HStack(spacing: 10) {
                Text("Configure").font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(Theme.surfaceHover).clipShape(Capsule())
                if let job = e.jobs.first, e.enabled {
                    Text(job.cron).font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Theme.textSecondary)
                }
                Spacer()
            }
            .padding(.top, 2)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
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
                Text("\(entries.filter(\.enabled).count)/\(entries.count)")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface).clipShape(Capsule())
                Spacer()
                Text("what Vera is connected to").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
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
                    Text(e.displayName).font(.system(size: 15, weight: .semibold))
                    HStack(spacing: 5) {
                        Circle().fill(shotStatusColor(e)).frame(width: 6, height: 6)
                        Text(shotStatusText(e)).font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
                Spacer(minLength: 8)
                if e.configured { StatePill(on: e.enabled) }
            }
            Text(e.unlocksLine).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            if let pairing = e.pairing, pairing.active {
                HStack(spacing: 6) {
                    Image(systemName: "link").font(.system(size: 10))
                    Text("Paired — \(pairing.label)").font(.system(size: 11, weight: .medium))
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
            Text(e.configured ? "Configure" : "Add")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(e.configured ? Theme.textPrimary : .white)
                .padding(.horizontal, 14).padding(.vertical, 6)
                .background(e.configured ? Theme.surfaceHover : Theme.accent)
                .clipShape(Capsule())
                .padding(.top, 2)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
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
        case "error": e.lastTestDetail.isEmpty ? "Error" : "Error — \(e.lastTestDetail)"
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
                Text("3").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface).clipShape(Capsule())
                Spacer()
                Text("what Vera can use").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
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
