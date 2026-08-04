import SwiftUI
import AppKit

/// Forces a foreground, regular-activation window when launched from an SPM executable
/// (otherwise `swift run` can open the window in the background).
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        // Only force the Dock/cmd-tab icon when running UNBUNDLED (`swift run`), where there's
        // no .icns. In a packaged .app the bundle's Vera.icns is the correct, mipmapped icon —
        // overriding it with the raw full-bleed PNG is what made the Dock icon look wrong.
        let bundled = Bundle.main.bundleURL.pathExtension == "app"
        if !bundled, let icon = Brand.icon { NSApp.applicationIconImage = icon }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

struct VeraApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store: ChatStore
    @StateObject private var tools: ToolsStore
    @StateObject private var voice: VoiceSession
    @StateObject private var config: ConfigStore
    @StateObject private var updates = UpdateChecker()
    @StateObject private var engine = EngineManager()

    init() {
        let configInstance = ConfigStore()
        let native = configInstance.nativeResolved
        let legacy = OWUIConfig.load()
        let ambient = legacy ?? OWUIConfig.ambientOnly(native: native)
        let socket = legacy.map { VeraSocket(config: $0) }
        let client = ambient.map { OWUIClient(config: $0) }
        let admin = legacy.map { c in
            OWUIAdminClient(baseURL: c.baseURL, modelID: c.model,
                            token: { try await socket!.currentToken() })
        }
        let repository: LocalChatRepository?
        let repositoryError: String?
        do {
            repository = try LocalChatRepository()
            repositoryError = nil
        } catch {
            repository = nil
            repositoryError = "Local history couldn't open: \(error.localizedDescription)"
        }
        let storeInstance = ChatStore(
            config: ambient,
            client: client,
            socket: socket,
            nativeConfig: native,
            nativeTransport: native.map { NativeChatClient(config: $0) },
            repository: repository,
            repositoryError: repositoryError,
            hasLegacyOWUI: legacy != nil,
            nativeSystemPrompt: configInstance.nativeSettings.systemPrompt,
            nativeEnabledToolIDs: configInstance.nativeSettings.enabledToolIDs,
            nativeCapabilityOverrides: configInstance.nativeSettings.capabilityOverrides,
            visionBridgeConfig: configInstance.visionBridgeResolved,
            nativeMemorySettings: configInstance.nativeSettings.memory,
            nativeMemoryService: configInstance.nativeMemoryService,
            sweepOrphanedAttachments: true)
        _store = StateObject(wrappedValue: storeInstance)
        _config = StateObject(wrappedValue: configInstance)
        _tools = StateObject(wrappedValue: ToolsStore(admin: admin, socket: socket))
        _voice = StateObject(wrappedValue: VoiceSession(client: VoiceClient(base: legacy?.voiceBase),
                                                        socket: socket, store: storeInstance))
    }

    var body: some Scene {
        WindowGroup("Vera") {
            ContentView()
                .environmentObject(store)
                .environmentObject(tools)
                .environmentObject(voice)
                .environmentObject(config)
                .environmentObject(updates)
                .environmentObject(engine)
                .frame(minWidth: 920, minHeight: 600)
                .preferredColorScheme(config.colorSchemeOverride)
                .task { updates.start() }
                .task { await engine.reconcileOnLaunch() }
                .task { await RemindersBridge.autostartIfEnabled(veraAPIBase: config.veraAPIBase) }
        }
        .defaultSize(width: 1180, height: 760)
        .commands {
            CommandGroup(after: .appInfo) {
                Button(updates.checking ? "Checking…" : "Check for Updates…") {
                    Task { await updates.check(manual: true) }
                }
                .disabled(updates.checking)
            }
        }

        Settings {
            SettingsView()
                .environmentObject(config)
                .environmentObject(store)
                .environmentObject(tools)   // the Plugins and MCP tabs render from the shared ToolsStore
                .environmentObject(updates)
                .environmentObject(engine)
                .preferredColorScheme(config.colorSchemeOverride)
        }
    }
}
