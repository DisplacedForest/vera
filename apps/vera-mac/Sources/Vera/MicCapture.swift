import AVFoundation

/// Captures mic audio on a shared engine, downsamples to 16 kHz mono, runs a simple energy (RMS)
/// VAD, and emits a 16-bit PCM WAV for each detected utterance. Phase-1 endpointing — crude vs
/// Silero, enough to prove the round trip. The tap stays installed for the whole session; VAD is
/// gated by `active` so we don't accumulate while Vera is thinking/speaking (no barge-in yet).
final class MicCapture: @unchecked Sendable {
    // Tunables (by ear; dB). Endpointing is RELATIVE to an adaptive noise floor so it
    // self-calibrates to any mic/room — fixed dB gates broke when the room floor sat above
    // the silence threshold (turn never ended). Gates = floor + margin.
    var startMarginDB: Float = 6        // rise above the noise floor to declare speech
    var endMarginDB: Float = 3          // within this of the floor counts as silence (hysteresis)
    var startMs: Double = 200           // sustained voiced audio before a turn "starts"
    var endSilenceMs: Double = 500      // trailing silence that ends a turn (AVM-like)
    var minVoicedMs: Double = 350       // ignore blips shorter than this

    private let engine: AVAudioEngine
    private let target = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                       sampleRate: 16_000, channels: 1, interleaved: false)!
    private var converter: AVAudioConverter?
    private var tapInstalled = false
    private let lock = NSLock()

    // VAD state (lock-guarded).
    private var active = false
    private var inSpeech = false
    private var voicedMs: Double = 0
    private var silenceMs: Double = 0
    private var utteranceMs: Double = 0
    private var samples: [Float] = []
    private var preroll: [Float] = []                                  // rolling pre-speech audio
    private lazy var prerollMax = Int(target.sampleRate * 0.35)        // ~350 ms onset buffer
    private var noiseFloorDB: Float = -45   // adaptive floor: fast attack down, slow release up

    /// Called (off-main, @Sendable) with the captured utterance as a 16-bit PCM WAV.
    var onUtterance: (@Sendable (Data) -> Void)?
    /// Called (off-main, @Sendable) with (level 0...1, dBFS) for the orb + diagnostics.
    var onLevel: (@Sendable (Float, Float) -> Void)?
    /// Called (off-main, @Sendable) with (adaptive noiseFloorDB, inSpeech) for live VAD tuning.
    var onDebug: (@Sendable (Float, Bool) -> Void)?
    /// Called (off-main, @Sendable) during listening with int16 16 kHz mono PCM frames, for
    /// streaming STT. Fires for every active tap buffer regardless of VAD state so speech
    /// onset is streamed; end-of-turn is still decided locally by the energy VAD.
    var onFrame: (@Sendable (Data) -> Void)?

    private var frameCount = 0   // tap callbacks seen (single audio thread; no lock needed)

    init(engine: AVAudioEngine) { self.engine = engine }

    /// Install the tap once for the session, capturing the input's native hardware format.
    ///
    /// Phase 1 does NOT enable voice processing (AEC). On macOS, VPIO delivers *silent* input
    /// buffers when it can't pair the input device with the current output device (e.g. a USB
    /// display mic vs. a different speaker), which is exactly our setup. AEC is only needed to stop
    /// TTS playback from feeding back into the mic during barge-in — which Phase 1 doesn't have
    /// (the mic VAD is paused while Vera speaks). Re-introduce AEC with explicit device pairing in
    /// Phase 2 alongside barge-in.
    func install() {
        guard !tapInstalled else { return }
        let input = engine.inputNode
        let inFormat = input.inputFormat(forBus: 0)
        converter = AVAudioConverter(from: inFormat, to: target)
        NSLog("vera-voice-dbg: install inFmt=\(inFormat.sampleRate)Hz ch=\(inFormat.channelCount) converter=\(converter != nil ? "ok" : "NIL")")
        input.installTap(onBus: 0, bufferSize: 1024, format: inFormat) { [weak self] buf, _ in
            self?.process(buf)
        }
        tapInstalled = true
    }

    func remove() {
        guard tapInstalled else { return }
        engine.inputNode.removeTap(onBus: 0)
        tapInstalled = false
    }

    /// Turn VAD/endpointing on (listening) or off (thinking/speaking). Resets utterance state.
    func setActive(_ on: Bool) {
        lock.lock(); active = on; resetUtterance(); lock.unlock()
    }

    private func resetUtterance() {
        inSpeech = false; voicedMs = 0; silenceMs = 0; utteranceMs = 0
        samples.removeAll(keepingCapacity: true)
        preroll.removeAll(keepingCapacity: true)
    }

    private func process(_ inBuf: AVAudioPCMBuffer) {
        let isActive: Bool = { lock.lock(); defer { lock.unlock() }; return active }()
        guard isActive, let conv = converter else { return }

        let ratio = target.sampleRate / inBuf.format.sampleRate
        let cap = AVAudioFrameCount(Double(inBuf.frameLength) * ratio) + 256
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: cap) else { return }
        var fed = false
        var err: NSError?
        conv.convert(to: out, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true; status.pointee = .haveData; return inBuf
        }
        guard err == nil, out.frameLength > 0, let ch = out.floatChannelData?[0] else { return }
        let n = Int(out.frameLength)

        var sum: Float = 0
        for i in 0..<n { let s = ch[i]; sum += s * s }
        let rms = sqrt(sum / Float(n))
        let db: Float = rms > 0 ? 20 * log10(rms) : -120
        let frameMs = Double(n) / target.sampleRate * 1000
        let level = max(0, min(1, (db + 60) / 60))   // map −60…0 dBFS → 0…1

        let frame = Array(UnsafeBufferPointer(start: ch, count: n))
        // Adaptive noise floor: attack DOWN fast toward quiet, release UP slowly so sustained
        // speech doesn't drag it up. Gates are relative to the floor → self-calibrating.
        if db < noiseFloorDB {
            noiseFloorDB += (db - noiseFloorDB) * 0.25
        } else {
            noiseFloorDB += (db - noiseFloorDB) * 0.01
        }
        noiseFloorDB = max(-65, min(-25, noiseFloorDB))
        let voiced = db >= noiseFloorDB + startMarginDB
        let silent = db < noiseFloorDB + endMarginDB

        frameCount += 1
        if frameCount % 20 == 0 {
            NSLog("vera-voice-dbg: frame=\(frameCount) db=\(String(format: "%.1f", db)) lvl=\(String(format: "%.2f", level)) inSpeech=\(inSpeech)")
        }

        var finished: [Float]?
        var started = false
        var speechSnapshot = false
        lock.lock()
        if inSpeech {
            samples.append(contentsOf: frame)
            utteranceMs += frameMs
            if silent {
                silenceMs += frameMs
                if silenceMs >= endSilenceMs {
                    if (utteranceMs - silenceMs) >= minVoicedMs { finished = samples }
                    resetUtterance()
                }
            } else {
                silenceMs = 0
            }
        } else {
            // Keep a rolling pre-roll of recent audio (below the gate too) so the utterance
            // includes speech onset — fixes clipped first syllables.
            preroll.append(contentsOf: frame)
            if preroll.count > prerollMax { preroll.removeFirst(preroll.count - prerollMax) }
            if voiced {
                voicedMs += frameMs
                if voicedMs >= startMs {
                    inSpeech = true
                    silenceMs = 0
                    utteranceMs = voicedMs
                    samples = preroll                      // seed with the buffered onset
                    preroll.removeAll(keepingCapacity: true)
                    started = true
                }
            } else {
                voicedMs = 0
            }
        }
        speechSnapshot = inSpeech
        lock.unlock()

        if started { NSLog("vera-voice-dbg: SPEECH START (db=\(String(format: "%.1f", db)))") }
        if let f = finished { NSLog("vera-voice-dbg: UTTERANCE \(f.count) samples (\(String(format: "%.2f", Double(f.count)/16000))s)") }

        onLevel?(level, db)
        onDebug?(noiseFloorDB, speechSnapshot)
        // Stream the just-converted 16 kHz mono frame for streaming STT (active = listening).
        if let cb = onFrame { cb(MicCapture.pcm16(frame)) }
        if let f = finished { onUtterance?(MicCapture.wav16k(f)) }
    }

    /// Pack 16 kHz mono float samples into raw little-endian 16-bit PCM (no WAV header).
    static func pcm16(_ samples: [Float]) -> Data {
        var d = Data(capacity: samples.count * 2)
        for s in samples {
            var v = Int16(max(-1, min(1, s)) * 32767)
            withUnsafeBytes(of: &v) { d.append(contentsOf: $0) }
        }
        return d
    }

    /// Pack 16 kHz mono float samples into a little-endian 16-bit PCM WAV.
    static func wav16k(_ samples: [Float]) -> Data {
        let sampleRate: Int32 = 16_000
        let channels: Int16 = 1
        let bits: Int16 = 16
        let blockAlign = channels * (bits / 8)
        let byteRate = sampleRate * Int32(blockAlign)
        let dataBytes = Int32(samples.count * 2)

        var d = Data()
        func put<T>(_ v: T) { var x = v; withUnsafeBytes(of: &x) { d.append(contentsOf: $0) } }
        d.append("RIFF".data(using: .ascii)!); put(Int32(36) + dataBytes); d.append("WAVE".data(using: .ascii)!)
        d.append("fmt ".data(using: .ascii)!); put(Int32(16)); put(Int16(1)); put(channels)
        put(sampleRate); put(byteRate); put(blockAlign); put(bits)
        d.append("data".data(using: .ascii)!); put(dataBytes)
        for s in samples { put(Int16(max(-1, min(1, s)) * 32767)) }
        return d
    }
}
