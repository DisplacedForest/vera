import EventKit
import Foundation
import Network

/// In-app Apple Reminders bridge. Serves the same HTTP contract as the standalone
/// vera-reminders service (GET /health, /lists, /reminders; POST /reminders; PATCH
/// /reminders/{id}) so vera-api's proxy and the OWUI tool reach it unchanged. It runs
/// only while Vera.app is open, which is sufficient: reminders are touched only on an
/// explicit chat ask. Because the app carries NSRemindersFullAccessUsageDescription,
/// the first access shows the native macOS prompt.
final class RemindersBridge: @unchecked Sendable {
    static let shared = RemindersBridge()

    private let store = EKEventStore()
    private let queue = DispatchQueue(label: "vera.reminders.bridge")
    private var listener: NWListener?
    private(set) var port: UInt16 = 8132

    var isRunning: Bool { queue.sync { listener != nil } }

    // MARK: lifecycle

    func start(port: UInt16 = 8132) throws {
        try queue.sync {
            if listener != nil { return }
            let params = NWParameters.tcp
            params.allowLocalEndpointReuse = true
            let l = try NWListener(using: params, on: NWEndpoint.Port(rawValue: port)!)
            self.port = port
            l.newConnectionHandler = { [weak self] conn in self?.handle(conn) }
            l.start(queue: queue)
            listener = l
        }
        Task { await requestAccess() }
    }

    func stop() {
        queue.sync {
            listener?.cancel()
            listener = nil
        }
    }

    /// On app launch, start serving if vera-api has Apple Reminders enabled, and re-assert
    /// this Mac's LAN address (a DHCP change since last run self-heals). No-op otherwise.
    static func autostartIfEnabled(veraAPIBase: URL?) async {
        guard let base = veraAPIBase else { return }
        let client = IntegrationsClient(base: base)
        guard case .ok(let entries) = await client.fetch(),
              entries.first(where: { $0.id == "apple_reminders" })?.enabled == true else { return }
        try? shared.start()
        if let host = LANAddress.selfHost(for: base) {
            _ = await client.save(id: "apple_reminders",
                                  fields: ["url": "http://\(host):\(shared.port)"], enabled: true)
        }
    }

    // MARK: access

    @discardableResult
    func requestAccess() async -> Bool {
        if EKEventStore.authorizationStatus(for: .reminder) == .fullAccess { return true }
        return (try? await store.requestFullAccessToReminders()) ?? false
    }

    private var hasAccess: Bool { EKEventStore.authorizationStatus(for: .reminder) == .fullAccess }

    // MARK: EventKit

    private func calendars() -> [EKCalendar] { store.calendars(for: .reminder) }

    /// Best-guess shopping list for the OWUI tool's default-list valve — the first list
    /// whose name contains "shopping", else "" (the model then names the list explicitly).
    /// Empty until access is granted.
    func suggestedDefaultList() -> String {
        guard hasAccess else { return "" }
        return calendars().map(\.title).first { $0.lowercased().contains("shopping") } ?? ""
    }

    private func calendar(named name: String) -> EKCalendar? {
        let want = name.trimmingCharacters(in: .whitespaces).lowercased()
        return calendars().first { $0.title.trimmingCharacters(in: .whitespaces).lowercased() == want }
    }

    private func fetch(_ cals: [EKCalendar]) -> [EKReminder] {
        let pred = store.predicateForReminders(in: cals.isEmpty ? nil : cals)
        let sem = DispatchSemaphore(value: 0)
        var out: [EKReminder] = []
        store.fetchReminders(matching: pred) { items in
            out = items ?? []
            sem.signal()
        }
        sem.wait()
        return out
    }

    private func iso(_ c: DateComponents?) -> String? {
        guard let c, let y = c.year, let m = c.month, let d = c.day else { return nil }
        if let hh = c.hour {
            return String(format: "%04d-%02d-%02dT%02d:%02d", y, m, d, hh, c.minute ?? 0)
        }
        return String(format: "%04d-%02d-%02d", y, m, d)
    }

    private func components(_ s: String) -> DateComponents? {
        let f = ISO8601DateFormatter()
        f.formatOptions = s.count > 10 ? [.withInternetDateTime] : [.withFullDate]
        let cal = Calendar.current
        if s.count > 10, let dt = f.date(from: s) {
            return cal.dateComponents([.year, .month, .day, .hour, .minute], from: dt)
        }
        let parts = s.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return DateComponents(year: parts[0], month: parts[1], day: parts[2])
    }

    private func sentenceCased(_ t: String) -> String {
        let s = t.trimmingCharacters(in: .whitespaces)
        guard let first = s.first else { return s }
        return first.uppercased() + s.dropFirst()
    }

    private func normalize(_ r: EKReminder) -> [String: Any] {
        [
            "id": r.calendarItemIdentifier,
            "title": r.title ?? "",
            "notes": r.notes as Any? ?? NSNull(),
            "due": iso(r.dueDateComponents) as Any? ?? NSNull(),
            "completed": r.isCompleted,
            "list": r.calendar?.title as Any? ?? NSNull(),
        ]
    }

    // MARK: routing

    private struct Req {
        let method: String
        let path: String
        let query: [String: String]
        let body: [String: Any]
    }

    private func route(_ req: Req) -> (Int, [String: Any]) {
        if req.method == "GET" && req.path == "/health" {
            return (200, ["ok": true, "reminders_access": hasAccess])
        }
        guard hasAccess else { return (503, ["detail": "Reminders access not granted on this Mac"]) }

        switch (req.method, req.path) {
        case ("GET", "/lists"):
            return (200, ["ok": true, "lists": calendars().map { ["id": $0.calendarIdentifier, "name": $0.title] }])

        case ("GET", "/reminders"):
            let wantCompleted = (req.query["completed"] ?? "false").lowercased() == "true"
            let cals: [EKCalendar]
            if let name = req.query["list"] {
                guard let c = calendar(named: name) else {
                    return (404, ["detail": "no reminders list named '\(name)'"])
                }
                cals = [c]
            } else {
                cals = calendars()
            }
            let items = fetch(cals).filter { $0.isCompleted == wantCompleted }.map(normalize)
            return (200, ["ok": true, "reminders": items])

        case ("POST", "/reminders"):
            guard let list = req.body["list"] as? String, let title = req.body["title"] as? String else {
                return (422, ["detail": "list and title are required"])
            }
            guard let cal = calendar(named: list) else {
                return (404, ["detail": "no reminders list named '\(list)'"])
            }
            let r = EKReminder(eventStore: store)
            r.calendar = cal
            r.title = sentenceCased(title)
            if let notes = req.body["notes"] as? String { r.notes = notes }
            if let due = req.body["due"] as? String, let c = components(due) { r.dueDateComponents = c }
            do { try store.save(r, commit: true) } catch { return (500, ["detail": "\(error.localizedDescription)"]) }
            return (200, ["ok": true, "reminder": normalize(r)])

        default:
            // PATCH /reminders/{id}
            if req.method == "PATCH", req.path.hasPrefix("/reminders/") {
                let rid = String(req.path.dropFirst("/reminders/".count))
                guard let r = store.calendarItem(withIdentifier: rid) as? EKReminder else {
                    return (404, ["detail": "no reminder with id '\(rid)'"])
                }
                if let done = req.body["completed"] as? Bool { r.isCompleted = done }
                if let title = req.body["title"] as? String { r.title = title }
                if let notes = req.body["notes"] as? String { r.notes = notes }
                if let due = req.body["due"] as? String, let c = components(due) { r.dueDateComponents = c }
                do { try store.save(r, commit: true) } catch { return (500, ["detail": "\(error.localizedDescription)"]) }
                return (200, ["ok": true, "reminder": normalize(r)])
            }
            return (404, ["detail": "not found"])
        }
    }

    // MARK: HTTP plumbing

    private func handle(_ conn: NWConnection) {
        conn.start(queue: queue)
        receive(conn, buffer: Data())
    }

    private func receive(_ conn: NWConnection, buffer: Data) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self else { conn.cancel(); return }
            var buf = buffer
            if let data { buf.append(data) }
            if let req = self.parse(buf) {
                let (status, json) = self.route(req)
                self.respond(conn, status: status, json: json)
            } else if isComplete || error != nil {
                conn.cancel()
            } else {
                self.receive(conn, buffer: buf)
            }
        }
    }

    /// Parse a complete HTTP request from the buffer, or nil if more bytes are needed.
    private func parse(_ buf: Data) -> Req? {
        guard let headerEnd = buf.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let head = String(decoding: buf[..<headerEnd.lowerBound], as: UTF8.self)
        let lines = head.split(separator: "\r\n", omittingEmptySubsequences: false)
        guard let requestLine = lines.first else { return nil }
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2 else { return nil }
        let method = String(parts[0])
        let target = String(parts[1])

        var contentLength = 0
        for line in lines.dropFirst() where line.lowercased().hasPrefix("content-length:") {
            contentLength = Int(line.split(separator: ":")[1].trimmingCharacters(in: .whitespaces)) ?? 0
        }
        let bodyStart = headerEnd.upperBound
        let have = buf.distance(from: bodyStart, to: buf.endIndex)
        if have < contentLength { return nil }

        var path = target
        var query: [String: String] = [:]
        if let q = target.firstIndex(of: "?") {
            path = String(target[..<q])
            for pair in target[target.index(after: q)...].split(separator: "&") {
                let kv = pair.split(separator: "=", maxSplits: 1)
                let k = kv[0].removingPercentEncoding ?? String(kv[0])
                let v = kv.count > 1 ? (kv[1].removingPercentEncoding ?? String(kv[1])) : ""
                query[k] = v
            }
        }
        var body: [String: Any] = [:]
        if contentLength > 0 {
            let bodyData = buf[bodyStart..<buf.index(bodyStart, offsetBy: contentLength)]
            body = (try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any]) ?? [:]
        }
        return Req(method: method, path: path, query: query, body: body)
    }

    private func respond(_ conn: NWConnection, status: Int, json: [String: Any]) {
        let payload = (try? JSONSerialization.data(withJSONObject: json)) ?? Data("{}".utf8)
        let reason = status == 200 ? "OK" : "Error"
        var head = "HTTP/1.1 \(status) \(reason)\r\n"
        head += "Content-Type: application/json\r\n"
        head += "Content-Length: \(payload.count)\r\n"
        head += "Connection: close\r\n\r\n"
        var out = Data(head.utf8)
        out.append(payload)
        conn.send(content: out, completion: .contentProcessed { _ in conn.cancel() })
    }
}
