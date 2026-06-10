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
    func start() async {
        guard let socket else { return }
        for await label in socket.statusStream {
            invocations.insert(Invocation(label: label, at: Date()), at: 0)
            if invocations.count > 20 { invocations.removeLast(invocations.count - 20) }
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
