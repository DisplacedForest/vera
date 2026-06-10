import Foundation
import SocketIO

/// One streamed event from OWUI's pipeline (delivered over Socket.IO, not raw SSE).
enum StreamEvent: Sendable {
    case status(String)     // tool/progress line (knowledge_search, memory extraction, …)
    case content(String)    // assistant text SO FAR — cumulative, replace don't append
    case done
}

/// Streams completions through OWUI's pipeline (memory + web_search + knowledge) instead of
/// hitting the model server directly. OWUI emits the stream over Socket.IO to the `user:{id}` room, so we
/// must (1) sign in for a JWT, (2) join that room, then (3) POST the completion and read `events`.
///
/// Thread model: socket.io-client-swift fires callbacks on its handle queue, so this is a plain
/// reference type guarded by a lock. The UI consumes the `AsyncThrowingStream` (which hops back to
/// the main actor in ChatStore), so no shared mutable UI state is touched off-main.
final class VeraSocket: @unchecked Sendable {
    private let config: OWUIConfig
    private let lock = NSLock()

    private var manager: SocketManager?
    private var socket: SocketIOClient?
    private var jwt: String?
    private var ready = false
    private var waiters: [CheckedContinuation<Void, Error>] = []
    private var conns: [String: AsyncThrowingStream<StreamEvent, Error>.Continuation] = [:]

    /// Broadcast of every `status` event (tool/progress labels) for the MCP activity feed.
    let statusStream: AsyncStream<String>
    private let statusCont: AsyncStream<String>.Continuation

    init(config: OWUIConfig) {
        self.config = config
        var c: AsyncStream<String>.Continuation!
        self.statusStream = AsyncStream(bufferingPolicy: .bufferingNewest(40)) { c = $0 }
        self.statusCont = c
    }

    var sessionID: String { manager?.engine?.sid ?? "" }

    /// Ensure the socket is connected/joined, then hand back the signed-in JWT (for admin REST).
    func currentToken() async throws -> String {
        try await ensureConnected()
        return token()
    }

    // Synchronous lock helpers — NSLock.lock/unlock are unavailable from async contexts,
    // so all guarded access goes through these non-async methods.
    private func withLock<T>(_ body: () -> T) -> T {
        lock.lock(); defer { lock.unlock() }
        return body()
    }
    private func isReady() -> Bool { withLock { ready } }
    private func setJWT(_ token: String) { withLock { jwt = token } }
    private func token() -> String { withLock { jwt ?? config.apiKey } }
    private func setSocket(_ m: SocketManager, _ s: SocketIOClient) { withLock { manager = m; socket = s } }
    private func setConn(_ id: String, _ c: AsyncThrowingStream<StreamEvent, Error>.Continuation) { withLock { conns[id] = c } }
    private func conn(_ id: String) -> AsyncThrowingStream<StreamEvent, Error>.Continuation? { withLock { conns[id] } }
    private func dropConn(_ id: String) { withLock { conns[id] = nil } }

    enum SocketError: Error, LocalizedError {
        case noCredentials, signinFailed(Int), connectTimeout
        var errorDescription: String? {
            switch self {
            case .noCredentials: return "Set owui_email + owui_password in ~/.vera/config.json"
            case .signinFailed(let c): return "OWUI sign-in failed (HTTP \(c))"
            case .connectTimeout: return "Timed out joining the OWUI socket"
            }
        }
    }

    // MARK: - Sign in (REST) → JWT

    private func signin() async throws -> String {
        guard let email = config.email, let password = config.password,
              !email.isEmpty, !password.isEmpty else { throw SocketError.noCredentials }
        var req = URLRequest(url: config.baseURL.appendingPathComponent("/api/v1/auths/signin"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["email": email, "password": password])
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = obj["token"] as? String else { throw SocketError.signinFailed(code) }
        return token
    }

    // MARK: - Connect + join the user room

    /// Idempotent: signs in once, connects the socket, and resolves when `user-join` is acked.
    func ensureConnected() async throws {
        if isReady() { return }

        let jwtToken = try await signin()
        setJWT(jwtToken)

        let mgr = SocketManager(socketURL: config.baseURL, config: [
            .path("/ws/socket.io"),
            .forceWebsockets(true),
            .version(.three),
            .reconnects(true),
            .log(false),
            .compress,
        ])
        let sock = mgr.defaultSocket
        setSocket(mgr, sock)

        sock.on("events") { [weak self] data, _ in
            self?.handleEvents(data)
        }
        sock.on(clientEvent: .connect) { [weak self] _, _ in
            guard let self else { return }
            // Join the per-user room; OWUI acks with {id, name} on success.
            self.socket?.emitWithAck("user-join", ["auth": ["token": jwtToken]]).timingOut(after: 10) { [weak self] _ in
                self?.markReady()
            }
        }

        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            withLock { waiters.append(cont) }
            sock.connect(withPayload: ["token": jwtToken])   // payload → server-side `auth` arg
            // Safety timeout so a wedged connect doesn't hang send() forever.
            DispatchQueue.global().asyncAfter(deadline: .now() + 12) { [weak self] in
                self?.failWaiters(SocketError.connectTimeout)
            }
        }
    }

    private func markReady() {
        let w: [CheckedContinuation<Void, Error>] = withLock {
            if ready { return [] }
            ready = true
            let pending = waiters; waiters = []
            return pending
        }
        w.forEach { $0.resume() }
    }

    private func failWaiters(_ error: Error) {
        let w: [CheckedContinuation<Void, Error>] = withLock {
            if ready { return [] }
            let pending = waiters; waiters = []
            return pending
        }
        w.forEach { $0.resume(throwing: error) }
    }

    // MARK: - Stream a reply through the pipeline

    /// Fire a completion and stream OWUI's pipeline output for it. `chatID` tags the request;
    /// `messageID` routes the inbound events back to this stream.
    func streamReply(chatID: String, messageID: String, messages: [[String: Any]], files: [[String: Any]]? = nil) -> AsyncThrowingStream<StreamEvent, Error> {
        // Serialize to Data (Sendable) before the Task — [[String:Any]] isn't Sendable.
        let msgData = (try? JSONSerialization.data(withJSONObject: messages)) ?? Data("[]".utf8)
        let fileData = files.flatMap { try? JSONSerialization.data(withJSONObject: $0) }
        return AsyncThrowingStream { continuation in
            Task {
                do {
                    try await ensureConnected()
                    setConn(messageID, continuation)
                    continuation.onTermination = { [weak self] _ in
                        self?.dropConn(messageID)
                    }
                    try await postCompletion(chatID: chatID, messageID: messageID, messagesJSON: msgData, filesJSON: fileData)
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }

    // OWUI does NOT auto-attach a model's toolIds/features to a raw completion — the
    // web client adds them, so the app must too, or Vera has no tools/code-interpreter in-app.
    private var _toolIDs: [String] = []
    private var _featureIDs: [String] = []
    private var _capsLoaded = false

    private func loadCapsIfNeeded() async {
        if withLock({ _capsLoaded }) { return }
        var tids: [String] = []
        var fids: [String] = []
        let base = config.baseURL.absoluteString.hasSuffix("/")
            ? String(config.baseURL.absoluteString.dropLast()) : config.baseURL.absoluteString
        if let url = URL(string: base + "/api/v1/models/model?id=\(config.model)") {
            var r = URLRequest(url: url)
            r.setValue("Bearer \(token())", forHTTPHeaderField: "Authorization")
            if let (data, _) = try? await URLSession.shared.data(for: r),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let meta = obj["meta"] as? [String: Any] {
                tids = meta["toolIds"] as? [String] ?? []
                fids = meta["defaultFeatureIds"] as? [String] ?? []
            }
        }
        withLock { _toolIDs = tids; _featureIDs = fids; _capsLoaded = true }
    }

    private func postCompletion(chatID: String, messageID: String, messagesJSON: Data, filesJSON: Data?) async throws {
        let bearer = token()
        await loadCapsIfNeeded()
        let (tids, fids) = withLock { (_toolIDs, _featureIDs) }
        var features: [String: Bool] = [:]
        for f in fids { features[f] = true }
        let messages = (try? JSONSerialization.jsonObject(with: messagesJSON)) ?? []
        var payload: [String: Any] = [
            "model": config.model,
            "stream": true,
            "messages": messages,
            "chat_id": chatID,
            "session_id": sessionID,
            "id": messageID,
        ]
        // Pure OpenAI unless server-specific template kwargs are configured.
        if let kwargs = config.chatTemplateKwargsObject() {
            payload["chat_template_kwargs"] = kwargs
        }
        if !tids.isEmpty { payload["tool_ids"] = tids }
        if !features.isEmpty { payload["features"] = features }
        if let filesJSON, let files = try? JSONSerialization.jsonObject(with: filesJSON) { payload["files"] = files }
        var req = URLRequest(url: config.baseURL.appendingPathComponent("/api/chat/completions"))
        req.httpMethod = "POST"
        req.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw NSError(domain: "OWUI", code: code, userInfo: [NSLocalizedDescriptionKey: "completion HTTP \(code): \(body.prefix(160))"])
        }
        // Response carries only {status, task_ids}; the actual stream arrives on the socket.
    }

    // MARK: - Event routing

    private func handleEvents(_ data: [Any]) {
        guard let payload = data.first as? [String: Any],
              let messageID = payload["message_id"] as? String,
              let inner = payload["data"] as? [String: Any],
              let type = inner["type"] as? String else { return }

        let body = inner["data"] as? [String: Any] ?? [:]

        // Activity feed: broadcast every status label (even hidden, even from other clients),
        // independent of whether this app has a local stream for the message.
        if type == "status", let label = (body["action"] as? String) ?? (body["description"] as? String) {
            statusCont.yield(label)
        }

        guard let cont = conn(messageID) else { return }

        switch type {
        case "chat:completion":
            if let content = body["content"] as? String, !content.isEmpty {
                cont.yield(.content(content))
            }
            if body["done"] as? Bool == true {
                if let final = body["content"] as? String, !final.isEmpty { cont.yield(.content(final)) }
                cont.yield(.done); cont.finish()
                dropConn(messageID)
            }
        case "status":
            if body["hidden"] as? Bool == true { return }
            if let desc = body["description"] as? String { cont.yield(.status(desc)) }
            else if let action = body["action"] as? String { cont.yield(.status(action)) }
        case "chat:active":
            if body["active"] as? Bool == false {
                cont.yield(.done); cont.finish()
                dropConn(messageID)
            }
        default:
            break
        }
    }
}
