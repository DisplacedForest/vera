import SwiftUI
import AppKit

/// Headless screenshot helper — renders a surface to a PNG with no window, no screen-capture
/// permission needed. Used for design review (shots dropped into iCloud).
@MainActor
enum Shot {
    static func render(view: String, to path: String) {
        let store = ChatStore()
        let size = CGSize(width: 1180, height: 760)

        let content: AnyView
        if view == "voice" {
            let voice = VoiceSession.preview(state: .listening, level: 0.6, store: store)
            content = AnyView(
                VoiceView()
                    .environmentObject(voice)
                    .frame(width: size.width, height: size.height)
                    .background(Color.black)
            )
        } else if view == "pulse-detail" {
            content = AnyView(
                ShotDetailView(card: .deepMock())
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg)
            )
        } else if view == "blocks" {
            let demo = """
            Here's how Openda's output stacks up — and why the Juventus dip is system, not talent. [1]

            ```vera:stats
            {"cards":[{"value":"33","label":"goals","sub":"69 Bundesliga games"},{"value":"0.23","label":"G+A / 90","sub":"at Juventus"},{"value":"6.64","label":"xG","sub":"this season"}]}
            ```

            | Club | Goals | Apps | Mins/Goal |
            | --- | --- | --- | --- |
            | Leipzig | 14 | 20 | 128 |
            | Juventus | 2 | 34 | 1500 |

            ```vera:chart
            {"type":"groupedBar","title":"League goals by season","yLabel":"goals","series":[{"name":"Openda","points":[{"x":"22-23","y":11},{"x":"23-24","y":14},{"x":"24-25","y":2}]}]}
            ```

            Bottom line: a system that fit his runs, and one that didn't. [1,2]
            """
            let demoSources = [PulseSource(n: 1, title: "BBC Sport", url: "https://www.bbc.co.uk/sport"),
                               PulseSource(n: 2, title: "The Athletic", url: "https://theathletic.com")]
            content = AnyView(
                VStack(alignment: .leading, spacing: 16) {
                    MessageRow(message: Message(role: .assistant, text: demo, sources: demoSources))
                }
                .padding(24).frame(width: size.width, height: size.height, alignment: .top)
                .background(Theme.bg).environmentObject(store)
            )
        } else if view == "pulse-chat" {
            let card = PulseCard.deepMock()
            content = AnyView(
                VStack(alignment: .leading, spacing: 16) {
                    MessageRow(message: Message(role: .assistant, text: card.body, pulse: card))
                    MessageRow(message: Message(role: .user, text: "Who would you target to replace Anderson?"))
                }
                .padding(24)
                .frame(width: size.width, height: size.height, alignment: .top)
                .background(Theme.bg)
                .environmentObject(store)
            )
        } else if view == "pulse-nominal" || view == "pulse-status" {
            // The pinned-vein chip row above a (trimmed) feed so the chips stay on-screen.
            // nominal = no status cards (System chip quiet); status = lit (dot + count).
            let feed = Array(PulseCard.mock().prefix(2))
            store.pulseCards = view == "pulse-status" ? feed + PulseCard.statusMock() : feed
            content = AnyView(
                ShotView(store: store, section: .pulse)
                    .environmentObject(store)
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg)
            )
        } else if view == "pulse-vein" {
            // The System vein overlay — status tiles grouped by category.
            content = AnyView(
                ShotVeinView(vein: PulseVein.mock()[0], cards: PulseCard.statusMock())
                    .environmentObject(store)
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg)
            )
        } else if view == "pulse-media" {
            // The weekly media-curation digest — per-row Add/Skip + Add all/Skip all.
            content = AnyView(
                VStack(alignment: .leading, spacing: 16) {
                    Text(PulseCard.mediaDigestMock().title).font(.system(size: 24, weight: .bold))
                        .foregroundStyle(Theme.textPrimary)
                    MediaDigestView(card: .mediaDigestMock())
                }
                .padding(28).frame(width: size.width, height: size.height, alignment: .top)
                .background(Theme.bg).environmentObject(store)
            )
        } else if view == "pulse-update" {
            // The available-stack-updates digest — per-row Confirm-to-apply, grouped by source.
            content = AnyView(
                VStack(alignment: .leading, spacing: 16) {
                    Text(PulseCard.updateDigestMock().title).font(.system(size: 24, weight: .bold))
                        .foregroundStyle(Theme.textPrimary)
                    UpdatesDigestView(card: .updateDigestMock())
                }
                .padding(28).frame(width: size.width, height: size.height, alignment: .top)
                .background(Theme.bg).environmentObject(store)
            )
        } else if view == "pulse-groom" {
            // The memory-tending audit detail — the real diff + restore / undo affordances.
            content = AnyView(
                VStack(alignment: .leading, spacing: 16) {
                    Text(PulseCard.groomMock().title).font(.system(size: 24, weight: .bold))
                        .foregroundStyle(Theme.textPrimary)
                    GroomChangeSetView(card: .groomMock())
                }
                .padding(28).frame(width: size.width, height: size.height, alignment: .top)
                .background(Theme.bg).environmentObject(store)
            )
        } else if view == "plugins-add" {
            content = AnyView(
                ShotPluginSheetView()
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg.opacity(0.94))
            )
        } else if view == "plugins-consent" {
            let entry = PluginEntry.mock().first { $0.id == "home_assistant" } ?? PluginEntry.mock()[0]
            content = AnyView(
                ConsentSheet(entry: entry, feature: entry.features[0], plugins: PluginsStore())
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg.opacity(0.94))
                    .environment(\.colorScheme, .dark)
                    .foregroundStyle(Theme.textPrimary)
            )
        } else if view == "settings" {
            content = AnyView(
                SettingsShotView()
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg)
            )
        } else if view == "onboarding" {
            content = AnyView(
                OnboardingShotView()
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg)
            )
        } else if view == "update" {
            content = AnyView(
                UpdateShotView()
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg)
            )
        } else {
            let section: AppSection
            switch view {
            case "pulse": section = .pulse
            case "veins": section = .veins
            case "journal": section = .journal; store.journalEntries = JournalEntry.mock()
            case "memory": section = .memory
            case "plugins": section = .plugins
            case "mcp": section = .mcp
            case "agentic": section = .agentic
            default: section = .chat
            }
            content = AnyView(
                ShotView(store: store, section: section,
                         emptyChat: view == "empty" || view == "attach",
                         attachDemo: view == "attach")
                    .environmentObject(store)
                    .frame(width: size.width, height: size.height)
                    .background(Theme.bg)
            )
        }

        let renderer = ImageRenderer(content: content)
        renderer.scale = 2.0
        guard let nsImage = renderer.nsImage,
              let tiff = nsImage.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:]) else {
            FileHandle.standardError.write(Data("render failed\n".utf8))
            exit(1)
        }
        do {
            try png.write(to: URL(fileURLWithPath: path))
            print("wrote \(path)")
            exit(0)
        } catch {
            FileHandle.standardError.write(Data("write failed: \(error)\n".utf8))
            exit(1)
        }
    }
}

/// Render-safe mirror of the plugin Add sheet (ImageRenderer can't draw live TextFields) —
/// the registry-generated field editor with a secret shown as set-without-echo and an
/// inline Test result.
private struct ShotPluginSheetView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                PluginLogo(id: "grocy", size: 28)
                Text("Add Grocy").font(.system(size: 16, weight: .semibold))
            }
            .padding(16)
            Divider().overlay(Theme.hairline)
            VStack(alignment: .leading, spacing: 14) {
                fieldBox("Base URL", value: "http://192.0.2.10:9283", secret: false)
                fieldBox("API key", value: "•••• (set, leave blank to keep)", secret: true)
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Color(red: 0.36, green: 0.78, blue: 0.5))
                    Text("Grocy 4.6.0").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
            }
            .padding(16)
            Divider().overlay(Theme.hairline)
            HStack(spacing: 10) {
                pill("Test", filled: false)
                Spacer()
                pill("Cancel", filled: false)
                pill("Save & Enable", filled: true)
            }
            .padding(12)
        }
        .frame(width: 480)
        .background(Theme.bg)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
        .foregroundStyle(Theme.textPrimary)
        .environment(\.colorScheme, .dark)
    }

    private func fieldBox(_ label: String, value: String, secret: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 13, weight: .medium))
            Text(value).font(.system(size: 12))
                .foregroundStyle(secret ? Theme.textSecondary : Theme.textPrimary)
                .padding(.horizontal, 8).padding(.vertical, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.hairline, lineWidth: 1))
        }
    }

    private func pill(_ title: String, filled: Bool) -> some View {
        Text(title).font(.system(size: 12, weight: .semibold))
            .foregroundStyle(filled ? .white : Theme.textPrimary)
            .padding(.horizontal, 14).padding(.vertical, 6)
            .background(filled ? Theme.accent : Theme.surfaceHover)
            .clipShape(Capsule())
    }
}

/// Render-safe mirror of the Pulse article detail (no ScrollView / Markdown, which ImageRenderer
/// can't draw) — uses the same block model + citation chips / inline images / sources row.
struct ShotDetailView: View {
    let card: PulseCard
    private var blocks: [PulseBlock] {
        pulseBlocks(PulseMarkers.stripSourcesSection(card.body), images: card.inlineImages)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Color(hex: card.tint)?.frame(height: 120).frame(maxWidth: .infinity)
            VStack(alignment: .leading, spacing: 16) {
                Text(card.title).font(.system(size: 26, weight: .bold)).foregroundStyle(Theme.textPrimary)
                ForEach(blocks) { block in
                    switch block {
                    case .paragraph(_, let text, let refs):
                        VStack(alignment: .leading, spacing: 8) {
                            Text(text.strippedMarkdown()).font(.system(size: 15)).foregroundStyle(Theme.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                            let chips = refs.compactMap { n in card.sourceList.first { $0.n == n } }
                            if !chips.isEmpty { HStack(spacing: 6) { ForEach(chips) { CitationChip(source: $0) } } }
                        }
                    case .image(let im):
                        PulseInlineImageView(image: im)
                    case .chart(_, let spec):
                        ChartBlockView(spec: spec)
                    case .stats(_, let cards):
                        StatCardsView(cards: cards)
                    }
                }
                if !card.sourceList.isEmpty { SourcesRow(sources: card.sourceList) }
            }
            .padding(24).frame(maxWidth: 720, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
            Spacer()
        }
    }
}

/// Render-safe mirror of the Pulse vein overlay — no ScrollView, so ImageRenderer captures
/// the text-forward status tiles + their confirm affordance.
struct ShotVeinView: View {
    let vein: PulseVein
    let cards: [PulseCard]
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Image(systemName: vein.icon).font(.system(size: 18, weight: .semibold))
                Text(vein.label).font(.system(size: 22, weight: .bold))
            }
            .foregroundStyle(Theme.textPrimary)
            // The System vein groups by category; other veins render flat.
            if vein.kind == "status" {
                ForEach(PulseCategory.allCases) { cat in
                    let group = cards.filter { PulseCategory.of($0) == cat }
                    if !group.isEmpty {
                        HStack(spacing: 8) {
                            Image(systemName: cat.icon).font(.system(size: 13, weight: .semibold))
                            Text(cat.title).font(.system(size: 14, weight: .semibold))
                        }
                        .foregroundStyle(Theme.textSecondary).padding(.top, 6)
                        ForEach(group) { StatusCardTile(card: $0) }
                    }
                }
            } else {
                ForEach(cards) { StatusCardTile(card: $0) }
            }
        }
        .padding(24).frame(maxWidth: 720, alignment: .leading)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Theme.bg)
    }
}

/// Render-safe mirror of the Settings window (ImageRenderer can't draw live TextFields or the
/// tabbed Settings chrome) — Connection tab with sample values and an inline test result.
struct SettingsShotView: View {
    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 3) {
                    shotTab("Connection", "link", active: true)
                    shotTab("Model", "cpu", active: false)
                    shotTab("Services", "server.rack", active: false)
                    shotTab("Identity", "person", active: false)
                }
                .padding(3).background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 9))
                .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline, lineWidth: 1))

                VStack(alignment: .leading, spacing: 10) {
                    Text("OPEN WEBUI").font(.system(size: 10, weight: .semibold)).tracking(0.5)
                        .foregroundStyle(Theme.textSecondary)
                    fieldRow("Base URL", "http://my-owui-host:6590")
                    fieldRow("API key", "••••••••••••••••")
                    fieldRow("Email", "you@example.com")
                    fieldRow("Password", "••••••••")
                    HStack(spacing: 10) {
                        Text("Test connection").font(.system(size: 12, weight: .medium))
                            .padding(.horizontal, 12).padding(.vertical, 5)
                            .background(Theme.surfaceHover).clipShape(RoundedRectangle(cornerRadius: 6))
                        Label("Signed in as Jordan", systemImage: "checkmark.circle.fill")
                            .font(.system(size: 12)).foregroundStyle(Color(red: 0.36, green: 0.78, blue: 0.5))
                        Spacer()
                    }
                }
                HStack {
                    Text("Saved").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    Spacer()
                    Text("Save").font(.system(size: 12, weight: .medium))
                        .padding(.horizontal, 14).padding(.vertical, 5)
                        .background(Theme.accent.opacity(0.9)).foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
            .padding(22).frame(width: 560)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .foregroundStyle(Theme.textPrimary)
        .environment(\.colorScheme, .dark)
    }

    private func shotTab(_ title: String, _ icon: String, active: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 11, weight: .medium))
            Text(title).font(.system(size: 12, weight: .medium))
        }
        .foregroundStyle(active ? Theme.textPrimary : Theme.textSecondary)
        .frame(maxWidth: .infinity).padding(.vertical, 5)
        .background(active ? Theme.surfaceHover : Color.clear)
        .clipShape(RoundedRectangle(cornerRadius: 7))
    }

    private func fieldRow(_ label: String, _ value: String) -> some View {
        HStack(spacing: 10) {
            Text(label).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                .frame(width: 90, alignment: .trailing)
            Text(value).font(.system(size: 12))
                .padding(.horizontal, 8).padding(.vertical, 5)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.hairline, lineWidth: 1))
        }
    }
}

/// Render-safe mirror of the first-run onboarding sheet.
struct OnboardingShotView: View {
    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 12) {
                    VeraMark(size: 28)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Welcome to Vera").font(.system(size: 20, weight: .semibold))
                        Text("Point the app at your Open WebUI and vera-api, and you're chatting.")
                            .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    }
                }
                VStack(alignment: .leading, spacing: 10) {
                    fieldRow("Open WebUI URL", "http://my-owui-host:6590")
                    fieldRow("Open WebUI email", "you@example.com")
                    fieldRow("Open WebUI password", "••••••••")
                    fieldRow("Open WebUI API key", "••••••••••••••••")
                    fieldRow("Model id (as registered in OWUI)", "your-vera-model")
                    fieldRow("vera-api URL (optional)", "http://my-api-host:8089")
                }
                HStack {
                    Spacer()
                    Text("Skip for now").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    Text("Test & Connect").font(.system(size: 12, weight: .medium))
                        .padding(.horizontal, 14).padding(.vertical, 6)
                        .background(Theme.accent.opacity(0.9)).foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
            .padding(24).frame(width: 480)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .foregroundStyle(Theme.textPrimary)
        .environment(\.colorScheme, .dark)
    }

    private func fieldRow(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.textSecondary)
            Text(value).font(.system(size: 12))
                .padding(.horizontal, 8).padding(.vertical, 5)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.hairline, lineWidth: 1))
        }
    }
}

/// Render-safe update surfaces: the sidebar banner (with a mock newer release) and the
/// About versions block with a minor-version mismatch showing.
@MainActor
struct UpdateShotView: View {
    private let checker: UpdateChecker

    init() {
        let c = UpdateChecker()
        c.available = ReleaseInfo(
            tag_name: "v0.2.0",
            html_url: "https://github.com/DisplacedForest/vera/releases/latest",
            body: nil,
            assets: [.init(name: "Vera.app.zip", browser_download_url: "https://example.invalid/Vera.app.zip")])
        checker = c
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            Text("Sidebar banner").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
            UpdateBanner().environmentObject(checker).frame(width: 300)

            Text("Settings — About").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
            VStack(alignment: .leading, spacing: 10) {
                HStack { Text("App").foregroundStyle(Theme.textSecondary); Spacer(); Text("0.1.0") }
                HStack { Text("vera-api").foregroundStyle(Theme.textSecondary); Spacer(); Text("0.2.0") }
                Label("App and server minor versions differ — update the older side when convenient.",
                      systemImage: "exclamationmark.triangle")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            .font(.system(size: 13))
            .padding(14).frame(width: 300)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline, lineWidth: 1))

            Spacer(minLength: 0)
        }
        .padding(36)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .foregroundStyle(Theme.textPrimary)
        .environment(\.colorScheme, .dark)
    }
}
