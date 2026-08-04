import Foundation
import AVFoundation

final class SelfTestNativeTransport: NativeChatTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var captured: [[NativeChatMessage]] = []
    private var shouldInterrupt = false

    var histories: [[NativeChatMessage]] { withLock { captured } }
    var interrupt: Bool {
        get { withLock { shouldInterrupt } }
        set { withLock { shouldInterrupt = newValue } }
    }

    func discoverModels() async throws -> [String] { ["local-model"] }

    func stream(
        messages: [NativeChatMessage], model: String, tools: [NativeToolSchema]
    ) -> AsyncThrowingStream<NativeChatStreamSnapshot, Error> {
        withLock { captured.append(messages) }
        let interrupted = interrupt
        return AsyncThrowingStream { continuation in
            continuation.yield(NativeChatStreamSnapshot(
                content: interrupted ? "Partial" : "Native reply", toolCalls: [], finishReason: "stop"))
            if interrupted {
                continuation.finish(throwing: NativeChatClient.ClientError.server("test interruption"))
            } else {
                continuation.finish()
            }
        }
    }

    private func withLock<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}

final class SelfTestPulseFeed: PulseFeedProviding, @unchecked Sendable {
    private let lock = NSLock()
    private var scripted: PulseFeedResult
    private var served = 0

    init(_ result: PulseFeedResult) { scripted = result }

    var callCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return served
    }

    func set(_ result: PulseFeedResult) {
        lock.lock()
        defer { lock.unlock() }
        scripted = result
    }

    func pulseFeed() async -> PulseFeedResult { serve() }

    private func serve() -> PulseFeedResult {
        lock.lock()
        defer { lock.unlock() }
        served += 1
        return scripted
    }
}

final class SelfTestFailingCreateRepository: ChatRepository, @unchecked Sendable {
    let inner: LocalChatRepository

    init(inner: LocalChatRepository) { self.inner = inner }

    func listConversations() throws -> [Conversation] { try inner.listConversations() }
    func messages(conversationID: String) throws -> [Message] { try inner.messages(conversationID: conversationID) }
    func recentMessages(conversationID: String, limit: Int) throws -> [Message] {
        try inner.recentMessages(conversationID: conversationID, limit: limit)
    }
    func saveConversation(_ conversation: Conversation) throws { try inner.saveConversation(conversation) }
    func saveMessage(_ message: Message, conversationID: String, ordinal: Int) throws {
        try inner.saveMessage(message, conversationID: conversationID, ordinal: ordinal)
    }
    func deleteConversation(_ id: String) throws { try inner.deleteConversation(id) }
    func conversation(originType: String, originID: String) throws -> Conversation? {
        try inner.conversation(originType: originType, originID: originID)
    }
    func createOriginConversation(_ conversation: Conversation, seed: Message) throws {
        throw NSError(domain: "SelfTest", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "scripted create failure"])
    }
}

final class ScriptedNativeToolTransport: NativeChatTransport, @unchecked Sendable {
    private let lock = NSLock()
    private let rounds: [[NativeChatStreamSnapshot]]
    private var index = 0
    private var capturedHistories: [[NativeChatMessage]] = []
    private var capturedSchemas: [[NativeToolSchema]] = []

    init(rounds: [[NativeChatStreamSnapshot]]) {
        self.rounds = rounds
    }

    var histories: [[NativeChatMessage]] { locked { capturedHistories } }
    var schemas: [[NativeToolSchema]] { locked { capturedSchemas } }

    func discoverModels() async throws -> [String] { ["local-model"] }

    func stream(
        messages: [NativeChatMessage], model: String, tools: [NativeToolSchema]
    ) -> AsyncThrowingStream<NativeChatStreamSnapshot, Error> {
        let snapshots: [NativeChatStreamSnapshot] = locked {
            capturedHistories.append(messages)
            capturedSchemas.append(tools)
            defer { index += 1 }
            return rounds.indices.contains(index) ? rounds[index] : []
        }
        return AsyncThrowingStream { continuation in
            for snapshot in snapshots { continuation.yield(snapshot) }
            continuation.finish()
        }
    }

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}

final class InterruptedNativeToolTransport: NativeChatTransport, @unchecked Sendable {
    func discoverModels() async throws -> [String] { ["local-model"] }

    func stream(
        messages: [NativeChatMessage], model: String, tools: [NativeToolSchema]
    ) -> AsyncThrowingStream<NativeChatStreamSnapshot, Error> {
        AsyncThrowingStream { continuation in
            continuation.yield(NativeChatStreamSnapshot(
                content: "Partial response", toolCalls: [
                    NativeChatToolCall(id: "interrupted", name: "apple_reminders_list", arguments: "{}"),
                ], finishReason: nil))
            continuation.finish(throwing: NativeToolError.failed("Test transport interruption"))
        }
    }
}

final class SelfTestRemindersService: NativeRemindersService, @unchecked Sendable {
    var authorized = true
    var shouldFail = false
    var listCalls = 0
    var createCalls = 0
    var completeCalls = 0

    var nativeAuthorization: NativeRemindersAuthorization { authorized ? .authorized : .denied }

    func nativeRequestAccess() async -> Bool {
        authorized = true
        return true
    }

    func nativeLists() async throws -> [NativeReminderList] {
        [NativeReminderList(id: "errands", name: "Errands")]
    }

    func nativeReminders(list: String?, completed: Bool) async throws -> [NativeReminder] {
        if shouldFail { throw NativeToolError.failed("EventKit test failure") }
        listCalls += 1
        return [NativeReminder(
            id: completed ? "done" : "open", title: completed ? "Finished" : "Buy milk",
            notes: nil, due: nil, completed: completed, list: list ?? "Errands")]
    }

    func nativeCreateReminder(
        list: String, title: String, notes: String?, due: String?
    ) async throws -> NativeReminder {
        if shouldFail { throw NativeToolError.failed("EventKit test failure") }
        createCalls += 1
        return NativeReminder(id: "created", title: title, notes: notes, due: due, completed: false, list: list)
    }

    func nativeCompleteReminder(id: String) async throws -> NativeReminder {
        if shouldFail { throw NativeToolError.failed("EventKit test failure") }
        completeCalls += 1
        return NativeReminder(id: id, title: "Done", notes: nil, due: nil, completed: true, list: "Errands")
    }
}

final class SelfTestToolExecutionState: @unchecked Sendable {
    private let lock = NSLock()
    private var executions = 0

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return executions
    }

    func execute() -> NativeJSONValue {
        lock.lock()
        executions += 1
        lock.unlock()
        return .object(["ok": .bool(true)])
    }
}

final class SelfTestMemoryService: NativeMemoryServing, @unchecked Sendable {
    private let lock = NSLock()
    private var embeds = 0
    private var extracts = 0

    var embedCalls: Int { locked { embeds } }
    var extractionCalls: Int { locked { extracts } }

    func embed(_ text: String) async throws -> [Double] {
        locked { embeds += 1 }
        return [1, 0]
    }

    func proposals(
        user: String, assistant: String, sourceConversationID: String?, sourceMessageID: String?,
        existing: [NativeMemoryRecord], now: Date
    ) async throws -> [NativeMemoryProposal] {
        locked { extracts += 1 }
        return [NativeMemoryProposal(
            id: UUID().uuidString, kind: .create, title: "Meeting preference",
            summary: "Prefers morning meetings", details: ["The user prefers morning meetings"],
            group: .you, category: .preference, bank: "personal", durability: .durable,
            expiry: nil, targetIDs: [], sourceConversationID: sourceConversationID,
            sourceMessageID: sourceMessageID, createdAt: now, status: .pending)]
    }

    func proposal(
        request: String, existing: [NativeMemoryRecord], now: Date
    ) async throws -> [NativeMemoryProposal] {
        locked { extracts += 1 }
        return [NativeMemoryProposal(
            id: UUID().uuidString, kind: .create, title: "Requested change",
            summary: request, details: [request], group: .you, category: .preference,
            bank: "personal", durability: .durable, expiry: nil, targetIDs: [],
            sourceConversationID: nil, sourceMessageID: nil, createdAt: now, status: .pending)]
    }

    private func locked<T>(_ work: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return work()
    }
}

@MainActor
enum SelfTest {
    static func run() async {
        runPure()
        runNativeSettings()
        await runNativeTools()
        await runNativeMemory()
        await runNativeStore()
        await runPulseContinuation()
        guard let cfg = OWUIConfig.load() else {
            print("SELFTEST OK (offline). No OWUI config (~/.vera/config.json), live checks skipped")
            exit(0)
        }
        await runLive(cfg)
    }

    private static func runNativeTools() async {
        do {
            let service = SelfTestRemindersService()
            let registry = NativeToolRegistry(definitions: NativeRemindersTools.definitions(service: service))
            guard registry.active(enabledIDs: []).isEmpty,
                  registry.active(enabledIDs: ["apple-reminders"]).count == 4 else {
                print("SELFTEST ERROR: native tool registry filtering"); exit(1)
            }
            service.authorized = false
            guard registry.active(enabledIDs: ["apple-reminders"]).isEmpty else {
                print("SELFTEST ERROR: unavailable native tool exposed"); exit(1)
            }
            service.authorized = true

            guard NativeRemindersTools.resolveListName(
                "Shopping List",
                in: [
                    NativeReminderList(id: "shopping", name: "Shopping"),
                    NativeReminderList(id: "work", name: "Work"),
                ]) == "Shopping",
                  NativeRemindersTools.resolveListName(
                    "List",
                    in: [
                        NativeReminderList(id: "one", name: "List"),
                        NativeReminderList(id: "two", name: "Reminders"),
                    ]) == "List" else {
                print("SELFTEST ERROR: native reminder list alias resolution"); exit(1)
            }

            var accumulator = NativeChatDeltaAccumulator()
            _ = try accumulator.consume("{\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_1\",\"type\":\"function\",\"function\":{\"name\":\"apple_reminders_\",\"arguments\":\"{\\\"list\\\":\\\"Err\"}}]}}]}")
            let fragmented = try accumulator.consume("{\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"name\":\"list\",\"arguments\":\"ands\\\"}\"}},{\"index\":1,\"id\":\"call_2\",\"type\":\"function\",\"function\":{\"name\":\"apple_reminders_list\",\"arguments\":\"{}\"}}]},\"finish_reason\":\"tool_calls\"}]}")
            guard fragmented.toolCalls.count == 2,
                  fragmented.toolCalls[0].name == "apple_reminders_list",
                  fragmented.toolCalls[0].arguments == "{\"list\":\"Errands\"}" else {
                print("SELFTEST ERROR: streamed native tool accumulation"); exit(1)
            }

            let requestClient = NativeChatClient(config: NativeChatConfig(
                baseURL: URL(string: "https://models.example/v1")!, apiKey: nil,
                model: "local-model", chatTemplateKwargs: "{\"enable_thinking\":false}"))
            let request = try requestClient.request(
                messages: [NativeChatMessage(role: "user", content: "List reminders")],
                model: "local-model",
                tools: registry.active(enabledIDs: ["apple-reminders"]).map(\.schema))
            let requestBody = try JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any]
            let requestTools = requestBody?["tools"] as? [[String: Any]]
            let requestKwargs = requestBody?["chat_template_kwargs"] as? [String: Any]
            let requestNames = requestTools?.compactMap { ($0["function"] as? [String: Any])?["name"] as? String }
            guard requestTools?.count == 4,
                  requestNames?.contains("apple_reminders_list") == true,
                  requestNames?.contains("apple_reminders_get_lists") == true,
                  requestKwargs?["enable_thinking"] as? Bool == false,
                  requestBody?["tool_choice"] as? String == "auto" else {
                print("SELFTEST ERROR: standard native tool request shape"); exit(1)
            }

            let transport = ScriptedNativeToolTransport(rounds: [
                [NativeChatStreamSnapshot(content: "", toolCalls: [
                    NativeChatToolCall(id: "call_1", name: "apple_reminders_list", arguments: "{}"),
                    NativeChatToolCall(id: "call_2", name: "apple_reminders_list", arguments: "{\"completed\":true}"),
                ], finishReason: "tool_calls")],
                [NativeChatStreamSnapshot(content: "You have two reminders.", toolCalls: [], finishReason: "stop")],
            ])
            let loop = NativeToolLoop(transport: transport, registry: registry)
            var final: NativeToolTurnSnapshot?
            for try await snapshot in loop.stream(
                messages: [NativeChatMessage(role: "user", content: "What is due?")],
                model: "local-model", enabledToolIDs: ["apple-reminders"]) {
                final = snapshot
            }
            guard final?.content == "You have two reminders.",
                  final?.activities.count == 2,
                  final?.activities.allSatisfy({ $0.state == .succeeded }) == true,
                  service.listCalls == 2,
                  transport.histories.count == 2,
                  transport.histories[0].first(where: { $0.role == "system" })?.content.contains(
                    "Do not claim that you lack access") == true,
                  transport.histories[1].contains(where: { $0.role == "tool" && $0.toolCallID == "call_1" }) else {
                print("SELFTEST ERROR: native tool continuation"); exit(1)
            }

            let invalidTransport = ScriptedNativeToolTransport(rounds: [[
                NativeChatStreamSnapshot(content: "", toolCalls: [
                    NativeChatToolCall(id: "bad", name: "apple_reminders_create", arguments: "{\"title\":7}"),
                ], finishReason: "tool_calls")
            ], [NativeChatStreamSnapshot(content: "I could not create it.", toolCalls: [], finishReason: "stop")]])
            var invalidFinal: NativeToolTurnSnapshot?
            for try await snapshot in NativeToolLoop(transport: invalidTransport, registry: registry).stream(
                messages: [NativeChatMessage(role: "user", content: "Add it")],
                model: "local-model", enabledToolIDs: ["apple-reminders"]) {
                invalidFinal = snapshot
            }
            guard invalidFinal?.activities.first?.state == .failed, service.createCalls == 0 else {
                print("SELFTEST ERROR: malformed native tool executed"); exit(1)
            }

            let unknownTransport = ScriptedNativeToolTransport(rounds: [[
                NativeChatStreamSnapshot(content: "", toolCalls: [
                    NativeChatToolCall(id: "unknown", name: "not_registered", arguments: "{}"),
                ], finishReason: "tool_calls")
            ], [NativeChatStreamSnapshot(content: "That tool is unavailable.", toolCalls: [], finishReason: "stop")]])
            var unknownFinal: NativeToolTurnSnapshot?
            for try await snapshot in NativeToolLoop(transport: unknownTransport, registry: registry).stream(
                messages: [NativeChatMessage(role: "user", content: "Use another tool")],
                model: "local-model", enabledToolIDs: []) {
                unknownFinal = snapshot
            }
            guard unknownFinal?.activities.first?.state == .failed,
                  unknownTransport.schemas.first?.isEmpty == true else {
                print("SELFTEST ERROR: unknown or disabled native tool executed"); exit(1)
            }

            var interruptedFinal: NativeToolTurnSnapshot?
            do {
                for try await snapshot in NativeToolLoop(
                    transport: InterruptedNativeToolTransport(), registry: registry
                ).stream(
                    messages: [NativeChatMessage(role: "user", content: "List reminders")],
                    model: "local-model", enabledToolIDs: ["apple-reminders"]
                ) {
                    interruptedFinal = snapshot
                }
                print("SELFTEST ERROR: interrupted native tool stream completed"); exit(1)
            } catch NativeToolError.failed(let detail) where detail == "Test transport interruption" {
            }
            guard interruptedFinal?.content == "Partial response",
                  interruptedFinal?.activities.first?.state == .failed,
                  interruptedFinal?.activities.first?.result?.contains("Test transport interruption") == true else {
                print("SELFTEST ERROR: interrupted native tool remained pending"); exit(1)
            }

            let baselineLists = service.listCalls
            let limitedTransport = ScriptedNativeToolTransport(rounds: (1...(NativeToolLoop.maximumRounds + 1)).map { round in
                [NativeChatStreamSnapshot(content: "", toolCalls: [
                    NativeChatToolCall(
                        id: "limit_\(round)",
                        name: round > NativeToolLoop.maximumRounds ? "apple_reminders_list" : "not_registered",
                        arguments: "{}"),
                ], finishReason: "tool_calls")]
            })
            var limitedFinal: NativeToolTurnSnapshot?
            do {
                for try await snapshot in NativeToolLoop(transport: limitedTransport, registry: registry).stream(
                    messages: [NativeChatMessage(role: "user", content: "Keep going")],
                    model: "local-model", enabledToolIDs: ["apple-reminders"]) {
                    limitedFinal = snapshot
                }
                print("SELFTEST ERROR: native tool round limit not enforced"); exit(1)
            } catch NativeToolLoop.LoopError.roundLimit {
            }
            guard service.listCalls == baselineLists,
                  limitedFinal?.activities.last?.state == .failed else {
                print("SELFTEST ERROR: native tool executed beyond round limit"); exit(1)
            }

            let active = registry.active(enabledIDs: ["apple-reminders"])
            let create = active.first { $0.name == "apple_reminders_create" }!
            let complete = active.first { $0.name == "apple_reminders_complete" }!
            _ = try await create.execute(create.validatedArguments("{\"list\":\"Errands\",\"title\":\"Buy milk\"}"))
            _ = try await complete.execute(complete.validatedArguments("{\"id\":\"created\"}"))
            guard service.createCalls == 1, service.completeCalls == 1 else {
                print("SELFTEST ERROR: reminders create or complete executor"); exit(1)
            }
            service.shouldFail = true
            do {
                _ = try await create.execute(create.validatedArguments("{\"list\":\"Errands\",\"title\":\"Fail\"}"))
                print("SELFTEST ERROR: reminders executor failure hidden"); exit(1)
            } catch NativeToolError.failed(let detail) where detail == "EventKit test failure" {
            }
            service.shouldFail = false

            let textTransport = ScriptedNativeToolTransport(rounds: [[
                NativeChatStreamSnapshot(content: "Plain text works.", toolCalls: [], finishReason: "stop")
            ]])
            var textFinal: NativeToolTurnSnapshot?
            for try await snapshot in NativeToolLoop(transport: textTransport, registry: registry).stream(
                messages: [NativeChatMessage(role: "user", content: "Hello")],
                model: "local-model", enabledToolIDs: []) {
                textFinal = snapshot
            }
            guard textFinal?.content == "Plain text works.", textFinal?.activities.isEmpty == true,
                  textTransport.schemas.first?.isEmpty == true else {
                print("SELFTEST ERROR: text-only native endpoint behavior"); exit(1)
            }

            let confirmationState = SelfTestToolExecutionState()
            let confirmationDefinition = NativeToolDefinition(
                id: "confirmation-test", name: "confirmation_test", title: "Confirmation test",
                description: "Exercise confirmation gating.",
                parameters: .object([
                    "type": .string("object"), "properties": .object([:]),
                    "required": .array([]), "additionalProperties": .bool(false),
                ]),
                confirmation: .required,
                isAvailable: { true },
                execute: { _ in confirmationState.execute() })
            let confirmationRegistry = NativeToolRegistry(definitions: [confirmationDefinition])
            func confirmationTransport(_ id: String) -> ScriptedNativeToolTransport {
                ScriptedNativeToolTransport(rounds: [[
                    NativeChatStreamSnapshot(content: "", toolCalls: [
                        NativeChatToolCall(id: id, name: "confirmation_test", arguments: "{}"),
                    ], finishReason: "tool_calls")
                ], [NativeChatStreamSnapshot(content: "Finished.", toolCalls: [], finishReason: "stop")]])
            }
            var deniedFinal: NativeToolTurnSnapshot?
            for try await snapshot in NativeToolLoop(
                transport: confirmationTransport("denied"), registry: confirmationRegistry
            ).stream(
                messages: [NativeChatMessage(role: "user", content: "Run it")],
                model: "local-model", enabledToolIDs: ["confirmation-test"]
            ) {
                deniedFinal = snapshot
            }
            guard deniedFinal?.activities.first?.state == .failed,
                  deniedFinal?.activities.first?.result?.contains("not confirmed") == true,
                  confirmationState.count == 0 else {
                print("SELFTEST ERROR: confirmation denial executed native tool"); exit(1)
            }
            var confirmedFinal: NativeToolTurnSnapshot?
            for try await snapshot in NativeToolLoop(
                transport: confirmationTransport("confirmed"), registry: confirmationRegistry,
                confirm: { _ in true }
            ).stream(
                messages: [NativeChatMessage(role: "user", content: "Run it")],
                model: "local-model", enabledToolIDs: ["confirmation-test"]
            ) {
                confirmedFinal = snapshot
            }
            guard confirmedFinal?.activities.first?.state == .succeeded,
                  confirmationState.count == 1 else {
                print("SELFTEST ERROR: confirmed native tool did not execute"); exit(1)
            }

            let stalledDefinition = NativeToolDefinition(
                id: "timeout-test", name: "timeout_test", title: "Timeout test",
                description: "Exercise bounded execution.",
                parameters: .object([
                    "type": .string("object"), "properties": .object([:]),
                    "required": .array([]), "additionalProperties": .bool(false),
                ]),
                confirmation: .none,
                isAvailable: { true },
                execute: { _ in
                    await withUnsafeContinuation { (_: UnsafeContinuation<NativeJSONValue, Never>) in }
                })
            let timeoutTransport = ScriptedNativeToolTransport(rounds: [[
                NativeChatStreamSnapshot(content: "", toolCalls: [
                    NativeChatToolCall(id: "stalled", name: "timeout_test", arguments: "{}"),
                ], finishReason: "tool_calls")
            ], [NativeChatStreamSnapshot(content: "The tool failed safely.", toolCalls: [], finishReason: "stop")]])
            var timeoutFinal: NativeToolTurnSnapshot?
            for try await snapshot in NativeToolLoop(
                transport: timeoutTransport,
                registry: NativeToolRegistry(definitions: [stalledDefinition]),
                executionTimeout: .milliseconds(25)
            ).stream(
                messages: [NativeChatMessage(role: "user", content: "Run it")],
                model: "local-model", enabledToolIDs: ["timeout-test"]
            ) {
                timeoutFinal = snapshot
            }
            guard timeoutFinal?.content == "The tool failed safely.",
                  timeoutFinal?.activities.first?.state == .failed,
                  timeoutFinal?.activities.first?.result?.contains("timed out") == true else {
                print("SELFTEST ERROR: stalled native tool did not time out safely"); exit(1)
            }
            print("  native tools OK (registry, standard request, fragmented calls, continuation, validation, limits, confirmation, timeout, reminders, text-only)")
        } catch {
            print("SELFTEST ERROR: native tools \(error)"); exit(1)
        }
    }

    private static func runNativeSettings() {
        let legacy: [String: Any] = [
            "model_base": "https://models.example/v1",
            "model": "model-b",
            "model_api_key": "secret",
        ]
        var settings = NativeChatSettings.load(from: legacy)
        guard settings.version == 2,
              settings.profiles.count == 1,
              settings.activeProfile?.name == "My model endpoint",
              settings.activeProfile?.selectedModel == "model-b",
              settings.activeProfile?.selectionBasis == .restored,
              settings.onboardingState == .complete,
              settings.systemPrompt == NativeChatSettings.defaultSystemPrompt,
              settings.memory.enabled == false else {
            print("SELFTEST ERROR: native settings migration"); exit(1)
        }
        settings.systemPrompt = "Changed prompt"
        settings.resetSystemPrompt()
        guard settings.systemPrompt == NativeChatSettings.defaultSystemPrompt else {
            print("SELFTEST ERROR: native prompt reset"); exit(1)
        }
        let restoredProfileID = settings.activeProfileID
        settings.addProfile()
        settings.updateActiveProfile {
            $0.name = "Second endpoint"
            $0.baseURL = "https://other.example/v1"
        }
        guard settings.activeProfile?.name == "Second endpoint" else {
            print("SELFTEST ERROR: native endpoint profile add"); exit(1)
        }
        settings.activeProfileID = restoredProfileID
        let discoveryConfig = NativeChatConfigurationResolver.discovery(
            profile: settings.activeProfile,
            apiKey: "saved-key",
            environment: [
                "VERA_MODEL_BASE": "https://override.example/v1",
                "VERA_MODEL_API_KEY": "override-key",
            ])
        guard discoveryConfig == NativeDiscoveryConfiguration(
            baseURL: "https://override.example/v1", apiKey: "override-key") else {
            print("SELFTEST ERROR: native discovery environment overrides"); exit(1)
        }
        settings.cacheModels(["model-z", "model-a"])
        guard settings.activeProfile?.discoveredModels == ["model-a", "model-z"],
              settings.activeProfile?.selectedModel == "model-b" else {
            print("SELFTEST ERROR: native model cache changed selection"); exit(1)
        }
        settings.selectModel("model-a", basis: .user)
        settings.enabledToolIDs = ["missing", "disabled"]
        settings.onboardingState = .skipped
        settings.onboardingStep = 2
        let persisted = settings.merging(into: legacy)
        let roundTrip = NativeChatSettings.load(from: persisted)
        guard roundTrip == settings,
              persisted["model_api_key"] == nil,
              NativeChatToolCatalog.exposed(enabledIDs: settings.enabledToolIDs).isEmpty,
              ModelDiscoveryState.classify(NativeChatClient.ClientError.http(401, "")) == .authenticationFailed,
              ModelDiscoveryState.classify(NativeChatClient.ClientError.invalidResponse) == .malformed,
              ModelDiscoveryState.classify(NativeChatClient.ClientError.noUsableModels) == .noUsableModels,
              ModelDiscoveryState.classify(URLError(.cannotConnectToHost)) == .networkFailed else {
            print("SELFTEST ERROR: native settings persistence or state classification"); exit(1)
        }
        let config = NativeChatConfig(
            baseURL: URL(string: "https://models.example/v1")!,
            apiKey: nil,
            model: "model-a",
            chatTemplateKwargs: nil)
        let client = NativeChatClient(config: config)
        do {
            let request = try client.request(messages: [
                NativeChatMessage(role: "system", content: "Changed prompt"),
                NativeChatMessage(role: "user", content: "Hello"),
            ], model: "model-a")
            guard let body = request.httpBody,
                  let object = try JSONSerialization.jsonObject(with: body) as? [String: Any],
                  object["model"] as? String == "model-a",
                  object["tools"] == nil,
                  let messages = object["messages"] as? [[String: String]],
                  messages.first?["role"] == "system",
                  messages.first?["content"] == "Changed prompt" else {
                print("SELFTEST ERROR: native request settings shape"); exit(1)
            }
        } catch {
            print("SELFTEST ERROR: native request settings shape \(error)"); exit(1)
        }
        print("  native settings OK (migration, persistence, prompt, discovery, tools, request shape)")
    }

    private static func runNativeMemory() async {
        do {
            let repository = try LocalChatRepository(inMemory: true)
            let now = Date(timeIntervalSince1970: 2_000_000_000)
            let approved = NativeMemoryRecord(
                id: "approved", title: "Favorite drink", summary: "Prefers natural wine",
                details: ["The user prefers natural wine"], group: .you, category: .preference,
                bank: "personal", durability: .durable, expiry: nil, status: .approved,
                embedding: [1, 0], sourceConversationID: "source", sourceMessageID: nil,
                createdAt: now, updatedAt: now)
            let expired = NativeMemoryRecord(
                id: "expired", title: "Conference", summary: "Attending a conference",
                details: ["Attending a conference"], group: .areas, category: .plan,
                bank: "work", durability: .episodic,
                expiry: Date(timeIntervalSince1970: now.timeIntervalSince1970 - 1),
                status: .approved, embedding: [1, 0], sourceConversationID: nil,
                sourceMessageID: nil, createdAt: now, updatedAt: now)
            try repository.saveMemory(approved, decision: .accepted, note: "Test approval")
            try repository.saveMemory(expired, decision: .accepted, note: "Test approval")
            let proposal = NativeMemoryProposal(
                id: "proposal", kind: .update, title: "Update favorite drink",
                summary: "Change the saved preference", details: ["Now prefers orange wine"],
                group: .you, category: .preference, bank: "personal", durability: .durable,
                expiry: nil, targetIDs: [approved.id], sourceConversationID: "source",
                sourceMessageID: nil, createdAt: now, status: .pending)
            try repository.saveProposal(proposal)
            guard try repository.approvedMemories().count == 2,
                  try repository.pendingProposals().count == 1 else {
                print("SELFTEST ERROR: native memory repository review boundary"); exit(1)
            }
            let ranked = NativeMemoryRecall.rank(
                records: try repository.approvedMemories(), query: [1, 0], bankScope: "personal",
                now: now, itemLimit: 8, tokenLimit: 700)
            guard ranked.map(\.record.id) == [approved.id] else {
                print("SELFTEST ERROR: native memory rank, bank, or expiry filter"); exit(1)
            }
            let assembled = NativeMemoryPromptAssembler.build(
                messages: [Message(role: .user, text: "What do I like?")],
                systemPrompt: "System policy", selected: ranked)
            guard assembled.map(\.role) == ["system", "system", "user"],
                  assembled[0].content == "System policy",
                  assembled[1].content.contains("USER-APPROVED MEMORY CONTEXT"),
                  assembled[2].content == "What do I like?" else {
                print("SELFTEST ERROR: native memory prompt ordering"); exit(1)
            }
            let proposalJSON = """
            {"proposals":[
              {"kind":"create","title":"Morning routine","summary":"Prefers a quiet start","details":["The user prefers a quiet morning routine"],"group":"You","category":"preference","bank":"personal","durability":"durable","expiry":null,"target_ids":[]},
              {"kind":"update","title":"Favorite drink","summary":"Prefers orange wine","details":["The user prefers orange wine"],"group":"You","category":"preference","bank":"personal","durability":"durable","expiry":null,"target_ids":["approved"]},
              {"kind":"merge","title":"Favorite drink","summary":"Combined drink preference","details":["The user prefers natural and orange wine"],"group":"You","category":"preference","bank":"personal","durability":"durable","expiry":null,"target_ids":["approved"]},
              {"kind":"suppress","title":"Hide drink preference","summary":"Stop recalling the drink preference","details":["Do not recall the drink preference"],"group":"You","category":"preference","bank":"personal","durability":"durable","expiry":null,"target_ids":["approved"]},
              {"kind":"expire","title":"Conference","summary":"Conference context expired","details":["The conference ended on 2033-05-20"],"group":"Areas","category":"plan","bank":"work","durability":"episodic","expiry":"2033-05-20","target_ids":["expired"]},
              {"kind":"delete","title":"Forget drink preference","summary":"Delete the drink preference","details":["Delete the saved drink preference"],"group":"You","category":"preference","bank":"personal","durability":"durable","expiry":null,"target_ids":["approved"]}
            ]}
            """
            let parsed = NativeMemoryProposalParser.parse(
                proposalJSON, sourceConversationID: "source", sourceMessageID: "message",
                existing: [approved, expired], now: now)
            guard Set(parsed.map(\.kind)) == Set([
                .create, .update, .merge, .suppress, .expire, .delete,
            ]), NativeMemoryProposalParser.parse(
                "{\"proposals\":[{\"kind\":\"create\",\"title\":\"Tomorrow\",\"summary\":\"Plan tomorrow\",\"details\":[\"Do it tomorrow\"],\"group\":\"Areas\",\"category\":\"plan\",\"bank\":\"personal\",\"durability\":\"durable\",\"expiry\":null,\"target_ids\":[]}]}",
                sourceConversationID: nil, sourceMessageID: nil, existing: [], now: now).isEmpty,
                  NativeMemoryProposalParser.parse(
                    "{\"proposals\":[{\"kind\":\"create\",\"title\":\"Credential\",\"summary\":\"OPENAI_API_KEY=sk-1234567890abcdef\",\"details\":[\"Keep this secret\"],\"group\":\"You\",\"category\":\"other\",\"bank\":\"personal\",\"durability\":\"durable\",\"expiry\":null,\"target_ids\":[]}]}",
                    sourceConversationID: nil, sourceMessageID: nil, existing: [], now: now).isEmpty,
                  NativeMemorySafety.containsSensitiveData("OPENAI_API_KEY=sk-1234567890abcdef"),
                  NativeMemorySafety.containsSensitiveData("action_token=act_live_123456789"),
                  NativeMemorySafety.containsSensitiveData("act_live_123456789"),
                  NativeMemorySafety.containsSensitiveData("<think>private chain</think>"),
                  NativeMemorySafety.containsSensitiveData("<think>private chain"),
                  NativeMemorySafety.containsSensitiveData("<thought>private chain</thought>"),
                  NativeMemorySafety.containsSensitiveData("<thought>private chain"),
                  NativeMemorySafety.containsSensitiveData("<|thought|>private chain"),
                  NativeMemorySafety.containsSensitiveData("[thought] private chain"),
                  NativeMemorySafety.containsSensitiveData("(thought) private chain"),
                  NativeMemorySafety.containsSensitiveData("[analysis] private chain"),
                  NativeMemorySafety.containsSensitiveData("{\"actionToken\":\"act_live_123456789\"}"),
                  NativeMemorySafety.containsSensitiveData("{\"privateKey\":\"opaque-value\"}"),
                  NativeMemorySafety.containsSensitiveData("{\"credentials\":{\"username\":\"alice\",\"token\":\"opaque-value\"}}"),
                  NativeMemorySafety.containsSensitiveData("{\"settings\":{\"memoryApiUrl\":\"https://example.test\",\"model\":\"private-model\"}}"),
                  NativeMemorySafety.containsSensitiveData("{\"url\":\"https://example.test/v1\"}"),
                  NativeMemorySafety.containsSensitiveData("{\"service_url\":\"https://example.test/v1\"}"),
                  NativeMemorySafety.containsSensitiveData("{\"preference\":\"morning meetings\"}"),
                  NativeMemorySafety.containsSensitiveData("Profile: {\"service_url\":\"https://example.test\",\"token\":\"opaque\"}"),
                  NativeMemorySafety.containsSensitiveData("Profile: [{\"preference\":\"morning meetings\"}]"),
                  NativeMemorySafety.containsSensitiveData("token: opaque-value"),
                  NativeMemorySafety.containsSensitiveData("token = opaque-value"),
                  NativeMemorySafety.containsSensitiveData("service_url: https://example.test"),
                  NativeMemorySafety.containsSensitiveData("model: private-model"),
                  NativeMemorySafety.containsSensitiveData("settings:\n  memory_model: private-model"),
                  !NativeMemorySafety.containsSensitiveData("The user enjoys model trains"),
                  NativeMemorySafety.containsSensitiveData("<|analysis|>private chain<|end|>"),
                  NativeMemorySafety.containsSensitiveData("<|reasoning|>private chain"),
                  NativeMemorySafety.containsSensitiveData("Analysis\nprivate chain"),
                  NativeMemorySafety.containsSensitiveData("Reasoning = private chain"),
                  NativeMemorySafety.containsSensitiveData("Thought: private chain"),
                  NativeMemorySafety.containsSensitiveData("class Filter:\n    async def inlet(self, body):"),
                  NativeMemorySafety.containsSensitiveData("\"class Filter: async def inlet(self, body):\""),
                  NativeMemorySafety.boundedUTF8(
                    String(repeating: "🧠", count: 2_000), maximumBytes: 4_000).utf8.count == 4_000 else {
                print("SELFTEST ERROR: native memory structured proposal validation"); exit(1)
            }
            let similar = NativeMemoryRecord(
                id: "similar", title: "Drink preference", summary: "Likes natural wine",
                details: ["The user likes natural wine"], group: .you, category: .preference,
                bank: "personal", durability: .durable, expiry: nil, status: .approved,
                embedding: [0.99, 0.01], sourceConversationID: nil, sourceMessageID: nil,
                createdAt: now.addingTimeInterval(-10), updatedAt: now.addingTimeInterval(-10))
            var unsafeBank = similar
            unsafeBank.bank = "{\"actionToken\":\"act_live_123456789\"}"
            guard !NativeMemorySafety.recordIsSafe(unsafeBank) else {
                print("SELFTEST ERROR: native memory unsafe bank accepted"); exit(1)
            }
            var suppressed = approved
            suppressed.status = .suppressed
            let approvedFilter = NativeMemoryLibraryFilter(
                query: "wine", group: .you, bank: "personal", category: .preference,
                status: .approved, expiry: .noExpiry)
            let expiredFilter = NativeMemoryLibraryFilter(
                status: .approved, expiry: .expired)
            let suppressedFilter = NativeMemoryLibraryFilter(
                status: .suppressed, expiry: .active)
            guard approvedFilter.matches(approved, now: now),
                  !approvedFilter.matches(expired, now: now),
                  expiredFilter.matches(expired, now: now),
                  !expiredFilter.matches(approved, now: now),
                  suppressedFilter.matches(suppressed, now: now),
                  !suppressedFilter.matches(approved, now: now) else {
                print("SELFTEST ERROR: native memory library filters"); exit(1)
            }
            let semanticCreate = NativeMemoryProposal(
                id: "semantic", kind: .create, title: "Wine preference",
                summary: "Enjoys natural wine", details: ["The user enjoys natural wine"],
                group: .you, category: .preference, bank: "personal", durability: .durable,
                expiry: nil, targetIDs: [], sourceConversationID: nil, sourceMessageID: nil,
                createdAt: now, status: .pending)
            let reconciled = NativeMemoryReconciliation.reconcile(
                semanticCreate, candidateEmbedding: [1, 0], existing: [similar])
            let maintenance = NativeMemoryMaintenance.proposals(
                records: [approved, similar], existing: [], now: now,
                capacity: 2)
            guard reconciled.kind == .update, reconciled.targetIDs == [similar.id],
                  maintenance.contains(where: { $0.kind == .consolidate && Set($0.targetIDs) == Set([approved.id, similar.id]) }),
                  maintenance.contains(where: { $0.kind == .cleanup && !$0.targetIDs.isEmpty }) else {
                print("SELFTEST ERROR: native memory semantic reconciliation or actionable maintenance"); exit(1)
            }
            let capacityRepository = try LocalChatRepository(inMemory: true)
            for index in 0..<NativeMemoryRecall.capacity {
                var item = approved
                item.id = "capacity-\(index)"
                try capacityRepository.saveMemory(item, decision: .accepted, note: "Capacity test")
            }
            var overCapacity = approved
            overCapacity.id = "capacity-overflow"
            do {
                try capacityRepository.saveMemory(
                    overCapacity, decision: .accepted, note: "Capacity test")
                print("SELFTEST ERROR: native memory capacity was not enforced"); exit(1)
            } catch let error as NSError where error.domain == "NativeMemory" && error.code == 4 {
            }
            let privateAssistant = Message(role: .assistant, text: "Done", state: .complete)
            guard NativeMemoryExtractionPolicy.disposition(
                    user: "private: do not save this", assistant: privateAssistant,
                    conversationExcluded: false) == .privateTurn,
                  NativeMemoryExtractionPolicy.disposition(
                    user: "password=hunter2", assistant: privateAssistant,
                    conversationExcluded: false) == .privateTurn,
                  NativeMemoryExtractionPolicy.disposition(
                    user: "remember this", assistant: privateAssistant,
                    conversationExcluded: true) == .excluded,
                  NativeMemoryMaintenance.proposals(
                    records: [expired], existing: [], now: now).first?.kind == .expire else {
                print("SELFTEST ERROR: native memory extraction guards or expiry maintenance"); exit(1)
            }
            let reopenedURL = FileManager.default.temporaryDirectory
                .appendingPathComponent("vera-memory-\(UUID().uuidString)/vera.sqlite")
            let first = try LocalChatRepository(url: reopenedURL)
            try first.saveMemory(approved, decision: .accepted, note: "Test approval")
            let reopened = try LocalChatRepository(url: reopenedURL)
            guard try reopened.approvedMemories().first?.id == approved.id else {
                print("SELFTEST ERROR: native memory relaunch persistence"); exit(1)
            }
            try? FileManager.default.removeItem(at: reopenedURL.deletingLastPathComponent())
            guard NativeChatSettings.fresh.memory.enabled == false else {
                print("SELFTEST ERROR: native memory default opt-in"); exit(1)
            }
            let chatTransport = SelfTestNativeTransport()
            let memoryService = SelfTestMemoryService()
            var memorySettings = NativeMemorySettings.fresh
            memorySettings.enabled = true
            memorySettings.embeddingsModel = "embed-model"
            memorySettings.extractionModel = "extract-model"
            memorySettings.bankScope = "personal"
            memorySettings.generateFromChats = true
            let chatConfig = NativeChatConfig(
                baseURL: URL(string: "https://model.example/v1")!, apiKey: nil,
                model: "local-model", chatTemplateKwargs: nil)
            let store = ChatStore(
                config: nil, client: nil, socket: nil, nativeConfig: chatConfig,
                nativeTransport: chatTransport, repository: repository, hasLegacyOWUI: false,
                nativeMemorySettings: memorySettings, nativeMemoryService: memoryService)
            await store.connect()
            store.sendText("What do I like?")
            await waitForGeneration(store)
            for _ in 0..<100 where memoryService.extractionCalls == 0 {
                try? await Task.sleep(nanoseconds: 10_000_000)
            }
            guard memoryService.embedCalls == 2, memoryService.extractionCalls == 1,
                  chatTransport.histories.first?.map(\.role) == ["system", "system", "user"],
                  chatTransport.histories.first?[1].content.contains("Favorite drink") == true,
                  try repository.pendingProposals().contains(where: { $0.title == "Meeting preference" }) else {
                print("SELFTEST ERROR: native memory recall or review-only extraction integration"); exit(1)
            }
            var scanSettings = memorySettings
            scanSettings.searchPastChats = true
            let extractionsBeforeScan = memoryService.extractionCalls
            guard let boundedConversationID = store.selectedID,
                  try repository.recentMessages(
                    conversationID: boundedConversationID, limit: 1).count == 1 else {
                print("SELFTEST ERROR: native memory bounded history read"); exit(1)
            }
            store.updateNativeMemory(settings: scanSettings, service: memoryService)
            store.searchPastChatsForMemory()
            for _ in 0..<100 where memoryService.extractionCalls == extractionsBeforeScan {
                try? await Task.sleep(nanoseconds: 10_000_000)
            }
            guard memoryService.extractionCalls > extractionsBeforeScan else {
                print("SELFTEST ERROR: native memory past-chat search"); exit(1)
            }
            let sourceConversationID = store.selectedID
            store.section = .memory
            store.openMemorySource(conversationID: sourceConversationID)
            guard store.section == .chat, store.selectedID == sourceConversationID else {
                print("SELFTEST ERROR: native memory source inspection"); exit(1)
            }
            let callsBeforeSecret = memoryService.embedCalls + memoryService.extractionCalls
            store.requestMemoryChange("OPENAI_API_KEY=sk-1234567890abcdef")
            store.requestMemoryChange("action_token=act_live_123456789")
            store.requestMemoryChange("<think>private chain</think>")
            store.requestMemoryChange("class Filter:\n    async def inlet(self, body):")
            try? await Task.sleep(nanoseconds: 20_000_000)
            guard memoryService.embedCalls + memoryService.extractionCalls == callsBeforeSecret else {
                print("SELFTEST ERROR: native memory secret sent to optional service"); exit(1)
            }
            let disabledService = SelfTestMemoryService()
            let disabledTransport = SelfTestNativeTransport()
            let disabledStore = ChatStore(
                config: nil, client: nil, socket: nil, nativeConfig: chatConfig,
                nativeTransport: disabledTransport,
                repository: try LocalChatRepository(inMemory: true), hasLegacyOWUI: false,
                nativeMemorySettings: .fresh, nativeMemoryService: disabledService)
            await disabledStore.connect()
            disabledStore.sendText("No memory")
            await waitForGeneration(disabledStore)
            guard disabledService.embedCalls == 0, disabledService.extractionCalls == 0,
                  disabledTransport.histories.first?.map(\.role) == ["system", "user"] else {
                print("SELFTEST ERROR: disabled native memory performed work"); exit(1)
            }
            print("  native memory OK (opt-in, review, CRUD, recall, expiry, budgets, prompt, relaunch)")
        } catch {
            print("SELFTEST ERROR: native memory \(error)"); exit(1)
        }
    }

    private static func runNativeStore() async {
        do {
            let repository = try LocalChatRepository(inMemory: true)
            let config = NativeChatConfig(
                baseURL: URL(string: "https://model.example/v1")!,
                apiKey: nil,
                model: "local-model",
                chatTemplateKwargs: nil)
            let transport = SelfTestNativeTransport()
            let store = ChatStore(
                config: nil,
                client: nil,
                socket: nil,
                nativeConfig: config,
                nativeTransport: transport,
                repository: repository,
                hasLegacyOWUI: false)
            await store.connect()
            store.sendText("First")
            await waitForGeneration(store)
            store.sendText("Second")
            await waitForGeneration(store)
            guard let conversation = store.selected,
                  conversation.messages.count == 4,
                  conversation.messages[1].text == "Native reply",
                  conversation.messages[1].state == .complete,
                  transport.histories.count == 2,
                  transport.histories[1].map(\.content) == [
                    NativeChatSettings.defaultSystemPrompt, "First", "Native reply", "Second",
                  ],
                  try repository.messages(conversationID: conversation.id).count == 4 else {
                print("SELFTEST ERROR: native store multi-turn persistence"); exit(1)
            }
            let toolService = SelfTestRemindersService()
            let toolTransport = ScriptedNativeToolTransport(rounds: [
                [NativeChatStreamSnapshot(content: "", toolCalls: [
                    NativeChatToolCall(id: "store-call", name: "apple_reminders_list", arguments: "{}"),
                ], finishReason: "tool_calls")],
                [NativeChatStreamSnapshot(content: "One reminder.", toolCalls: [], finishReason: "stop")],
            ])
            let toolRepository = try LocalChatRepository(inMemory: true)
            let toolStore = ChatStore(
                config: nil,
                client: nil,
                socket: nil,
                nativeConfig: config,
                nativeTransport: toolTransport,
                repository: toolRepository,
                hasLegacyOWUI: false,
                nativeEnabledToolIDs: ["apple-reminders"],
                nativeToolRegistry: NativeToolRegistry(
                    definitions: NativeRemindersTools.definitions(service: toolService)))
            await toolStore.connect()
            toolStore.sendText("List reminders")
            await waitForGeneration(toolStore)
            guard let toolConversation = toolStore.selected,
                  let toolReply = toolConversation.messages.last,
                  toolReply.text == "One reminder.",
                  toolReply.toolActivities.first?.state == .succeeded,
                  try toolRepository.messages(conversationID: toolConversation.id).last?.toolActivities.first?.id == "store-call" else {
                print("SELFTEST ERROR: native store tool activity progression or persistence"); exit(1)
            }
            transport.interrupt = true
            store.sendText("Fail")
            await waitForGeneration(store)
            guard let interrupted = store.selected?.messages.last,
                  interrupted.text == "Partial",
                  interrupted.state == .interrupted,
                  interrupted.failure == "test interruption",
                  let id = store.selected?.id,
                  try repository.messages(conversationID: id).last?.state == .interrupted else {
                print("SELFTEST ERROR: native store interrupted persistence"); exit(1)
            }
            transport.interrupt = false
            store.sendText("After interruption")
            await waitForGeneration(store)
            guard transport.histories.last?.map(\.content) == [
                NativeChatSettings.defaultSystemPrompt,
                "First", "Native reply", "Second", "Native reply", "Fail", "After interruption",
            ] else {
                print("SELFTEST ERROR: native interrupted reply reused as prompt history"); exit(1)
            }
            let countBeforeBlockedSend = store.selected?.messages.count
            store.generating = true
            store.draft = "Concurrent"
            store.send()
            store.sendText("Concurrent")
            guard store.draft == "Concurrent" else {
                print("SELFTEST ERROR: native blocked send cleared draft"); exit(1)
            }
            store.draft = ""
            if let conversationIndex = store.conversations.firstIndex(where: { $0.id == store.selectedID }) {
                let question = Message(
                    role: .assistant,
                    text: "Choose",
                    ask: VeraAsk(question: "Choose", options: [.init(label: "A")]))
                store.conversations[conversationIndex].messages.append(question)
                store.submitAsk(messageID: question.id, selections: ["A"], other: "")
                guard store.conversations[conversationIndex].messages.last?.answered == false else {
                    print("SELFTEST ERROR: native blocked structured answer marked submitted"); exit(1)
                }
                store.conversations[conversationIndex].messages.removeLast()
            }
            store.generating = false
            guard store.selected?.messages.count == countBeforeBlockedSend else {
                print("SELFTEST ERROR: native concurrent send accepted"); exit(1)
            }
            let unavailable = ChatStore(
                config: nil,
                client: nil,
                socket: nil,
                nativeConfig: config,
                nativeTransport: transport,
                repository: nil,
                repositoryError: "database unavailable",
                hasLegacyOWUI: false)
            await unavailable.connect()
            unavailable.draft = "Must not stream"
            unavailable.send()
            guard unavailable.selected?.messages.isEmpty == true,
                  unavailable.draft == "Must not stream",
                  unavailable.chatConfigurationError == "database unavailable" else {
                print("SELFTEST ERROR: native chat proceeded without persistence"); exit(1)
            }
            print("  native store OK (progressive turns and tools, interrupted exclusion, serialized sends, required persistence)")
        } catch {
            print("SELFTEST ERROR: native store \(error)"); exit(1)
        }
    }

    private static func pulseFixtureCard() -> PulseCard {
        PulseCard(
            id: "seed-card", title: "River briefing",
            preview: "The gauge is trending up.", subtitle: "Pulse",
            imageURL: "https://img.example/cover.png", tint: "#334455",
            sources: ["https://a.example/one"],
            sourceList: [
                PulseSource(n: 1, title: "Gauge report", url: "https://a.example/one"),
                PulseSource(n: 2, title: "Insecure source", url: "ftp://bad.example/two"),
            ],
            inlineImages: [
                PulseInlineImage(n: 1, url: "https://img.example/inline.png", caption: "The gauge", sourceN: 1),
                PulseInlineImage(n: 2, url: "file:///etc/passwd", caption: "Bad", sourceN: nil),
            ],
            body: "The river rose overnight. [1]",
            status: "new", kind: "research", severity: "notice",
            action: PulseAction(verb: "ha.service", preview: "Do a thing", risk: "low",
                                reversible: true, token: "ACTION-COMMIT-TOKEN"),
            provenance: "heartbeat", category: "vera",
            changeSet: [{
                var entity = GroomSnapshot(kind: "entity", id: "router")
                entity.type = "device"
                entity.name = "router"
                entity.attrs = [
                    "room": "closet",
                    "password": "SECRET-ATTR-VALUE",
                    "api_key": "SECRET-ATTR-KEY",
                    "note": "Bearer SECRET-BEARER-VALUE",
                ]
                return GroomOp(index: 0, type: "gc", store: "knowledge",
                               reason: "orphan", before: [entity], after: nil)
            }()],
            items: [PulseDigestItem(itemID: "i1", title: "Pick", subtitle: "Sub", mediaType: nil,
                                    tmdbID: nil, token: "ITEM-COMMIT-TOKEN", state: "pending")])
    }

    private static func waitForContinuation(_ store: ChatStore, _ cardID: String) async {
        for _ in 0..<200 where store.pulseContinuation[cardID] == .opening {
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    private static func continuationStore(
        repository: any ChatRepository, feed: SelfTestPulseFeed,
        transport: (any NativeChatTransport)? = nil
    ) async -> ChatStore {
        let config = NativeChatConfig(
            baseURL: URL(string: "https://model.example/v1")!,
            apiKey: nil, model: "local-model", chatTemplateKwargs: nil)
        let store = ChatStore(
            config: nil, client: nil, socket: nil,
            nativeConfig: config, nativeTransport: transport,
            repository: repository, hasLegacyOWUI: false, pulseFeed: feed)
        await store.connect()
        store.section = .pulse
        return store
    }

    private static func runPulseContinuation() async {
        let card = pulseFixtureCard()
        let snapshot = PulseCardSnapshot(card: card, capturedAt: Date(timeIntervalSince1970: 1_000))
        guard let json = snapshot.encodedJSON(),
              !json.contains("ACTION-COMMIT-TOKEN"),
              !json.contains("ITEM-COMMIT-TOKEN"),
              !json.contains("SECRET-ATTR-VALUE"),
              !json.contains("SECRET-ATTR-KEY"),
              !json.contains("SECRET-BEARER-VALUE"),
              !json.contains("ftp://"),
              !json.contains("file://") else {
            print("SELFTEST ERROR: pulse snapshot leaked tokens or non-web URLs"); exit(1)
        }
        guard let decoded = PulseCardSnapshot.decode(json), decoded == snapshot else {
            print("SELFTEST ERROR: pulse snapshot round-trip decode"); exit(1)
        }
        let restored = decoded.card()
        guard restored.action == nil,
              restored.title == card.title,
              restored.body == card.body,
              restored.sourceList.map(\.url) == ["https://a.example/one"],
              restored.inlineImages.map(\.url) == ["https://img.example/inline.png"],
              restored.items.count == 1,
              restored.items.first?.token == nil,
              restored.changeSet.first?.before.first?.attrs == ["room": "closet"],
              restored.provenance == "heartbeat",
              restored.category == "vera",
              restored.severity == "notice" else {
            print("SELFTEST ERROR: pulse snapshot restore fidelity"); exit(1)
        }
        guard PulseCardSnapshot.decode(json.replacingOccurrences(
            of: "\"version\":1", with: "\"version\":2")) == nil else {
            print("SELFTEST ERROR: pulse snapshot accepted a future version"); exit(1)
        }
        var oversized = card
        oversized.body = String(repeating: "x", count: PulseCardSnapshot.maximumEncodedBytes + 1)
        guard PulseCardSnapshot(card: oversized, capturedAt: Date()).encodedJSON() == nil else {
            print("SELFTEST ERROR: pulse snapshot ignored the size cap"); exit(1)
        }
        let seedText = PulseSeed.text(for: restored)
        guard seedText.contains(card.title),
              seedText.contains("The river rose overnight."),
              seedText.contains("[1] Gauge report: https://a.example/one"),
              !seedText.contains("ftp://"),
              !seedText.contains("ACTION-COMMIT-TOKEN") else {
            print("SELFTEST ERROR: pulse seed text shape"); exit(1)
        }
        let seed = PulseSeed.message(for: card)
        guard seed.role == .assistant, seed.state == .complete,
              seed.contentType == .pulseCard, seed.pulse?.action == nil,
              seed.sources.map(\.n) == [1] else {
            print("SELFTEST ERROR: pulse seed message shape"); exit(1)
        }

        guard case .malformed = OWUIClient.parsePulseFeed(Data("not json".utf8)) else {
            print("SELFTEST ERROR: pulse feed malformed data accepted"); exit(1)
        }
        guard case .malformed = OWUIClient.parsePulseFeed(Data("{\"other\":true}".utf8)) else {
            print("SELFTEST ERROR: pulse feed missing cards accepted"); exit(1)
        }
        guard case .success(let emptyCards, let emptyIDs) =
                OWUIClient.parsePulseFeed(Data("{\"cards\":[]}".utf8)),
              emptyCards.isEmpty, emptyIDs.isEmpty else {
            print("SELFTEST ERROR: pulse feed valid empty"); exit(1)
        }
        let mixed = Data("""
            {"cards":[{"id":"good","title":"Good","body":"Body"},{"id":"broken"}]}
            """.utf8)
        guard case .success(let mixedCards, let mixedIDs) = OWUIClient.parsePulseFeed(mixed),
              mixedCards.map(\.id) == ["good"],
              mixedIDs == ["good", "broken"] else {
            print("SELFTEST ERROR: pulse feed partial parse"); exit(1)
        }

        do {
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("vera-selftest-\(UUID().uuidString)", isDirectory: true)
            defer { try? FileManager.default.removeItem(at: directory) }
            let url = directory.appendingPathComponent("continuation.sqlite")
            let repository = try LocalChatRepository(url: url)
            let seeded = PulseSeed.message(for: card)
            let conversation = Conversation(
                id: UUID().uuidString, title: card.title, messages: [seeded],
                updatedAt: Date(), isPersisted: true,
                originType: PulseSeed.originType, originID: card.id)
            try repository.createOriginConversation(conversation, seed: seeded)
            guard let found = try repository.conversation(
                originType: PulseSeed.originType, originID: card.id),
                  found.id == conversation.id, found.originID == card.id else {
                print("SELFTEST ERROR: pulse origin lookup"); exit(1)
            }
            let duplicate = Conversation(
                id: UUID().uuidString, title: card.title, messages: [],
                updatedAt: Date(), isPersisted: true,
                originType: PulseSeed.originType, originID: card.id)
            let duplicateSeed = PulseSeed.message(for: card)
            do {
                try repository.createOriginConversation(duplicate, seed: duplicateSeed)
                print("SELFTEST ERROR: duplicate pulse origin accepted"); exit(1)
            } catch {}
            guard try repository.listConversations().count == 1,
                  try repository.messages(conversationID: duplicate.id).isEmpty else {
                print("SELFTEST ERROR: duplicate pulse origin left partial rows"); exit(1)
            }
            let orphan = Conversation(
                id: UUID().uuidString, title: "Orphan", messages: [],
                updatedAt: Date(), isPersisted: true,
                originType: PulseSeed.originType, originID: "other-card")
            do {
                try repository.createOriginConversation(orphan, seed: seeded)
                print("SELFTEST ERROR: conflicting seed insert accepted"); exit(1)
            } catch {}
            guard try repository.conversation(
                originType: PulseSeed.originType, originID: "other-card") == nil else {
                print("SELFTEST ERROR: failed create left a partial conversation"); exit(1)
            }
            let reopened = try LocalChatRepository(url: url)
            let persisted = try reopened.messages(conversationID: conversation.id)
            guard persisted.count == 1,
                  let first = persisted.first,
                  first.state == .complete,
                  first.contentType == .pulseCard,
                  first.pulse?.title == card.title,
                  first.pulse?.action == nil,
                  first.pulse?.sourceList.map(\.url) == ["https://a.example/one"],
                  first.sources.map(\.n) == [1],
                  first.text.contains("The river rose overnight.") else {
                print("SELFTEST ERROR: pulse seed round-trip after restart"); exit(1)
            }
            try reopened.deleteConversation(conversation.id)
            guard try reopened.messages(conversationID: conversation.id).isEmpty,
                  try reopened.conversation(
                    originType: PulseSeed.originType, originID: card.id) == nil else {
                print("SELFTEST ERROR: pulse continuation cascade delete"); exit(1)
            }
        } catch {
            print("SELFTEST ERROR: pulse continuation database \(error)"); exit(1)
        }

        do {
            let repository = try LocalChatRepository(inMemory: true)
            let transport = SelfTestNativeTransport()
            let feed = SelfTestPulseFeed(.success(cards: [card], rawIDs: [card.id]))
            let store = await continuationStore(repository: repository, feed: feed, transport: transport)
            store.pulseDetail = card
            store.openPulseInChat(card)
            await waitForContinuation(store, card.id)
            guard store.pulseContinuation[card.id] == nil,
                  store.section == .chat,
                  store.pulseDetail == nil,
                  let selected = store.selected,
                  selected.originType == PulseSeed.originType,
                  selected.originID == card.id,
                  selected.messages.count == 1,
                  selected.messages.first?.state == .complete,
                  selected.messages.first?.pulse != nil,
                  transport.histories.isEmpty,
                  try repository.conversation(
                    originType: PulseSeed.originType, originID: card.id) != nil else {
                print("SELFTEST ERROR: first-time pulse continuation"); exit(1)
            }
            let continuedID = selected.id
            store.sendText("Tell me more")
            await waitForGeneration(store)
            guard let history = transport.histories.last,
                  history.map(\.content) == [
                    NativeChatSettings.defaultSystemPrompt,
                    selected.messages.first?.text ?? "",
                    "Tell me more",
                  ],
                  history[1].content.contains("[1] Gauge report: https://a.example/one"),
                  !history.map(\.content).joined().contains("ACTION-COMMIT-TOKEN") else {
                print("SELFTEST ERROR: pulse follow-up history shape"); exit(1)
            }
            store.section = .pulse
            store.openPulseInChat(card)
            await waitForContinuation(store, card.id)
            guard store.selectedID == continuedID,
                  store.section == .chat,
                  try repository.listConversations().filter({ $0.originID == card.id }).count == 1,
                  feed.callCount == 1 else {
                print("SELFTEST ERROR: repeat pulse continuation duplicated"); exit(1)
            }

            let offlineFeed = SelfTestPulseFeed(.transport)
            let offline = await continuationStore(repository: repository, feed: offlineFeed)
            offline.openPulseInChat(card)
            await waitForContinuation(offline, card.id)
            guard offline.pulseContinuation[card.id] == nil,
                  offline.section == .chat,
                  offline.selected?.originID == card.id,
                  offline.selected?.messages.first?.pulse != nil,
                  offlineFeed.callCount == 0 else {
                print("SELFTEST ERROR: existing pulse continuation required the network"); exit(1)
            }

            let failures: [(PulseFeedResult, String)] = [
                (.unconfigured, "Pulse isn't connected. Set up Vera API to continue this card."),
                (.transport, "Pulse couldn't be reached. Try again."),
                (.malformed, "This Pulse card couldn't be opened."),
                (.success(cards: [], rawIDs: []), "This Pulse card is no longer available."),
                (.success(cards: [], rawIDs: [card.id]), "This Pulse card couldn't be opened."),
            ]
            for (result, expected) in failures {
                let freshRepository = try LocalChatRepository(inMemory: true)
                let failing = await continuationStore(
                    repository: freshRepository, feed: SelfTestPulseFeed(result))
                failing.pulseDetail = card
                failing.openPulseInChat(card)
                await waitForContinuation(failing, card.id)
                guard failing.pulseContinuation[card.id] == .failed(expected),
                      failing.section == .pulse,
                      failing.pulseDetail?.id == card.id,
                      try freshRepository.conversation(
                        originType: PulseSeed.originType, originID: card.id) == nil else {
                    print("SELFTEST ERROR: pulse continuation failure state for \(expected)"); exit(1)
                }
            }

            let brokenInner = try LocalChatRepository(inMemory: true)
            let broken = await continuationStore(
                repository: SelfTestFailingCreateRepository(inner: brokenInner),
                feed: SelfTestPulseFeed(.success(cards: [card], rawIDs: [card.id])))
            broken.pulseDetail = card
            broken.openPulseInChat(card)
            await waitForContinuation(broken, card.id)
            guard broken.pulseContinuation[card.id]
                    == .failed(ChatStore.pulseContinuationDatabaseFailure),
                  broken.section == .pulse,
                  try brokenInner.conversation(
                    originType: PulseSeed.originType, originID: card.id) == nil else {
                print("SELFTEST ERROR: pulse continuation database failure state"); exit(1)
            }
            print("  pulse continuation OK (snapshot sanitization, typed feed, atomic origin create, offline reopen, failure states)")
        } catch {
            print("SELFTEST ERROR: pulse continuation store \(error)"); exit(1)
        }
    }

    private static func waitForGeneration(_ store: ChatStore) async {
        for _ in 0..<100 where store.generating {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    /// Live checks against the configured OWUI / vera-api deployment.
    private static func runLive(_ cfg: OWUIConfig) async {
        let client = OWUIClient(config: cfg)
        print("OWUI: \(cfg.baseURL.absoluteString)   model: \(cfg.model)")
        do {
            let chats = try await client.listChats()
            print("listChats OK (\(chats.count) chats)")
            if let first = chats.first {
                let msgs = await client.loadMessages(chatID: first.id)
                print("loadMessages OK ('\(first.title.prefix(40))' → \(msgs.count) messages)")
            }
            if let mems = await client.memories() {
                print("memories OK (\(mems.count) entries)")
            } else {
                print("memories FAILED (fetch error)")
            }
            // The folder id is deployment config, never baked in: PULSE_FOLDER_ID env or
            // the pulse_folder_id key in ~/.vera/config.json; absent -> skip the check.
            let env = ProcessInfo.processInfo.environment["PULSE_FOLDER_ID"]
            let folderID = (env?.isEmpty == false ? env : nil)
                ?? (ConfigFile.read()["pulse_folder_id"] as? String)
            if let folderID, !folderID.isEmpty {
                let cards = await client.pulseCards(folderID: folderID)
                print("pulseCards OK (\(cards.count) cards in the Pulse folder)")
            } else {
                print("pulseCards skipped. Set PULSE_FOLDER_ID (or pulse_folder_id in ~/.vera/config.json)")
            }

            // Stream through OWUI's pipeline (Socket.IO) — proves tools + memory fire.
            print("pipeline stream test (Socket.IO):")
            let socket = VeraSocket(config: cfg)
            var out = ""
            for try await ev in socket.streamReply(chatID: "local:selftest",
                                                    messageID: UUID().uuidString,
                                                    messages: [["role": "user", "content": "Reply with exactly: wired"]]) {
                switch ev {
                case .status(let s): print("  · status: \(s)")
                case .content(let c): out = c
                case .sources: break
                case .done: break
                }
            }
            print("  reply: \(out)")
            guard !out.isEmpty else { print("SELFTEST ERROR: empty pipeline reply"); exit(1) }

            // Tools must be available in-app (streamReply sends tool_ids/features explicitly).
            print("in-app tool test (kitchen via streamReply):")
            var toolStatuses: [String] = []
            var kitchenReply = ""
            for try await ev in socket.streamReply(chatID: "local:ser60",
                                                    messageID: UUID().uuidString,
                                                    messages: [["role": "user", "content": "What kitchen staples am I low on right now? Just list them."]]) {
                switch ev {
                case .status(let s): toolStatuses.append(s); print("  · status: \(s)")
                case .content(let c): kitchenReply = c
                case .sources: break
                case .done: break
                }
            }
            let firedTool = toolStatuses.contains { $0.lowercased().contains("kitchen") }
            print("  kitchen tool fired in-app: \(firedTool)")
            print("  reply: \(kitchenReply.prefix(160))")

            // Admin client — list registry + safe toolIds round-trip (restores state).
            print("MCP admin test:")
            let admin = OWUIAdminClient(baseURL: cfg.baseURL, modelID: cfg.model,
                                        token: { try await socket.currentToken() })
            let toolList = try await admin.listTools()
            let funcList = try await admin.listFunctions()
            let servers = try await admin.toolServers()
            let role = try await admin.currentRole()
            let valves = try await admin.toolValves(id: "web_search")
            print("  tools: \(toolList.count)  functions: \(funcList.count)  servers: \(servers.count)  role: \(role)")
            print("  web_search valves fields: \(valves.count)")
            let before = try await admin.veraModel().toolIds
            try await admin.setVeraToolIds(before)   // no-op write exercises the write path
            let after = try await admin.veraModel().toolIds
            guard before == after else { print("SELFTEST ERROR: toolIds round-trip changed state"); exit(1) }
            print("  toolIds round-trip OK: \(after)")

            // Image search endpoint (vera-api). Best-effort (needs a configured vera-api).
            if let apiBase = cfg.veraAPIBase {
                var iReq = URLRequest(url: apiBase.appendingPathComponent("/images/search"))
                iReq.httpMethod = "POST"; iReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
                iReq.httpBody = try JSONSerialization.data(withJSONObject: ["query": "coastal lighthouse", "max_results": 3])
                if let (iData, _) = try? await URLSession.shared.data(for: iReq),
                   let iObj = try? JSONSerialization.jsonObject(with: iData) as? [String: Any] {
                    print("  images/search OK (\((iObj["results"] as? [[String: Any]])?.count ?? 0) hits)")
                } else {
                    print("  images/search SKIP (vera-api not reachable / not deployed)")
                }
            } else {
                print("  images/search SKIP (vera-api base not configured)")
            }

            print("SELFTEST OK")
            exit(0)
        } catch {
            print("SELFTEST ERROR: \(error)")
            exit(1)
        }
    }

    /// Proves 401 session recovery against a live server: streams a reply, blocks on stdin
    /// while the orchestrating shell restarts open-webui, then streams again through the SAME
    /// VeraSocket instance. The second send must transparently re-sign-in and succeed.
    static func recoveryProbe() async {
        setbuf(stdout, nil)
        guard let cfg = OWUIConfig.load() else {
            print("RECOVERY ERROR: no OWUI config (~/.vera/config.json)"); exit(1)
        }
        let socket = VeraSocket(config: cfg)
        func stream(_ n: Int) async -> String {
            var out = ""
            do {
                for try await ev in socket.streamReply(chatID: "local:recovery\(n)",
                                                       messageID: UUID().uuidString,
                                                       messages: [["role": "user", "content": "Reply with exactly: alive\(n)"]]) {
                    if case .content(let c) = ev { out = c }
                }
            } catch {
                print("RECOVERY ERROR chat\(n): \(error.localizedDescription)"); exit(1)
            }
            return out
        }
        let first = await stream(1)
        guard !first.isEmpty else { print("RECOVERY ERROR: empty first reply"); exit(1) }
        print("CHAT1 OK: \(first.prefix(60))")
        print("WAITING: restart the server, then send a newline on stdin")
        _ = readLine()
        let second = await stream(2)
        guard !second.isEmpty else { print("RECOVERY ERROR: empty second reply"); exit(1) }
        print("CHAT2 OK: \(second.prefix(60))")
        print("RECOVERY OK")
        exit(0)
    }

    /// Pure, local checks — no network, no config required. CI runs exactly these; live
    /// checks follow only when an OWUI config exists.
    private static func runPure() {
        do {
            var sse = ChatSSEDecoder()
            let first = Data("data: {\"choices\":[{\"delta\":{\"content\":\"Hel\"}}]}\r\n".utf8)
            let second = Data("\r\ndata: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\ndata: [DO".utf8)
            let third = Data("NE]\n\n".utf8)
            guard try sse.feed(first).isEmpty else {
                print("SELFTEST ERROR: native SSE emitted an incomplete event"); exit(1)
            }
            let middle = try sse.feed(second)
            let final = try sse.feed(third)
            guard middle.count == 2,
                  try NativeChatClient.content(from: middle[0]) == "Hel",
                  try NativeChatClient.content(from: middle[1]) == "lo",
                  final == ["[DONE]"] else {
                print("SELFTEST ERROR: native SSE fragmented decode"); exit(1)
            }
            let modelData = Data("{\"data\":[{\"id\":\"zeta\"},{\"id\":\"alpha\"}]}".utf8)
            guard try NativeChatClient.modelIDs(from: modelData) == ["alpha", "zeta"] else {
                print("SELFTEST ERROR: native model discovery decode"); exit(1)
            }
            guard try NativeChatClient.modelIDs(from: Data("{\"data\":[]}".utf8)).isEmpty else {
                print("SELFTEST ERROR: native empty discovery decode"); exit(1)
            }
            do {
                _ = try NativeChatClient.modelIDs(from: Data("{\"data\":[{\"name\":\"missing id\"}]}".utf8))
                print("SELFTEST ERROR: native unusable discovery accepted"); exit(1)
            } catch NativeChatClient.ClientError.noUsableModels {
            }
            do {
                _ = try NativeChatClient.modelIDs(from: Data("{\"models\":[]}".utf8))
                print("SELFTEST ERROR: native malformed discovery accepted"); exit(1)
            } catch NativeChatClient.ClientError.invalidResponse {
            }
            do {
                _ = try NativeChatClient.modelIDs(from: Data("{\"data\":[{\"id\":\"valid\"},{\"name\":\"missing id\"}]}".utf8))
                print("SELFTEST ERROR: native partially malformed discovery accepted"); exit(1)
            } catch NativeChatClient.ClientError.invalidResponse {
            }
            do {
                _ = try NativeChatClient.content(from: "not-json")
                print("SELFTEST ERROR: native malformed event accepted"); exit(1)
            } catch NativeChatClient.ClientError.malformedEvent {
            }
            let nativeConfig = NativeChatConfig(
                baseURL: URL(string: "https://model.example/v1")!,
                apiKey: "test-token",
                model: "local-model",
                chatTemplateKwargs: "{\"enable_thinking\":false}")
            let nativeClient = NativeChatClient(config: nativeConfig)
            let nativeRequest = try nativeClient.request(
                messages: [NativeChatMessage(role: "user", content: "hello")], model: "local-model")
            let nativeBody = try JSONSerialization.jsonObject(with: nativeRequest.httpBody!) as? [String: Any]
            guard nativeRequest.url?.absoluteString == "https://model.example/v1/chat/completions",
                  nativeRequest.value(forHTTPHeaderField: "Authorization") == "Bearer test-token",
                  nativeBody?["model"] as? String == "local-model",
                  nativeBody?["stream"] as? Bool == true,
                  (nativeBody?["messages"] as? [[String: String]])?.first?["content"] == "hello",
                  (nativeBody?["chat_template_kwargs"] as? [String: Bool])?["enable_thinking"] == false else {
                print("SELFTEST ERROR: native request shape"); exit(1)
            }
            let noKey = NativeChatClient(config: NativeChatConfig(
                baseURL: nativeConfig.baseURL, apiKey: nil, model: nativeConfig.model, chatTemplateKwargs: nil))
            guard try noKey.request(messages: [], model: nativeConfig.model)
                .value(forHTTPHeaderField: "Authorization") == nil else {
                print("SELFTEST ERROR: native optional authorization"); exit(1)
            }
            print("  native transport OK (fragmented SSE, malformed event, discovery, request shape, optional auth)")

            let databaseDir = FileManager.default.temporaryDirectory
                .appendingPathComponent("vera-native-chat-\(UUID().uuidString)", isDirectory: true)
            let databaseURL = databaseDir.appendingPathComponent("vera.sqlite")
            let repository = try LocalChatRepository(url: databaseURL)
            let conversationID = UUID().uuidString
            var conversation = Conversation(
                id: conversationID, title: "Native chat", messages: [],
                createdAt: Date(timeIntervalSince1970: 100),
                updatedAt: Date(timeIntervalSince1970: 200), isPersisted: true, pinned: true)
            try repository.saveConversation(conversation)
            let user = Message(
                id: UUID(), role: .user, text: "hello", createdAt: Date(timeIntervalSince1970: 110), state: .complete)
            var assistant = Message(
                id: UUID(), role: .assistant, text: "par", createdAt: Date(timeIntervalSince1970: 120),
                state: .streaming, modelID: "local-model", toolActivities: [
                    NativeToolActivity(
                        id: "persisted-call", toolID: "apple-reminders", name: "apple_reminders_list",
                        title: "List reminders", round: 1, request: "{}",
                        result: "{\"reminders\":[]}", state: .succeeded, confirmationRequired: false),
                    NativeToolActivity(
                        id: "interrupted-call", toolID: "apple-reminders", name: "apple_reminders_create",
                        title: "Create reminder", round: 2, request: "{\"title\":\"Milk\"}",
                        result: nil, state: .pending, confirmationRequired: false),
                ])
            try repository.saveMessage(user, conversationID: conversationID, ordinal: 0)
            try repository.saveMessage(assistant, conversationID: conversationID, ordinal: 1)
            assistant.text = "partial"
            try repository.saveMessage(assistant, conversationID: conversationID, ordinal: 1)
            conversation.updatedAt = Date(timeIntervalSince1970: 300)
            try repository.saveConversation(conversation)
            let reopened = try LocalChatRepository(url: databaseURL)
            let storedConversations = try reopened.listConversations()
            let storedMessages = try reopened.messages(conversationID: conversationID)
            guard storedConversations.count == 1, storedConversations[0].pinned,
                  storedConversations[0].updatedAt.timeIntervalSince1970 == 300,
                  storedMessages.count == 2, storedMessages[0].text == "hello",
                  storedMessages[1].text == "partial", storedMessages[1].state == .interrupted,
                  storedMessages[1].toolActivities.first?.result == "{\"reminders\":[]}",
                  storedMessages[1].toolActivities.last?.state == .failed,
                  storedMessages[1].toolActivities.last?.result?.contains("interrupted") == true,
                  storedMessages[1].failure == "The response was interrupted when Vera closed" else {
                print("SELFTEST ERROR: native local history round-trip"); exit(1)
            }
            try reopened.deleteConversation(conversationID)
            guard try reopened.listConversations().isEmpty,
                  try reopened.messages(conversationID: conversationID).isEmpty else {
                print("SELFTEST ERROR: native conversation cascade delete"); exit(1)
            }
            try? FileManager.default.removeItem(at: databaseDir)
            print("  native history OK (migration, CRUD, restart, durable tool activity, interrupted reply, cascade)")

            // vera:ask block parses out of an assistant reply (pure, local).
            let demo = "Sure, happy to help.\n```vera:ask\n{\"question\":\"Pick one\",\"multiSelect\":false,\"options\":[{\"label\":\"A\",\"description\":\"first\"},{\"label\":\"B\",\"description\":\"second\"}]}\n```"
            let (clean, parsedAsk) = VeraAsk.parse(demo)
            guard let parsedAsk, parsedAsk.options.count == 2, !clean.contains("vera:ask"), clean == "Sure, happy to help." else {
                print("SELFTEST ERROR: vera:ask parse"); exit(1)
            }
            print("  vera:ask parse OK (\(parsedAsk.options.count) options, clean=\(clean.debugDescription))")

            // vera-artifact block parses out of an assistant reply (pure, local).
            let demoArt = "Here's the page.\n:::vera-artifact id=\"lp\" title=\"Landing\" type=\"html\"\n<h1>Hi</h1>\n<p>x</p>\n:::\nLet me know."
            let (artClean, arts) = Artifact.parse(demoArt)
            guard arts.count == 1, arts[0].type == .html, arts[0].id == "lp",
                  arts[0].content.contains("<h1>Hi</h1>"), !artClean.contains("vera-artifact") else {
                print("SELFTEST ERROR: vera-artifact parse"); exit(1)
            }
            print("  vera-artifact parse OK (\(arts[0].type.rawValue) '\(arts[0].title)', clean=\(artClean.debugDescription))")

            // Pulse deep-research markers + block parsing (pure, local).
            let pulseRaw = """
            <!--vera-image http://x/cover.png-->
            <!--vera-tint #472f22-->
            <!--vera-summary A complete one sentence summary.-->
            <!--vera-source 1|BBC Sport|https://bbc.co.uk/a-->
            <!--vera-source 2|The Athletic|https://theathletic.com/b-->
            <!--vera-inline 1|http://x/img1.jpg|Joe Carter|2-->

            I'm surfacing this because it matters. [1]

            [[img:1]]

            Ashvale paid a record fee. [1,2]
            """
            let pm = PulseMarkers.parse(pulseRaw)
            guard pm.image == "http://x/cover.png", pm.tint == "#472f22",
                  pm.sources.count == 2, pm.sources.first?.title == "BBC Sport",
                  pm.inlineImages.count == 1, pm.inlineImages.first?.sourceN == 2 else {
                print("SELFTEST ERROR: pulse marker parse"); exit(1)
            }
            let pBlocks = pulseBlocks(pm.body, images: pm.inlineImages)
            let paras: [(String, [Int])] = pBlocks.compactMap { b in
                if case .paragraph(_, let t, let r) = b { return (t, r) }; return nil
            }
            let imgs: [PulseInlineImage] = pBlocks.compactMap { b in
                if case .image(let im) = b { return im }; return nil
            }
            guard imgs.count == 1, paras.count == 2, paras[0].1 == [1], paras[1].1 == [1, 2],
                  !paras[1].0.contains("[1,2]") else {
                print("SELFTEST ERROR: pulse block parse"); exit(1)
            }
            print("  pulse markers OK (\(pm.sources.count) sources, \(pm.inlineImages.count) inline, \(paras.count) paras)")

            // Presentation block parsing (pure, local).
            let blockReply = "Compare:\n\n```vera:stats\n{\"cards\":[{\"value\":\"33\",\"label\":\"goals\",\"sub\":\"69 games\"}]}\n```\n\nAnd the trend:\n\n```vera:chart\n{\"type\":\"bar\",\"yLabel\":\"goals\",\"series\":[{\"name\":\"Openda\",\"points\":[{\"x\":\"23-24\",\"y\":14},{\"x\":\"24-25\",\"y\":2}]}]}\n```\n\nBottom line."
            let segs = VeraBlocks.segments(blockReply)
            let proseN = segs.filter { if case .prose = $0 { return true }; return false }.count
            let chartN = segs.filter { if case .chart = $0 { return true }; return false }.count
            let statN = segs.filter { if case .stats = $0 { return true }; return false }.count
            guard chartN == 1, statN == 1, proseN == 3,
                  case .chart(_, let spec) = segs.first(where: { if case .chart = $0 { return true }; return false })!,
                  spec.series.first?.points.count == 2 else {
                print("SELFTEST ERROR: presentation blocks parse \(proseN)/\(chartN)/\(statN)"); exit(1)
            }
            print("  presentation blocks OK (\(proseN) prose, \(chartN) chart, \(statN) stats)")

            // Wyoming framing round-trip (encode → parse) — pure, local, no network.
            var payload = Data(count: 320)
            for i in 0..<320 { payload[i] = UInt8(i & 0xFF) }
            let encoded = WyomingClient.encode(
                type: "audio-chunk",
                data: ["rate": 16000, "width": 2, "channels": 1, "timestamp": 0],
                payload: payload)
            guard let nl = encoded.firstIndex(of: 0x0A),
                  let header = try? JSONSerialization.jsonObject(with: encoded[encoded.startIndex..<nl]) as? [String: Any],
                  header["type"] as? String == "audio-chunk",
                  let dataLen = header["data_length"] as? Int,
                  header["payload_length"] as? Int == 320 else {
                print("SELFTEST ERROR: wyoming header"); exit(1)
            }
            let dataStart = encoded.index(after: nl)
            let dataEnd = encoded.index(dataStart, offsetBy: dataLen)
            guard let dataDict = try? JSONSerialization.jsonObject(with: encoded[dataStart..<dataEnd]) as? [String: Any],
                  dataDict["rate"] as? Int == 16000, dataDict["width"] as? Int == 2,
                  dataDict["channels"] as? Int == 1 else {
                print("SELFTEST ERROR: wyoming data"); exit(1)
            }
            let payloadBytes = Data(encoded[dataEnd..<encoded.index(dataEnd, offsetBy: 320)])
            guard payloadBytes == payload else {
                print("SELFTEST ERROR: wyoming payload bytes corrupted"); exit(1)
            }
            print("  wyoming framing OK (type=audio-chunk, data={rate,width,channels}, payload 320B intact)")

            // Scheduler plumbing: cron summaries + tolerant GET /scheduler/jobs decode (pure, local).
            let cronCases = [
                ("0 5 * * *", "Daily 5:00 AM"), ("*/20 * * * *", "Every 20 min"),
                ("0 */6 * * *", "Every 6 hours"), ("0 6,18 * * *", "Daily 6:00 AM & 6:00 PM"),
                ("0 9 * * 0", "Sundays 9:00 AM"), ("oddball", "Custom schedule"),
                ("0 6 * * 1-5", "Custom schedule"), ("15 3 1 * *", "Custom schedule"),
            ]
            for (cron, want) in cronCases where cronSummary(cron) != want {
                print("SELFTEST ERROR: cronSummary(\(cron)) = \(cronSummary(cron)), want \(want)"); exit(1)
            }
            let schedJSON = """
            {"enabled": true, "jobs": [
              {"id": "pulse", "label": "Pulse briefing", "cron": "0 5 * * *", "enabled": true,
               "last_run": {"ts": 1750000000, "ok": true, "detail": "6 cards"}, "next_run": "2026-06-10T05:00:00"},
              {"id": "heartbeat", "cron": "*/20 * * * *", "enabled": false, "env_locked": true}
            ]}
            """
            guard let schedObj = try? JSONSerialization.jsonObject(with: Data(schedJSON.utf8)),
                  let state = SchedulerState.parse(schedObj), state.masterEnabled,
                  state.jobs.count == 2, state.jobs[0].lastRunOK == true,
                  state.jobs[0].lastRunDetail == "6 cards", state.jobs[0].nextRun != nil,
                  state.jobs[1].envLocked, !state.jobs[1].enabled, state.jobs[1].label == "heartbeat" else {
                print("SELFTEST ERROR: scheduler state parse"); exit(1)
            }
            print("  scheduler OK (\(cronCases.count) cron summaries, jobs decode (incl. env-locked))")

            // Canvas graph manifest decode: flows with stages/state, surfaces with stats.
            let graphJSON = """
            {"flows": [
              {"id": "pulse", "label": "Pulse briefing", "title": "Pulse briefing run", "kind": "job",
               "icon": "newspaper", "tint": "accent", "group": "Ambient", "feeds": ["pulse_feed"],
               "tools": ["websearch"], "running": false, "stage_layout": "pipeline",
               "stages": [{"id": "triage", "label": "Triage", "icon": "globe", "tint": "accent"}],
               "stage_state": {"state": "ok", "rounds": 2, "proposed": 9,
                               "gates": {"dedup": 3}, "injected": 6,
                               "warnings": ["starved run: 6/8"], "finished_at": 1750000000}},
              {"id": "heartbeat", "label": "Heartbeat", "kind": "heartbeat", "icon": "heart",
               "tint": "accent", "group": "Heartbeat", "feeds": ["memory"], "tools": [],
               "running": true, "stage_layout": "fan",
               "stages": [{"id": "learn", "label": "Learn", "icon": "sparkles", "tint": "accent",
                           "feeds": ["memory"]}],
               "branch_state": {"learn": {"kind": "learn", "detail": "a topic", "ts": 1750000000}}}
            ],
            "surfaces": [{"id": "pulse_feed", "label": "Pulse feed", "icon": "newspaper",
                          "stat": "6 cards today"},
                         {"id": "memory", "label": "Memory", "icon": "archivebox", "stat": null}]}
            """
            guard let graphObj = try? JSONSerialization.jsonObject(with: Data(graphJSON.utf8)),
                  let graph = AgenticGraph.parse(graphObj),
                  graph.flows.count == 2, graph.surfaces.count == 2,
                  let gp = graph.flow("pulse"), gp.stages.count == 1,
                  gp.pulseState?.gates["dedup"] == 3, gp.pulseState?.injected == 6,
                  gp.pulseState?.warnings.count == 1,
                  let hb = graph.flow("heartbeat"), hb.kind == "heartbeat", hb.running,
                  hb.branchState["learn"]?.detail == "a topic",
                  graph.surfaces[0].stat == "6 cards today", graph.surfaces[1].stat == nil else {
                print("SELFTEST ERROR: agentic graph parse"); exit(1)
            }
            print("  agentic graph OK (flows + stages + state, surfaces incl. nil stat)")

            guard let editorCatalog = WorkflowCatalog.fixture(),
                  editorCatalog.nodes.count == 8,
                  editorCatalog.paletteNodes.count == 8,
                  editorCatalog.paletteCategories == ["core", "visual"],
                  editorCatalog.profile.canonicalOrder == ["pulse.triage", "pulse.gates", "pulse.synthesis", "pulse.claim_audit",
                                                          "pulse.cover_art", "pulse.visual_review", "pulse.cover_retry", "pulse.inject"],
                  editorCatalog.label(for: "pulse.inject") == "Inject" else {
                print("SELFTEST ERROR: workflow catalog parse"); exit(1)
            }
            guard let reviewSpec = editorCatalog.node(for: "pulse.visual_review"),
                  case .number(let lowBound, let highBound) = reviewSpec.fields.first?.kind,
                  lowBound == 0, highBound == 1,
                  reviewSpec.fields.first?.defaultValue == .double(0.8),
                  let retrySpec = editorCatalog.node(for: "pulse.cover_retry"),
                  case .choice(let attemptOptions) = retrySpec.fields.first?.kind,
                  attemptOptions == [.int(0), .int(1)],
                  retrySpec.defaultConfig["max_attempts"] == .int(1) else {
                print("SELFTEST ERROR: workflow catalog schema"); exit(1)
            }
            let malformedCatalogJSON = """
            {"nodes":[{"type":"pulse.triage","label":"Triage","icon":"globe","tint":"accent","category":"core","config_schema":{},"insertable":false},
                      {"type":"broken","label":"Broken","icon":"globe","tint":"accent","category":"core",
                       "config_schema":{"level":{"type":"choice","options":[]}},"insertable":true}],
             "profile":{"id":"pulse","spine":[],"insertable_categories":[],"pairs":[]}}
            """
            guard let malformedObject = try? JSONSerialization.jsonObject(with: Data(malformedCatalogJSON.utf8)),
                  WorkflowCatalog.parse(malformedObject) == nil else {
                print("SELFTEST ERROR: workflow catalog strictness"); exit(1)
            }
            guard let openField = WorkflowSchemaField.parse(key: "budget", raw: ["type": "number", "min": 0]),
                  case .number(let openLow, let openHigh) = openField.kind,
                  openLow == 0, openHigh == nil,
                  WorkflowSchemaField.parse(key: "mode", raw: ["type": "gradient"]) == nil,
                  WorkflowSchemaField.parse(key: "budget", raw: ["type": "number", "min": "invalid"]) == nil,
                  WorkflowCatalogNode.parse(["type": "x", "label": "X", "config_schema": "broken"]) == nil,
                  WorkflowProfile.parse(["id": "pulse", "pairs": "broken"]) == nil,
                  WorkflowProfile.parse(["id": "pulse", "pairs": [["types": ["a"]]]]) == nil,
                  WorkflowProfile.parse(["id": "generic"])?.spine == [] else {
                print("SELFTEST ERROR: workflow schema field bounds"); exit(1)
            }
            let workflowJSON = """
            {"id":"pulse","nodes":[
              {"id":"cover_art","type":"pulse.cover_art","config":{"style":"editorial"}},
              {"id":"visual_review","type":"pulse.visual_review","config":{"threshold":0.8}},
              {"id":"cover_retry","type":"pulse.cover_retry","config":{"max_attempts":1}}
            ],"edges":[{"from":"cover_art","to":"visual_review"},{"from":"visual_review","to":"cover_retry"}]}
            """
            guard let workflowObject = try? JSONSerialization.jsonObject(with: Data(workflowJSON.utf8)),
                  let workflow = PulseWorkflowDefinition.parse(workflowObject),
                  workflow.nodes[1].config["threshold"] == .double(0.8),
                  workflow.nodes[2].config["max_attempts"] == .int(1) else {
                print("SELFTEST ERROR: pulse workflow parse"); exit(1)
            }
            let roundTripped = workflow.jsonObject()
            guard let tripNodes = roundTripped["nodes"] as? [[String: Any]],
                  let thresholdBack = (tripNodes[1]["config"] as? [String: Any])?["threshold"] as? Double,
                  thresholdBack == 0.8,
                  let attemptsBack = (tripNodes[2]["config"] as? [String: Any])?["max_attempts"] as? NSNumber,
                  !CFNumberIsFloatType(attemptsBack), attemptsBack.intValue == 1 else {
                print("SELFTEST ERROR: pulse workflow config round trip"); exit(1)
            }
            guard PulseWorkflowClient.rejectionMessage(from: Data(#"{"detail":"core stages are out of order"}"#.utf8)) == "core stages are out of order",
                  PulseWorkflowClient.rejectionMessage(from: Data("{}".utf8)) == nil else {
                print("SELFTEST ERROR: workflow rejection parse"); exit(1)
            }
            let editorStore = PulseWorkflowStore.fixture()
            editorStore.startConnection(from: "cover_art")
            guard editorStore.connectionSourceID == nil else {
                print("SELFTEST ERROR: active workflow connection"); exit(1)
            }
            editorStore.draft = editorStore.active
            editorStore.startConnection(from: "cover_art")
            editorStore.completeConnection(to: "inject")
            guard !(editorStore.draft?.definition.edges.contains(PulseWorkflowEdge(from: "cover_art", to: "inject")) ?? true) else {
                print("SELFTEST ERROR: visual workflow connection"); exit(1)
            }
            editorStore.selectedNodeID = "visual_review"
            editorStore.removeSelectedNode()
            guard editorStore.draft?.definition.nodes.contains(where: { $0.type == "pulse.visual_review" }) == false,
                  editorStore.draft?.definition.edges.contains(PulseWorkflowEdge(from: "cover_art", to: "cover_retry")) == true,
                  editorStore.validationMessage == "Add Visual review to complete this path.",
                  editorStore.canSave == false else {
                print("SELFTEST ERROR: visual workflow removal"); exit(1)
            }
            editorStore.placeNodeInDraft("pulse.visual_review", at: CGPoint(x: 1030, y: 310))
            guard editorStore.draft?.definition.edges.contains(PulseWorkflowEdge(from: "cover_art", to: "visual_review")) == true,
                  editorStore.draft?.definition.edges.contains(PulseWorkflowEdge(from: "visual_review", to: "cover_retry")) == true,
                  editorStore.draft?.definition.edges.contains(PulseWorkflowEdge(from: "cover_art", to: "cover_retry")) == false,
                  editorStore.draft?.definition.node(withID: "visual_review")?.config["threshold"] == .double(0.8),
                  editorStore.validationMessage == nil else {
                print("SELFTEST ERROR: visual workflow wire insertion"); exit(1)
            }
            editorStore.selectedNodeID = "visual_review"
            editorStore.setConfigValue("threshold", .double(0.6))
            guard editorStore.draft?.definition.node(withID: "visual_review")?.config["threshold"] == .double(0.6) else {
                print("SELFTEST ERROR: schema config mutation"); exit(1)
            }
            let nodeCount = editorStore.draft?.definition.nodes.count
            editorStore.placeNodeInDraft("pulse.triage", at: CGPoint(x: 180, y: 120))
            guard editorStore.draft?.definition.nodes.count == nodeCount,
                  editorStore.draft?.definition.positions["triage"] == PulseWorkflowPoint(x: 180, y: 120) else {
                print("SELFTEST ERROR: installed workflow node placement"); exit(1)
            }
            print("  pulse workflow OK (served catalog + schema fields + profile validation)")

            let runFixtureStore = PulseWorkflowStore.runFixture()
            guard runFixtureStore.mode == .run,
                  let fixtureRun = runFixtureStore.latestRun,
                  fixtureRun.state == "ok",
                  fixtureRun.nodes.count == 8,
                  fixtureRun.nodeRun("missing") == nil,
                  let triageRun = fixtureRun.nodeRun("triage"),
                  triageRun.countsLine == "3 rounds",
                  triageRun.duration == 60,
                  let synthesisRun = fixtureRun.nodeRun("synthesis"),
                  synthesisRun.state == "warning",
                  synthesisRun.error == "one card starved for sources",
                  fixtureRun.nodeRun("gates")?.countsLine == "6 items",
                  fixtureRun.version?.id == "fixture-pinned",
                  fixtureRun.version?.number == 2,
                  fixtureRun.version?.definition.nodes.count == 8,
                  runFixtureStore.runVersion?.id == "fixture-pinned" else {
                print("SELFTEST ERROR: workflow run parse"); exit(1)
            }
            let pinnedRunJSON = """
            {"id":"r2","state":"ok","started_at":1754250000,
             "version":{"id":"v-old","version":1,"state":"archived","definition":{"id":"pulse",
               "nodes":[{"id":"triage","type":"pulse.triage","config":{}},
                        {"id":"experiment","type":"flow.filter","config":{}},
                        {"id":"inject","type":"pulse.inject","config":{}}],
               "edges":[{"from":"triage","to":"experiment"},{"from":"experiment","to":"inject"}]}},
             "nodes":[{"id":"experiment","state":"ok","output":{"items":2},"started_at":1754250001,"finished_at":1754250002}]}
            """
            guard let pinnedObject = try? JSONSerialization.jsonObject(with: Data(pinnedRunJSON.utf8)),
                  let pinnedRun = PulseWorkflowRun.parse(pinnedObject),
                  pinnedRun.version?.definition.node(withID: "experiment") != nil,
                  pinnedRun.nodeRun("experiment")?.countsLine == "2 items" else {
                print("SELFTEST ERROR: workflow run pinned version"); exit(1)
            }
            guard PulseWorkflowRun.classify(nil) == (nil, false),
                  PulseWorkflowRun.classify(NSNull()) == (nil, false),
                  PulseWorkflowRun.classify(["id": "broken"]) == (nil, true),
                  PulseWorkflowRun.classify(pinnedObject).unreadable == false else {
                print("SELFTEST ERROR: workflow run classification"); exit(1)
            }
            let brokenVersion = PulseWorkflowRun.classify(["id": "r3", "state": "ok", "started_at": 1754250000,
                                                          "version": ["broken": true]])
            let nullVersion = PulseWorkflowRun.classify(["id": "r4", "state": "ok", "started_at": 1754250000,
                                                        "version": NSNull()])
            let absentVersion = PulseWorkflowRun.classify(["id": "r5", "state": "ok", "started_at": 1754250000])
            guard brokenVersion == (nil, true),
                  nullVersion == (nil, true),
                  absentVersion.unreadable == false,
                  absentVersion.run?.version == nil else {
                print("SELFTEST ERROR: workflow run version strictness"); exit(1)
            }
            let evidence = fixtureRun.cardEvidence
            guard evidence.count == 3,
                  evidence[1].cardID == "card-2",
                  evidence[1].retryCount == 1,
                  evidence[1].state == "retried",
                  evidence[1].verdictLine == "Retried \u{b7} score 0.55 \u{b7} retry 1",
                  evidence[0].verdictLine == "Accepted \u{b7} score 0.94" else {
                print("SELFTEST ERROR: workflow run evidence"); exit(1)
            }
            guard PulseWorkflowRun.parse(["id": "x", "state": "ok"]) == nil,
                  PulseWorkflowRun.parse(NSNull()) == nil,
                  let bareRun = PulseWorkflowRun.parse(["id": "x", "state": "running", "started_at": 1754250000]),
                  bareRun.nodes.isEmpty, bareRun.cardEvidence.isEmpty, bareRun.finishedAt == nil,
                  bareRun.version == nil,
                  PulseWorkflowNodeRun.parse(["id": "n"]) == nil,
                  let openNode = PulseWorkflowNodeRun.parse(["id": "n", "state": "running", "started_at": 1754250000]),
                  openNode.duration == nil, openNode.countsLine == nil,
                  let legacyNode = PulseWorkflowNodeRun.parse(["id": "n", "state": "warning",
                                                              "output": ["review": ["accept": true], "rounds": 1]]),
                  legacyNode.output.keys.sorted() == ["rounds"], legacyNode.countsLine == "1 rounds" else {
                print("SELFTEST ERROR: workflow run strictness"); exit(1)
            }
            guard runDurationText(0.4) == "under 1s", runDurationText(75) == "75s", runDurationText(212) == "3m 32s" else {
                print("SELFTEST ERROR: run duration format"); exit(1)
            }
            let emptyRunStore = PulseWorkflowStore.fixture()
            emptyRunStore.mode = .run
            guard emptyRunStore.latestRun == nil else {
                print("SELFTEST ERROR: run empty state"); exit(1)
            }
            print("  workflow run mode OK (record parse + node mapping + evidence + degradation)")

            // Config file round-trip on a temp path: write → read preserves strings + unknown keys.
            let tmp = FileManager.default.temporaryDirectory
                .appendingPathComponent("vera-selftest-\(UUID().uuidString)/config.json")
            try ConfigFile.write(["base": "http://owui.example:6590", "owner_name": "Jordan",
                                  "custom_extra": ["keep": true]], at: tmp)
            let back = ConfigFile.read(at: tmp)
            guard back["base"] as? String == "http://owui.example:6590",
                  back["owner_name"] as? String == "Jordan",
                  (back["custom_extra"] as? [String: Any])?["keep"] as? Bool == true else {
                print("SELFTEST ERROR: config file round-trip"); exit(1)
            }
            try? FileManager.default.removeItem(at: tmp.deletingLastPathComponent())
            print("  config round-trip OK (strings + unknown keys preserved)")

            // Tool log round-trip on a temp path: append JSONL lines → load newest-first, capped.
            let logURL = FileManager.default.temporaryDirectory
                .appendingPathComponent("vera-selftest-\(UUID().uuidString).jsonl")
            let t0 = Date(timeIntervalSince1970: 1750000000)
            for i in 0..<4 {
                ToolLog.append(Invocation(label: "tool_\(i)", at: t0.addingTimeInterval(Double(i) * 60)),
                               to: logURL)
            }
            let logBack = ToolLog.load(from: logURL)
            let capped = ToolLog.load(limit: 2, from: logURL)
            guard logBack.count == 4, logBack.first?.label == "tool_3", logBack.last?.label == "tool_0",
                  abs(logBack.first!.at.timeIntervalSince(t0.addingTimeInterval(180))) < 1,
                  capped.count == 2, capped.first?.label == "tool_3", capped.last?.label == "tool_2" else {
                print("SELFTEST ERROR: tool log round-trip"); exit(1)
            }
            try? FileManager.default.removeItem(at: logURL)
            print("  tool log OK (4 appended, newest-first load, tail cap honored)")

            // Update semver compare: the decision table behind the update banner.
            let semverCases: [(String, String, Int)] = [
                ("0.1.0", "0.1.0", 0),       // equal -> no banner
                ("0.1.0", "v0.1.0", 0),      // tag prefix tolerated
                ("0.1.0", "0.1.1", -1),      // patch-newer release
                ("0.1.0", "0.2.0", -1),      // minor-newer release
                ("0.2.0", "0.1.9", 1),       // running build ahead
                ("0.1", "0.1.0", 0),         // ragged lengths
                ("0.1.0", "garbage", 1),     // junk tag can never look newer
            ]
            for (a, b, want) in semverCases where Semver.compare(a, b) != want {
                print("SELFTEST ERROR: semver compare \(a) vs \(b)"); exit(1)
            }
            guard Semver.minor("0.3.1") == 3, Semver.minor("v1.2.0") == 2 else {
                print("SELFTEST ERROR: semver minor extraction"); exit(1)
            }
            print("  update semver OK (\(semverCases.count) compare cases, minor extraction)")

            // Resource bundle must resolve in THIS layout (packaged .app or .build binary) —
            // the generated Bundle.module accessor varies by toolchain and has shipped builds
            // that only resolve on the machine that built them.
            guard VeraResources.bundle != nil, Brand.glyph != nil,
                  VeraResources.url("mermaid.min", ext: "js") != nil else {
                print("SELFTEST ERROR: resource bundle unresolved (mark/mermaid missing)"); exit(1)
            }
            print("  resources OK (bundle resolved, mark + mermaid present)")

            // Chat history graph: automation-written chats store turns only in
            // history.messages (id-keyed, parent-linked); the thread follows currentId.
            let graphChat: [String: Any] = [
                "messages": [["role": "user", "content": "only the user turn"]],
                "history": [
                    "currentId": "c",
                    "messages": [
                        "a": ["id": "a", "role": "user", "content": "q1"],
                        "b": ["id": "b", "role": "assistant", "content": "r1", "parentId": "a"],
                        "c": ["id": "c", "role": "user", "content": "q2", "parentId": "b"],
                        "x": ["id": "x", "role": "assistant", "content": "abandoned branch", "parentId": "a"],
                    ],
                ],
            ]
            let ordered = OWUIClient.ChatHistory.orderedMessages(graphChat)
            guard ordered.count == 3,
                  ordered.map({ $0["content"] as? String }) == ["q1", "r1", "q2"] else {
                print("SELFTEST ERROR: history graph reconstruction"); exit(1)
            }
            let flatChat: [String: Any] = ["messages": [["role": "user", "content": "flat"]]]
            guard OWUIClient.ChatHistory.orderedMessages(flatChat).count == 1 else {
                print("SELFTEST ERROR: history flat-list fallback"); exit(1)
            }
            print("  chat history OK (graph walk follows currentId, flat fallback intact)")

            // Reasoning details blocks are stripped at render; tool_calls handling unchanged.
            let reasoned = "<details type=\"reasoning\" done=\"true\"><summary>Thought</summary>thinking…</details>\nThe actual answer."
            let (cleanR, callsR) = ToolCallParser.parse(reasoned)
            guard cleanR == "The actual answer.", callsR.isEmpty else {
                print("SELFTEST ERROR: reasoning block strip"); exit(1)
            }
            print("  reasoning strip OK (details removed, reply intact)")

            // OWUI source payloads map to numbered chips in payload order.
            let mapped = OWUISources.parse([
                ["source": ["name": "BBC Sport"], "metadata": [["source": "https://bbc.co.uk/a"]]],
                ["source": ["name": "https://theathletic.com/b"]],
                ["source": ["name": "no url here"]],  // unresolvable -> dropped
            ])
            guard mapped.count == 2, mapped[0].n == 1, mapped[0].title == "BBC Sport",
                  mapped[0].url == "https://bbc.co.uk/a", mapped[1].url == "https://theathletic.com/b" else {
                print("SELFTEST ERROR: OWUI source mapping"); exit(1)
            }
            print("  source mapping OK (\(mapped.count) chips, unresolvable dropped)")

            let legacyEvent: [String: Any] = ["content": "partial reply", "done": false]
            let snapshotEvent: [String: Any] = ["output": [
                ["type": "reasoning", "content": [["type": "output_text", "text": "thinking about it"]]],
                ["type": "message", "role": "assistant", "content": [["type": "output_text", "text": "Paris is"]]],
            ]]
            let doneEvent: [String: Any] = ["done": true, "output": [
                ["type": "reasoning", "content": [["type": "output_text", "text": "thinking about it"]]],
                ["type": "message", "role": "assistant", "content": [["type": "output_text", "text": "Paris is the capital."]]],
            ]]
            let chunkEvent: [String: Any] = ["choices": [["delta": ["role": "assistant", "content": NSNull()]]]]
            let toolTurnEvent: [String: Any] = ["done": true, "output": [
                ["type": "message", "role": "assistant", "content": [["type": "output_text", "text": "Let me check.\n"]]],
                ["type": "function_call", "id": "fc_1", "call_id": "c1", "name": "kitchen_status", "arguments": "{}", "status": "completed"],
                ["type": "function_call_output", "id": "fco_1", "call_id": "c1", "output": [["type": "input_text", "text": "Low: <eggs> & milk"]], "status": "completed"],
                ["type": "message", "role": "assistant", "content": [["type": "output_text", "text": "Here is the answer."]]],
            ]]
            let toolTurnText = VeraSocket.completionText(toolTurnEvent) ?? ""
            let (toolClean, toolCalls) = ToolCallParser.parse(toolTurnText)
            guard VeraSocket.completionText(legacyEvent) == "partial reply",
                  VeraSocket.completionText(snapshotEvent) == "Paris is",
                  VeraSocket.completionText(doneEvent) == "Paris is the capital.",
                  VeraSocket.completionText(doneEvent)?.contains("thinking") == false,
                  VeraSocket.completionText(chunkEvent) == nil,
                  toolClean.hasPrefix("Let me check."), toolClean.hasSuffix("Here is the answer."),
                  toolCalls.count == 1, toolCalls[0].name == "kitchen_status",
                  toolCalls[0].detail == "Low: <eggs> & milk" else {
                print("SELFTEST ERROR: completion event text extraction"); exit(1)
            }
            print("  completion text OK (legacy string, output snapshot, done, reasoning excluded, chunk nil, tool-turn chips round-trip)")

            for preset in SchedulePreset.allCases {
                guard SchedulePreset.match(preset.cron) == preset else {
                    print("SELFTEST ERROR: schedule preset \(preset.rawValue) round-trip"); exit(1)
                }
            }
            guard SchedulePreset.match("7 3 2 1 *") == nil else {
                print("SELFTEST ERROR: unmatched cron should stay custom"); exit(1)
            }

            let draftJSON = """
            {"kind": "river_gauge", "label": "River gauge", "icon": "water.waves",
             "nominal_label": "normal", "blurb": "the local gauge", "schedule": "*/30 * * * *",
             "providers": [{"id": "gauge_url", "label": "Gauge", "hint": "", "default": "https://g/x"}],
             "options": [{"group": "Focus", "fields": [{"id": "f", "label": "F", "type": "text",
                          "choices": [], "hint": "", "default": "x"}]}],
             "pipeline": [{"block": "http_fetch", "params": {"url": "{providers.gauge_url}"}},
                          {"block": "trip_band", "params": {"hi": 21.5}}]}
            """
            guard let dObj = try? JSONSerialization.jsonObject(with: Data(draftJSON.utf8)) as? [String: Any],
                  var draft = VeinDraft.parse(dObj),
                  draft.usedBlocks == ["http_fetch", "trip_band"],
                  draft.hasBand, !draft.hasBar, draft.bandHi == "21.5" else {
                print("SELFTEST ERROR: vein draft parse"); exit(1)
            }
            draft.bandLo = "5"
            let enc = draft.encode()
            guard let reparsed = VeinDraft.parse(enc), reparsed.bandLo == "5", reparsed.bandHi == "21.5",
                  (enc["providers"] as? [[String: Any]])?.count == 1,
                  (enc["options"] as? [[String: Any]])?.count == 1 else {
                print("SELFTEST ERROR: vein draft encode round-trip"); exit(1)
            }
            let stripped = draft.stripping(["trip_band"])
            guard stripped.usedBlocks == ["http_fetch"] else {
                print("SELFTEST ERROR: vein draft tool stripping"); exit(1)
            }
            print("  vein builder OK (\(SchedulePreset.allCases.count) presets, draft round-trip, tool strip)")

            let engineBaseCases: [(String?, String?, Int, String?)] = [
                ("local", "http://remote:8089", 9000, "http://127.0.0.1:9000"),
                ("off", "http://remote:8089", 8089, nil),
                ("remote", "http://remote:8089", 8089, "http://remote:8089"),
                (nil, "http://remote:8089", 8089, "http://remote:8089"),
                (nil, "", 8089, nil),
                (nil, nil, 8089, nil),
            ]
            for (m, r, p, want) in engineBaseCases {
                let got = OWUIConfig.effectiveVeraAPIBase(mode: m, remote: r, port: p)?.absoluteString
                guard got == want else {
                    print("SELFTEST ERROR: effective base mode=\(m ?? "nil") remote=\(r ?? "nil") = \(got ?? "nil"), want \(want ?? "nil")")
                    exit(1)
                }
            }

            guard EngineManager.needsEngineUpdate(installed: nil, app: "0.3.0"),
                  EngineManager.needsEngineUpdate(installed: "0.2.0", app: "0.3.0"),
                  !EngineManager.needsEngineUpdate(installed: "0.3.0", app: "0.3.0") else {
                print("SELFTEST ERROR: engine version-compare decision"); exit(1)
            }

            guard EngineManager.versionsToPrune(["0.1.0", "0.2.0", "0.3.0", "0.0.9"]).sorted() == ["0.0.9", "0.1.0"],
                  EngineManager.versionsToPrune(["0.2.0", "0.3.0"]).isEmpty else {
                print("SELFTEST ERROR: engine version prune selection"); exit(1)
            }

            guard EngineManager.sha256Hex(Data("abc".utf8)) ==
                    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" else {
                print("SELFTEST ERROR: engine sha256"); exit(1)
            }
            let goodSha = String(repeating: "a", count: 64) + "  vera-api-macos-arm64.zip\n"
            guard EngineManager.expectedSha(fromAsset: goodSha) == String(repeating: "a", count: 64),
                  EngineManager.expectedSha(fromAsset: "not a hash") == nil else {
                print("SELFTEST ERROR: engine sha asset parse"); exit(1)
            }

            guard let engineTpl = EngineManager.plistTemplate() else {
                print("SELFTEST ERROR: engine plist template missing from bundle"); exit(1)
            }
            let fakeHome = "/home/tester"
            let binPath = "\(fakeHome)/.vera/engine/0.3.0/vera-api/vera-api"
            let renderedPlist = EngineManager.renderPlist(engineTpl, home: fakeHome,
                                                          binaryPath: binPath, port: 8123)
            guard renderedPlist.contains("<string>\(binPath)</string>"),
                  renderedPlist.contains("<key>VERA_PORT</key><string>8123</string>"),
                  renderedPlist.contains("<string>\(fakeHome)/.vera/data</string>"),
                  renderedPlist.contains("<string>\(fakeHome)/.vera/engine/engine.log</string>"),
                  renderedPlist.contains("<string>com.vera.engine</string>"),
                  !renderedPlist.contains("@") else {
                print("SELFTEST ERROR: engine plist render"); exit(1)
            }
            print("  local engine OK (\(engineBaseCases.count) base cases, version-compare, prune, sha256, plist render)")
        } catch {
            print("SELFTEST ERROR: \(error)")
            exit(1)
        }
    }

    /// DEBUG-ONLY: stream a wav's PCM through the real Wyoming ASR server and print the
    /// transcript, then round-trip the text through the TTS server. Proves the Swift Wyoming client
    /// works against the live servers without a mic. Remove/guard after validation.
    static func voiceE2E(wavPath: String) async {
        let host = ProcessInfo.processInfo.environment["VERA_VOICE_HOST"] ?? "127.0.0.1"
        let client = VoiceClient(host: host)
        print("voice-e2e: host=\(host) wav=\(wavPath)")

        guard let pcm = load16kMonoInt16(path: wavPath) else {
            print("voice-e2e ERROR: could not read/convert \(wavPath)"); exit(1)
        }
        print("voice-e2e: \(pcm.count) PCM bytes (\(pcm.count / 2) samples @ 16k)")

        do {
            let stream = try await client.startTranscription()
            // Feed in frames; size from env (samples) so we can probe engine chunk sensitivity.
            let frameSamples = Int(ProcessInfo.processInfo.environment["VERA_E2E_FRAME"] ?? "512") ?? 512
            let frameBytes = frameSamples * 2
            var off = 0
            while off < pcm.count {
                let end = min(off + frameBytes, pcm.count)
                stream.send(pcm.subdata(in: off..<end))
                off = end
            }
            let text = try await stream.finish()
            print("voice-e2e TRANSCRIPT: \(text.debugDescription)")

            let wav = try await client.synthesize("End to end voice is working.", voice: nil)
            print("voice-e2e TTS: \(wav.count) WAV bytes returned")
            exit(text.isEmpty ? 1 : 0)
        } catch {
            print("voice-e2e ERROR: \(error)"); exit(1)
        }
    }

    /// Read a WAV file and return int16 16 kHz mono little-endian PCM (downsampling/downmixing).
    private static func load16kMonoInt16(path: String) -> Data? {
        guard let file = try? AVAudioFile(forReading: URL(fileURLWithPath: path)) else { return nil }
        let inFormat = file.processingFormat
        let frames = AVAudioFrameCount(file.length)
        guard frames > 0,
              let inBuf = AVAudioPCMBuffer(pcmFormat: inFormat, frameCapacity: frames),
              (try? file.read(into: inBuf)) != nil,
              let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000,
                                         channels: 1, interleaved: true),
              let conv = AVAudioConverter(from: inFormat, to: target) else { return nil }
        let ratio = 16000.0 / inFormat.sampleRate
        let outCap = AVAudioFrameCount(Double(frames) * ratio) + 1024
        guard let outBuf = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: outCap) else { return nil }
        var fed = false
        var err: NSError?
        conv.convert(to: outBuf, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true; status.pointee = .haveData; return inBuf
        }
        guard err == nil, let ch = outBuf.int16ChannelData else { return nil }
        let n = Int(outBuf.frameLength)
        return Data(bytes: ch[0], count: n * 2)
    }

    /// One-shot: append the `vera:ask` + `vera-artifact` conventions to Vera's system prompt (idempotent).
    static func installConventions() async {
        guard let cfg = OWUIConfig.load() else { print("no OWUI config"); exit(1) }
        let socket = VeraSocket(config: cfg)
        let admin = OWUIAdminClient(baseURL: cfg.baseURL, modelID: cfg.model,
                                    token: { try await socket.currentToken() })
        do {
            let ask = try await admin.ensureAskConvention()
            let art = try await admin.ensureArtifactConvention()
            let pres = try await admin.ensurePresentationConventions()
            print("vera:ask: \(ask ? "ADDED" : "present"); vera-artifact: \(art ? "ADDED" : "present"); presentation tools: \(pres ? "ADDED" : "present").")
            exit(0)
        } catch {
            print("install error: \(error)")
            exit(1)
        }
    }
}
