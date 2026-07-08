import SwiftUI

/// The Settings window (⌘,) — every endpoint and identity value editable in-app, written to
/// `~/.vera/config.json`. Env vars still win over file values; env-overridden fields carry
/// an InfoTip naming the variable. Cheap changes apply live; OWUI session changes offer a reconnect.
struct SettingsView: View {
    @EnvironmentObject var store: ChatStore
    var body: some View {
        TabView(selection: $store.settingsTab) {
            ConnectionTab().tabItem { Label("Connection", systemImage: "link") }.tag(SettingsTab.connection)
            ModelTab().tabItem { Label("Model", systemImage: "cpu") }.tag(SettingsTab.model)
            ServicesTab().tabItem { Label("Services", systemImage: "server.rack") }.tag(SettingsTab.services)
            // Plugins and MCP are configuration surfaces, not destinations — they live here, not the sidebar.
            PluginsView().tabItem { Label("Plugins", systemImage: "shippingbox") }.tag(SettingsTab.plugins)
            MCPView().tabItem { Label("MCP", systemImage: "puzzlepiece.extension") }.tag(SettingsTab.mcp)
            IdentityTab().tabItem { Label("Identity", systemImage: "person") }.tag(SettingsTab.identity)
            AboutTab().tabItem { Label("About", systemImage: "info.circle") }.tag(SettingsTab.about)
        }
        // Wider than a plain form window so the moved Plugins/MCP surfaces (860-wide content) breathe;
        // a fixed height keeps their scrolling panes from collapsing in the Settings scene.
        .frame(width: 860, height: 640)
    }
}

// MARK: - Tabs

private struct ConnectionTab: View {
    @EnvironmentObject var config: ConfigStore
    var body: some View {
        Form {
            Section("Open WebUI") {
                ConfigField(label: "Base URL", key: "base", placeholder: "http://my-owui-host:6590")
                ConfigField(label: "API key", key: "api_key", secure: true)
                ConfigField(label: "Email", key: "owui_email", placeholder: "you@example.com")
                ConfigField(label: "Password", key: "owui_password", secure: true)
                InlineTest(title: "Test connection") {
                    try await ConnectionTest.owui(base: config["base"],
                                                  email: config["owui_email"],
                                                  password: config["owui_password"])
                }
            }
            SaveSection()
        }
        .formStyle(.grouped)
    }
}

private struct ModelTab: View {
    @EnvironmentObject var config: ConfigStore
    @State private var advanced = false
    var body: some View {
        Form {
            Section("Model") {
                ConfigField(label: "Model id (required)", key: "model", placeholder: "your-vera-model")
            }
            Section {
                DisclosureGroup("Advanced", isExpanded: $advanced) {
                    ConfigField(label: "Completions URL", key: "completions_url",
                                placeholder: "pre-filled from the OWUI base when empty",
                                tip: "The raw OpenAI-style endpoint used as a fallback path. Leave empty to go through Open WebUI.")
                    ConfigField(label: "Chat template kwargs", key: "chat_template_kwargs",
                                placeholder: "{\"enable_thinking\": false}",
                                tip: "Server-specific chat-template options as JSON (e.g. the Qwen3 thinking toggle on llama.cpp/vLLM). Leave empty for strict OpenAI endpoints.")
                }
            }
            SaveSection()
        }
        .formStyle(.grouped)
    }
}

private struct ServicesTab: View {
    @EnvironmentObject var config: ConfigStore
    var body: some View {
        Form {
            EngineSection()
            Section("Voice") {
                ConfigField(label: "Base URL", key: "voice_base", placeholder: "http://my-voice-host:8131")
                InlineTest(title: "Test voice") {
                    try await ConnectionTest.voice(base: config["voice_base"])
                }
            }
            SaveSection()
        }
        .formStyle(.grouped)
    }
}

private struct EngineSection: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var engine: EngineManager

    private var mode: EngineMode {
        if let m = EngineMode(rawValue: config["engine_mode"]) { return m }
        return config["vera_api_base"].isEmpty ? .off : .remote
    }

    private var modeBinding: Binding<EngineMode> {
        Binding(get: { mode }, set: { new in
            config["engine_mode"] = new.rawValue
            try? config.save()
            Task { await engine.apply(mode: new) }
        })
    }

    var body: some View {
        Section("Engine") {
            Picker("Runs", selection: modeBinding) {
                Text("Remote URL").tag(EngineMode.remote)
                Text("On this Mac").tag(EngineMode.local)
                Text("Off").tag(EngineMode.off)
            }
            .pickerStyle(.segmented)

            switch mode {
            case .remote: remoteBody
            case .local: localBody
            case .off: offBody
            }
        }
    }

    private var remoteBody: some View {
        Group {
            ConfigField(label: "Base URL", key: "vera_api_base", placeholder: "http://my-api-host:8089")
            InlineTest(title: "Test vera-api") {
                try await ConnectionTest.http(base: config["vera_api_base"],
                                              path: "health/services", label: "vera-api")
            }
        }
    }

    private var offBody: some View {
        Text("vera-api is turned off. Pulse, veins, and the ambient features stay in their unconfigured state.")
            .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
    }

    @ViewBuilder private var localBody: some View {
        LabeledContent("Version", value: engine.installedVersion ?? "Not installed")
        LabeledContent("Status", value: statusText)
        LabeledContent("Data", value: engine.dataPathDisplay)
        HStack(spacing: 6) {
            TextField("Port", text: config.binding("engine_port"), prompt: Text("8089"))
                .autocorrectionDisabled().frame(width: 120)
            InfoTip(text: "The loopback port the engine binds. Change it only if 8089 is taken.")
            Spacer(minLength: 0)
        }
        HStack(spacing: 10) {
            if engine.running {
                Button("Stop") { Task { await engine.stopEngine() } }.disabled(engine.busy)
            } else {
                Button(engine.busy ? "Starting…" : "Start") { Task { await engine.startEngine() } }
                    .disabled(engine.busy)
            }
            if engine.busy { ProgressView().controlSize(.small) }
            if let msg = phaseMessage {
                Text(msg).font(.system(size: 12)).foregroundStyle(phaseColor)
            }
            Spacer(minLength: 0)
        }
        if let detail = engine.runDetail {
            Label(detail, systemImage: "exclamationmark.triangle")
                .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        }
        Text("Log: \(engine.logPathDisplay)")
            .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
        HStack(spacing: 10) {
            Text("Effective base").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            Text("http://127.0.0.1:\(engine.port)")
                .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            Spacer(minLength: 0)
        }
        MaintenanceControls()
    }

    private var statusText: String {
        engine.running ? "Running" : "Stopped"
    }

    private var phaseMessage: String? {
        switch engine.phase {
        case .idle, .healthy: return nil
        case .resolving: return "Finding the release…"
        case .downloading: return "Downloading…"
        case .verifying: return "Verifying checksum…"
        case .unpacking: return "Installing…"
        case .launching: return "Starting the engine…"
        case .failed(let m): return m
        }
    }

    private var phaseColor: Color {
        if case .failed = engine.phase { return .red }
        return Theme.textSecondary
    }
}

private struct MaintenanceControls: View {
    @EnvironmentObject var engine: EngineManager
    @State private var confirmDelete = false

    var body: some View {
        HStack(spacing: 10) {
            Button("Remove engine files") { engine.removeEngineFiles() }.disabled(engine.busy)
            Button("Delete data", role: .destructive) { confirmDelete = true }
            Spacer(minLength: 0)
        }
        .confirmationDialog("Delete the engine's data directory?", isPresented: $confirmDelete) {
            Button("Delete data", role: .destructive) { engine.deleteData() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This permanently removes ~/.vera/data (Pulse history, memory, veins). This cannot be undone.")
        }
    }
}

private struct IdentityTab: View {
    @EnvironmentObject var config: ConfigStore
    var body: some View {
        Form {
            Section("Identity") {
                ConfigField(label: "Your name", key: "owner_name", placeholder: "how Vera greets you",
                            tip: "Drives the greeting and the sidebar chip. Leave empty for a nameless greeting.")
            }
            Section("Appearance") {
                Picker("Appearance", selection: config.binding("appearance")) {
                    Text("System").tag("")
                    Text("Light").tag("light")
                    Text("Dark").tag("dark")
                }
                .pickerStyle(.segmented)
            }
            SaveSection()
        }
        .formStyle(.grouped)
    }
}

private struct AboutTab: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var updates: UpdateChecker
    @State private var serverVersion: String?

    var body: some View {
        Form {
            Section("Versions") {
                LabeledContent("App", value: AppVersion.current)
                LabeledContent("vera-api", value: serverVersion ?? "N/A")
                if let server = serverVersion,
                   Semver.minor(server) != Semver.minor(AppVersion.current) {
                    Label("App and server minor versions differ. Update the older side when convenient.",
                          systemImage: "exclamationmark.triangle")
                        .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                }
            }
            Section("Updates") {
                if AppVersion.isSelfBuilt {
                    Text("Built from source. Update with git pull.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                } else {
                    HStack(spacing: 10) {
                        Button(updates.checking ? "Checking…" : "Check for Updates") {
                            Task { await updates.check(manual: true) }
                        }
                        .disabled(updates.checking)
                        if let release = updates.available {
                            Button("Install \(release.tag_name)") { Task { await updates.install() } }
                                .disabled(updates.installing)
                        }
                        if let result = updates.lastResult {
                            Text(result).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
            Section("Project") {
                Button("github.com/\(UpdateChecker.repo)") {
                    openExternal("https://github.com/\(UpdateChecker.repo)")
                }
                .buttonStyle(.link)
            }
        }
        .formStyle(.grouped)
        .task { await loadServerVersion() }
    }

    private func loadServerVersion() async {
        let base = config["vera_api_base"].trimmingCharacters(in: .whitespaces)
        guard !base.isEmpty, let url = URL(string: "\(base)/version") else { return }
        if let (data, _) = try? await URLSession.shared.data(from: url),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            serverVersion = obj["version"] as? String
        }
    }
}

// MARK: - Building blocks

/// One editable config.json field. Env-overridden keys stay editable (edits land in the
/// file as usual) but an InfoTip explains that the environment value wins at resolution.
private struct ConfigField: View {
    @EnvironmentObject var config: ConfigStore
    let label: String
    let key: String
    var secure = false
    var placeholder = ""
    var tip = ""

    var body: some View {
        HStack(spacing: 6) {
            if secure {
                SecureField(label, text: config.binding(key), prompt: Text(placeholder))
            } else {
                TextField(label, text: config.binding(key), prompt: Text(placeholder))
                    .autocorrectionDisabled()
            }
            if let env = config.envOverride(key) {
                InfoTip(text: "Set by \(env) in the environment. The environment value wins; change it there.")
            } else if !tip.isEmpty {
                InfoTip(text: tip)
            }
        }
    }
}

/// A test button with its result inline (never a silent pass/fail).
private struct InlineTest: View {
    let title: String
    let run: () async throws -> String
    private enum Phase: Equatable { case idle, running, ok(String), fail(String) }
    @State private var phase: Phase = .idle

    var body: some View {
        HStack(spacing: 10) {
            Button(title) {
                phase = .running
                Task {
                    do { phase = .ok(try await run()) }
                    catch { phase = .fail(error.localizedDescription) }
                }
            }
            .disabled(phase == .running)
            switch phase {
            case .idle: EmptyView()
            case .running: ProgressView().controlSize(.small)
            case .ok(let m):
                Label(m, systemImage: "checkmark.circle.fill")
                    .font(.system(size: 12)).foregroundStyle(.green)
            case .fail(let m):
                Label(m, systemImage: "xmark.circle.fill")
                    .font(.system(size: 12)).foregroundStyle(.red)
            }
            Spacer(minLength: 0)
        }
    }
}

/// Save footer shared by every tab: writes the file, applies live where cheap, and offers a
/// reconnect when the OWUI session itself changed.
private struct SaveSection: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var store: ChatStore
    @State private var status: String?
    @State private var pendingReconnect: OWUIConfig?

    var body: some View {
        Section {
            HStack(spacing: 10) {
                if let status {
                    Text(status).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                }
                Spacer()
                if let cfg = pendingReconnect {
                    Button("Reconnect now") {
                        store.adopt(cfg)
                        pendingReconnect = nil
                        status = "Reconnected"
                    }
                }
                Button("Save") { save() }.keyboardShortcut("s", modifiers: .command)
            }
        }
    }

    private func save() {
        do { try config.save() } catch {
            status = "Save failed: \(error.localizedDescription)"
            return
        }
        guard let resolved = config.resolved else {
            status = "Saved. Add the OWUI URL and API key to connect"
            return
        }
        guard let live = store.currentConfig else {
            store.adopt(resolved)
            status = "Saved. Connecting…"
            return
        }
        let sessionChanged = live.baseURL != resolved.baseURL || live.apiKey != resolved.apiKey
            || live.email != resolved.email || live.password != resolved.password
        if sessionChanged {
            pendingReconnect = resolved
            status = "Saved. Reconnect to apply the connection change"
        } else {
            store.applyLight(resolved)
            status = "Saved"
        }
    }
}
