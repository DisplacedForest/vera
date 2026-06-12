import Foundation
import AVFoundation

/// Headless proof the OWUI client works against a live server — no GUI needed.
/// Run: `Vera --selftest` with OWUI_BASE + OWUI_KEY in the environment.
@MainActor
enum SelfTest {
    static func run() async {
        runPure()
        guard let cfg = OWUIConfig.load() else {
            print("SELFTEST OK (offline) — no OWUI config (~/.vera/config.json), live checks skipped")
            exit(0)
        }
        await runLive(cfg)
    }

    /// Live checks against the configured OWUI / vera-api deployment.
    private static func runLive(_ cfg: OWUIConfig) async {
        let client = OWUIClient(config: cfg)
        print("OWUI: \(cfg.baseURL.absoluteString)   model: \(cfg.model)")
        do {
            let chats = try await client.listChats()
            print("listChats OK — \(chats.count) chats")
            if let first = chats.first {
                let msgs = await client.loadMessages(chatID: first.id)
                print("loadMessages OK — '\(first.title.prefix(40))' → \(msgs.count) messages")
            }
            if let mems = await client.memories() {
                print("memories OK — \(mems.count) entries")
            } else {
                print("memories FAILED — fetch error")
            }
            // The folder id is deployment config, never baked in: PULSE_FOLDER_ID env or
            // the pulse_folder_id key in ~/.vera/config.json; absent -> skip the check.
            let env = ProcessInfo.processInfo.environment["PULSE_FOLDER_ID"]
            let folderID = (env?.isEmpty == false ? env : nil)
                ?? (ConfigFile.read()["pulse_folder_id"] as? String)
            if let folderID, !folderID.isEmpty {
                let cards = await client.pulseCards(folderID: folderID)
                print("pulseCards OK — \(cards.count) cards in the Pulse folder")
            } else {
                print("pulseCards skipped — set PULSE_FOLDER_ID (or pulse_folder_id in ~/.vera/config.json)")
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
                    print("  images/search OK — \((iObj["results"] as? [[String: Any]])?.count ?? 0) hits")
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

    /// Pure, local checks — no network, no config required. CI runs exactly these; live
    /// checks follow only when an OWUI config exists.
    private static func runPure() {
        do {
            // vera:ask block parses out of an assistant reply (pure, local).
            let demo = "Sure, happy to help.\n```vera:ask\n{\"question\":\"Pick one\",\"multiSelect\":false,\"options\":[{\"label\":\"A\",\"description\":\"first\"},{\"label\":\"B\",\"description\":\"second\"}]}\n```"
            let (clean, parsedAsk) = VeraAsk.parse(demo)
            guard let parsedAsk, parsedAsk.options.count == 2, !clean.contains("vera:ask"), clean == "Sure, happy to help." else {
                print("SELFTEST ERROR: vera:ask parse"); exit(1)
            }
            print("  vera:ask parse OK — \(parsedAsk.options.count) options, clean=\(clean.debugDescription)")

            // vera-artifact block parses out of an assistant reply (pure, local).
            let demoArt = "Here's the page.\n:::vera-artifact id=\"lp\" title=\"Landing\" type=\"html\"\n<h1>Hi</h1>\n<p>x</p>\n:::\nLet me know."
            let (artClean, arts) = Artifact.parse(demoArt)
            guard arts.count == 1, arts[0].type == .html, arts[0].id == "lp",
                  arts[0].content.contains("<h1>Hi</h1>"), !artClean.contains("vera-artifact") else {
                print("SELFTEST ERROR: vera-artifact parse"); exit(1)
            }
            print("  vera-artifact parse OK — \(arts[0].type.rawValue) '\(arts[0].title)', clean=\(artClean.debugDescription)")

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
            print("  pulse markers OK — \(pm.sources.count) sources, \(pm.inlineImages.count) inline, \(paras.count) paras")

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
            print("  presentation blocks OK — \(proseN) prose, \(chartN) chart, \(statN) stats")

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
            print("  wyoming framing OK — type=audio-chunk, data={rate,width,channels}, payload 320B intact")

            // Scheduler plumbing: cron summaries + tolerant GET /scheduler/jobs decode (pure, local).
            let cronCases = [
                ("0 5 * * *", "Daily 5:00 AM"), ("*/20 * * * *", "Every 20 min"),
                ("0 */6 * * *", "Every 6 hours"), ("0 6,18 * * *", "Daily 6:00 AM & 6:00 PM"),
                ("0 9 * * 0", "Sundays 9:00 AM"), ("oddball", "oddball"),
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
            print("  scheduler OK — \(cronCases.count) cron summaries, jobs decode (incl. env-locked)")

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
            print("  config round-trip OK — strings + unknown keys preserved")

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
            print("  tool log OK — 4 appended, newest-first load, tail cap honored")

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
            print("  update semver OK — \(semverCases.count) compare cases, minor extraction")

            // Resource bundle must resolve in THIS layout (packaged .app or .build binary) —
            // the generated Bundle.module accessor varies by toolchain and has shipped builds
            // that only resolve on the machine that built them.
            guard VeraResources.bundle != nil, Brand.flame != nil,
                  VeraResources.url("mermaid.min", ext: "js") != nil else {
                print("SELFTEST ERROR: resource bundle unresolved (flame/mermaid missing)"); exit(1)
            }
            print("  resources OK — bundle resolved, flame + mermaid present")

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
            print("  chat history OK — graph walk follows currentId, flat fallback intact")

            // Reasoning details blocks are stripped at render; tool_calls handling unchanged.
            let reasoned = "<details type=\"reasoning\" done=\"true\"><summary>Thought</summary>thinking…</details>\nThe actual answer."
            let (cleanR, callsR) = ToolCallParser.parse(reasoned)
            guard cleanR == "The actual answer.", callsR.isEmpty else {
                print("SELFTEST ERROR: reasoning block strip"); exit(1)
            }
            print("  reasoning strip OK — details removed, reply intact")

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
            print("  source mapping OK — \(mapped.count) chips, unresolvable dropped")
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
