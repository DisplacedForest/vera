import SwiftUI

/// Live state for the Veins pane — vera-api's vein catalog (manifests merged with
/// runtime state). Cards render purely from the API; the app hardcodes no vein list,
/// fields, or option groups, so a new server-side vein appears here with no app change.
@MainActor
final class VeinsStore: ObservableObject {
    enum Phase { case loading, unconfigured, unreachable, unsupported, ready }
    @Published var phase: Phase = .loading
    @Published var entries: [VeinEntry] = []
    @Published var active = 0
    @Published var cap = 6
    @Published var busy: Set<String> = []
    @Published var error: String?

    private var client: VeinsClient?
    var baseDescription: String { client?.base.absoluteString ?? "vera-api" }

    func configure(base: URL?) {
        client = base.map { VeinsClient(base: $0) }
        if client == nil { phase = .unconfigured }
    }

    func refresh() async {
        guard let client else { phase = .unconfigured; return }
        switch await client.fetch() {
        case .unreachable: phase = .unreachable
        case .unsupported: phase = .unsupported
        case .ok(let list, let active, let cap):
            entries = list
            self.active = active
            self.cap = cap
            phase = .ready
        }
    }

    func setEnabled(_ entry: VeinEntry, _ on: Bool) {
        guard let client else { return }
        busy.insert(entry.kind)
        Task {
            if let detail = await client.save(kind: entry.kind, enabled: on) { error = detail }
            await refresh()
            busy.remove(entry.kind)
        }
    }

    func save(kind: String, enabled: Bool?, options: [String: String]?,
              providers: [String: String]?, cron: String?) async -> String? {
        guard let client else { return "vera-api isn't configured" }
        let detail = await client.save(kind: kind, enabled: enabled, options: options,
                                       providers: providers, cron: cron)
        if detail == nil { await refresh() }
        return detail
    }

    func test(kind: String) async -> [(slot: String, ok: Bool, detail: String)] {
        guard let client else { return [("test", false, "vera-api isn't configured")] }
        return await client.test(kind: kind)
    }
}

/// The Veins pane — which ambient watch veins run, each scoped and scheduled to taste.
struct VeinsView: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var store: ChatStore
    @Environment(\.openSettings) private var openSettings
    @StateObject private var veins = VeinsStore()
    @State private var editing: VeinEntry?

    // A vein's unmet requirement points at Plugins, now a Settings tab: select it, then open Settings.
    private func openPlugins() {
        store.settingsTab = .plugins
        openSettings()
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if let e = veins.error { errorRow(e) }
                    content
                }
                .padding(.horizontal, 28).padding(.vertical, 18)
                .frame(maxWidth: 860, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
        .task {
            veins.configure(base: config.resolved?.veraAPIBase)
            await veins.refresh()
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
                await veins.refresh()
            }
        }
        .sheet(item: $editing) { entry in
            VeinSheet(entry: entry, veins: veins,
                      openPlugins: openPlugins)
        }
    }

    private var header: some View {
        HStack {
            Text("Veins").font(.system(size: 22, weight: .bold))
            InfoTip(text: "The ambient watches pinned above the Pulse feed.", size: 13)
            if case .ready = veins.phase {
                Text("\(veins.active)/\(veins.cap)")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.surface).clipShape(Capsule())
            }
            Spacer()
        }
        .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 8)
        .frame(maxWidth: 860, alignment: .leading)
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder private var content: some View {
        switch veins.phase {
        case .loading:
            RowCard {
                ProgressView().controlSize(.small)
                Text("Loading veins…").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
        case .unconfigured:
            statusCard(icon: "gearshape", title: "vera-api isn't configured",
                       note: "Set the vera-api URL in Settings to manage veins.")
        case .unreachable:
            statusCard(icon: "exclamationmark.triangle", title: "vera-api unreachable",
                       note: "Couldn't load the vein catalog from \(veins.baseDescription).", retry: true)
        case .unsupported:
            statusCard(icon: "rectangle.split.3x1", title: "Veins not available",
                       note: "This vera-api doesn't expose the vein catalog yet. Update vera-api to manage veins here.",
                       retry: true)
        case .ready:
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 380), spacing: 14, alignment: .top)],
                      alignment: .leading, spacing: 14) {
                ForEach(veins.entries) { entry in
                    VeinCard(entry: entry, veins: veins,
                             onConfigure: { editing = entry },
                             openPlugins: openPlugins)
                }
            }
        }
    }

    private func errorRow(_ e: String) -> some View {
        HStack {
            Text(e).font(.system(size: 12)).foregroundStyle(.red)
            Spacer()
            Button { veins.error = nil } label: {
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
                Button("Retry") { Task { await veins.refresh() } }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.accent)
            }
        }
    }
}

// MARK: - One vein card

private struct VeinCard: View {
    let entry: VeinEntry
    @ObservedObject var veins: VeinsStore
    var onConfigure: () -> Void
    var openPlugins: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                RoundedRectangle(cornerRadius: 9).fill(Theme.surfaceHover)
                    .overlay(Image(systemName: entry.icon)
                        .font(.system(size: 17)).foregroundStyle(Theme.textPrimary))
                    .frame(width: 40, height: 40)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(entry.label).font(.system(size: 15, weight: .semibold))
                        InfoTip(text: entry.blurb)
                    }
                    HStack(spacing: 5) {
                        Circle().fill(entry.enabled
                                      ? Color(red: 0.36, green: 0.78, blue: 0.5)
                                      : Theme.textSecondary.opacity(0.5))
                            .frame(width: 6, height: 6)
                        Text(entry.enabled ? "On" : "Off")
                            .font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    }
                }
                Spacer(minLength: 8)
                if veins.busy.contains(entry.kind) {
                    ProgressView().controlSize(.small)
                } else if entry.canEnable || entry.enabled {
                    Toggle("", isOn: Binding(get: { entry.enabled },
                                             set: { veins.setEnabled(entry, $0) }))
                        .toggleStyle(.switch).controlSize(.small).labelsHidden()
                        .tint(Theme.accent)
                }
            }

            ForEach(entry.requires.filter { !$0.met }, id: \.label) { req in
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle").font(.system(size: 10))
                    Text("Requires \(req.label): \(req.detail)")
                        .font(.system(size: 11, weight: .medium))
                        .lineLimit(1).truncationMode(.tail)
                        .help("Requires \(req.label): \(req.detail)")
                    if req.integration != nil {
                        Button("Open Plugins") { openPlugins() }
                            .buttonStyle(.plain).font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                    }
                }
                .foregroundStyle(.orange)
                .padding(.horizontal, 9).padding(.vertical, 4)
                .background(Color.orange.opacity(0.12)).clipShape(Capsule())
            }

            Spacer(minLength: 0)

            HStack(spacing: 10) {
                Button("Configure") { onConfigure() }
                    .buttonStyle(.plain)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(Theme.surfaceHover)
                    .clipShape(Capsule())
                if let job = entry.jobs.first, entry.enabled {
                    Text(cronSummary(job.cron)).font(.system(size: 11))
                        .foregroundStyle(Theme.textSecondary)
                        .help(job.label)
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
}

// MARK: - Configure sheet (generated from the manifest)

/// Field/option editor generated from the vein's declared providers and option groups.
/// Test runs the vein's live provider probes before anything is saved.
struct VeinSheet: View {
    let entry: VeinEntry
    @ObservedObject var veins: VeinsStore
    var openPlugins: () -> Void = {}
    @Environment(\.dismiss) private var dismiss

    @State private var providerValues: [String: String] = [:]
    @State private var textValues: [String: String] = [:]
    @State private var boolValues: [String: Bool] = [:]
    @State private var cron: String = ""
    @State private var testing = false
    @State private var testResults: [(slot: String, ok: Bool, detail: String)] = []
    @State private var saving = false
    @State private var error: String?

    private static let cronPresets: [(String, String)] = [
        ("Hourly", "0 * * * *"), ("Every 6 hours", "0 */6 * * *"),
        ("Twice daily", "0 6,18 * * *"), ("Daily", "0 6 * * *"), ("Weekly", "0 9 * * 0"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: entry.icon).font(.system(size: 16))
                Text("Configure \(entry.label)").font(.system(size: 16, weight: .semibold))
            }
            .padding(16)
            Divider().overlay(Theme.hairline)

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    ForEach(entry.requires.filter { !$0.met }, id: \.label) { req in
                        HStack(spacing: 6) {
                            Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
                            Text("Requires \(req.label): \(req.detail)").font(.system(size: 12))
                            if req.integration != nil {
                                Button("Open Plugins") { dismiss(); openPlugins() }
                                    .buttonStyle(.plain).font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(Theme.accent)
                            }
                        }
                        .foregroundStyle(.orange)
                    }

                    ForEach(entry.providers) { p in providerEditor(p) }
                    ForEach(entry.options) { group in optionGroup(group) }
                    if let job = entry.jobs.first { scheduleEditor(job) }

                    ForEach(testResults, id: \.slot) { r in
                        HStack(spacing: 6) {
                            Image(systemName: r.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                                .foregroundStyle(r.ok ? Color(red: 0.36, green: 0.78, blue: 0.5)
                                                      : Color(red: 0.92, green: 0.42, blue: 0.38))
                            Text("\(r.slot): \(r.detail)").font(.system(size: 12))
                                .foregroundStyle(Theme.textSecondary)
                        }
                    }
                    if let error {
                        Text(error).font(.system(size: 12)).foregroundStyle(.red)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(16)
            }

            Divider().overlay(Theme.hairline)
            HStack(spacing: 10) {
                Button(testing ? "Testing…" : "Test") { test() }
                    .disabled(testing || saving)
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                if entry.enabled {
                    Button("Disable", role: .destructive) { save(enable: false) }.disabled(saving)
                }
                Button(saving ? "Saving…" : (entry.enabled ? "Save" : "Save & Enable")) { save(enable: true) }
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || (!entry.enabled && !entry.canEnable))
            }
            .padding(12)
        }
        .frame(width: 500)
        .frame(minHeight: 300, maxHeight: 600)
        .background(Theme.bg)
    }

    // MARK: editors (by declared type — nothing vein-specific)

    private func providerEditor(_ p: VeinProvider) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(p.label).font(.system(size: 13, weight: .medium))
                if !p.hint.isEmpty { InfoTip(text: p.hint) }
            }
            TextField(p.defaultValue, text: Binding(
                get: { providerValues[p.id] ?? p.value },
                set: { providerValues[p.id] = $0 }))
                .textFieldStyle(.roundedBorder).autocorrectionDisabled()
        }
    }

    private func optionGroup(_ group: VeinOptionGroup) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(group.group.uppercased()).font(.system(size: 10, weight: .semibold)).tracking(0.6)
                .foregroundStyle(Theme.textSecondary)
            ForEach(group.fields) { f in fieldEditor(f) }
        }
    }

    @ViewBuilder private func fieldEditor(_ f: VeinField) -> some View {
        switch f.type {
        case "bool":
            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Text(f.label).font(.system(size: 12, weight: .medium))
                    if !f.hint.isEmpty { InfoTip(text: f.hint, size: 10) }
                }
                Spacer(minLength: 8)
                Toggle("", isOn: Binding(get: { boolValues[f.id] ?? f.isOn },
                                         set: { boolValues[f.id] = $0 }))
                    .toggleStyle(.switch).controlSize(.mini).labelsHidden().tint(Theme.accent)
            }
            .padding(.horizontal, 10).padding(.vertical, 7)
            .background(Theme.bg.opacity(0.5)).clipShape(RoundedRectangle(cornerRadius: 8))
        case "choice":
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(f.label).font(.system(size: 12, weight: .medium))
                    if !f.hint.isEmpty { InfoTip(text: f.hint, size: 10) }
                }
                Picker("", selection: Binding(
                    get: { textValues[f.id] ?? (f.value.isEmpty ? (f.choices.first ?? "") : f.value) },
                    set: { textValues[f.id] = $0 })) {
                    ForEach(f.choices, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden()
            }
        default:  // text / number — a text field; the server coerces by declared type
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(f.label).font(.system(size: 12, weight: .medium))
                    if !f.hint.isEmpty { InfoTip(text: f.hint, size: 10) }
                }
                TextField("", text: Binding(get: { textValues[f.id] ?? f.value },
                                            set: { textValues[f.id] = $0 }))
                    .textFieldStyle(.roundedBorder).autocorrectionDisabled()
            }
        }
    }

    private func scheduleEditor(_ job: VeinJob) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("SCHEDULE").font(.system(size: 10, weight: .semibold)).tracking(0.6)
                .foregroundStyle(Theme.textSecondary)
            HStack(spacing: 8) {
                Picker("", selection: Binding(
                    get: { Self.cronPresets.first { $0.1 == (cron.isEmpty ? job.cron : cron) }?.1 ?? "custom" },
                    set: { cron = $0 == "custom" ? (cron.isEmpty ? job.cron : cron) : $0 })) {
                    ForEach(Self.cronPresets, id: \.1) { Text($0.0).tag($0.1) }
                    Text("Custom").tag("custom")
                }
                .labelsHidden().frame(width: 160)
                TextField(job.cron, text: Binding(get: { cron.isEmpty ? job.cron : cron },
                                                  set: { cron = $0 }))
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(width: 140)
                Text(job.label).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                Spacer(minLength: 0)
            }
        }
    }

    // MARK: actions

    /// Only values the user actually changed go to the server (stringified; the server
    /// coerces by each field's declared type).
    private func editedPayload() -> (options: [String: String]?, providers: [String: String]?, cron: String?) {
        var options: [String: String] = [:]
        for (id, v) in boolValues { options[id] = v ? "true" : "false" }
        for (id, v) in textValues {
            options[id] = v.trimmingCharacters(in: .whitespaces)
        }
        var providers: [String: String] = [:]
        for (id, v) in providerValues { providers[id] = v.trimmingCharacters(in: .whitespaces) }
        let cronEdit = (cron.isEmpty || cron == entry.jobs.first?.cron) ? nil : cron
        return (options.isEmpty ? nil : options, providers.isEmpty ? nil : providers, cronEdit)
    }

    private func test() {
        testing = true; testResults = []
        Task {
            testResults = await veins.test(kind: entry.kind)
            testing = false
        }
    }

    private func save(enable: Bool) {
        saving = true; error = nil
        let payload = editedPayload()
        Task {
            let detail = await veins.save(kind: entry.kind, enabled: enable,
                                          options: payload.options, providers: payload.providers,
                                          cron: payload.cron)
            saving = false
            if let detail { error = detail } else { dismiss() }
        }
    }
}

// MARK: - Onboarding step

/// The vein catalog as an onboarding pick-list — fully skippable (skip = an empty chip
/// row, the honest default). Selecting a vein opens its config sheet.
struct VeinsOnboardingStep: View {
    let base: URL
    var onDone: () -> Void
    @StateObject private var veins = VeinsStore()
    @State private var editing: VeinEntry?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Pick your veins").font(.system(size: 18, weight: .semibold))
                Text("Ambient watches pinned above the Pulse feed: weather, stack health, external signals. All optional; add them any time from the Veins pane.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            switch veins.phase {
            case .loading:
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Loading the vein catalog…").font(.system(size: 12))
                        .foregroundStyle(Theme.textSecondary)
                }
            case .ready:
                VStack(spacing: 6) {
                    ForEach(veins.entries) { e in
                        HStack(spacing: 10) {
                            Image(systemName: e.icon).font(.system(size: 13)).frame(width: 18)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(e.label).font(.system(size: 13, weight: .medium))
                                Text(e.blurb).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 8)
                            if e.enabled {
                                Label("On", systemImage: "checkmark.circle.fill")
                                    .font(.system(size: 11, weight: .medium))
                                    .foregroundStyle(Color(red: 0.36, green: 0.78, blue: 0.5))
                            } else {
                                Button(e.canEnable ? "Add" : "Needs setup") { editing = e }
                                    .buttonStyle(.plain).font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(e.canEnable ? Theme.accent : Theme.textSecondary)
                            }
                        }
                        .padding(.horizontal, 10).padding(.vertical, 7)
                        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }
            default:
                Text("Couldn't load the vein catalog from vera-api. You can set veins up later from the Veins pane.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("Done") { onDone() }.keyboardShortcut(.defaultAction)
            }
        }
        .task {
            veins.configure(base: base)
            await veins.refresh()
        }
        .sheet(item: $editing) { entry in
            VeinSheet(entry: entry, veins: veins)
        }
    }
}
