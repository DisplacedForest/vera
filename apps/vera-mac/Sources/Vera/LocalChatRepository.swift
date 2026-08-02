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

protocol NativeMemoryRepository: Sendable {
    func allMemories() throws -> [NativeMemoryRecord]
    func approvedMemories() throws -> [NativeMemoryRecord]
    func pendingProposals() throws -> [NativeMemoryProposal]
    func saveMemory(_ memory: NativeMemoryRecord, decision: NativeMemoryDecision, note: String) throws
    func saveProposal(_ proposal: NativeMemoryProposal) throws
    func decideProposal(_ id: String, status: NativeMemoryProposalStatus) throws
    func deleteMemory(_ id: String) throws
}

final class LocalChatRepository: ChatRepository, NativeMemoryRepository, @unchecked Sendable {
    let database: DatabaseQueue

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
        migrator.registerMigration("nativeMemoryV3") { db in
            try db.execute(sql: """
                CREATE TABLE native_memories (
                    id TEXT PRIMARY KEY NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    library_group TEXT NOT NULL,
                    category TEXT NOT NULL,
                    bank TEXT NOT NULL,
                    durability TEXT NOT NULL,
                    expiry DOUBLE,
                    status TEXT NOT NULL,
                    embedding_json TEXT,
                    embedding_state TEXT NOT NULL,
                    source_conversation_id TEXT,
                    source_message_id TEXT,
                    created_at DOUBLE NOT NULL,
                    updated_at DOUBLE NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX native_memories_recall ON native_memories(status, bank, expiry);
                CREATE TABLE native_memory_proposals (
                    id TEXT PRIMARY KEY NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target_ids_json TEXT NOT NULL,
                    source_conversation_id TEXT,
                    source_message_id TEXT,
                    created_at DOUBLE NOT NULL,
                    proposal_json TEXT NOT NULL
                );
                CREATE INDEX native_memory_proposals_status ON native_memory_proposals(status, created_at);
                CREATE TABLE native_memory_changes (
                    id TEXT PRIMARY KEY NOT NULL,
                    memory_id TEXT,
                    proposal_id TEXT,
                    decision TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at DOUBLE NOT NULL,
                    change_json TEXT NOT NULL
                );
                """)
        }
        migrator.registerMigration("nativeMemoryConversationExclusionV4") { db in
            try db.alter(table: "conversations") { table in
                table.add(column: "memory_excluded", .boolean).notNull().defaults(to: false)
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
                SELECT id, title, created_at, updated_at, pinned, memory_excluded
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
                        pinned: row["pinned"],
                        memoryExcluded: row["memory_excluded"])
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
                INSERT INTO conversations (id, title, created_at, updated_at, pinned, memory_excluded)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at,
                    pinned = excluded.pinned,
                    memory_excluded = excluded.memory_excluded
                """, arguments: [
                    conversation.id,
                    conversation.title,
                    conversation.createdAt.timeIntervalSince1970,
                    conversation.updatedAt.timeIntervalSince1970,
                    conversation.pinned,
                    conversation.memoryExcluded,
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

    func allMemories() throws -> [NativeMemoryRecord] {
        try database.read { db in
            try Row.fetchAll(db, sql: "SELECT record_json FROM native_memories ORDER BY updated_at DESC, id ASC")
                .compactMap { row in Self.decode(row["record_json"], as: NativeMemoryRecord.self) }
        }
    }

    func approvedMemories() throws -> [NativeMemoryRecord] {
        try database.read { db in
            try Row.fetchAll(db, sql: """
                SELECT record_json FROM native_memories
                WHERE status = 'approved'
                ORDER BY updated_at DESC, id ASC
                """).compactMap { row in Self.decode(row["record_json"], as: NativeMemoryRecord.self) }
        }
    }

    func pendingProposals() throws -> [NativeMemoryProposal] {
        try database.read { db in
            try Row.fetchAll(db, sql: """
                SELECT proposal_json FROM native_memory_proposals
                WHERE status = 'pending'
                ORDER BY created_at ASC, id ASC
                """).compactMap { row in Self.decode(row["proposal_json"], as: NativeMemoryProposal.self) }
        }
    }

    func saveMemory(
        _ memory: NativeMemoryRecord, decision: NativeMemoryDecision, note: String
    ) throws {
        guard memory.durability == .durable || memory.expiry != nil else {
            throw NSError(domain: "NativeMemory", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Episodic memory requires an absolute expiry date"])
        }
        try database.write { db in
            try Self.writeMemory(memory, db: db)
            try Self.writeChange(
                NativeMemoryChange(
                    id: UUID().uuidString, memoryID: memory.id, proposalID: nil,
                    decision: decision, note: note, createdAt: Date()), db: db)
        }
    }

    func saveProposal(_ proposal: NativeMemoryProposal) throws {
        guard proposal.durability == .durable || proposal.expiry != nil else {
            throw NSError(domain: "NativeMemory", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "Episodic proposal requires an absolute expiry date"])
        }
        let raw = try Self.encode(proposal)
        let targets = try Self.encode(proposal.targetIDs)
        try database.write { db in
            try db.execute(sql: """
                INSERT INTO native_memory_proposals
                    (id, kind, status, target_ids_json, source_conversation_id,
                     source_message_id, created_at, proposal_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    status = excluded.status,
                    target_ids_json = excluded.target_ids_json,
                    proposal_json = excluded.proposal_json
                """, arguments: [
                    proposal.id, proposal.kind.rawValue, proposal.status.rawValue, targets,
                    proposal.sourceConversationID, proposal.sourceMessageID,
                    proposal.createdAt.timeIntervalSince1970, raw,
                ])
        }
    }

    func decideProposal(_ id: String, status: NativeMemoryProposalStatus) throws {
        try database.write { db in
            guard let row = try Row.fetchOne(
                db, sql: "SELECT proposal_json FROM native_memory_proposals WHERE id = ?", arguments: [id]),
                  var proposal = Self.decode(row["proposal_json"], as: NativeMemoryProposal.self) else { return }
            proposal.status = status
            try db.execute(
                sql: "UPDATE native_memory_proposals SET status = ?, proposal_json = ? WHERE id = ?",
                arguments: [status.rawValue, try Self.encode(proposal), id])
            try Self.writeChange(
                NativeMemoryChange(
                    id: UUID().uuidString, memoryID: proposal.targetIDs.first, proposalID: id,
                    decision: status == .accepted ? .accepted : .dismissed,
                    note: status == .accepted ? "Approved memory proposal" : "Dismissed memory proposal",
                    createdAt: Date()), db: db)
        }
    }

    func deleteMemory(_ id: String) throws {
        try database.write { db in
            guard let row = try Row.fetchOne(
                db, sql: "SELECT record_json FROM native_memories WHERE id = ?", arguments: [id]),
                  var memory = Self.decode(row["record_json"], as: NativeMemoryRecord.self) else { return }
            memory.status = .deleted
            memory.embedding = nil
            memory.updatedAt = Date()
            try Self.writeMemory(memory, db: db)
            try Self.writeChange(
                NativeMemoryChange(
                    id: UUID().uuidString, memoryID: id, proposalID: nil,
                    decision: .deleted, note: "Deleted by the user", createdAt: Date()), db: db)
        }
    }

    private static func writeMemory(_ memory: NativeMemoryRecord, db: Database) throws {
        try db.execute(sql: """
            INSERT INTO native_memories
                (id, title, summary, details_json, library_group, category, bank,
                 durability, expiry, status, embedding_json, embedding_state,
                 source_conversation_id, source_message_id, created_at, updated_at, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                summary = excluded.summary,
                details_json = excluded.details_json,
                library_group = excluded.library_group,
                category = excluded.category,
                bank = excluded.bank,
                durability = excluded.durability,
                expiry = excluded.expiry,
                status = excluded.status,
                embedding_json = excluded.embedding_json,
                embedding_state = excluded.embedding_state,
                source_conversation_id = excluded.source_conversation_id,
                source_message_id = excluded.source_message_id,
                updated_at = excluded.updated_at,
                record_json = excluded.record_json
            """, arguments: [
                memory.id, memory.title, memory.summary, try encode(memory.details),
                memory.group.rawValue, memory.category.rawValue, memory.bank,
                memory.durability.rawValue, memory.expiry?.timeIntervalSince1970,
                memory.status.rawValue, try memory.embedding.map(encode),
                memory.embeddingState.rawValue, memory.sourceConversationID,
                memory.sourceMessageID, memory.createdAt.timeIntervalSince1970,
                memory.updatedAt.timeIntervalSince1970, try encode(memory),
            ])
    }

    private static func writeChange(_ change: NativeMemoryChange, db: Database) throws {
        try db.execute(sql: """
            INSERT INTO native_memory_changes
                (id, memory_id, proposal_id, decision, note, created_at, change_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, arguments: [
                change.id, change.memoryID, change.proposalID, change.decision.rawValue,
                change.note, change.createdAt.timeIntervalSince1970, try encode(change),
            ])
    }

    private static func encode<T: Encodable>(_ value: T) throws -> String {
        String(decoding: try JSONEncoder().encode(value), as: UTF8.self)
    }

    private static func decode<T: Decodable>(_ raw: String?, as type: T.Type) -> T? {
        guard let raw, let data = raw.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }
}
