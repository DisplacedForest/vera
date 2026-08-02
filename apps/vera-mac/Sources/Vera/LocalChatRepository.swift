import Foundation
import GRDB

enum MessageState: String, Sendable, Hashable {
    case streaming
    case complete
    case interrupted
}

protocol ChatRepository: Sendable {
    func listConversations() throws -> [Conversation]
    func messages(conversationID: String) throws -> [Message]
    func saveConversation(_ conversation: Conversation) throws
    func saveMessage(_ message: Message, conversationID: String, ordinal: Int) throws
    func deleteConversation(_ id: String) throws
}

final class LocalChatRepository: ChatRepository, @unchecked Sendable {
    private let database: DatabaseQueue

    static var defaultURL: URL {
        let directory: URL
        if let override = ProcessInfo.processInfo.environment["VERA_CONFIG_DIR"], !override.isEmpty {
            directory = URL(fileURLWithPath: override, isDirectory: true)
        } else {
            directory = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".vera", isDirectory: true)
        }
        return directory.appendingPathComponent("vera.sqlite")
    }

    convenience init() throws {
        try self.init(url: Self.defaultURL)
    }

    init(url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var configuration = Configuration()
        configuration.prepareDatabase { db in try db.execute(sql: "PRAGMA foreign_keys = ON") }
        database = try DatabaseQueue(path: url.path, configuration: configuration)
        try migrate()
        try recoverInterruptedMessages()
    }

    init(inMemory: Bool) throws {
        database = try DatabaseQueue()
        try migrate()
        try recoverInterruptedMessages()
    }

    private func migrate() throws {
        var migrator = DatabaseMigrator()
        migrator.registerMigration("nativeChatV1") { db in
            try db.execute(sql: """
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY NOT NULL,
                    title TEXT NOT NULL,
                    created_at DOUBLE NOT NULL,
                    updated_at DOUBLE NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY NOT NULL,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at DOUBLE NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('streaming', 'complete', 'interrupted')),
                    failure TEXT,
                    model_id TEXT,
                    UNIQUE(conversation_id, ordinal)
                );
                CREATE INDEX messages_conversation_order ON messages(conversation_id, ordinal);
                """)
        }
        migrator.registerMigration("nativeChatToolActivityV2") { db in
            try db.alter(table: "messages") { table in
                table.add(column: "tool_activity_json", .text)
            }
        }
        try migrator.migrate(database)
    }

    private func recoverInterruptedMessages() throws {
        try database.write { db in
            let rows = try Row.fetchAll(db, sql: """
                SELECT id, tool_activity_json
                FROM messages
                WHERE state = 'streaming' AND tool_activity_json IS NOT NULL
                """)
            for row in rows {
                guard let id: String = row["id"],
                      let raw: String = row["tool_activity_json"],
                      let data = raw.data(using: .utf8),
                      var activities = try? JSONDecoder().decode([NativeToolActivity].self, from: data) else { continue }
                for index in activities.indices where activities[index].state == .pending {
                    activities[index].state = .failed
                    activities[index].result = "{\"error\":\"The tool call was interrupted when Vera closed\"}"
                }
                let recovered = String(decoding: try JSONEncoder().encode(activities), as: UTF8.self)
                try db.execute(
                    sql: "UPDATE messages SET tool_activity_json = ? WHERE id = ?",
                    arguments: [recovered, id])
            }
            try db.execute(sql: """
                UPDATE messages
                SET state = 'interrupted',
                    failure = COALESCE(failure, 'The response was interrupted when Vera closed')
                WHERE state = 'streaming'
                """)
        }
    }

    func listConversations() throws -> [Conversation] {
        try database.read { db in
            try Row.fetchAll(db, sql: """
                SELECT id, title, created_at, updated_at, pinned
                FROM conversations
                ORDER BY pinned DESC, updated_at DESC
                """).map { row in
                    Conversation(
                        id: row["id"],
                        title: row["title"],
                        messages: [],
                        createdAt: Date(timeIntervalSince1970: row["created_at"]),
                        updatedAt: Date(timeIntervalSince1970: row["updated_at"]),
                        isPersisted: true,
                        pinned: row["pinned"])
                }
        }
    }

    func messages(conversationID: String) throws -> [Message] {
        try database.read { db in
            try Row.fetchAll(db, sql: """
                SELECT id, role, content, created_at, state, failure, model_id, tool_activity_json
                FROM messages
                WHERE conversation_id = ?
                ORDER BY ordinal ASC
                """, arguments: [conversationID]).compactMap { row in
                    guard let id = UUID(uuidString: row["id"]),
                          let role = Message.Role(rawValue: row["role"]),
                          let state = MessageState(rawValue: row["state"]) else { return nil }
                    let activityData: Data? = (row["tool_activity_json"] as String?).flatMap { $0.data(using: .utf8) }
                    let activities = activityData.flatMap { try? JSONDecoder().decode([NativeToolActivity].self, from: $0) } ?? []
                    return Message(
                        id: id,
                        role: role,
                        text: row["content"],
                        createdAt: Date(timeIntervalSince1970: row["created_at"]),
                        state: state,
                        failure: row["failure"],
                        modelID: row["model_id"],
                        toolActivities: activities)
                }
        }
    }

    func saveConversation(_ conversation: Conversation) throws {
        try database.write { db in
            try db.execute(sql: """
                INSERT INTO conversations (id, title, created_at, updated_at, pinned)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at,
                    pinned = excluded.pinned
                """, arguments: [
                    conversation.id,
                    conversation.title,
                    conversation.createdAt.timeIntervalSince1970,
                    conversation.updatedAt.timeIntervalSince1970,
                    conversation.pinned,
                ])
        }
    }

    func saveMessage(_ message: Message, conversationID: String, ordinal: Int) throws {
        let activityJSON: String?
        if message.toolActivities.isEmpty {
            activityJSON = nil
        } else {
            activityJSON = String(decoding: try JSONEncoder().encode(message.toolActivities), as: UTF8.self)
        }
        try database.write { db in
            try db.execute(sql: """
                INSERT INTO messages
                    (id, conversation_id, ordinal, role, content, created_at, state, failure, model_id, tool_activity_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content = excluded.content,
                    state = excluded.state,
                    failure = excluded.failure,
                    model_id = excluded.model_id,
                    tool_activity_json = excluded.tool_activity_json
                """, arguments: [
                    message.id.uuidString,
                    conversationID,
                    ordinal,
                    message.role.rawValue,
                    message.text,
                    message.createdAt.timeIntervalSince1970,
                    message.state.rawValue,
                    message.failure,
                    message.modelID,
                    activityJSON,
                ])
        }
    }

    func deleteConversation(_ id: String) throws {
        try database.write { db in
            try db.execute(sql: "DELETE FROM conversations WHERE id = ?", arguments: [id])
        }
    }
}
