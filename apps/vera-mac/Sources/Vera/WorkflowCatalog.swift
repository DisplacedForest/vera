import AppKit
import SwiftUI

enum WorkflowConfigValue: Hashable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)

    static func parse(_ raw: Any) -> WorkflowConfigValue {
        if let number = raw as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return .bool(number.boolValue) }
            if CFNumberIsFloatType(number) { return .double(number.doubleValue) }
            return .int(number.intValue)
        }
        if let text = raw as? String { return .string(text) }
        return .string(String(describing: raw))
    }

    var jsonValue: Any {
        switch self {
        case .string(let value): return value
        case .int(let value): return value
        case .double(let value): return value
        case .bool(let value): return value
        }
    }

    var display: String {
        switch self {
        case .string(let value): return value.isEmpty ? value : value.prefix(1).uppercased() + value.dropFirst()
        case .int(let value): return String(value)
        case .double(let value): return value.formatted(.number.precision(.fractionLength(1)))
        case .bool(let value): return value ? "On" : "Off"
        }
    }

    var doubleValue: Double? {
        switch self {
        case .int(let value): return Double(value)
        case .double(let value): return value
        default: return nil
        }
    }
}

struct WorkflowSchemaField: Hashable, Identifiable {
    enum Kind: Hashable {
        case choice([WorkflowConfigValue])
        case number(min: Double?, max: Double?)
        case bool
        case text
    }

    let key: String
    let kind: Kind
    let defaultValue: WorkflowConfigValue?

    var id: String { key }

    var label: String {
        let words = key.split(separator: "_").joined(separator: " ")
        return words.isEmpty ? key : words.prefix(1).uppercased() + words.dropFirst()
    }

    static func parse(key: String, raw: Any) -> WorkflowSchemaField? {
        guard let object = raw as? [String: Any], let type = object["type"] as? String else { return nil }
        let fallback = object["default"].map(WorkflowConfigValue.parse)
        switch type {
        case "choice":
            guard let options = object["options"] as? [Any], !options.isEmpty else { return nil }
            return WorkflowSchemaField(key: key, kind: .choice(options.map(WorkflowConfigValue.parse)), defaultValue: fallback)
        case "number":
            guard let low = bound(object["min"]), let high = bound(object["max"]) else { return nil }
            return WorkflowSchemaField(key: key, kind: .number(min: low, max: high), defaultValue: fallback)
        case "bool":
            return WorkflowSchemaField(key: key, kind: .bool, defaultValue: fallback)
        case "text":
            return WorkflowSchemaField(key: key, kind: .text, defaultValue: fallback)
        default:
            return nil
        }
    }

    private static func bound(_ raw: Any?) -> Double?? {
        guard let raw else { return .some(nil) }
        guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        return .some(number.doubleValue)
    }
}

func workflowDisplayName(_ type: String) -> String {
    let tail = type.split(separator: ".").last.map(String.init) ?? type
    var words: [String] = []
    var current = ""
    for character in tail {
        if character == "_" || character == "-" {
            if !current.isEmpty { words.append(current); current = "" }
        } else if character.isUppercase, current.last?.isLowercase == true {
            words.append(current)
            current = String(character)
        } else {
            current.append(character)
        }
    }
    if !current.isEmpty { words.append(current) }
    let joined = words.joined(separator: " ").lowercased()
    return joined.isEmpty ? tail : joined.prefix(1).uppercased() + joined.dropFirst()
}

struct WorkflowCatalogNode: Hashable, Identifiable {
    let type: String
    let label: String
    let description: String
    let icon: String
    let tint: String
    let category: String
    let insertable: Bool
    let fields: [WorkflowSchemaField]

    var id: String { type }

    var categoryLabel: String {
        category.isEmpty ? "Node" : category.prefix(1).uppercased() + category.dropFirst()
    }

    var defaultConfig: [String: WorkflowConfigValue] {
        fields.reduce(into: [:]) { result, field in
            if let value = field.defaultValue { result[field.key] = value }
        }
    }

    static func parse(_ raw: Any) -> WorkflowCatalogNode? {
        guard let object = raw as? [String: Any],
              let type = object["type"] as? String,
              let label = object["label"] as? String,
              let description = object["description"] as? String,
              !description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let icon = object["icon"] as? String ?? "puzzlepiece"
        let resolvedIcon = NSImage(systemSymbolName: icon, accessibilityDescription: nil) == nil ? "puzzlepiece" : icon
        let schema: [String: Any]
        if let rawSchema = object["config_schema"] {
            guard let dictionary = rawSchema as? [String: Any] else { return nil }
            schema = dictionary
        } else {
            schema = [:]
        }
        var fields: [WorkflowSchemaField] = []
        for key in schema.keys.sorted() {
            guard let raw = schema[key] as? [String: Any], let fieldType = raw["type"] as? String else { return nil }
            if let field = WorkflowSchemaField.parse(key: key, raw: raw) {
                fields.append(field)
            } else if ["choice", "number", "bool", "text"].contains(fieldType) {
                return nil
            }
        }
        return WorkflowCatalogNode(type: type,
                                   label: label,
                                   description: description,
                                   icon: resolvedIcon,
                                   tint: object["tint"] as? String ?? "accent",
                                   category: object["category"] as? String ?? "transform",
                                   insertable: object["insertable"] as? Bool ?? false,
                                   fields: fields)
    }
}

struct WorkflowProfilePair: Hashable {
    let types: [String]
    let after: String
    let before: String

    static func parse(_ raw: Any) -> WorkflowProfilePair? {
        guard let object = raw as? [String: Any],
              let types = object["types"] as? [String],
              let after = object["after"] as? String,
              let before = object["before"] as? String else { return nil }
        return WorkflowProfilePair(types: types, after: after, before: before)
    }
}

struct WorkflowProfile: Hashable {
    let id: String
    let spine: [String]
    let insertableCategories: [String]
    let pairs: [WorkflowProfilePair]
    let triggers: [String]

    var pairTypes: [String] { pairs.flatMap(\.types) }

    var canonicalOrder: [String] {
        var order = triggers + spine
        for pair in pairs {
            let anchor = order.firstIndex(of: pair.before) ?? order.endIndex
            order.insert(contentsOf: pair.types, at: anchor)
        }
        return order
    }

    static func parse(_ raw: Any) -> WorkflowProfile? {
        guard let object = raw as? [String: Any], let id = object["id"] as? String,
              let spine = stringList(object["spine"]),
              let categories = stringList(object["insertable_categories"]),
              let triggers = stringList(object["triggers"]) else { return nil }
        var pairs: [WorkflowProfilePair] = []
        if let rawPairs = object["pairs"] {
            guard let list = rawPairs as? [Any] else { return nil }
            for entry in list {
                guard let pair = WorkflowProfilePair.parse(entry) else { return nil }
                pairs.append(pair)
            }
        }
        return WorkflowProfile(id: id, spine: spine, insertableCategories: categories, pairs: pairs,
                               triggers: triggers)
    }

    private static func stringList(_ raw: Any?) -> [String]? {
        guard let raw else { return [] }
        return raw as? [String]
    }
}

struct WorkflowCatalog: Hashable {
    let nodes: [WorkflowCatalogNode]
    let profile: WorkflowProfile

    func node(for type: String) -> WorkflowCatalogNode? {
        nodes.first { $0.type == type }
    }

    func label(for type: String) -> String {
        node(for: type)?.label ?? workflowDisplayName(type)
    }

    var paletteNodes: [WorkflowCatalogNode] {
        nodes.filter { entry in
            profile.triggers.contains(entry.type)
                || profile.spine.contains(entry.type)
                || profile.pairTypes.contains(entry.type)
                || (entry.insertable && profile.insertableCategories.contains(entry.category))
        }
    }

    var paletteCategories: [String] {
        var order: [String] = []
        for entry in paletteNodes where !order.contains(entry.category) {
            order.append(entry.category)
        }
        return order
    }

    func isRequired(_ type: String) -> Bool {
        profile.spine.contains(type) || profile.triggers.contains(type)
    }

    func isTriggerType(_ type: String) -> Bool {
        node(for: type)?.category == "trigger"
    }

    func triggerIDs(in definition: PulseWorkflowDefinition) -> Set<String> {
        Set(definition.nodes.filter { isTriggerType($0.type) }.map(\.id))
    }

    func canonicalIndex(of type: String) -> Int? {
        profile.canonicalOrder.firstIndex(of: type)
    }

    static func parse(_ raw: Any) -> WorkflowCatalog? {
        guard let object = raw as? [String: Any],
              let rawNodes = object["nodes"] as? [Any],
              let profile = WorkflowProfile.parse(object["profile"] as Any) else { return nil }
        var nodes: [WorkflowCatalogNode] = []
        for rawNode in rawNodes {
            guard let node = WorkflowCatalogNode.parse(rawNode) else { return nil }
            nodes.append(node)
        }
        return nodes.isEmpty ? nil : WorkflowCatalog(nodes: nodes, profile: profile)
    }
}
