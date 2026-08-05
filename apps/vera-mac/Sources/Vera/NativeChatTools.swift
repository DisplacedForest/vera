import Foundation

enum NativeJSONValue: Codable, Equatable, Hashable, Sendable {
    case object([String: NativeJSONValue])
    case array([NativeJSONValue])
    case string(String)
    case number(Double)
    case bool(Bool)
    case null

    init(any value: Any) throws {
        switch value {
        case let value as [String: Any]:
            self = .object(try value.mapValues { try NativeJSONValue(any: $0) })
        case let value as [Any]:
            self = .array(try value.map { try NativeJSONValue(any: $0) })
        case let value as String:
            self = .string(value)
        case let value as Bool:
            self = .bool(value)
        case let value as NSNumber:
            self = .number(value.doubleValue)
        case is NSNull:
            self = .null
        default:
            throw NativeToolError.invalidArguments("Arguments contain an unsupported JSON value")
        }
    }

    var any: Any {
        switch self {
        case .object(let value): return value.mapValues(\.any)
        case .array(let value): return value.map(\.any)
        case .string(let value): return value
        case .number(let value): return value
        case .bool(let value): return value
        case .null: return NSNull()
        }
    }
}

struct NativeToolSchema: Equatable, Hashable, Sendable {
    let name: String
    let description: String
    let parameters: NativeJSONValue

    var requestObject: [String: Any] {
        [
            "type": "function",
            "function": [
                "name": name,
                "description": description,
                "parameters": parameters.any,
            ],
        ]
    }
}

struct NativeChatToolCall: Codable, Equatable, Hashable, Sendable {
    let id: String
    let name: String
    let arguments: String
}

enum NativeToolActivityState: String, Codable, Equatable, Hashable, Sendable {
    case pending
    case succeeded
    case failed
}

struct NativeToolActivity: Codable, Equatable, Hashable, Identifiable, Sendable {
    let id: String
    let toolID: String
    let name: String
    let title: String
    let round: Int
    var request: String
    var result: String?
    var state: NativeToolActivityState
    let confirmationRequired: Bool
}

struct NativeToolConfirmationRequest: Equatable, Hashable, Identifiable, Sendable {
    let id: String
    let toolID: String
    let name: String
    let title: String
    let request: String

    init(activity: NativeToolActivity) {
        id = activity.id
        toolID = activity.toolID
        name = activity.name
        title = activity.title
        request = activity.request
    }
}

enum NativeToolConfirmation: String, Equatable, Sendable {
    case none
    case required
}

enum NativeToolError: Error, LocalizedError, Equatable {
    case unavailable
    case invalidArguments(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .unavailable: return "This tool isn't available for this chat"
        case .invalidArguments(let detail): return "The tool arguments are invalid: \(detail)"
        case .failed(let detail): return detail
        }
    }
}

struct NativeToolDefinition: Sendable {
    let id: String
    let name: String
    let title: String
    let description: String
    let parameters: NativeJSONValue
    let confirmation: NativeToolConfirmation
    var timeout: Duration? = nil
    let isAvailable: @Sendable () -> Bool
    let execute: @Sendable ([String: NativeJSONValue]) async throws -> NativeJSONValue

    var schema: NativeToolSchema {
        NativeToolSchema(name: name, description: description, parameters: parameters)
    }

    func validatedArguments(_ raw: String) throws -> [String: NativeJSONValue] {
        guard let data = raw.data(using: .utf8),
              let any = try? JSONSerialization.jsonObject(with: data),
              let object = any as? [String: Any] else {
            throw NativeToolError.invalidArguments("Expected a JSON object")
        }
        let values = try object.mapValues { try NativeJSONValue(any: $0) }
        guard case .object(let schema) = parameters,
              case .object(let properties)? = schema["properties"] else { return values }
        let required: Set<String>
        if case .array(let names)? = schema["required"] {
            required = Set(names.compactMap { if case .string(let value) = $0 { value } else { nil } })
        } else {
            required = []
        }
        for name in required where values[name] == nil {
            throw NativeToolError.invalidArguments("Missing required field '\(name)'")
        }
        if schema["additionalProperties"] == .bool(false) {
            let unknown = Set(values.keys).subtracting(properties.keys)
            if let name = unknown.sorted().first {
                throw NativeToolError.invalidArguments("Unknown field '\(name)'")
            }
        }
        for (name, value) in values {
            guard case .object(let property) = properties[name],
                  case .string(let type)? = property["type"] else { continue }
            switch (type, value) {
            case ("string", .string(let text)) where !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty:
                break
            case ("boolean", .bool):
                break
            case ("string", _):
                throw NativeToolError.invalidArguments("Field '\(name)' must be a non-empty string")
            case ("boolean", _):
                throw NativeToolError.invalidArguments("Field '\(name)' must be a boolean")
            default:
                break
            }
        }
        return values
    }
}

struct NativeToolRegistry: Sendable {
    let definitions: [NativeToolDefinition]

    func active(enabledIDs: Set<String>) -> [NativeToolDefinition] {
        definitions.filter { enabledIDs.contains($0.id) && $0.isAvailable() }
    }
}

enum NativeRemindersAuthorization: Equatable, Sendable {
    case notDetermined
    case denied
    case authorized
}

struct NativeReminderList: Codable, Equatable, Sendable {
    let id: String
    let name: String
}

struct NativeReminder: Codable, Equatable, Sendable {
    let id: String
    let title: String
    let notes: String?
    let due: String?
    let completed: Bool
    let list: String?
}

protocol NativeRemindersService: AnyObject, Sendable {
    var nativeAuthorization: NativeRemindersAuthorization { get }
    func nativeRequestAccess() async -> Bool
    func nativeLists() async throws -> [NativeReminderList]
    func nativeReminders(list: String?, completed: Bool) async throws -> [NativeReminder]
    func nativeCreateReminder(list: String, title: String, notes: String?, due: String?) async throws -> NativeReminder
    func nativeCompleteReminder(id: String) async throws -> NativeReminder
}

enum NativeRemindersTools {
    static func definitions(service: any NativeRemindersService) -> [NativeToolDefinition] {
        let available: @Sendable () -> Bool = { service.nativeAuthorization == .authorized }
        return [
            NativeToolDefinition(
                id: "apple-reminders",
                name: "apple_reminders_get_lists",
                title: "List reminder lists",
                description: "List available Apple Reminders lists by exact name. Use this first when the user names a list informally, including shopping or grocery list requests, then use the returned exact name. Never invent a list name.",
                parameters: objectSchema(properties: [:], required: []),
                confirmation: .none,
                isAvailable: available,
                execute: { _ in
                    let lists = try await service.nativeLists()
                    return try encode(["lists": lists])
                }),
            NativeToolDefinition(
                id: "apple-reminders",
                name: "apple_reminders_list",
                title: "List reminders",
                description: "Read the contents of an Apple Reminders list. Use this for natural requests such as what is on my shopping list. If the exact saved list name is unknown, call apple_reminders_get_lists first.",
                parameters: objectSchema(
                    properties: [
                        "list": .object(["type": .string("string"), "description": .string("Optional reminder list name")]),
                        "completed": .object(["type": .string("boolean"), "description": .string("Whether to list completed reminders")]),
                    ], required: []),
                confirmation: .none,
                isAvailable: available,
                execute: { arguments in
                    let list = try await resolveListName(arguments.string("list"), service: service)
                    let completed = arguments.bool("completed") ?? false
                    let reminders = try await service.nativeReminders(list: list, completed: completed)
                    return try encode(["reminders": reminders])
                }),
            NativeToolDefinition(
                id: "apple-reminders",
                name: "apple_reminders_create",
                title: "Create reminder",
                description: "Create an Apple Reminder after an explicit chat request.",
                parameters: objectSchema(
                    properties: [
                        "list": .object(["type": .string("string"), "description": .string("Exact reminder list name")]),
                        "title": .object(["type": .string("string"), "description": .string("Reminder title")]),
                        "notes": .object(["type": .string("string"), "description": .string("Optional notes")]),
                        "due": .object(["type": .string("string"), "description": .string("Optional ISO 8601 date or date-time")]),
                    ], required: ["list", "title"]),
                confirmation: .none,
                isAvailable: available,
                execute: { arguments in
                    guard let list = arguments.string("list"), let title = arguments.string("title") else {
                        throw NativeToolError.invalidArguments("List and title are required")
                    }
                    guard let resolvedList = try await resolveListName(list, service: service) else {
                        throw NativeToolError.failed("No reminders list named '\(list)'")
                    }
                    let reminder = try await service.nativeCreateReminder(
                        list: resolvedList, title: title, notes: arguments.string("notes"), due: arguments.string("due"))
                    return try encode(["reminder": reminder])
                }),
            NativeToolDefinition(
                id: "apple-reminders",
                name: "apple_reminders_complete",
                title: "Complete reminder",
                description: "Mark one Apple Reminder complete after an explicit chat request.",
                parameters: objectSchema(
                    properties: [
                        "id": .object(["type": .string("string"), "description": .string("Exact reminder identifier returned by the list tool")]),
                    ], required: ["id"]),
                confirmation: .none,
                isAvailable: available,
                execute: { arguments in
                    guard let id = arguments.string("id") else {
                        throw NativeToolError.invalidArguments("A reminder identifier is required")
                    }
                    let reminder = try await service.nativeCompleteReminder(id: id)
                    return try encode(["reminder": reminder])
                }),
        ]
    }

    private static func objectSchema(
        properties: [String: NativeJSONValue], required: [String]
    ) -> NativeJSONValue {
        .object([
            "type": .string("object"),
            "properties": .object(properties),
            "required": .array(required.map(NativeJSONValue.string)),
            "additionalProperties": .bool(false),
        ])
    }

    private static func encode<T: Encodable>(_ value: T) throws -> NativeJSONValue {
        let data = try JSONEncoder().encode(value)
        return try NativeJSONValue(any: JSONSerialization.jsonObject(with: data))
    }

    static func resolveListName(_ requested: String, in lists: [NativeReminderList]) -> String? {
        let requested = requested.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !requested.isEmpty else { return nil }
        if let exact = lists.first(where: { $0.name.caseInsensitiveCompare(requested) == .orderedSame }) {
            return exact.name
        }
        let wanted = canonicalListName(requested)
        let matches = lists.filter { canonicalListName($0.name) == wanted }
        return matches.count == 1 ? matches[0].name : nil
    }

    private static func resolveListName(
        _ requested: String?, service: any NativeRemindersService
    ) async throws -> String? {
        guard let requested else { return nil }
        let lists = try await service.nativeLists()
        guard let resolved = resolveListName(requested, in: lists) else {
            throw NativeToolError.failed("No reminders list named '\(requested)'")
        }
        return resolved
    }

    private static func canonicalListName(_ value: String) -> String {
        var value = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        for suffix in [" reminders list", " reminder list", " reminders", " reminder", " list"]
        where value.hasSuffix(suffix) {
            value.removeLast(suffix.count)
            break
        }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

struct NativeToolTurnSnapshot: Equatable, Sendable {
    let content: String
    let activities: [NativeToolActivity]
}

private final class NativeToolExecutionRace: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<NativeJSONValue, Error>?
    private var finished = false
    private var cancelWork: (@Sendable () -> Void)?

    init(_ continuation: CheckedContinuation<NativeJSONValue, Error>) {
        self.continuation = continuation
    }

    func installCancellation(_ cancel: @escaping @Sendable () -> Void) {
        lock.lock()
        if finished {
            lock.unlock()
            cancel()
            return
        }
        cancelWork = cancel
        lock.unlock()
    }

    func resolve(_ result: Result<NativeJSONValue, Error>) {
        lock.lock()
        guard !finished else {
            lock.unlock()
            return
        }
        finished = true
        let continuation = continuation
        self.continuation = nil
        let cancelWork = cancelWork
        self.cancelWork = nil
        lock.unlock()
        cancelWork?()
        continuation?.resume(with: result)
    }
}

enum NativeChatHistoryBuilder {
    static func build(
        messages: [Message], systemPrompt: String,
        imageLoader: ((MessageAttachment) -> String?)? = nil,
        capabilities: ModelCapabilityProfile? = nil,
        contextCeiling: Int? = nil
    ) -> [NativeChatMessage] {
        var history: [NativeChatMessage] = []
        let prompt = systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        if !prompt.isEmpty { history.append(NativeChatMessage(role: "system", content: prompt)) }
        let allowsImages = capabilities?.acceptsImages ?? true
        for message in messages where message.state == .complete {
            if message.role == .user {
                let route = message.routeNote?.route
                var images: [String] = []
                var unloadable: [String] = []
                if allowsImages, route == nil || route == .direct, let load = imageLoader {
                    for attachment in message.attachments where attachment.isImage {
                        if let dataURL = load(attachment) {
                            images.append(dataURL)
                        } else {
                            unloadable.append(attachment.name)
                        }
                    }
                }
                var content = message.text
                if route == .bridged, let disclosure = message.routeNote?.disclosure,
                   !disclosure.isEmpty {
                    content = content.isEmpty ? disclosure : content + "\n\n" + disclosure
                }
                if !unloadable.isEmpty {
                    let marker = unloadable.map {
                        "[Attached image \"\($0)\" could not be loaded for this request]"
                    }.joined(separator: "\n")
                    content = content.isEmpty ? marker : content + "\n\n" + marker
                }
                if !content.isEmpty || !images.isEmpty {
                    history.append(NativeChatMessage(role: "user", content: content, images: images))
                }
                continue
            }
            let rounds = Dictionary(grouping: message.toolActivities, by: \.round)
            for round in rounds.keys.sorted() {
                let activities = rounds[round] ?? []
                let calls = activities.map {
                    NativeChatToolCall(id: $0.id, name: $0.name, arguments: $0.request)
                }
                history.append(NativeChatMessage(role: "assistant", content: "", toolCalls: calls))
                for activity in activities {
                    history.append(NativeChatMessage(
                        role: "tool",
                        content: activity.result ?? "{\"error\":\"Tool result unavailable\"}",
                        toolCallID: activity.id))
                }
            }
            if !message.text.isEmpty {
                history.append(NativeChatMessage(role: "assistant", content: message.text))
            }
        }
        if let capabilities, capabilities.acceptsImages {
            history = capped(history, limit: max(capabilities.maxImagesPerRequest, 1))
        }
        if let contextCeiling {
            history = trimmed(history, ceiling: contextCeiling)
        }
        return history
    }

    static let approximateCharactersPerToken = 4
    static let approximateCharactersPerImage = 4000

    static func trimmed(_ history: [NativeChatMessage], ceiling: Int) -> [NativeChatMessage] {
        let budget = ceiling * approximateCharactersPerToken
        func cost(_ message: NativeChatMessage) -> Int {
            message.content.count
                + message.images.count * approximateCharactersPerImage
                + message.toolCalls.reduce(0) { $0 + $1.name.count + $1.arguments.count }
        }
        var result = history
        var total = result.reduce(0) { $0 + cost($1) }
        while total > budget {
            guard let index = result.firstIndex(where: { $0.role != "system" }) else { break }
            let remaining = result.count - index
            guard remaining > 1 else { break }
            let dropped = result.remove(at: index)
            total -= cost(dropped)
            if !dropped.toolCalls.isEmpty {
                while index < result.count, result[index].role == "tool" {
                    let tool = result.remove(at: index)
                    total -= cost(tool)
                }
            }
        }
        return result
    }

    private static func capped(_ history: [NativeChatMessage], limit: Int) -> [NativeChatMessage] {
        var total = history.reduce(0) { $0 + $1.images.count }
        guard total > limit else { return history }
        var result = history
        for index in result.indices {
            guard total > limit else { break }
            let images = result[index].images
            guard !images.isEmpty else { continue }
            let excess = min(images.count, total - limit)
            let kept = Array(images.dropFirst(excess))
            total -= excess
            result[index] = NativeChatMessage(
                role: result[index].role, content: result[index].content,
                toolCallID: result[index].toolCallID, toolCalls: result[index].toolCalls,
                images: kept)
        }
        return result.filter { message in
            message.role != "user" || !message.content.isEmpty || !message.images.isEmpty
        }
    }
}

struct NativeToolLoop: Sendable {
    static let maximumRounds = 4
    static let maximumCalls = 8
    static let defaultExecutionTimeout = Duration.seconds(20)

    enum LoopError: Error, LocalizedError, Equatable {
        case roundLimit
        case callLimit

        var errorDescription: String? {
            switch self {
            case .roundLimit: return "The tool turn stopped after \(NativeToolLoop.maximumRounds) rounds"
            case .callLimit: return "The tool turn stopped after \(NativeToolLoop.maximumCalls) calls"
            }
        }
    }

    let transport: any NativeChatTransport
    let registry: NativeToolRegistry
    let executionTimeout: Duration
    let confirm: @Sendable (NativeToolActivity) async -> Bool

    init(
        transport: any NativeChatTransport,
        registry: NativeToolRegistry,
        executionTimeout: Duration = Self.defaultExecutionTimeout,
        confirm: @escaping @Sendable (NativeToolActivity) async -> Bool = { _ in false }
    ) {
        self.transport = transport
        self.registry = registry
        self.executionTimeout = executionTimeout
        self.confirm = confirm
    }

    func stream(
        messages: [NativeChatMessage], model: String, enabledToolIDs: Set<String>
    ) -> AsyncThrowingStream<NativeToolTurnSnapshot, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let active = registry.active(enabledIDs: enabledToolIDs)
                    let definitions = Dictionary(
                        active.map { ($0.name, $0) }, uniquingKeysWith: { first, _ in first })
                    var seen: Set<String> = []
                    let schemas = active.filter { seen.insert($0.name).inserted }.map(\.schema)
                    var history = messages
                    var content = ""
                    var activities: [NativeToolActivity] = []
                    var callCount = 0
                    for round in 1...(Self.maximumRounds + 1) {
                        var last = NativeChatStreamSnapshot(content: "", toolCalls: [], finishReason: nil)
                        do {
                            for try await snapshot in transport.stream(messages: history, model: model, tools: schemas) {
                                last = snapshot
                                for call in snapshot.toolCalls {
                                    let definition = definitions[call.name]
                                    let activity = NativeToolActivity(
                                        id: call.id,
                                        toolID: definition?.id ?? call.name,
                                        name: call.name,
                                        title: definition?.title ?? call.name,
                                        round: round,
                                        request: call.arguments,
                                        result: nil,
                                        state: .pending,
                                        confirmationRequired: definition?.confirmation == .required)
                                    if let index = activities.firstIndex(where: { $0.id == call.id }) {
                                        if activities[index].state == .pending { activities[index] = activity }
                                    } else {
                                        activities.append(activity)
                                    }
                                }
                                continuation.yield(NativeToolTurnSnapshot(
                                    content: Self.join(content, snapshot.content), activities: activities))
                            }
                        } catch is CancellationError {
                            throw CancellationError()
                        } catch {
                            content = Self.join(content, last.content)
                            Self.failPending(in: &activities, message: error.localizedDescription)
                            continuation.yield(NativeToolTurnSnapshot(
                                content: content, activities: activities))
                            throw error
                        }
                        content = Self.join(content, last.content)
                        guard !last.toolCalls.isEmpty else {
                            continuation.yield(NativeToolTurnSnapshot(content: content, activities: activities))
                            continuation.finish()
                            return
                        }
                        if callCount + last.toolCalls.count > Self.maximumCalls {
                            Self.fail(last.toolCalls, in: &activities, message: LoopError.callLimit.localizedDescription)
                            continuation.yield(NativeToolTurnSnapshot(content: content, activities: activities))
                            throw LoopError.callLimit
                        }
                        if round > Self.maximumRounds {
                            Self.fail(last.toolCalls, in: &activities, message: LoopError.roundLimit.localizedDescription)
                            continuation.yield(NativeToolTurnSnapshot(content: content, activities: activities))
                            throw LoopError.roundLimit
                        }
                        callCount += last.toolCalls.count
                        history.append(NativeChatMessage(role: "assistant", content: last.content, toolCalls: last.toolCalls))
                        for call in last.toolCalls {
                            let result: String
                            let state: NativeToolActivityState
                            do {
                                guard let definition = definitions[call.name], definition.isAvailable() else {
                                    throw NativeToolError.unavailable
                                }
                                guard !call.id.isEmpty, !call.name.isEmpty else {
                                    throw NativeToolError.invalidArguments("Tool call identifier and name are required")
                                }
                                let arguments = try definition.validatedArguments(call.arguments)
                                if definition.confirmation == .required {
                                    guard let activity = activities.first(where: { $0.id == call.id }),
                                          await confirm(activity) else {
                                        throw NativeToolError.failed("The tool call was not confirmed")
                                    }
                                }
                                let value = try await Self.execute(
                                    definition, arguments: arguments,
                                    timeout: definition.timeout ?? executionTimeout)
                                result = try Self.serializedResult(value)
                                state = .succeeded
                            } catch {
                                result = Self.errorJSON(error.localizedDescription)
                                state = .failed
                            }
                            if let index = activities.firstIndex(where: { $0.id == call.id }) {
                                activities[index].result = result
                                activities[index].state = state
                            }
                            history.append(NativeChatMessage(role: "tool", content: result, toolCallID: call.id))
                            continuation.yield(NativeToolTurnSnapshot(content: content, activities: activities))
                        }
                    }
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private static func join(_ prior: String, _ next: String) -> String {
        guard !prior.isEmpty else { return next }
        guard !next.isEmpty else { return prior }
        return prior + "\n\n" + next
    }

    private static func execute(
        _ definition: NativeToolDefinition,
        arguments: [String: NativeJSONValue],
        timeout: Duration
    ) async throws -> NativeJSONValue {
        try await withCheckedThrowingContinuation { continuation in
            let race = NativeToolExecutionRace(continuation)
            let work = Task {
                do {
                    race.resolve(.success(try await definition.execute(arguments)))
                } catch {
                    race.resolve(.failure(error))
                }
            }
            race.installCancellation { work.cancel() }
            Task {
                do {
                    try await Task.sleep(for: timeout)
                    race.resolve(.failure(NativeToolError.failed("The tool call timed out")))
                } catch {
                }
            }
        }
    }

    private static func fail(_ calls: [NativeChatToolCall], in activities: inout [NativeToolActivity], message: String) {
        for call in calls {
            if let index = activities.firstIndex(where: { $0.id == call.id }) {
                activities[index].state = .failed
                activities[index].result = errorJSON(message)
            }
        }
    }

    private static func failPending(in activities: inout [NativeToolActivity], message: String) {
        for index in activities.indices where activities[index].state == .pending {
            activities[index].state = .failed
            activities[index].result = errorJSON(message)
        }
    }

    static func serializedResult(_ value: NativeJSONValue) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: value.any, options: [.sortedKeys])
        return String(decoding: data, as: UTF8.self)
    }

    private static func errorJSON(_ message: String) -> String {
        let bounded = String(message.prefix(500))
        let data = try? JSONSerialization.data(withJSONObject: ["error": bounded], options: [.sortedKeys])
        return data.map { String(decoding: $0, as: UTF8.self) } ?? "{\"error\":\"Tool failed\"}"
    }
}

private extension Dictionary where Key == String, Value == NativeJSONValue {
    func string(_ key: String) -> String? {
        guard case .string(let value)? = self[key] else { return nil }
        return value
    }

    func bool(_ key: String) -> Bool? {
        guard case .bool(let value)? = self[key] else { return nil }
        return value
    }
}
