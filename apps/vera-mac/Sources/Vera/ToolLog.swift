import Foundation

/// Persistent tool-invocation history: one JSON line per status event, appended as it
/// arrives, surviving restarts (same Application Support home as the artifact library).
enum ToolLog {
    /// In-memory/history cap — enough to browse weeks of activity without bloating launch.
    static let loadLimit = 500

    static var defaultURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Vera", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("tool-log.jsonl")
    }

    /// Append one invocation as a single JSON line.
    static func append(_ inv: Invocation, to url: URL = defaultURL) {
        guard var line = try? JSONEncoder.toolLog.encode(inv) else { return }
        line.append(0x0A)
        if let handle = try? FileHandle(forWritingTo: url) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: line)
        } else {
            try? line.write(to: url)
        }
    }

    /// Read the persisted log, newest first, capped at `limit` (the tail of the file).
    static func load(limit: Int = loadLimit, from url: URL = defaultURL) -> [Invocation] {
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return [] }
        let lines = text.split(separator: "\n").suffix(limit)
        return lines.compactMap { try? JSONDecoder.toolLog.decode(Invocation.self, from: Data($0.utf8)) }
            .reversed()
    }
}

extension JSONEncoder {
    static let toolLog: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        return e
    }()
}

extension JSONDecoder {
    static let toolLog: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()
}
