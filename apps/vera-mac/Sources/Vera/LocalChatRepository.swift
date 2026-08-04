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
    func recentMessages(conversationID: String, limit: Int) throws -> [Message]
    func saveConversation(_ conversation: Conversation) throws
    func saveMessage(_ message: Message, conversationID: String, ordinal: Int) throws
    func deleteConversation(_ id: String) throws
    func conversation(originType: String, originID: String) throws -> Conversation?
    func createOriginConversation(_ conversation: Conversation, seed: Message) throws
    func referencedAttachmentFileNames() throws -> Set<String>
}

protocol NativeMemoryRepository: Sendable {
    func allMemories() throws -> [NativeMemoryRecord]
    func approvedMemories() throws -> [NativeMemoryRecord]
    func pendingProposals() throws -> [NativeMemoryProposal]
    func saveMemory(_ memory: NativeMemoryRecord, decision: NativeMemoryDecision, note: String) throws
    func saveProposal(_ proposal: NativeMemoryProposal) throws
    func decideProposal(_ id: String, status: NativeMemoryProposalStatus, note: String) throws
    func deleteMemory(_ id: String, note: String) throws
    func memoryChanges(limit: Int) throws -> [NativeMemoryChange]
}

extension NativeMemoryRepository {
    func decideProposal(_ id: String, status: NativeMemoryProposalStatus) throws {
        try decideProposal(
            id, status: status,
            note: status == .accepted ? "Approved memory proposal" : "Dismissed memory proposal")
    }

    func deleteMemory(_ id: String) throws {
        try deleteMemory(id, note: "Deleted by the user")
    }
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
        migrator.registerMigration("pulseContinuationV5") { db in
            try db.alter(table: "conversations") { table in
                table.add(column: "origin_type", .text)
                table.add(column: "origin_id", .text)
            }
            try db.execute(sql: """
                CREATE UNIQUE INDEX conversations_origin
                ON conversations(origin_type, origin_id)
                WHERE origin_type IS NOT NULL AND origin_id IS NOT NULL
                """)
            try db.alter(table: "messages") { table in
                table.add(column: "content_type", .text).notNull().defaults(to: "text")
                table.add(column: "metadata_json", .text)
            }
        }
        migrator.registerMigration("nativeAttachmentsV6") { db in
            try db.alter(table: "messages") { table in
                table.add(column: "attachments_json", .text)
            }
        }
        migrator.registerMigration("attachmentRoutingV7") { db in
            try db.alter(table: "messages") { table in
                table.add(column: "route_json", .text)
            }
        }
        migrator.registerMigration("messageSourcesV8") { db in
            try db.alter(table: "messages") { table in
                table.add(column: "sources_json", .text)
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
                SELECT id, title, created_at, updated_at, pinned, memory_excluded, origin_type, origin_id
                FROM conversations
                ORDER BY pinned DESC, updated_at DESC
                """).map(Self.decodeConversation)
        }
    }

    func conversation(originType: String, originID: String) throws -> Conversation? {
        try database.read { db in
            try Row.fetchOne(db, sql: """
                SELECT id, title, created_at, updated_at, pinned, memory_excluded, origin_type, origin_id
                FROM conversations
                WHERE origin_type = ? AND origin_id = ?
                """, arguments: [originType, originID]).map(Self.decodeConversation)
        }
    }

    func createOriginConversation(_ conversation: Conversation, seed: Message) throws {
        let metadata = Self.metadataJSON(for: seed)
        try database.write { db in
            try db.execute(sql: """
                INSERT INTO conversations
                    (id, title, created_at, updated_at, pinned, memory_excluded, origin_type, origin_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, arguments: [
                    conversation.id,
                    conversation.title,
                    conversation.createdAt.timeIntervalSince1970,
                    conversation.updatedAt.timeIntervalSince1970,
                    conversation.pinned,
                    conversation.memoryExcluded,
                    conversation.originType,
                    conversation.originID,
                ])
            try db.execute(sql: """
                INSERT INTO messages
                    (id, conversation_id, ordinal, role, content, created_at, state,
                     failure, model_id, tool_activity_json, content_type, metadata_json,
                     attachments_json, route_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, arguments: [
                    seed.id.uuidString,
                    conversation.id,
                    0,
                    seed.role.rawValue,
                    seed.text,
                    seed.createdAt.timeIntervalSince1970,
                    seed.state.rawValue,
                    seed.failure,
                    seed.modelID,
                    nil,
                    seed.contentType.rawValue,
                    metadata,
                    Self.attachmentsJSON(for: seed),
                    Self.routeJSON(for: seed),
                ])
        }
    }

    func messages(conversationID: String) throws -> [Message] {
        try database.read { db in
            let rows = try Row.fetchAll(db, sql: """
                SELECT id, role, content, created_at, state, failure, model_id,
                       tool_activity_json, content_type, metadata_json, attachments_json,
                       route_json, sources_json
                FROM messages
                WHERE conversation_id = ?
                ORDER BY ordinal ASC
                """, arguments: [conversationID])
            return Self.decodeMessages(rows)
        }
    }

    func recentMessages(conversationID: String, limit: Int) throws -> [Message] {
        guard limit > 0 else { return [] }
        return try database.read { db in
            let rows = try Row.fetchAll(db, sql: """
                SELECT id, role, content, created_at, state, failure, model_id,
                       tool_activity_json, content_type, metadata_json, attachments_json,
                       route_json, sources_json
                FROM (
                    SELECT id, role, content, created_at, state, failure, model_id,
                           tool_activity_json, content_type, metadata_json, attachments_json,
                           route_json, sources_json, ordinal
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY ordinal DESC
                    LIMIT ?
                )
                ORDER BY ordinal ASC
                """, arguments: [conversationID, limit])
            return Self.decodeMessages(rows)
        }
    }

    func saveConversation(_ conversation: Conversation) throws {
        try database.write { db in
            try db.execute(sql: """
                INSERT INTO conversations
                    (id, title, created_at, updated_at, pinned, memory_excluded, origin_type, origin_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    conversation.originType,
                    conversation.originID,
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
        let metadata = Self.metadataJSON(for: message)
        try database.write { db in
            try db.execute(sql: """
                INSERT INTO messages
                    (id, conversation_id, ordinal, role, content, created_at, state,
                     failure, model_id, tool_activity_json, content_type, metadata_json,
                     attachments_json, route_json, sources_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content = excluded.content,
                    state = excluded.state,
                    failure = excluded.failure,
                    model_id = excluded.model_id,
                    tool_activity_json = excluded.tool_activity_json,
                    content_type = excluded.content_type,
                    metadata_json = excluded.metadata_json,
                    attachments_json = excluded.attachments_json,
                    route_json = excluded.route_json,
                    sources_json = excluded.sources_json
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
                    message.contentType.rawValue,
                    metadata,
                    Self.attachmentsJSON(for: message),
                    Self.routeJSON(for: message),
                    Self.sourcesJSON(for: message),
                ])
        }
    }

    func deleteConversation(_ id: String) throws {
        try database.write { db in
            try db.execute(sql: "DELETE FROM conversations WHERE id = ?", arguments: [id])
        }
    }

    func referencedAttachmentFileNames() throws -> Set<String> {
        try database.read { db in
            let rows = try Row.fetchAll(
                db, sql: "SELECT attachments_json FROM messages WHERE attachments_json IS NOT NULL")
            var names: Set<String> = []
            for row in rows {
                let attachments = Self.decode(
                    row["attachments_json"] as String?, as: [MessageAttachment].self) ?? []
                for attachment in attachments {
                    if let fileName = attachment.fileName { names.insert(fileName) }
                }
            }
            return names
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
        guard NativeMemorySafety.recordIsSafe(memory) else {
            throw NSError(domain: "NativeMemory", code: 3,
                          userInfo: [NSLocalizedDescriptionKey: "Memory cannot store credentials or secrets"])
        }
        try database.write { db in
            let exists = try Bool.fetchOne(
                db, sql: "SELECT EXISTS(SELECT 1 FROM native_memories WHERE id = ?)",
                arguments: [memory.id]) ?? false
            let approvedCount = try Int.fetchOne(
                db, sql: "SELECT COUNT(*) FROM native_memories WHERE status = 'approved'") ?? 0
            if memory.status == .approved, !exists, approvedCount >= NativeMemoryRecall.capacity {
                throw NSError(domain: "NativeMemory", code: 4,
                              userInfo: [NSLocalizedDescriptionKey: "Review the capacity proposal before adding memory"])
            }
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
        guard NativeMemorySafety.proposalIsSafe(proposal) else {
            throw NSError(domain: "NativeMemory", code: 5,
                          userInfo: [NSLocalizedDescriptionKey: "Memory proposals cannot store credentials or secrets"])
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

    func decideProposal(_ id: String, status: NativeMemoryProposalStatus, note: String) throws {
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
                    note: note, createdAt: Date()), db: db)
        }
    }

    func deleteMemory(_ id: String, note: String) throws {
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
                    decision: .deleted, note: note, createdAt: Date()), db: db)
        }
    }

    func memoryChanges(limit: Int) throws -> [NativeMemoryChange] {
        try database.read { db in
            try Row.fetchAll(db, sql: """
                SELECT change_json FROM native_memory_changes
                ORDER BY created_at DESC, id ASC
                LIMIT ?
                """, arguments: [limit])
                .compactMap { row in Self.decode(row["change_json"], as: NativeMemoryChange.self) }
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

    private static func decodeMessages(_ rows: [Row]) -> [Message] {
        rows.compactMap { row in
            guard let id = UUID(uuidString: row["id"]),
                  let role = Message.Role(rawValue: row["role"]),
                  let state = MessageState(rawValue: row["state"]) else { return nil }
            let activityData: Data? = (row["tool_activity_json"] as String?).flatMap { $0.data(using: .utf8) }
            let activities = activityData.flatMap {
                try? JSONDecoder().decode([NativeToolActivity].self, from: $0)
            } ?? []
            let contentType = MessageContentType(rawValue: (row["content_type"] as String?) ?? "text") ?? .text
            let attachments = decode(row["attachments_json"] as String?, as: [MessageAttachment].self) ?? []
            var message = Message(
                id: id, role: role, text: row["content"],
                createdAt: Date(timeIntervalSince1970: row["created_at"]),
                state: state, failure: row["failure"], modelID: row["model_id"],
                attachments: attachments, toolActivities: activities, contentType: contentType,
                routeNote: decode(row["route_json"] as String?, as: MessageRouteNote.self))
            if let sources = decode(row["sources_json"] as String?, as: [PulseSource].self),
               !sources.isEmpty {
                message.sources = sources
            }
            if contentType == .pulseCard,
               let raw: String = row["metadata_json"],
               let snapshot = PulseCardSnapshot.decode(raw) {
                let card = snapshot.card()
                message.pulse = card
                message.sources = card.sourceList
            }
            return message
        }
    }

    private static func metadataJSON(for message: Message) -> String? {
        guard message.contentType == .pulseCard, let card = message.pulse else { return nil }
        return PulseCardSnapshot(card: card, capturedAt: message.createdAt).encodedJSON()
    }

    private static func attachmentsJSON(for message: Message) -> String? {
        let persistable = message.attachments.filter { $0.fileName != nil }
        guard !persistable.isEmpty else { return nil }
        return try? encode(persistable)
    }

    private static func routeJSON(for message: Message) -> String? {
        guard let note = message.routeNote else { return nil }
        return try? encode(note)
    }

    private static func sourcesJSON(for message: Message) -> String? {
        guard message.contentType != .pulseCard, !message.sources.isEmpty else { return nil }
        return try? encode(message.sources)
    }

    private static func decodeConversation(_ row: Row) -> Conversation {
        Conversation(
            id: row["id"],
            title: row["title"],
            messages: [],
            createdAt: Date(timeIntervalSince1970: row["created_at"]),
            updatedAt: Date(timeIntervalSince1970: row["updated_at"]),
            isPersisted: true,
            pinned: row["pinned"],
            memoryExcluded: row["memory_excluded"],
            originType: row["origin_type"],
            originID: row["origin_id"])
    }

    private static func decode<T: Decodable>(_ raw: String?, as type: T.Type) -> T? {
        guard let raw, let data = raw.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }
}
