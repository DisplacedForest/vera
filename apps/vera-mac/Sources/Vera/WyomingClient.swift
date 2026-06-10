import Foundation
import Network

/// One parsed Wyoming event: a JSON header (`type` + optional `data` dict) with an optional
/// binary `payload`. The wire format is a newline-terminated JSON header line, optionally followed
/// by `data_length` bytes of UTF-8 JSON (merged into `data`) and `payload_length` bytes of binary.
struct WyomingEvent: @unchecked Sendable {   // `data` holds JSON primitives only; safe to ferry
    let type: String
    let data: [String: Any]?
    let payload: Data?
}

/// Minimal Wyoming protocol client over a single TCP connection (`Network` framework). Serializes
/// outbound events and parses the inbound stream into `WyomingEvent`s exposed as an `AsyncStream`.
/// Used for streaming ASR (:10300) and TTS (:10200) in vera-voice. ~Sendable: the receive loop runs
/// off-main on its own dispatch queue; callers hop to the main actor for UI state.
final class WyomingClient: @unchecked Sendable {
    private let conn: NWConnection
    private let queue = DispatchQueue(label: "vera.wyoming")
    private let lock = NSLock()
    private var inbound = Data()                 // partial-read buffer (lock-guarded)
    private var continuation: AsyncStream<WyomingEvent>.Continuation?
    private var connectResumed = false           // one-shot guard for connect() (lock-guarded)

    /// Inbound events, in order, until the connection closes or fails.
    let events: AsyncStream<WyomingEvent>

    init(host: String, port: UInt16) {
        self.conn = NWConnection(host: NWEndpoint.Host(host),
                                 port: NWEndpoint.Port(rawValue: port)!,
                                 using: .tcp)
        var cont: AsyncStream<WyomingEvent>.Continuation!
        self.events = AsyncStream { cont = $0 }
        self.continuation = cont
    }

    /// Open the connection and start the receive loop. Resumes once `.ready`.
    func connect() async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            conn.stateUpdateHandler = { [weak self] state in
                guard let self else { return }
                switch state {
                case .ready:
                    if self.claimConnect() { cont.resume() }
                    self.receiveLoop()
                case .failed(let err):
                    if self.claimConnect() { cont.resume(throwing: err) }
                    self.finish()
                case .cancelled:
                    self.finish()
                default:
                    break
                }
            }
            conn.start(queue: queue)
        }
    }

    /// Close the connection and end the event stream.
    func close() {
        conn.cancel()
        finish()
    }

    /// Returns true exactly once — the first caller owns resuming the connect() continuation.
    private func claimConnect() -> Bool {
        lock.lock(); defer { lock.unlock() }
        if connectResumed { return false }
        connectResumed = true
        return true
    }

    private func finish() {
        lock.lock(); let c = continuation; continuation = nil; lock.unlock()
        c?.finish()
    }

    // MARK: - Encoding

    /// Serialize one Wyoming event to wire bytes: header JSON line `\n`, then (optional) the `data`
    /// JSON blob, then (optional) the binary payload. Mirrors `wyoming/event.py::write_event`.
    static func encode(type: String, data: [String: Any]?, payload: Data?) -> Data {
        var header: [String: Any] = ["type": type, "version": "1.9.0"]
        var dataBytes: Data?
        if let data, !data.isEmpty,
           let d = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]) {
            dataBytes = d
            header["data_length"] = d.count
        }
        if let payload, !payload.isEmpty {
            header["payload_length"] = payload.count
        }
        var out = Data()
        if let line = try? JSONSerialization.data(withJSONObject: header, options: [.sortedKeys]) {
            out.append(line)
        }
        out.append(0x0A)   // newline
        if let dataBytes { out.append(dataBytes) }
        if let payload, !payload.isEmpty { out.append(payload) }
        return out
    }

    /// Send one Wyoming event.
    func send(type: String, data: [String: Any]? = nil, payload: Data? = nil) async throws {
        let bytes = Self.encode(type: type, data: data, payload: payload)
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            conn.send(content: bytes, completion: .contentProcessed { err in
                if let err { cont.resume(throwing: err) } else { cont.resume() }
            })
        }
    }

    // MARK: - Decoding

    private func receiveLoop() {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] chunk, _, isComplete, err in
            guard let self else { return }
            if let chunk, !chunk.isEmpty {
                self.lock.lock(); self.inbound.append(chunk); self.lock.unlock()
                self.drainEvents()
            }
            if isComplete || err != nil {
                self.finish()
                return
            }
            self.receiveLoop()
        }
    }

    /// Parse as many complete events as the buffer holds. An event needs: a full header line, then
    /// `data_length` + `payload_length` bytes; if any part is short we leave the buffer for the next
    /// receive. Mirrors `wyoming/event.py::read_event` (separate `data` blob, then payload).
    private func drainEvents() {
        while true {
            lock.lock()
            let buf = inbound
            lock.unlock()

            // Need a complete newline-terminated header line.
            guard let nl = buf.firstIndex(of: 0x0A) else { return }
            let headerData = buf[buf.startIndex..<nl]
            guard let header = try? JSONSerialization.jsonObject(with: headerData) as? [String: Any],
                  let type = header["type"] as? String else {
                // Malformed header line — drop it and continue.
                lock.lock(); inbound.removeSubrange(buf.startIndex...nl); lock.unlock()
                continue
            }

            let dataLen = (header["data_length"] as? Int) ?? 0
            let payloadLen = (header["payload_length"] as? Int) ?? 0
            let afterHeader = buf.index(after: nl)
            let needed = dataLen + payloadLen
            let available = buf.distance(from: afterHeader, to: buf.endIndex)
            if available < needed { return }   // wait for more bytes

            var cursor = afterHeader
            var data: [String: Any]? = header["data"] as? [String: Any]
            if dataLen > 0 {
                let end = buf.index(cursor, offsetBy: dataLen)
                let blob = buf[cursor..<end]
                if let parsed = try? JSONSerialization.jsonObject(with: blob) as? [String: Any] {
                    if var merged = data { for (k, v) in parsed { merged[k] = v }; data = merged }
                    else { data = parsed }
                }
                cursor = end
            }
            var payload: Data?
            if payloadLen > 0 {
                let end = buf.index(cursor, offsetBy: payloadLen)
                payload = Data(buf[cursor..<end])
                cursor = end
            }

            lock.lock()
            inbound.removeSubrange(buf.startIndex..<cursor)
            let c = continuation
            lock.unlock()
            c?.yield(WyomingEvent(type: type, data: data, payload: payload))
        }
    }
}
