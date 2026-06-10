import SwiftUI
import AppKit

/// The Plugins store — one card per integration from vera-api's registry, with the
/// service's mark, live status, a one-click Add flow (vera-api config + OWUI tool
/// attach), and experimental features behind a consent sheet. Cards render from the
/// API response; the app hardcodes no integration list.
struct PluginsView: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var tools: ToolsStore
    @StateObject private var plugins = PluginsStore()
    @State private var editing: PluginEntry?
    @State private var consent: ConsentContext?

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if let e = plugins.error { errorRow(e) }
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
            plugins.configure(base: config.resolved?.veraAPIBase, tools: tools)
            await plugins.refresh()
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
                await plugins.refresh()
            }
        }
        .sheet(item: $editing) { entry in
            PluginSheet(entry: entry, plugins: plugins)
        }
        .sheet(item: $consent) { ctx in
            ConsentSheet(entry: ctx.entry, feature: ctx.feature, plugins: plugins)
        }
    }

    private var header: some View {
        HStack {
            Text("Plugins").font(.system(size: 22, weight: .bold))
            if case .ready = plugins.phase {
                Text("\(plugins.entries.filter(\.enabled).count)/\(plugins.entries.count)")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.surface).clipShape(Capsule())
            }
            Spacer()
            Text("what Vera is connected to").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
        }
        .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 8)
        .frame(maxWidth: 860, alignment: .leading)
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder private var content: some View {
        switch plugins.phase {
        case .loading:
            RowCard {
                ProgressView().controlSize(.small)
                Text("Loading integrations…").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
        case .unconfigured:
            statusCard(icon: "gearshape", title: "vera-api isn't configured",
                       note: "Set the vera-api URL in Settings to manage integrations.")
        case .unreachable:
            statusCard(icon: "exclamationmark.triangle", title: "vera-api unreachable",
                       note: "Couldn't load integrations from \(plugins.baseDescription).", retry: true)
        case .unsupported:
            statusCard(icon: "shippingbox", title: "Integrations not available",
                       note: "This vera-api doesn't expose the integration registry yet — update vera-api to manage plugins here.",
                       retry: true)
        case .ready:
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 380), spacing: 14, alignment: .top)],
                      alignment: .leading, spacing: 14) {
                ForEach(plugins.entries) { entry in
                    PluginCard(entry: entry, plugins: plugins,
                               onConfigure: { editing = entry },
                               onFeatureToggle: { feature, on in
                                   featureToggle(entry, feature, on)
                               })
                }
            }
        }
    }

    private func featureToggle(_ entry: PluginEntry, _ feature: PluginFeature, _ on: Bool) {
        if on && !feature.acked {
            consent = ConsentContext(entry: entry, feature: feature)   // first enable: consent sheet
        } else {
            Task {
                if let detail = await plugins.setFeature(entry, feature, enabled: on, ack: false) {
                    plugins.error = detail
                }
            }
        }
    }

    private func errorRow(_ e: String) -> some View {
        HStack {
            Text(e).font(.system(size: 12)).foregroundStyle(.red)
            Spacer()
            Button { plugins.error = nil } label: {
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
                Button("Retry") { Task { await plugins.refresh() } }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.accent)
            }
        }
    }
}

struct ConsentContext: Identifiable {
    let entry: PluginEntry
    let feature: PluginFeature
    var id: String { "\(entry.id).\(feature.id)" }
}

// MARK: - The service mark

/// A plugin's bundled logo (official marks, normalized PNGs in Resources/plugins),
/// falling back to a themed glyph so an unknown registry entry still gets a face.
struct PluginLogo: View {
    let id: String
    var size: CGFloat = 40

    private static var cache: [String: NSImage] = [:]
    private static func image(_ id: String) -> NSImage? {
        if let hit = cache[id] { return hit }
        guard let url = Bundle.module.url(forResource: id, withExtension: "png"),
              let img = NSImage(contentsOf: url) else { return nil }
        cache[id] = img
        return img
    }

    var body: some View {
        Group {
            if let img = Self.image(id) {
                Image(nsImage: img).resizable().interpolation(.high).scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: size * 0.22))
            } else {
                RoundedRectangle(cornerRadius: size * 0.22).fill(Theme.surfaceHover)
                    .overlay(Image(systemName: "shippingbox")
                        .font(.system(size: size * 0.42)).foregroundStyle(Theme.textSecondary))
            }
        }
        .frame(width: size, height: size)
    }
}

// MARK: - One plugin card

private struct PluginCard: View {
    let entry: PluginEntry
    @ObservedObject var plugins: PluginsStore
    var onConfigure: () -> Void
    var onFeatureToggle: (PluginFeature, Bool) -> Void

    private var pendingNote: String? { plugins.owuiPending[entry.id] }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                PluginLogo(id: entry.id)
                VStack(alignment: .leading, spacing: 3) {
                    Text(entry.displayName).font(.system(size: 15, weight: .semibold))
                    StatusChip(entry: entry, pendingNote: pendingNote)
                }
                Spacer(minLength: 8)
                if plugins.busy.contains(entry.id) {
                    ProgressView().controlSize(.small)
                } else if entry.configured {
                    Toggle("", isOn: Binding(get: { entry.enabled },
                                             set: { plugins.setEnabled(entry, $0) }))
                        .toggleStyle(.switch).controlSize(.small).labelsHidden()
                        .tint(Theme.accent)
                }
            }

            Text(entry.unlocksLine)
                .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if let pairing = entry.pairing, pairing.active {
                HStack(spacing: 6) {
                    Image(systemName: "link").font(.system(size: 10))
                    Text("Paired — \(pairing.label)").font(.system(size: 11, weight: .medium))
                }
                .foregroundStyle(Theme.accent)
                .padding(.horizontal, 9).padding(.vertical, 4)
                .background(Theme.accent.opacity(0.12)).clipShape(Capsule())
            }

            ForEach(entry.features) { feature in
                FeatureRow(entry: entry, feature: feature,
                           locked: !entry.enabled,
                           busy: plugins.busy.contains(entry.id),
                           onToggle: { onFeatureToggle(feature, $0) })
            }

            HStack(spacing: 10) {
                Button(entry.configured ? "Configure" : "Add") { onConfigure() }
                    .buttonStyle(.plain)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(entry.configured ? Theme.textPrimary : .white)
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .background(entry.configured ? Theme.surfaceHover : Theme.accent)
                    .clipShape(Capsule())
                if pendingNote != nil {
                    Button("Retry OWUI step") { plugins.retryOWUI(entry) }
                        .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Theme.accent)
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
}

/// Status chip: Not connected / Connected / Off / Error (detail on hover) / OWUI step pending.
private struct StatusChip: View {
    let entry: PluginEntry
    let pendingNote: String?

    private var label: (text: String, color: Color, help: String) {
        if let pendingNote, entry.enabled {
            return ("OWUI step pending", .orange, pendingNote)
        }
        switch entry.status {
        case "enabled":
            return ("Connected", Color(red: 0.36, green: 0.78, blue: 0.5),
                    entry.lastTestDetail.isEmpty ? "Connected" : entry.lastTestDetail)
        case "error":
            return ("Error", Color(red: 0.92, green: 0.42, blue: 0.38),
                    entry.lastTestDetail.isEmpty ? "Connection error" : entry.lastTestDetail)
        case "configured":
            return ("Off", Theme.textSecondary, "Configured but switched off")
        default:
            return ("Not connected", Theme.textSecondary, "No connection configured")
        }
    }

    var body: some View {
        let l = label
        HStack(spacing: 5) {
            Circle().fill(l.color).frame(width: 6, height: 6)
            Text(l.text).font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.textSecondary)
        }
        .help(l.help)
    }
}

/// One experimental feature row under its parent card.
private struct FeatureRow: View {
    let entry: PluginEntry
    let feature: PluginFeature
    let locked: Bool
    let busy: Bool
    var onToggle: (Bool) -> Void

    var body: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(feature.label).font(.system(size: 12, weight: .medium))
                    Text("EXPERIMENTAL").font(.system(size: 8, weight: .bold)).tracking(0.5)
                        .foregroundStyle(.orange)
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(Color.orange.opacity(0.15)).clipShape(Capsule())
                }
                if locked {
                    Text("Enable \(entry.displayName) first")
                        .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                }
            }
            Spacer(minLength: 8)
            if locked {
                Image(systemName: "lock.fill").font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
            } else {
                Toggle("", isOn: Binding(get: { feature.enabled }, set: { onToggle($0) }))
                    .toggleStyle(.switch).controlSize(.mini).labelsHidden()
                    .tint(.orange).disabled(busy)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(Theme.bg.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .help(feature.ramifications)
    }
}

// MARK: - Add / Configure sheet

/// Field editor generated from the registry's field definitions. Secrets are secure
/// inputs whose existing values show as "set" without echoing. Test runs the live
/// probe with the sheet's current values before anything is saved.
struct PluginSheet: View {
    let entry: PluginEntry
    @ObservedObject var plugins: PluginsStore
    @Environment(\.dismiss) private var dismiss

    @State private var values: [String: String] = [:]
    @State private var testing = false
    @State private var testResult: (ok: Bool, detail: String)?
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                PluginLogo(id: entry.id, size: 28)
                Text(entry.configured ? "Configure \(entry.displayName)" : "Add \(entry.displayName)")
                    .font(.system(size: 16, weight: .semibold))
            }
            .padding(16)
            Divider().overlay(Theme.hairline)

            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(entry.fields) { f in fieldEditor(f) }
                    if let r = testResult {
                        HStack(spacing: 6) {
                            Image(systemName: r.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                                .foregroundStyle(r.ok ? Color(red: 0.36, green: 0.78, blue: 0.5)
                                                      : Color(red: 0.92, green: 0.42, blue: 0.38))
                            Text(r.detail).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
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
                    .keyboardShortcut(.defaultAction).disabled(saving)
            }
            .padding(12)
        }
        .frame(width: 480)
        .frame(minHeight: 280, maxHeight: 540)
        .background(Theme.bg)
    }

    @ViewBuilder private func fieldEditor(_ f: PluginField) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(f.label).font(.system(size: 13, weight: .medium))
                if f.envLocked {
                    Image(systemName: "lock.fill").font(.system(size: 9)).foregroundStyle(Theme.textSecondary)
                        .help("Pinned by the server's environment — change it there.")
                }
            }
            if !f.hint.isEmpty {
                Text(f.hint).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            if f.secret {
                SecureField(f.isSet ? "•••• (set — leave blank to keep)" : "",
                            text: binding(f.id))
                    .textFieldStyle(.roundedBorder).disabled(f.envLocked)
            } else if !f.choices.isEmpty {
                Picker("", selection: binding(f.id)) {
                    ForEach(f.choices, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden().disabled(f.envLocked)
                .onAppear {
                    // an empty stored value means the server default — the first choice
                    if values[f.id] == nil { values[f.id] = f.value.isEmpty ? (f.choices.first ?? "") : f.value }
                }
            } else {
                TextField(f.value.isEmpty ? "" : f.value, text: binding(f.id))
                    .textFieldStyle(.roundedBorder).disabled(f.envLocked)
                    .onAppear { if values[f.id] == nil { values[f.id] = f.value } }
            }
        }
    }

    private func binding(_ id: String) -> Binding<String> {
        Binding(get: { values[id] ?? "" }, set: { values[id] = $0 })
    }

    /// Only fields the user actually typed into go to the server — an untouched secret
    /// keeps its stored value.
    private var editedFields: [String: String] {
        var out: [String: String] = [:]
        for f in entry.fields where !f.envLocked {
            let v = (values[f.id] ?? "").trimmingCharacters(in: .whitespaces)
            if f.secret { if !v.isEmpty { out[f.id] = v } }
            else if v != f.value { out[f.id] = v }
        }
        return out
    }

    private func test() {
        testing = true; testResult = nil
        Task {
            testResult = await plugins.test(id: entry.id, fields: editedFields.isEmpty ? nil : editedFields)
            testing = false
        }
    }

    private func save(enable: Bool) {
        saving = true; error = nil
        Task {
            let detail = await plugins.save(id: entry.id, fields: editedFields, enable: enable)
            saving = false
            if let detail { error = detail } else { dismiss() }
        }
    }
}

// MARK: - Experimental consent sheet

/// First-time enable of an experimental feature: the server-provided ramifications text,
/// verbatim, with Enable sending `ack: true`. The server enforces the same contract.
struct ConsentSheet: View {
    let entry: PluginEntry
    let feature: PluginFeature
    @ObservedObject var plugins: PluginsStore
    @Environment(\.dismiss) private var dismiss
    @State private var enabling = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Image(systemName: "flask").font(.system(size: 15)).foregroundStyle(.orange)
                Text(feature.label).font(.system(size: 16, weight: .semibold))
                Text("EXPERIMENTAL").font(.system(size: 9, weight: .bold)).tracking(0.5)
                    .foregroundStyle(.orange)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Color.orange.opacity(0.15)).clipShape(Capsule())
            }
            Text(feature.ramifications)
                .font(.system(size: 13)).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .lineSpacing(3)
            Text("Part of \(entry.displayName). You can turn it off any time; this notice won't be shown again.")
                .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            if let error {
                Text(error).font(.system(size: 12)).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(enabling ? "Enabling…" : "Enable") {
                    enabling = true; error = nil
                    Task {
                        let detail = await plugins.setFeature(entry, feature, enabled: true, ack: true)
                        enabling = false
                        if let detail { error = detail } else { dismiss() }
                    }
                }
                .keyboardShortcut(.defaultAction).disabled(enabling)
            }
        }
        .padding(20).frame(width: 440)
        .background(Theme.bg)
    }
}
