import Foundation

struct PulseCardSnapshot: Codable, Equatable {
    struct Source: Codable, Equatable {
        let n: Int
        let title: String
        let url: String
    }

    struct InlineImage: Codable, Equatable {
        let n: Int
        let url: String
        let caption: String
        let sourceN: Int?
    }

    struct DigestItem: Codable, Equatable {
        let itemID: String
        let title: String
        let subtitle: String
        let mediaType: String?
        let tmdbID: Int?
        let state: String
        let poster: String?
        let link: String?
        let group: String?
    }

    struct GroomSnapshotRecord: Codable, Equatable {
        let kind: String
        let id: String
        let topic: String
        let content: String
        let tier: String
        let type: String
        let name: String
        let attrs: [String: String]
        let schemaFields: [String]
        let entityCount: Int
        let migrated: [String]
    }

    struct GroomOpRecord: Codable, Equatable {
        let index: Int
        let type: String
        let store: String
        let reason: String
        let before: [GroomSnapshotRecord]
        let after: GroomSnapshotRecord?
    }

    static let currentVersion = 1
    static let maximumEncodedBytes = 262_144

    let version: Int
    let cardID: String
    let title: String
    let preview: String
    let subtitle: String
    let imageURL: String?
    let tint: String?
    let sources: [Source]
    let inlineImages: [InlineImage]
    let body: String
    let kind: String
    let severity: String?
    let category: String?
    let provenance: String
    let capturedAt: Date
    let changeSet: [GroomOpRecord]
    let items: [DigestItem]

    init(card: PulseCard, capturedAt: Date) {
        version = Self.currentVersion
        cardID = card.id
        title = card.title
        preview = card.preview
        subtitle = card.subtitle
        imageURL = Self.webURL(card.imageURL)
        tint = card.tint
        sources = card.sourceList.compactMap { source in
            guard let url = Self.webURL(source.url) else { return nil }
            return Source(n: source.n, title: source.title, url: url)
        }
        inlineImages = card.inlineImages.compactMap { image in
            guard let url = Self.webURL(image.url) else { return nil }
            return InlineImage(n: image.n, url: url, caption: image.caption, sourceN: image.sourceN)
        }
        body = card.body
        kind = card.kind
        severity = card.severity
        category = card.category
        provenance = card.provenance
        self.capturedAt = capturedAt
        changeSet = card.changeSet.map { op in
            GroomOpRecord(
                index: op.index, type: op.type, store: op.store, reason: op.reason,
                before: op.before.map(Self.groomRecord), after: op.after.map(Self.groomRecord))
        }
        items = card.items.map { item in
            DigestItem(
                itemID: item.itemID, title: item.title, subtitle: item.subtitle,
                mediaType: item.mediaType, tmdbID: item.tmdbID, state: item.state,
                poster: Self.webURL(item.poster), link: Self.webURL(item.link), group: item.group)
        }
    }

    func card() -> PulseCard {
        let sourceList = sources.map { PulseSource(n: $0.n, title: $0.title, url: $0.url) }
        return PulseCard(
            id: cardID, title: title, preview: preview, subtitle: subtitle,
            imageURL: imageURL, tint: tint,
            sources: sourceList.map { $0.url }, sourceList: sourceList,
            inlineImages: inlineImages.map {
                PulseInlineImage(n: $0.n, url: $0.url, caption: $0.caption, sourceN: $0.sourceN)
            },
            body: body, status: nil, kind: kind, severity: severity, action: nil,
            provenance: provenance, read: true, category: category,
            changeSet: changeSet.map { op in
                GroomOp(index: op.index, type: op.type, store: op.store, reason: op.reason,
                        before: op.before.map(Self.groomSnapshot), after: op.after.map(Self.groomSnapshot))
            },
            items: items.map { item in
                PulseDigestItem(
                    itemID: item.itemID, title: item.title, subtitle: item.subtitle,
                    mediaType: item.mediaType, tmdbID: item.tmdbID, token: nil, state: item.state,
                    poster: item.poster, link: item.link, group: item.group)
            })
    }

    func encodedJSON() -> String? {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(self), data.count <= Self.maximumEncodedBytes else { return nil }
        return String(decoding: data, as: UTF8.self)
    }

    static func decode(_ json: String) -> PulseCardSnapshot? {
        guard let data = json.data(using: .utf8) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        guard let snapshot = try? decoder.decode(PulseCardSnapshot.self, from: data),
              snapshot.version <= currentVersion else { return nil }
        return snapshot
    }

    private static let credentialKeyPattern =
        "(?i)(password|passwd|secret|token|api[_-]?key|apikey|authorization|auth[_-]?header|bearer|credential|private[_-]?key|client[_-]?secret|access[_-]?key|session[_-]?id|cookie)"
    private static let credentialValuePattern =
        "(?i)(bearer\\s+\\S+|basic\\s+[A-Za-z0-9+/=]{8,}|eyJ[A-Za-z0-9_-]{10,})"

    private static func sanitizedAttrs(_ attrs: [String: String]) -> [String: String] {
        attrs.filter { key, value in
            key.range(of: credentialKeyPattern, options: .regularExpression) == nil
                && value.range(of: credentialValuePattern, options: .regularExpression) == nil
        }
    }

    private static func webURL(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty,
              let url = URL(string: raw),
              url.scheme == "http" || url.scheme == "https" else { return nil }
        return raw
    }

    private static func groomRecord(_ snapshot: GroomSnapshot) -> GroomSnapshotRecord {
        GroomSnapshotRecord(
            kind: snapshot.kind, id: snapshot.id, topic: snapshot.topic, content: snapshot.content,
            tier: snapshot.tier, type: snapshot.type, name: snapshot.name,
            attrs: sanitizedAttrs(snapshot.attrs),
            schemaFields: snapshot.schemaFields, entityCount: snapshot.entityCount,
            migrated: snapshot.migrated)
    }

    private static func groomSnapshot(_ record: GroomSnapshotRecord) -> GroomSnapshot {
        var snapshot = GroomSnapshot(kind: record.kind, id: record.id)
        snapshot.topic = record.topic
        snapshot.content = record.content
        snapshot.tier = record.tier
        snapshot.type = record.type
        snapshot.name = record.name
        snapshot.attrs = record.attrs
        snapshot.schemaFields = record.schemaFields
        snapshot.entityCount = record.entityCount
        snapshot.migrated = record.migrated
        return snapshot
    }
}

enum PulseSeed {
    static let originType = "pulse"

    static func text(for card: PulseCard) -> String {
        var lines: [String] = [card.title]
        let body = card.body.isEmpty ? card.preview : card.body
        if !body.isEmpty { lines.append(body) }
        if !card.items.isEmpty {
            lines.append(card.items.map { item in
                item.subtitle.isEmpty ? "- \(item.title)" : "- \(item.title): \(item.subtitle)"
            }.joined(separator: "\n"))
        }
        let sources = card.sourceList
            .filter { URL(string: $0.url)?.scheme == "http" || URL(string: $0.url)?.scheme == "https" }
        if !sources.isEmpty {
            lines.append("Sources:\n" + sources.map { "[\($0.n)] \($0.title): \($0.url)" }.joined(separator: "\n"))
        }
        return lines.joined(separator: "\n\n")
    }

    static func message(for card: PulseCard, at date: Date = Date()) -> Message {
        let snapshot = PulseCardSnapshot(card: card, capturedAt: date)
        let restored = snapshot.card()
        return Message(
            role: .assistant, text: text(for: restored), createdAt: date, state: .complete,
            pulse: restored, sources: restored.sourceList, contentType: .pulseCard)
    }
}
