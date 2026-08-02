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

@MainActor
enum SelfTest {
    static func run() async {
        runPure()
        runNativeSettings()
        await runNativeTools()
        await runNativeStore()
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
                model: "local-model", chatTemplateKwargs: nil))
            let request = try requestClient.request(
                messages: [NativeChatMessage(role: "user", content: "List reminders")],
                model: "local-model",
                tools: registry.active(enabledIDs: ["apple-reminders"]).map(\.schema))
            let requestBody = try JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any]
            let requestTools = requestBody?["tools"] as? [[String: Any]]
            let requestNames = requestTools?.compactMap { ($0["function"] as? [String: Any])?["name"] as? String }
            guard requestTools?.count == 4,
                  requestNames?.contains("apple_reminders_list") == true,
                  requestNames?.contains("apple_reminders_get_lists") == true else {
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
            print("  native tools OK (registry, standard request, fragmented calls, continuation, validation, limits, reminders, text-only)")
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
        guard settings.version == 1,
              settings.profiles.count == 1,
              settings.activeProfile?.name == "My model endpoint",
              settings.activeProfile?.selectedModel == "model-b",
              settings.activeProfile?.selectionBasis == .restored,
              settings.onboardingState == .complete,
              settings.systemPrompt == NativeChatSettings.defaultSystemPrompt else {
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
