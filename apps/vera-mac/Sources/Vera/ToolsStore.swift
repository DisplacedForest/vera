import SwiftUI

/// State for the MCP section: what Vera can use, plus a live tool-invocation feed.
/// Writes are optimistic and revert on failure (admin-gated).
@MainActor
final class ToolsStore: ObservableObject {
    @Published var tools: [ToolEntry] = []
    @Published var functions: [FunctionEntry] = []
    @Published var servers: [ToolServer] = []
    @Published var invocations: [Invocation] = []
    @Published var isAdmin = false
    @Published var error: String?

    private let admin: OWUIAdminClient?
    private let socket: VeraSocket?
    var isLive: Bool { admin != nil }

    init(admin: OWUIAdminClient?, socket: VeraSocket?) {
        self.admin = admin
        self.socket = socket
    }

    // MARK: Load

    func load() async {
        guard let admin else { return }
        do {
            async let toolsF = admin.listTools()
            async let funcsF = admin.listFunctions()
            async let serversF = admin.toolServers()
            async let modelF = admin.veraModel()
            async let roleF = admin.currentRole()

            var t = try await toolsF
            let enabled = Set(try await modelF.toolIds)
            for i in t.indices { t[i].availableToVera = enabled.contains(t[i].id) }
            tools = t
            functions = try await funcsF
            servers = try await serversF
            isAdmin = (try await roleF) == "admin"
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Consume the socket's status broadcast → activity feed + per-tool "last used".
    /// The feed is seeded from the persisted tool log so history survives restarts,
    /// and every live event is appended to it.
    func start() async {
        invocations = ToolLog.load()
        guard let socket else { return }
        for await label in socket.statusStream {
            let inv = Invocation(label: label, at: Date())
            invocations.insert(inv, at: 0)
            if invocations.count > ToolLog.loadLimit { invocations.removeLast(invocations.count - ToolLog.loadLimit) }
            ToolLog.append(inv)
            stampLastUsed(label)
        }
    }

    private func stampLastUsed(_ label: String) {
        let l = label.lowercased()
        let id: String? = (l.contains("knowledge") || l.contains("web") || l.contains("search")) ? "web_search"
            : (l.contains("home") || l.contains("assistant")) ? "home_assistant" : nil
        if let id, let i = tools.firstIndex(where: { $0.id == id }) { tools[i].lastUsed = Date() }
    }

    // MARK: Writes (optimistic + revert)

    func setAvailable(_ tool: ToolEntry, _ on: Bool) {
        guard isAdmin, let admin, let i = tools.firstIndex(of: tool) else { return }
        let prev = tools[i].availableToVera
        tools[i].availableToVera = on
        let ids = tools.filter { $0.availableToVera }.map(\.id)
        Task {
            do { try await admin.setVeraToolIds(ids) }
            catch {
                if let j = tools.firstIndex(where: { $0.id == tool.id }) { tools[j].availableToVera = prev }
                self.error = error.localizedDescription
            }
        }
    }

    /// Attach or detach a set of tools on the Vera model by id (the Plugins store's
    /// one-click OWUI step). Returns whether OWUI accepted the change.
    /// Install (create-or-update) the bundled Reminders OWUI tool and set its valves, so
    /// enabling Apple Reminders needs no manual OWUI paste. Reloads the tool list on success.
    /// Returns false (and sets `error`) if OWUI isn't an admin session or the write fails.
    func installReminders(veraApiURL: String, defaultList: String) async -> Bool {
        guard isAdmin, let admin else { error = "needs an OWUI admin session"; return false }
        guard let url = Bundle.module.url(forResource: "reminders_tool", withExtension: "py"),
              let content = try? String(contentsOf: url, encoding: .utf8) else {
            error = "bundled reminders tool source missing"
            return false
        }
        let meta: [String: Any] = [
            "description": "Apple Reminders read/write (shared lists included) via vera-api.",
            "manifest": ["title": "Reminders", "author": "vera",
                         "description": "Read and write Apple Reminders lists (shared lists included) via vera-api. Call for anything about a reminders or shopping list: reading it, adding items, checking items off.",
                         "version": "0.1.0"],
        ]
        do {
            try await admin.upsertTool(id: "reminders", name: "Reminders", content: content, meta: meta)
            let valves = try JSONSerialization.data(withJSONObject:
                ["vera_api_url": veraApiURL, "default_list": defaultList])
            try await admin.updateToolValves(id: "reminders", json: valves)
            await load()
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    func setToolsAttached(_ ids: [String], _ on: Bool) async -> Bool {
        guard isAdmin, let admin else { return false }
        let prev = tools
        for i in tools.indices where ids.contains(tools[i].id) { tools[i].availableToVera = on }
        let enabled = tools.filter { $0.availableToVera }.map(\.id)
        do {
            try await admin.setVeraToolIds(enabled)
            return true
        } catch {
            tools = prev
            self.error = error.localizedDescription
            return false
        }
    }

    func toggleFunction(_ fn: FunctionEntry) {
        guard isAdmin, let admin, let i = functions.firstIndex(of: fn) else { return }
        let prev = functions[i].isActive
        functions[i].isActive.toggle()
        Task {
            do {
                let now = try await admin.toggleFunction(id: fn.id)
                if let j = functions.firstIndex(where: { $0.id == fn.id }) { functions[j].isActive = now }
            } catch {
                if let j = functions.firstIndex(where: { $0.id == fn.id }) { functions[j].isActive = prev }
                self.error = error.localizedDescription
            }
        }
    }

    func addServer(url: String, name: String, key: String) {
        guard isAdmin, let admin else { return }
        let prev = servers
        servers.append(ToolServer(url: url, name: name.isEmpty ? url : name, key: key, enabled: true))
        Task { do { try await admin.setToolServers(servers) } catch { servers = prev; self.error = error.localizedDescription } }
    }

    func removeServer(_ s: ToolServer) {
        guard isAdmin, let admin else { return }
        let prev = servers
        servers.removeAll { $0.id == s.id }
        Task { do { try await admin.setToolServers(servers) } catch { servers = prev; self.error = error.localizedDescription } }
    }

    func setServerEnabled(_ s: ToolServer, _ on: Bool) {
        guard isAdmin, let admin, let i = servers.firstIndex(of: s) else { return }
        let prev = servers
        servers[i].enabled = on
        Task { do { try await admin.setToolServers(servers) } catch { servers = prev; self.error = error.localizedDescription } }
    }

    // MARK: Valves

    func loadValves(forTool id: String) async -> [ValveField] {
        guard let admin else { return [] }
        return (try? await admin.toolValves(id: id)) ?? []
    }
    func loadValves(forFunction id: String) async -> [ValveField] {
        guard let admin else { return [] }
        return (try? await admin.functionValves(id: id)) ?? []
    }

    func saveValves(id: String, isFunction: Bool, fields: [ValveField]) {
        guard isAdmin, let admin else { return }
        var values: [String: Any] = [:]
        for f in fields { values[f.key] = coerce(f) }
        guard let json = try? JSONSerialization.data(withJSONObject: values) else { return }
        Task {
            do {
                if isFunction { try await admin.updateFunctionValves(id: id, json: json) }
                else { try await admin.updateToolValves(id: id, json: json) }
            } catch { self.error = error.localizedDescription }
        }
    }

    private func coerce(_ f: ValveField) -> Any {
        switch f.type {
        case .string: return f.value
        case .int: return Int(f.value) ?? 0
        case .number: return Double(f.value) ?? 0
        case .bool: return f.value == "true"
        case .unknown:
            if let d = f.value.data(using: .utf8), let o = try? JSONSerialization.jsonObject(with: d) { return o }
            return f.value
        }
    }
}
