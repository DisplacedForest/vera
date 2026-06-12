import Foundation

/// A tool OWUI exposes to the model (e.g. web_search, home_assistant).
struct ToolEntry: Identifiable, Hashable {
    let id: String
    var name: String
    var description: String
    var availableToVera: Bool   // present in Vera's model.meta.toolIds
    var lastUsed: Date?         // stamped from the live status feed
}

/// An OWUI Function (filter/pipe/action) — e.g. Adaptive Memory.
struct FunctionEntry: Identifiable, Hashable {
    let id: String
    var name: String
    var type: String       // "filter" | "pipe" | "action"
    var isActive: Bool
}

/// An external OpenAPI tool-server connection (how MCP reaches OWUI).
struct ToolServer: Identifiable, Hashable {
    var id: String { url }
    var url: String
    var name: String
    var key: String
    var enabled: Bool
}

/// A single tool/status event from Vera's pipeline — live off the socket or replayed
/// from the persisted tool log. The id only drives list identity, so it is fresh per decode.
struct Invocation: Identifiable, Hashable, Codable {
    let id = UUID()
    let label: String
    let at: Date

    private enum CodingKeys: String, CodingKey { case label, at }
}

enum ValveType { case string, int, number, bool, unknown }

/// One editable config field, derived from a tool/function's JSON-Schema valves spec.
/// `value` is always the string form so a single generic form drives every field;
/// it is serialized back to its real type on save.
struct ValveField: Identifiable, Hashable {
    var id: String { key }
    let key: String
    var title: String
    var help: String
    var type: ValveType
    var value: String
}
