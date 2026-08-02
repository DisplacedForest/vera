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
        try migrator.migrate(database)
    }

    private func recoverInterruptedMessages() throws {
        try database.write { db in
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
                SELECT id, role, content, created_at, state, failure, model_id
                FROM messages
                WHERE conversation_id = ?
                ORDER BY ordinal ASC
                """, arguments: [conversationID]).compactMap { row in
                    guard let id = UUID(uuidString: row["id"]),
                          let role = Message.Role(rawValue: row["role"]),
                          let state = MessageState(rawValue: row["state"]) else { return nil }
                    return Message(
                        id: id,
                        role: role,
                        text: row["content"],
                        createdAt: Date(timeIntervalSince1970: row["created_at"]),
                        state: state,
                        failure: row["failure"],
                        modelID: row["model_id"])
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
        try database.write { db in
            try db.execute(sql: """
                INSERT INTO messages
                    (id, conversation_id, ordinal, role, content, created_at, state, failure, model_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content = excluded.content,
                    state = excluded.state,
                    failure = excluded.failure,
                    model_id = excluded.model_id
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
                ])
        }
    }

    func deleteConversation(_ id: String) throws {
        try database.write { db in
            try db.execute(sql: "DELETE FROM conversations WHERE id = ?", arguments: [id])
        }
    }
}
