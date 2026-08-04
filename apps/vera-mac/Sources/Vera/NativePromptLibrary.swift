import Foundation

enum PromptScope: String, Codable, CaseIterable, Identifiable, Sendable {
    case persona
    case user

    var id: String { rawValue }

    var label: String {
        switch self {
        case .persona: return "Persona"
        case .user: return "User context"
        }
    }

    var explanation: String {
        switch self {
        case .persona: return "Vera's durable identity, sent with every request after the app policy."
        case .user: return "Context about you, sent after the persona on every request."
        }
    }
}

struct PromptProfile: Codable, Equatable, Identifiable, Sendable {
    var id: String
    var name: String
    var scope: PromptScope
    var content: String
    var createdAt: Date
    var updatedAt: Date

    static func fresh(name: String, scope: PromptScope, content: String, at date: Date = Date()) -> PromptProfile {
        PromptProfile(id: UUID().uuidString, name: name, scope: scope, content: content,
                      createdAt: date, updatedAt: date)
    }
}

struct ReusablePrompt: Codable, Equatable, Identifiable, Sendable {
    var id: String
    var name: String
    var content: String
    var createdAt: Date
    var updatedAt: Date

    static func fresh(name: String, content: String, at date: Date = Date()) -> ReusablePrompt {
        ReusablePrompt(id: UUID().uuidString, name: name, content: content,
                       createdAt: date, updatedAt: date)
    }
}

struct PromptRevision: Codable, Equatable, Identifiable, Sendable {
    var id: String
    var entityID: String
    var content: String
    var createdAt: Date
}

protocol NativePromptLibraryRepository: Sendable {
    func promptProfiles() throws -> [PromptProfile]
    func promptProfile(_ id: String) throws -> PromptProfile?
    func savePromptProfile(_ profile: PromptProfile) throws
    func migratePromptProfile(_ profile: PromptProfile) throws
    func deletePromptProfile(_ id: String) throws
    func reusablePrompts() throws -> [ReusablePrompt]
    func saveReusablePrompt(_ prompt: ReusablePrompt) throws
    func deleteReusablePrompt(_ id: String) throws
    func promptRevisions(entityID: String) throws -> [PromptRevision]
}

enum PromptValidationError: Error, Equatable, LocalizedError {
    case emptyName
    case emptyContent
    case nameTooLong(limit: Int)
    case contentTooLong(limit: Int)
    case containsSecret
    case claimsPolicyScope

    var errorDescription: String? {
        switch self {
        case .emptyName: return "Give the prompt a name."
        case .emptyContent: return "The prompt text is empty."
        case .nameTooLong(let limit): return "The name is longer than \(limit) characters."
        case .contentTooLong(let limit): return "The prompt is longer than \(limit) characters."
        case .containsSecret: return "The prompt looks like it contains a credential or secret. Remove it before saving."
        case .claimsPolicyScope: return "The prompt claims the app policy scope. Policy text is owned by the app and cannot be user-authored."
        }
    }
}

enum NativePromptValidation {
    static let nameLimit = 120
    static let contentLimit = 12_000
    static let instructionsLimit = 2_000
    static let importByteLimit = 65_536

    private static let policyMarkers = [
        #"(?im)^\s*APP POLICY\s*$"#,
        #"(?i)\bscope\s*[:=]\s*policy\b"#,
        #"(?i)these rules take precedence over every later section"#,
    ]

    static func policyMarker(in value: String) -> Bool {
        policyMarkers.contains { value.range(of: $0, options: .regularExpression) != nil }
    }

    static func validate(name: String, content: String, contentLimit: Int = contentLimit) throws {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedContent = content.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmedName.isEmpty { throw PromptValidationError.emptyName }
        if trimmedName.count > nameLimit { throw PromptValidationError.nameTooLong(limit: nameLimit) }
        if trimmedContent.isEmpty { throw PromptValidationError.emptyContent }
        if trimmedContent.count > contentLimit { throw PromptValidationError.contentTooLong(limit: contentLimit) }
        if NativeMemorySafety.containsSecret(name) || NativeMemorySafety.containsSecret(content) {
            throw PromptValidationError.containsSecret
        }
        if policyMarker(in: name) || policyMarker(in: content) {
            throw PromptValidationError.claimsPolicyScope
        }
    }

    static func validateInstructions(_ content: String) throws {
        let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if trimmed.count > instructionsLimit {
            throw PromptValidationError.contentTooLong(limit: instructionsLimit)
        }
        if NativeMemorySafety.containsSecret(trimmed) { throw PromptValidationError.containsSecret }
        if policyMarker(in: trimmed) { throw PromptValidationError.claimsPolicyScope }
    }
}

enum PromptDocumentError: Error, Equatable, LocalizedError {
    case oversize(limit: Int)
    case missingFrontMatter
    case missingField(String)
    case unknownScope(String)
    case invalid(PromptValidationError)

    var errorDescription: String? {
        switch self {
        case .oversize(let limit): return "The file is larger than \(limit / 1_024) KB."
        case .missingFrontMatter: return "The file has no vera-prompt front matter header."
        case .missingField(let field): return "The front matter is missing the \(field) field."
        case .unknownScope(let scope): return "Unknown scope \"\(scope)\". Use persona, user, or reusable."
        case .invalid(let error): return error.errorDescription
        }
    }
}

enum PromptDocumentKind: String, Equatable, Sendable {
    case persona
    case user
    case reusable

    var scope: PromptScope? {
        switch self {
        case .persona: return .persona
        case .user: return .user
        case .reusable: return nil
        }
    }
}

struct PromptDocument: Equatable, Sendable {
    var name: String
    var kind: PromptDocumentKind
    var content: String

    var exported: String {
        """
        ---
        vera-prompt: 1
        name: \(name)
        scope: \(kind.rawValue)
        ---
        \(content)
        """
    }

    static func parse(_ raw: String) throws -> PromptDocument {
        if raw.utf8.count > NativePromptValidation.importByteLimit {
            throw PromptDocumentError.oversize(limit: NativePromptValidation.importByteLimit)
        }
        let lines = raw.components(separatedBy: "\n")
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---" else {
            throw PromptDocumentError.missingFrontMatter
        }
        var fields: [String: String] = [:]
        var bodyStart: Int?
        for index in 1..<lines.count {
            let line = lines[index].trimmingCharacters(in: .whitespaces)
            if line == "---" {
                bodyStart = index + 1
                break
            }
            guard let colon = line.firstIndex(of: ":") else { continue }
            let key = String(line[..<colon]).trimmingCharacters(in: .whitespaces).lowercased()
            let value = String(line[line.index(after: colon)...]).trimmingCharacters(in: .whitespaces)
            fields[key] = value
        }
        guard let bodyStart, fields["vera-prompt"] != nil else {
            throw PromptDocumentError.missingFrontMatter
        }
        guard let name = fields["name"], !name.isEmpty else {
            throw PromptDocumentError.missingField("name")
        }
        guard let rawScope = fields["scope"], !rawScope.isEmpty else {
            throw PromptDocumentError.missingField("scope")
        }
        guard let kind = PromptDocumentKind(rawValue: rawScope.lowercased()) else {
            throw PromptDocumentError.unknownScope(rawScope)
        }
        let content = lines[bodyStart...].joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            try NativePromptValidation.validate(name: name, content: content)
        } catch let error as PromptValidationError {
            throw PromptDocumentError.invalid(error)
        }
        return PromptDocument(name: name, kind: kind, content: content)
    }
}

enum PromptPreviewComposer {
    static func compose(
        profiles: [PromptProfile],
        activePersonaID: String?,
        selection: PromptLibrarySelection?,
        draft: String
    ) -> (persona: String, userScope: String) {
        var selectedProfileID: String?
        if case .profile(let id) = selection { selectedProfileID = id }
        let personas = profiles.filter { $0.scope == .persona }
        let persona: String
        if let selectedProfileID, personas.contains(where: { $0.id == selectedProfileID }) {
            persona = draft
        } else {
            persona = (personas.first { $0.id == activePersonaID } ?? personas.first)?.content ?? ""
        }
        let userScope = profiles.filter { $0.scope == .user }
            .map { $0.id == selectedProfileID ? draft : $0.content }
            .joined(separator: "\n\n")
        return (persona, userScope)
    }
}

enum NativePromptMigration {
    @discardableResult
    static func run(
        repository: any NativePromptLibraryRepository,
        settings: inout NativeChatSettings
    ) throws -> Bool {
        let personas = try repository.promptProfiles().filter { $0.scope == .persona }
        if let active = settings.activePersonaID, personas.contains(where: { $0.id == active }) {
            return false
        }
        if let first = personas.first {
            settings.activePersonaID = first.id
            return true
        }
        let legacy = settings.systemPrompt
        let profile = PromptProfile.fresh(
            name: "Vera",
            scope: .persona,
            content: legacy.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? NativeChatSettings.defaultSystemPrompt : legacy)
        try repository.migratePromptProfile(profile)
        settings.activePersonaID = profile.id
        return true
    }
}
