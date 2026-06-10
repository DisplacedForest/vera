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
    @StateObject private var config = ConfigStore()

    init() {
        // Build the graph once so ChatStore, ToolsStore, and VoiceSession share a single
        // VeraSocket (one signed-in session, one event stream). With no config the stores start
        // dormant and first-run onboarding wires them up.
        let cfg = OWUIConfig.load()
        let socket = cfg.map { VeraSocket(config: $0) }
        let client = cfg.map { OWUIClient(config: $0) }
        let admin = cfg.map { c in
            OWUIAdminClient(baseURL: c.baseURL, modelID: c.model,
                            token: { try await socket!.currentToken() })
        }
        let storeInstance = ChatStore(config: cfg, client: client, socket: socket)
        _store = StateObject(wrappedValue: storeInstance)
        _tools = StateObject(wrappedValue: ToolsStore(admin: admin, socket: socket))
        _voice = StateObject(wrappedValue: VoiceSession(client: VoiceClient(base: cfg?.voiceBase),
                                                        socket: socket, store: storeInstance))
    }

    var body: some Scene {
        WindowGroup("Vera") {
            ContentView()
                .environmentObject(store)
                .environmentObject(tools)
                .environmentObject(voice)
                .environmentObject(config)
                .frame(minWidth: 920, minHeight: 600)
                .preferredColorScheme(.dark)
        }
        .defaultSize(width: 1180, height: 760)

        Settings {
            SettingsView()
                .environmentObject(config)
                .environmentObject(store)
        }
    }
}

/// Grabs the hosting NSWindow once and gives it a transparent, full-size-content title bar so
/// the full-height surfaces sit *under* a hidden title bar instead of being clipped by it.
struct WindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        DispatchQueue.main.async {
            guard let w = v.window else { return }
            w.titlebarAppearsTransparent = true
            w.titleVisibility = .hidden
            w.styleMask.insert(.fullSizeContentView)
        }
        return v
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
