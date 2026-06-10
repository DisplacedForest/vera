import AVFoundation

/// Ordered FIFO playback of WAV chunks (Kokoro, 24 kHz mono) through a player node on a shared
/// engine, so sentence N+1 can synthesize while sentence N plays. Phase 1 has no barge-in;
/// `stop()` flushes the queue for future use.
final class AudioQueue: @unchecked Sendable {
    /// Canonical playback format — Kokoro emits 24 kHz mono; the main mixer resamples to hardware.
    static let canonical = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                         sampleRate: 24_000, channels: 1, interleaved: false)!

    private let engine: AVAudioEngine
    private let player = AVAudioPlayerNode()
    private let lock = NSLock()
    private var outstanding = 0       // buffers scheduled but not yet played back
    private var turnComplete = false  // no more chunks coming for this turn

    /// Fired (on the main queue) when the queue empties after `markTurnComplete()`.
    var onDrained: (@Sendable () -> Void)?

    init(engine: AVAudioEngine) {
        self.engine = engine
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: Self.canonical)
    }

    /// Begin playback — call once after the engine is running.
    func start() { if !player.isPlaying { player.play() } }

    /// Decode a WAV blob and schedule it after any already-queued audio.
    func enqueue(wav: Data) {
        guard let buffer = Self.decode(wav) else { return }
        lock.lock(); outstanding += 1; turnComplete = false; lock.unlock()
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            self?.bufferFinished()
        }
    }

    /// Signal that the turn's chunks are all queued; fire `onDrained` once the queue empties.
    func markTurnComplete() {
        let drainedNow: Bool = { lock.lock(); defer { lock.unlock() }
            turnComplete = true
            return outstanding == 0
        }()
        if drainedNow { fireDrained() }
    }

    /// Stop and flush everything (session teardown; future barge-in).
    func stop() {
        player.stop()
        lock.lock(); outstanding = 0; turnComplete = false; lock.unlock()
    }

    private func bufferFinished() {
        let drained: Bool = { lock.lock(); defer { lock.unlock() }
            outstanding = max(0, outstanding - 1)
            return outstanding == 0 && turnComplete
        }()
        if drained { fireDrained() }
    }

    private func fireDrained() {
        let cb = onDrained
        DispatchQueue.main.async { cb?() }
    }

    /// WAV bytes → PCM buffer at the canonical format (converting if a clip ever differs).
    private static func decode(_ wav: Data) -> AVAudioPCMBuffer? {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("vera-tts-\(UUID().uuidString).wav")
        defer { try? FileManager.default.removeItem(at: tmp) }
        guard (try? wav.write(to: tmp)) != nil,
              let file = try? AVAudioFile(forReading: tmp) else { return nil }
        let inFormat = file.processingFormat
        let frames = AVAudioFrameCount(file.length)
        guard frames > 0,
              let inBuf = AVAudioPCMBuffer(pcmFormat: inFormat, frameCapacity: frames),
              (try? file.read(into: inBuf)) != nil else { return nil }

        if inFormat.sampleRate == canonical.sampleRate && inFormat.channelCount == canonical.channelCount {
            return inBuf
        }
        guard let conv = AVAudioConverter(from: inFormat, to: canonical) else { return inBuf }
        let ratio = canonical.sampleRate / inFormat.sampleRate
        let outCap = AVAudioFrameCount(Double(frames) * ratio) + 1024
        guard let outBuf = AVAudioPCMBuffer(pcmFormat: canonical, frameCapacity: outCap) else { return inBuf }
        var fed = false
        var err: NSError?
        conv.convert(to: outBuf, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true; status.pointee = .haveData; return inBuf
        }
        return err == nil ? outBuf : inBuf
    }
}
