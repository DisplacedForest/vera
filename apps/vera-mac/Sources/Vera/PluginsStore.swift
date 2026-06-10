import SwiftUI

/// Live state for the Plugins store — vera-api's integration registry plus the OWUI
/// orchestration step (attach the right OWUI tools to the Vera model when a plugin
/// turns on). Cards render purely from the API; unreachable/unconfigured states render
/// honestly (never fake data).
@MainActor
final class PluginsStore: ObservableObject {
    enum Phase { case loading, unconfigured, unreachable, unsupported, ready }
    @Published var phase: Phase = .loading
    @Published var entries: [PluginEntry] = []
    @Published var busy: Set<String> = []
    @Published var error: String?
    /// Plugin ids whose OWUI side hasn't been wired yet (save succeeded, OWUI step didn't) —
    /// shown as "OWUI step pending" with a retry, never papered over.
    @Published var owuiPending: [String: String] = [:]

    private var client: IntegrationsClient?
    private weak var tools: ToolsStore?
    var baseDescription: String { client?.base.absoluteString ?? "vera-api" }

    func configure(base: URL?, tools: ToolsStore) {
        client = base.map { IntegrationsClient(base: $0) }
        self.tools = tools
        if client == nil { phase = .unconfigured }
    }

    func refresh() async {
        guard let client else { phase = .unconfigured; return }
        switch await client.fetch() {
        case .unreachable: phase = .unreachable
        case .unsupported: phase = .unsupported
        case .ok(let list):
            entries = list
            phase = .ready
            derivePendingFromOWUI()
        }
    }

    /// On load, surface plugins whose vera-api half is on but whose declared OWUI tools
    /// exist and aren't attached to the Vera model — an honest "half-wired" state.
    private func derivePendingFromOWUI() {
        guard let tools, tools.isLive else { return }
        let present = Dictionary(uniqueKeysWithValues: tools.tools.map { ($0.id, $0.availableToVera) })
        for e in entries where e.enabled {
            let expected = (PluginOWUI.tools[e.id] ?? []).filter { present[$0] != nil }
            if !expected.isEmpty, expected.contains(where: { present[$0] == false }),
               owuiPending[e.id] == nil {
                owuiPending[e.id] = "OWUI tools not attached yet"
            }
        }
    }

    /// Live connection probe (used by the sheet's Test button).
    func test(id: String, fields: [String: String]?) async -> (ok: Bool, detail: String) {
        guard let client else { return (false, "vera-api isn't configured") }
        return await client.test(id: id, fields: fields)
    }

    /// Save fields + enabled in one PUT, then run the plugin's OWUI step.
    /// Returns the server's refusal detail (sheet stays open), nil on success.
    func save(id: String, fields: [String: String], enable: Bool?) async -> String? {
        guard let client else { return "vera-api isn't configured" }
        busy.insert(id); defer { busy.remove(id) }
        if let detail = await client.save(id: id, fields: fields.isEmpty ? nil : fields, enabled: enable) {
            return detail
        }
        await refresh()
        if let entry = entries.first(where: { $0.id == id }) {
            await runOWUIStep(for: entry, enable: entry.enabled)
        }
        return nil
    }

    /// Toggle a whole plugin on/off from its card.
    func setEnabled(_ entry: PluginEntry, _ on: Bool) {
        guard let client else { return }
        busy.insert(entry.id)
        Task {
            if let detail = await client.save(id: entry.id, enabled: on) {
                error = detail
            } else {
                await refresh()
                await runOWUIStep(for: entry, enable: on)
            }
            busy.remove(entry.id)
        }
    }

    /// Toggle an experimental feature. `ack` must be true on a first-time enable —
    /// the consent sheet collects it; the server enforces it (400 without).
    /// Returns the refusal detail, nil on success.
    func setFeature(_ entry: PluginEntry, _ feature: PluginFeature, enabled: Bool, ack: Bool) async -> String? {
        guard let client else { return "vera-api isn't configured" }
        busy.insert(entry.id); defer { busy.remove(entry.id) }
        let detail = await client.save(id: entry.id,
                                       features: [feature.id: (enabled: enabled, ack: ack)])
        if detail == nil { await refresh() }
        return detail
    }

    // MARK: OWUI orchestration — reveal, don't replicate

    /// Attach (or detach) the plugin's declared OWUI tools on the Vera model via the
    /// existing admin client. Failure marks the card "OWUI step pending" — the vera-api
    /// half stays saved, partial state is shown honestly.
    func runOWUIStep(for entry: PluginEntry, enable: Bool) async {
        let declared = PluginOWUI.tools[entry.id] ?? []
        guard !declared.isEmpty else { owuiPending[entry.id] = nil; return }
        guard let tools, tools.isLive else {
            owuiPending[entry.id] = "OWUI isn't connected"
            return
        }
        await tools.load()
        guard tools.isAdmin else {
            owuiPending[entry.id] = "needs an OWUI admin session"
            return
        }
        // Shared tools (kitchen ⇄ grocy/mealie) detach only when no enabled plugin still claims them.
        var detachable = declared
        if !enable {
            let stillClaimed = Set(entries.filter { $0.enabled && $0.id != entry.id }
                .flatMap { PluginOWUI.tools[$0.id] ?? [] })
            detachable = declared.filter { !stillClaimed.contains($0) }
        }
        let targets = (enable ? declared : detachable).filter { id in tools.tools.contains { $0.id == id } }
        guard !targets.isEmpty else {
            owuiPending[entry.id] = enable ? "OWUI tools \(declared.joined(separator: ", ")) not installed" : nil
            return
        }
        if await tools.setToolsAttached(targets, enable) {
            owuiPending[entry.id] = nil
        } else {
            owuiPending[entry.id] = tools.error ?? "OWUI update failed"
        }
    }

    /// Retry affordance for a pending card.
    func retryOWUI(_ entry: PluginEntry) {
        busy.insert(entry.id)
        Task {
            await runOWUIStep(for: entry, enable: entry.enabled)
            busy.remove(entry.id)
        }
    }
}
