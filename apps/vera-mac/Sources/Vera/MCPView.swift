import SwiftUI

/// The MCP section — see, control, and provision the tools/functions/servers Vera can use.
struct MCPView: View {
    @EnvironmentObject var tools: ToolsStore
    @State private var valves: ValvesContext?
    @State private var showAddServer = false
    @State private var serverToRemove: ToolServer?

    var body: some View {
        VStack(spacing: 0) {
            header
            if tools.isLive && !tools.isAdmin { banner }
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    if let e = tools.error { errorRow(e) }
                    ActivitySection(invocations: tools.invocations)
                    toolsSection
                    functionsSection
                    serversSection
                }
                .padding(.horizontal, 28).padding(.vertical, 18)
                .frame(maxWidth: 860, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
        .task { await tools.load() }
        .sheet(item: $valves) { ctx in
            ValvesSheet(title: ctx.title,
                        load: { ctx.isFunction ? await tools.loadValves(forFunction: ctx.id)
                                                : await tools.loadValves(forTool: ctx.id) },
                        save: { tools.saveValves(id: ctx.id, isFunction: ctx.isFunction, fields: $0) })
        }
        .sheet(isPresented: $showAddServer) {
            AddServerSheet { url, name, key in tools.addServer(url: url, name: name, key: key) }
        }
        .confirmationDialog("Remove this tool server?", isPresented: Binding(
            get: { serverToRemove != nil }, set: { if !$0 { serverToRemove = nil } })) {
            Button("Remove", role: .destructive) { if let s = serverToRemove { tools.removeServer(s) }; serverToRemove = nil }
            Button("Cancel", role: .cancel) { serverToRemove = nil }
        } message: { Text(serverToRemove?.url ?? "") }
    }

    private var header: some View {
        HStack {
            Text("MCP").font(.system(size: 22, weight: .bold))
            Text("\(tools.tools.count + tools.functions.count + tools.servers.count)")
                .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface).clipShape(Capsule())
            Spacer()
            Text("what Vera can use").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
        }
        .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 8)
    }

    private var banner: some View {
        HStack(spacing: 8) {
            Image(systemName: "lock.fill").font(.system(size: 12))
            Text("Read-only — sign in as an admin to make changes.").font(.system(size: 12))
            Spacer()
        }
        .foregroundStyle(Theme.textSecondary)
        .padding(.horizontal, 28).padding(.vertical, 8).background(Theme.surface)
    }

    private func errorRow(_ e: String) -> some View {
        Text(e).font(.system(size: 12)).foregroundStyle(.red)
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.red.opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    // MARK: Tools

    private var toolsSection: some View {
        SectionBox(title: "Tools") {
            ForEach(tools.tools) { tool in
                RowCard {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            Text(tool.name).font(.system(size: 14, weight: .semibold))
                            if let used = tool.lastUsed, Date().timeIntervalSince(used) < 120 {
                                Circle().fill(Theme.accent).frame(width: 6, height: 6)
                            }
                        }
                        Text(tool.description).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 12)
                    gearButton(ValvesContext(id: tool.id, title: tool.name, isFunction: false))
                    Toggle("", isOn: Binding(get: { tool.availableToVera },
                                             set: { tools.setAvailable(tool, $0) }))
                        .labelsHidden().disabled(!tools.isAdmin)
                }
            }
        }
    }

    private var functionsSection: some View {
        SectionBox(title: "Functions") {
            ForEach(tools.functions) { fn in
                RowCard {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(fn.name).font(.system(size: 14, weight: .semibold))
                        Text(fn.type).font(.system(size: 10, weight: .medium)).foregroundStyle(Theme.textSecondary)
                            .padding(.horizontal, 7).padding(.vertical, 2)
                            .background(Theme.surfaceHover).clipShape(Capsule())
                    }
                    Spacer(minLength: 12)
                    gearButton(ValvesContext(id: fn.id, title: fn.name, isFunction: true))
                    Toggle("", isOn: Binding(get: { fn.isActive }, set: { _ in tools.toggleFunction(fn) }))
                        .labelsHidden().disabled(!tools.isAdmin)
                }
            }
        }
    }

    private var serversSection: some View {
        SectionBox(title: "Tool Servers") {
            if tools.servers.isEmpty {
                Text("No external tool servers connected.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary).padding(.vertical, 4)
            }
            ForEach(tools.servers) { s in
                RowCard {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.name).font(.system(size: 14, weight: .semibold))
                        Text(s.url).font(.system(size: 11)).foregroundStyle(Theme.textSecondary).lineLimit(1)
                    }
                    Spacer(minLength: 12)
                    Button { serverToRemove = s } label: {
                        Image(systemName: "trash").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                    }.buttonStyle(.plain).disabled(!tools.isAdmin)
                    Toggle("", isOn: Binding(get: { s.enabled }, set: { tools.setServerEnabled(s, $0) }))
                        .labelsHidden().disabled(!tools.isAdmin)
                }
            }
            Button { showAddServer = true } label: {
                HStack(spacing: 6) {
                    Image(systemName: "plus.circle.fill"); Text("Add server")
                }.font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.accent)
            }.buttonStyle(.plain).disabled(!tools.isAdmin).padding(.top, 4)
        }
    }

    private func gearButton(_ ctx: ValvesContext) -> some View {
        Button { valves = ctx } label: {
            Image(systemName: "slider.horizontal.3").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
        }.buttonStyle(.plain)
    }
}

// MARK: - Reusable bits

struct ValvesContext: Identifiable { let id: String; let title: String; let isFunction: Bool }

/// A titled group of rows.
struct SectionBox<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                .textCase(.uppercase)
            content
        }
    }
}

/// A single card row (HStack content).
struct RowCard<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        HStack(alignment: .center, spacing: 10) { content }
            .padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }
}

/// Live tool-invocation chips.
struct ActivitySection: View {
    let invocations: [Invocation]
    var body: some View {
        SectionBox(title: "Activity") {
            if invocations.isEmpty {
                Text("Vera isn't using any tools right now.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            } else {
                HStack(spacing: 8) {
                    ForEach(invocations.prefix(6)) { inv in
                        Text(inv.label).font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Theme.accent)
                            .padding(.horizontal, 9).padding(.vertical, 4)
                            .background(Theme.accent.opacity(0.14)).clipShape(Capsule())
                    }
                    Spacer(minLength: 0)
                }
            }
        }
    }
}

// MARK: - Valves sheet

struct ValvesSheet: View {
    let title: String
    let load: () async -> [ValveField]
    let save: ([ValveField]) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var fields: [ValveField] = []
    @State private var loaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title).font(.system(size: 16, weight: .semibold)).padding(16)
            Divider().overlay(Theme.hairline)
            if !loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if fields.isEmpty {
                Text("No configurable settings.").foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        ForEach($fields) { $f in fieldEditor($f) }
                    }.padding(16)
                }
            }
            Divider().overlay(Theme.hairline)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Save") { save(fields); dismiss() }.keyboardShortcut(.defaultAction)
                    .disabled(!loaded)
            }.padding(12)
        }
        .frame(width: 480, height: 540)
        .background(Theme.bg)
        .task { fields = await load(); loaded = true }
    }

    @ViewBuilder private func fieldEditor(_ f: Binding<ValveField>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(f.wrappedValue.title).font(.system(size: 13, weight: .medium))
            if !f.wrappedValue.help.isEmpty {
                Text(f.wrappedValue.help).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            switch f.wrappedValue.type {
            case .bool:
                Toggle("Enabled", isOn: Binding(get: { f.wrappedValue.value == "true" },
                                                set: { f.wrappedValue.value = $0 ? "true" : "false" }))
            case .unknown:
                TextEditor(text: f.value).font(.system(size: 12, design: .monospaced)).frame(height: 80)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.hairline))
            default:
                TextField("", text: f.value).textFieldStyle(.roundedBorder)
            }
        }
    }
}

// MARK: - Add server sheet

struct AddServerSheet: View {
    let add: (_ url: String, _ name: String, _ key: String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var url = ""
    @State private var name = ""
    @State private var key = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add tool server").font(.system(size: 16, weight: .semibold))
            Text("OpenAPI server URL (this is how MCP servers connect via a proxy).")
                .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            field("URL", "https://host:port/openapi.json", $url)
            field("Name", "Optional display name", $name)
            field("Auth key", "Optional bearer token", $key)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Add") { add(url, name, key); dismiss() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(url.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(18).frame(width: 460).background(Theme.bg)
    }

    private func field(_ label: String, _ placeholder: String, _ text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
            TextField(placeholder, text: text).textFieldStyle(.roundedBorder)
        }
    }
}
