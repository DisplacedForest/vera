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
    @StateObject private var voice: VoiceSession
    @StateObject private var config: ConfigStore
    @StateObject private var updates = UpdateChecker()
    @StateObject private var engine = EngineManager()

    init() {
        let configInstance = ConfigStore()
        let native = configInstance.nativeResolved
        let socket = VoiceSocketConfig.load().map { VeraSocket(config: $0) }
        let repository: LocalChatRepository?
        let repositoryError: String?
        do {
            repository = try LocalChatRepository()
            repositoryError = nil
        } catch {
            repository = nil
            repositoryError = "Local history couldn't open: \(error.localizedDescription)"
        }
        if let repository {
            var settings = configInstance.nativeSettings
            if (try? NativePromptMigration.run(repository: repository, settings: &settings)) == true {
                configInstance.nativeSettings = settings
                try? configInstance.save()
            }
        }
        let storeInstance = ChatStore(
            veraAPI: VeraAPIClient.resolved(model: native?.model ?? ""),
            nativeConfig: native,
            nativeTransport: native.map { NativeChatClient(config: $0) },
            repository: repository,
            repositoryError: repositoryError,
            nativeSystemPrompt: configInstance.nativeSettings.systemPrompt,
            nativePersonaID: configInstance.nativeSettings.activePersonaID,
            nativeOwnerName: configInstance.ownerName,
            nativeEnabledToolIDs: configInstance.nativeSettings.enabledToolIDs,
            nativeCapabilityOverrides: configInstance.nativeSettings.capabilityOverrides,
            visionBridgeConfig: configInstance.visionBridgeResolved,
            nativeMemorySettings: configInstance.nativeSettings.memory,
            nativeMemoryService: configInstance.nativeMemoryService,
            capabilityDeclarations: configInstance.capabilityTools.declarations,
            sweepOrphanedAttachments: true)
        _store = StateObject(wrappedValue: storeInstance)
        _config = StateObject(wrappedValue: configInstance)
        _voice = StateObject(wrappedValue: VoiceSession(client: VoiceClient(base: configInstance.voiceBase),
                                                        socket: socket, store: storeInstance))
    }

    var body: some Scene {
        WindowGroup("Vera") {
            ContentView()
                .environmentObject(store)
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
                .environmentObject(updates)
                .environmentObject(engine)
                .preferredColorScheme(config.colorSchemeOverride)
        }
    }
}
