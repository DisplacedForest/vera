import Foundation

/// Client for the `vera-voice` streaming voice servers. Transport is **Wyoming**
/// over TCP — streaming ASR on `:10300` (Parakeet, incremental STT) and TTS on `:10200` (Kokoro).
/// STT streams int16 16 kHz mono PCM during the utterance and finalises on `audio-stop`; TTS sends
/// one `synthesize` and reassembles the returned `audio-chunk` payloads into a WAV the AudioQueue
/// can decode. Host comes from `~/.vera/config.json` (`voice_base`); the legacy HTTP service on
/// `:8131` is untouched.
struct VoiceClient: Sendable {
    let host: String
    let asrPort: UInt16
    let ttsPort: UInt16

    /// Build from the configured voice base URL — we keep only the host and switch to the Wyoming
    /// ports. No configured base means no host: voice features report unconfigured, never a
    /// baked-in address.
    init(base: URL?, asrPort: UInt16 = 10300, ttsPort: UInt16 = 10200) {
        self.host = base?.host ?? ""
        self.asrPort = asrPort
        self.ttsPort = ttsPort
    }

    init(host: String, asrPort: UInt16 = 10300, ttsPort: UInt16 = 10200) {
        self.host = host
        self.asrPort = asrPort
        self.ttsPort = ttsPort
    }

    /// Best-effort reachability check — opens an ASR connection and tears it down.
    func health() async -> Bool {
        guard !host.isEmpty else { return false }
        let c = WyomingClient(host: host, port: asrPort)
        defer { c.close() }
        return (try? await c.connect()) != nil
    }

    // MARK: - Streaming STT

    /// Open a Wyoming ASR connection and send `audio-start`, returning a handle that forwards audio
    /// chunks during the utterance and finalises (`audio-stop` → `transcript`) on `finish()`.
    func startTranscription() async throws -> TranscriptionStream {
        guard !host.isEmpty else { throw VoiceError.notConfigured }
        let client = WyomingClient(host: host, port: asrPort)
        try await client.connect()
        try await client.send(type: "audio-start",
                              data: ["rate": 16000, "width": 2, "channels": 1])
        return TranscriptionStream(client: client)
    }

    // MARK: - Streaming TTS

    /// Synthesize `text` via Wyoming TTS: send `synthesize`, collect `audio-chunk` payloads until
    /// `audio-stop`, and wrap the PCM in a WAV `Data` (the AudioQueue decodes WAV). Format comes from
    /// the server's `audio-start` (Kokoro is 24 kHz mono int16). Signature kept so VoiceSession is
    /// unchanged.
    func synthesize(_ text: String, voice: String? = nil) async throws -> Data {
        guard !host.isEmpty else { throw VoiceError.notConfigured }
        let client = WyomingClient(host: host, port: ttsPort)
        try await client.connect()
        defer { client.close() }

        var data: [String: Any] = ["text": text]
        if let voice { data["voice"] = ["name": voice] }
        try await client.send(type: "synthesize", data: data)

        var rate = 24000, width = 2, channels = 1
        var pcm = Data()
        for await ev in client.events {
            switch ev.type {
            case "audio-start":
                if let d = ev.data {
                    rate = (d["rate"] as? Int) ?? rate
                    width = (d["width"] as? Int) ?? width
                    channels = (d["channels"] as? Int) ?? channels
                }
            case "audio-chunk":
                if let p = ev.payload { pcm.append(p) }
            case "audio-stop":
                return Self.wav(pcm: pcm, rate: Int32(rate), channels: Int16(channels), bits: Int16(width * 8))
            default:
                break
            }
        }
        // Stream ended without audio-stop — return whatever we got.
        return Self.wav(pcm: pcm, rate: Int32(rate), channels: Int16(channels), bits: Int16(width * 8))
    }

    /// Wrap raw little-endian PCM bytes in a WAV container for AudioQueue's decoder.
    static func wav(pcm: Data, rate: Int32, channels: Int16, bits: Int16) -> Data {
        let blockAlign = channels * (bits / 8)
        let byteRate = rate * Int32(blockAlign)
        let dataBytes = Int32(pcm.count)
        var d = Data()
        func put<T>(_ v: T) { var x = v; withUnsafeBytes(of: &x) { d.append(contentsOf: $0) } }
        d.append("RIFF".data(using: .ascii)!); put(Int32(36) + dataBytes); d.append("WAVE".data(using: .ascii)!)
        d.append("fmt ".data(using: .ascii)!); put(Int32(16)); put(Int16(1)); put(channels)
        put(rate); put(byteRate); put(blockAlign); put(bits)
        d.append("data".data(using: .ascii)!); put(dataBytes)
        d.append(pcm)
        return d
    }
}

/// A live streaming-STT session: forward int16 16 kHz mono PCM frames while listening, then call
/// `finish()` on end-of-turn to send `audio-stop` and await the `transcript` text.
final class TranscriptionStream: @unchecked Sendable {
    private let client: WyomingClient
    // Serial tail so audio chunks (and the trailing audio-stop) ship in capture order even though
    // `send(_:)` is fire-and-forget from the audio thread.
    private let lock = NSLock()
    private var tail: Task<Void, Never> = Task {}
    // Coalesce small mic buffers into ≥ minChunkBytes per audio-chunk. The streaming parakeet-mlx
    // engine crashes on tiny incremental chunks (<~1600 samples); 2048 samples (128ms @16k) is a
    // safe, low-latency floor. Buffer is lock-guarded (filled from the audio thread).
    private static let minChunkBytes = 2048 * 2
    private var pending = Data()

    init(client: WyomingClient) { self.client = client }

    /// Buffer int16 16 kHz mono PCM; ship an `audio-chunk` once a safe minimum has accumulated.
    func send(_ pcm16k: Data) {
        guard !pcm16k.isEmpty else { return }
        lock.lock()
        pending.append(pcm16k)
        let chunk: Data? = pending.count >= Self.minChunkBytes ? pending : nil
        if chunk != nil { pending = Data() }
        lock.unlock()
        if let chunk { ship(chunk) }
    }

    /// Enqueue one `audio-chunk` send (ordered, non-blocking).
    /// Take and clear the buffered tail (sync, lock-guarded).
    private func drainPending() -> Data {
        lock.lock(); defer { lock.unlock() }
        let d = pending; pending = Data(); return d
    }

    /// Snapshot the current serial tail task (sync, lock-guarded).
    private func currentTail() -> Task<Void, Never> {
        lock.lock(); defer { lock.unlock() }; return tail
    }

    private func ship(_ chunk: Data) {
        enqueue { [client] in
            try? await client.send(type: "audio-chunk",
                                   data: ["rate": 16000, "width": 2, "channels": 1,
                                          "timestamp": 0],
                                   payload: chunk)
        }
    }

    /// Chain `op` after any pending sends so order is preserved.
    private func enqueue(_ op: @escaping @Sendable () async -> Void) {
        lock.lock()
        let prev = tail
        tail = Task { await prev.value; await op() }
        lock.unlock()
    }

    /// Send `audio-stop` (after all queued chunks flush) and await the server's `transcript`.
    /// Closes the connection.
    func finish() async throws -> String {
        defer { client.close() }
        // Flush any buffered tail as a final chunk, zero-padding up to the engine's safe minimum so
        // it doesn't crash on a short remainder (the VAD's trailing silence dominates this tail).
        var tailBuf = drainPending()
        if !tailBuf.isEmpty {
            if tailBuf.count < Self.minChunkBytes {
                tailBuf.append(Data(count: Self.minChunkBytes - tailBuf.count))
            }
            ship(tailBuf)
        }
        await currentTail().value   // ensure all audio chunks have shipped
        try await client.send(type: "audio-stop")
        for await ev in client.events where ev.type == "transcript" {
            let text = (ev.data?["text"] as? String) ?? ""
            return text.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return ""
    }
}

enum VoiceError: Error, LocalizedError {
    case http(String, Int)
    case mic(String)
    case notConfigured
    var errorDescription: String? {
        switch self {
        case .http(let what, let code): return "\(what) failed (HTTP \(code))"
        case .mic(let m): return m
        case .notConfigured: return "Voice service not configured — set its URL in Settings"
        }
    }
}
