import SwiftUI
import AVFoundation
import AppKit

/// The in-app voice session — a `@MainActor` state machine that orchestrates
/// mic capture → energy-VAD endpointing → Whisper STT → Vera (the shared OWUI socket) →
/// sentence-chunked Kokoro TTS → ordered playback, logging each turn into the chat thread.
/// No barge-in yet: while Vera is thinking/speaking the mic VAD is paused.
@MainActor
final class VoiceSession: ObservableObject {
    enum State: Equatable { case idle, listening, transcribing, thinking, speaking }

    @Published private(set) var state: State = .idle
    @Published private(set) var level: Float = 0    // mic level 0...1 for the orb pulse
    @Published var lastError: String?
    @Published private(set) var statusLine: String?    // pipeline progress (e.g. "web search") while thinking
    @Published private(set) var debugDB: Float = -120   // live input level (dBFS) — diagnostics
    @Published private(set) var debugFrames = 0         // tap callbacks seen — diagnostics
    @Published private(set) var debugFloor: Float = -45 // adaptive noise floor (dBFS) — diagnostics
    @Published private(set) var debugSpeech = false     // VAD inSpeech state — diagnostics

    private let client: VoiceClient
    private let socket: VeraSocket?
    private unowned let store: ChatStore

    private let engine = AVAudioEngine()
    private let mic: MicCapture
    private let queue: AudioQueue

    private var streamTask: Task<Void, Never>?
    private var sttStream: TranscriptionStream?   // open while listening (streaming STT)
    private var warmed = false

    var isActive: Bool { state != .idle }

    init(client: VoiceClient, socket: VeraSocket?, store: ChatStore) {
        self.client = client
        self.socket = socket
        self.store = store
        self.mic = MicCapture(engine: engine)
        self.queue = AudioQueue(engine: engine)
        configureCallbacks()
    }

    private func configureCallbacks() {
        mic.onLevel = { [weak self] lvl, db in Task { @MainActor in self?.updateLevel(lvl, db) } }
        // Stream captured 16 kHz mono PCM frames into the open STT connection while listening.
        mic.onFrame = { [weak self] pcm in Task { @MainActor in self?.sttStream?.send(pcm) } }
        mic.onUtterance = { [weak self] _ in Task { @MainActor in self?.handleEndOfTurn() } }
        mic.onDebug = { [weak self] floor, sp in Task { @MainActor in self?.debugFloor = floor; self?.debugSpeech = sp } }
        queue.onDrained = { [weak self] in Task { @MainActor in self?.handleDrained() } }
    }

    private func updateLevel(_ lvl: Float, _ db: Float) {
        debugDB = db
        debugFrames += 1
        guard state == .listening else { return }
        level = lvl
    }

    // MARK: - Lifecycle

    func start() {
        guard state == .idle else { return }
        lastError = nil
        let device = AVCaptureDevice.default(for: .audio)?.localizedName ?? "NONE"
        NSLog("vera-voice-dbg: start() default audio input = \(device)")
        Task {
            guard await Self.requestMic() else { lastError = "Microphone access denied"; return }
            NSLog("vera-voice-dbg: mic permission granted")
            do {
                mic.install()
                try engine.start()
                queue.start()
                NSLog("vera-voice-dbg: engine started, isRunning=\(engine.isRunning)")
            } catch {
                NSLog("vera-voice-dbg: engine start FAILED: \(error.localizedDescription)")
                lastError = "Audio engine failed: \(error.localizedDescription)"
                return
            }
            await prewarm()
            beginListening()
        }
    }

    func stop() {
        streamTask?.cancel(); streamTask = nil
        sttStream = nil
        queue.stop()
        mic.setActive(false)
        mic.remove()
        engine.stop()
        level = 0
        state = .idle
    }

    private func beginListening() {
        state = .listening
        level = 0
        statusLine = nil
        // Open a fresh streaming-STT connection; mic frames flow into it while active.
        sttStream = nil
        Task {
            sttStream = try? await client.startTranscription()
        }
        mic.setActive(true)
    }

    /// Warm both models once at session open (in parallel) so the first real turn is fast: synth a
    /// throwaway phrase (Kokoro) and transcribe a short silent clip (Whisper). The heavy one-time
    /// model load also happens eagerly in the service at startup; this primes the per-session path.
    private func prewarm() async {
        guard !warmed else { return }
        warmed = true
        async let tts: Data? = try? client.synthesize("Ready.", voice: nil)
        // Warm the streaming STT path: open a connection, push a short silent chunk, finalise.
        async let stt: String? = {
            guard let s = try? await client.startTranscription() else { return nil }
            s.send(MicCapture.pcm16(Array(repeating: 0, count: 3200)))   // 0.2s @ 16kHz
            return try? await s.finish()
        }()
        _ = await (tts, stt)
    }

    // MARK: - Turn handling

    /// End-of-turn (energy-VAD): stop the mic, finalise the streaming STT connection to get the
    /// transcript, then run the EXISTING reply path verbatim.
    private func handleEndOfTurn() {
        guard state == .listening else { return }
        mic.setActive(false)
        state = .transcribing
        guard let stream = sttStream else { beginListening(); return }
        sttStream = nil
        Task {
            do {
                let text = try await stream.finish()
                guard !text.isEmpty else { beginListening(); return }
                guard let socket, let turn = store.beginVoiceTurn(userText: text) else {
                    lastError = "Voice needs OWUI configured (~/.vera/config.json)"
                    beginListening(); return
                }
                state = .thinking
                NSSound(named: "Tink")?.play()   // earcon: heard you, working on it
                streamReply(socket: socket, chatID: turn.chatID, messages: turn.messages)
            } catch {
                lastError = error.localizedDescription
                beginListening()
            }
        }
    }

    private func streamReply(socket: VeraSocket, chatID: String, messages: [[String: Any]]) {
        let messageID = UUID().uuidString
        var content = ""
        var spokenUpTo = 0   // character cursor into `content` already sent to TTS
        streamTask = Task {
            do {
                for try await event in socket.streamReply(chatID: chatID, messageID: messageID, messages: messages) {
                    if Task.isCancelled { return }
                    switch event {
                    case .sources:
                        break  // voice replies aren't rendered with chips
                    case .content(let raw):
                        content = raw
                        store.updateVoiceReply(chatID: chatID, text: content)
                        spokenUpTo = await flushSentences(content, from: spokenUpTo)
                    case .status(let s):
                        statusLine = s.replacingOccurrences(of: "_", with: " ")
                    case .done:
                        let chars = Array(content)
                        if spokenUpTo < chars.count {
                            let tail = String(chars[spokenUpTo...]).trimmingCharacters(in: .whitespacesAndNewlines)
                            if !tail.isEmpty { await speak(tail) }
                            spokenUpTo = chars.count
                        }
                        queue.markTurnComplete()
                    }
                }
            } catch {
                if !Task.isCancelled { lastError = error.localizedDescription }
            }
            // Empty/failed reply that never produced speech → resume listening directly.
            if state == .thinking { beginListening() }
        }
    }

    /// Flush speakable text beyond `from`. For the FIRST chunk of a turn (`from == 0`) we break at
    /// the first clause boundary (`,`/`;`/`:` as well as `.`/`!`/`?`) past a short minimum so audio
    /// starts fast; after that we batch on full sentence endings. Returns the new cursor.
    private func flushSentences(_ text: String, from: Int) async -> Int {
        let chars = Array(text)
        guard from < chars.count else { return from }
        let minLen = 12
        var lastBoundary = from

        if from == 0 {
            var i = from
            while i < chars.count {
                let c = chars[i]
                if c == "." || c == "!" || c == "?" || c == "," || c == ";" || c == ":" || c == "\n" {
                    let next = i + 1
                    if (next >= chars.count || chars[next] == " " || chars[next] == "\n"), (next - from) >= minLen {
                        lastBoundary = next; break   // first clause → speak immediately
                    }
                }
                i += 1
            }
        } else {
            var i = from
            while i < chars.count {
                let c = chars[i]
                if c == "." || c == "!" || c == "?" || c == "\n" {
                    let next = i + 1
                    if next >= chars.count || chars[next] == " " || chars[next] == "\n" { lastBoundary = next }
                }
                i += 1
            }
        }

        if lastBoundary > from {
            let sentence = String(chars[from..<lastBoundary]).trimmingCharacters(in: .whitespacesAndNewlines)
            if !sentence.isEmpty { await speak(sentence) }
            return lastBoundary
        }
        return from
    }

    private func speak(_ text: String) async {
        let spoken = VoiceSession.speechText(text)
        guard !spoken.isEmpty else { return }
        if state != .speaking { state = .speaking }
        if let wav = try? await client.synthesize(spoken, voice: nil) { queue.enqueue(wav: wav) }
    }

    /// Convert markdown to plain spoken text — Kokoro otherwise pronounces "**" as "asterisk",
    /// "#" as "hashtag", etc. Display keeps the rich markdown; only TTS gets the stripped form.
    static func speechText(_ md: String) -> String {
        var s = md.strippedMarkdown()
        // Remove any residual markers left by unpaired markdown across sentence-chunk boundaries.
        s = s.replacingOccurrences(of: "[*_`#>]", with: "", options: .regularExpression)
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func handleDrained() {
        guard state == .speaking else { return }
        beginListening()
    }

    /// Build a session pinned to a fixed visual state for headless screenshots
    /// (`--shot --view voice`). Not used by the running app.
    static func preview(state: State, level: Float, store: ChatStore) -> VoiceSession {
        let s = VoiceSession(client: VoiceClient(base: URL(string: "http://127.0.0.1:8131")!),
                             socket: nil, store: store)
        s.state = state
        s.level = level
        return s
    }

    // MARK: - Permission

    private static func requestMic() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .notDetermined:
            return await withCheckedContinuation { cont in
                AVCaptureDevice.requestAccess(for: .audio) { cont.resume(returning: $0) }
            }
        default: return false
        }
    }
}
