import SwiftUI
import AppKit

struct PromptLibraryView: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var store: ChatStore

    var body: some View {
        if let repository = store.promptLibrary {
            PromptLibraryArea(
                repository: repository,
                activePersonaID: config.nativeSettings.activePersonaID,
                onSelectActive: { id in
                    var updated = config.nativeSettings
                    updated.activePersonaID = id
                    config.nativeSettings = updated
                    store.adoptPersona(id)
                    try? config.save()
                })
        } else {
            Label("Local storage is unavailable, so prompts cannot be edited.", systemImage: "exclamationmark.triangle")
                .font(.system(size: 12)).foregroundStyle(.orange)
        }
    }
}

enum PromptLibrarySelection: Equatable {
    case profile(String)
    case reusable(String)
}

struct PromptLibraryArea: View {
    let repository: any NativePromptLibraryRepository
    let activePersonaID: String?
    let onSelectActive: (String) -> Void
    var previewTimestamp: Date = Date()

    @State private var profiles: [PromptProfile] = []
    @State private var reusable: [ReusablePrompt] = []
    @State private var selection: PromptLibrarySelection?
    @State private var editorName = ""
    @State private var editorContent = ""
    @State private var feedback: String?
    @State private var feedbackIsError = false
    @State private var showPreview = false
    @State private var showHistory = false
    @State private var revisions: [PromptRevision] = []

    private var personas: [PromptProfile] { profiles.filter { $0.scope == .persona } }
    private var userProfiles: [PromptProfile] { profiles.filter { $0.scope == .user } }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Personas, user context, and reusable prompts are stored in Vera's local database with revision history.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                Spacer()
                Menu {
                    Button("New persona") { create(scope: .persona) }
                    Button("New user context") { create(scope: .user) }
                    Button("New reusable prompt") { createReusable() }
                    Divider()
                    Button("Import from file") { importDocument() }
                } label: {
                    Label("Add", systemImage: "plus")
                }
                .menuStyle(.borderlessButton).fixedSize()
            }
            scopeGroup(
                title: "Personas", explanation: PromptScope.persona.explanation,
                entries: personas.map { row(for: $0, activable: true) })
            scopeGroup(
                title: "User context", explanation: PromptScope.user.explanation,
                entries: userProfiles.map { row(for: $0, activable: false) })
            scopeGroup(
                title: "Reusable prompts",
                explanation: "Named snippets you insert into a message on demand. They are sent as your message text, never as system text.",
                entries: reusable.map { reusableRow(for: $0) })
            if selection != nil { editor }
            if let feedback {
                Text(feedback).font(.system(size: 11))
                    .foregroundStyle(feedbackIsError ? .orange : Theme.textSecondary)
            }
        }
        .onAppear(perform: reload)
        .sheet(isPresented: $showPreview) { preview }
        .sheet(isPresented: $showHistory) { history }
    }

    private func scopeGroup(title: String, explanation: String, entries: [AnyView]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 12, weight: .semibold))
            Text(explanation).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            if entries.isEmpty {
                Text("Nothing here yet.").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
            ForEach(Array(entries.enumerated()), id: \.offset) { $0.element }
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func row(for profile: PromptProfile, activable: Bool) -> AnyView {
        AnyView(HStack(spacing: 8) {
            if activable {
                Button {
                    onSelectActive(profile.id)
                    note("\(profile.name) is now the active persona.")
                } label: {
                    Image(systemName: profile.id == activePersonaID ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle(profile.id == activePersonaID ? Theme.accent : Theme.textSecondary)
                }
                .buttonStyle(.plain)
                .help(profile.id == activePersonaID ? "Active persona" : "Make this the active persona")
            }
            Button {
                select(.profile(profile.id))
            } label: {
                Text(profile.name).font(.system(size: 12, weight: selection == .profile(profile.id) ? .semibold : .regular))
            }
            .buttonStyle(.plain)
            Spacer()
            Text(profile.scope.label).font(.system(size: 10))
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(.quaternary, in: Capsule())
        })
    }

    private func reusableRow(for prompt: ReusablePrompt) -> AnyView {
        AnyView(HStack(spacing: 8) {
            Button {
                select(.reusable(prompt.id))
            } label: {
                Text(prompt.name).font(.system(size: 12, weight: selection == .reusable(prompt.id) ? .semibold : .regular))
            }
            .buttonStyle(.plain)
            Spacer()
            Text("Reusable").font(.system(size: 10))
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(.quaternary, in: Capsule())
        })
    }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                TextField("Name", text: $editorName)
                    .textFieldStyle(.roundedBorder).frame(maxWidth: 280)
                Spacer()
                Text(selectionScopeLabel).font(.system(size: 10))
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(.quaternary, in: Capsule())
            }
            TextEditor(text: $editorContent)
                .font(.system(size: 13)).frame(minHeight: 140)
                .padding(6).background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 8))
            HStack(spacing: 10) {
                Button("Save", action: save)
                Button("Duplicate", action: duplicate)
                Button("Preview") { showPreview = true }
                Button("History") { loadHistory() }
                Button("Export", action: exportDocument)
                Spacer()
                Button("Delete", role: .destructive, action: deleteSelected)
            }
            .controlSize(.small)
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var preview: some View {
        let composed = PromptPreviewComposer.compose(
            profiles: profiles,
            activePersonaID: activePersonaID,
            selection: selection,
            draft: editorContent)
        let assembled = NativeContextAssembler.assemble(NativeContextInput(
            persona: composed.persona, userScope: composed.userScope,
            timestamp: previewTimestamp, timeZone: .current, contracts: []))
        return PromptPreviewPane(sections: assembled.sections, onDone: { showPreview = false })
            .frame(width: 560, height: 520)
    }

    private var history: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Revision history").font(.system(size: 14, weight: .semibold))
                Spacer()
                Button("Done") { showHistory = false }
            }
            .padding(14)
            Divider()
            if revisions.isEmpty {
                Text("No earlier revisions.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(revisions) { revision in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(revision.createdAt.formatted(date: .abbreviated, time: .shortened))
                                        .font(.system(size: 11, weight: .medium))
                                    Spacer()
                                    Button("Restore") { restore(revision) }.controlSize(.small)
                                }
                                Text(revision.content).font(.system(size: 11))
                                    .lineLimit(4).foregroundStyle(Theme.textSecondary)
                            }
                            .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                            .background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 6))
                        }
                    }
                    .padding(14)
                }
            }
        }
        .frame(width: 520, height: 420)
    }

    private var selectionScopeLabel: String {
        switch selection {
        case .profile(let id):
            return profiles.first { $0.id == id }?.scope.label ?? "Persona"
        case .reusable:
            return "Reusable"
        case nil:
            return ""
        }
    }

    func reload() {
        profiles = (try? repository.promptProfiles()) ?? []
        reusable = (try? repository.reusablePrompts()) ?? []
        if let selection, !exists(selection) { self.selection = nil }
    }

    private func exists(_ selection: PromptLibrarySelection) -> Bool {
        switch selection {
        case .profile(let id): return profiles.contains { $0.id == id }
        case .reusable(let id): return reusable.contains { $0.id == id }
        }
    }

    private func select(_ target: PromptLibrarySelection) {
        selection = target
        feedback = nil
        switch target {
        case .profile(let id):
            guard let profile = profiles.first(where: { $0.id == id }) else { return }
            editorName = profile.name
            editorContent = profile.content
        case .reusable(let id):
            guard let prompt = reusable.first(where: { $0.id == id }) else { return }
            editorName = prompt.name
            editorContent = prompt.content
        }
    }

    private func create(scope: PromptScope) {
        let profile = PromptProfile.fresh(
            name: scope == .persona ? "New persona" : "New user context",
            scope: scope,
            content: scope == .persona
                ? NativeChatSettings.defaultSystemPrompt
                : "Preferences and context about you that Vera should know.")
        apply { try repository.savePromptProfile(profile) }
        reload()
        select(.profile(profile.id))
    }

    private func createReusable() {
        let prompt = ReusablePrompt.fresh(
            name: "New prompt",
            content: "The text inserted into your message when you pick this prompt.")
        apply { try repository.saveReusablePrompt(prompt) }
        reload()
        select(.reusable(prompt.id))
    }

    private func save() {
        switch selection {
        case .profile(let id):
            guard var profile = profiles.first(where: { $0.id == id }) else { return }
            profile.name = editorName
            profile.content = editorContent
            profile.updatedAt = Date()
            if apply({ try repository.savePromptProfile(profile) }) { note("Saved.") }
        case .reusable(let id):
            guard var prompt = reusable.first(where: { $0.id == id }) else { return }
            prompt.name = editorName
            prompt.content = editorContent
            prompt.updatedAt = Date()
            if apply({ try repository.saveReusablePrompt(prompt) }) { note("Saved.") }
        case nil:
            break
        }
        reload()
    }

    private func duplicate() {
        switch selection {
        case .profile(let id):
            guard let profile = profiles.first(where: { $0.id == id }) else { return }
            let copy = PromptProfile.fresh(
                name: "\(profile.name) copy", scope: profile.scope, content: profile.content)
            if apply({ try repository.savePromptProfile(copy) }) {
                reload()
                select(.profile(copy.id))
            }
        case .reusable(let id):
            guard let prompt = reusable.first(where: { $0.id == id }) else { return }
            let copy = ReusablePrompt.fresh(name: "\(prompt.name) copy", content: prompt.content)
            if apply({ try repository.saveReusablePrompt(copy) }) {
                reload()
                select(.reusable(copy.id))
            }
        case nil:
            break
        }
    }

    private func deleteSelected() {
        switch selection {
        case .profile(let id):
            let deletingPersona = profiles.first { $0.id == id }?.scope == .persona
            if deletingPersona, personas.count == 1 {
                note("Keep at least one persona.", isError: true)
                return
            }
            let replacement = personas.first { $0.id != id }
            guard apply({ try repository.deletePromptProfile(id) }) else { break }
            if deletingPersona, id == activePersonaID, let replacement {
                onSelectActive(replacement.id)
                note("Deleted the active persona. \(replacement.name) is now active.")
            }
        case .reusable(let id):
            apply { try repository.deleteReusablePrompt(id) }
        case nil:
            break
        }
        selection = nil
        reload()
    }

    private func loadHistory() {
        switch selection {
        case .profile(let id), .reusable(let id):
            revisions = (try? repository.promptRevisions(entityID: id)) ?? []
        case nil:
            revisions = []
        }
        showHistory = true
    }

    private func restore(_ revision: PromptRevision) {
        editorContent = revision.content
        save()
        showHistory = false
        note("Restored the revision from \(revision.createdAt.formatted(date: .abbreviated, time: .shortened)).")
    }

    private func exportDocument() {
        let kind: PromptDocumentKind
        switch selection {
        case .profile(let id):
            kind = profiles.first { $0.id == id }?.scope == .user ? .user : .persona
        case .reusable:
            kind = .reusable
        case nil:
            return
        }
        let document = PromptDocument(name: editorName, kind: kind, content: editorContent)
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "\(editorName).md"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try document.exported.write(to: url, atomically: true, encoding: .utf8)
            note("Exported to \(url.lastPathComponent).")
        } catch {
            note(error.localizedDescription, isError: true)
        }
    }

    private func importDocument() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        guard panel.runModal() == .OK, let url = panel.urls.first else { return }
        do {
            guard let size = try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int,
                  size <= NativePromptValidation.importByteLimit else {
                throw PromptDocumentError.oversize(limit: NativePromptValidation.importByteLimit)
            }
            let raw = try String(contentsOf: url, encoding: .utf8)
            let document = try PromptDocument.parse(raw)
            if let scope = document.kind.scope {
                let profile = PromptProfile.fresh(name: document.name, scope: scope, content: document.content)
                try repository.savePromptProfile(profile)
                reload()
                select(.profile(profile.id))
            } else {
                let prompt = ReusablePrompt.fresh(name: document.name, content: document.content)
                try repository.saveReusablePrompt(prompt)
                reload()
                select(.reusable(prompt.id))
            }
            note("Imported \(document.name).")
        } catch {
            note("Import failed: \(error.localizedDescription)", isError: true)
        }
    }

    @discardableResult
    private func apply(_ operation: () throws -> Void) -> Bool {
        do {
            try operation()
            feedback = nil
            return true
        } catch {
            note(error.localizedDescription, isError: true)
            return false
        }
    }

    private func note(_ message: String, isError: Bool = false) {
        feedback = message
        feedbackIsError = isError
    }
}

struct PromptPreviewPane: View {
    let sections: [NativeContextSection]
    var onDone: (() -> Void)?
    var scrolls: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Request preview").font(.system(size: 14, weight: .semibold))
                Spacer()
                if let onDone { Button("Done", action: onDone) }
            }
            .padding(14)
            Divider()
            if scrolls {
                ScrollView { inner }
            } else {
                inner
                Spacer(minLength: 0)
            }
        }
    }

    private var inner: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Sections render in this order on every request. The app policy comes first and cannot be edited; your prompts follow it and cannot override it.")
                .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            ForEach(sections, id: \.name) { section in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 5) {
                        if section.name == "policy" {
                            Image(systemName: "lock.fill").font(.system(size: 9))
                                .foregroundStyle(Theme.textSecondary)
                        }
                        Text(Self.sectionTitle(section.name))
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    Text(section.content)
                        .font(.system(size: 11, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                        .background(section.name == "policy" ? Theme.bg.opacity(0.6) : Theme.bg)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
        }
        .padding(14)
    }

    static func sectionTitle(_ name: String) -> String {
        switch name {
        case "policy": return "App policy"
        case "persona": return "Persona"
        case "user": return "User context"
        case "conversation": return "Conversation instructions"
        case "session": return "Session"
        default: return name.capitalized
        }
    }
}

struct ConversationInstructionsChip: View {
    @EnvironmentObject var store: ChatStore
    let conversation: Conversation
    @State private var showing = false
    @State private var text = ""
    @State private var error: String?

    private var hasInstructions: Bool {
        !(conversation.instructions ?? "").isEmpty
    }

    var body: some View {
        Button {
            text = conversation.instructions ?? ""
            error = nil
            showing = true
        } label: {
            Label("Instructions", systemImage: hasInstructions ? "text.badge.checkmark" : "text.badge.plus")
                .font(.system(size: 11))
                .foregroundStyle(hasInstructions ? Theme.accent : Theme.textSecondary)
        }
        .buttonStyle(.plain)
        .help("Instructions that apply to this conversation only")
        .popover(isPresented: $showing, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Conversation instructions").font(.system(size: 12, weight: .semibold))
                Text("Sent with every request in this conversation, after your persona and user context.")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                TextEditor(text: $text)
                    .font(.system(size: 12)).frame(width: 320, height: 120)
                    .padding(4).background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 6))
                if let error {
                    Text(error).font(.system(size: 11)).foregroundStyle(.orange)
                }
                HStack {
                    Button("Clear") {
                        store.setConversationInstructions(conversation.id, "")
                        showing = false
                    }
                    Spacer()
                    Button("Save") {
                        do {
                            try NativePromptValidation.validateInstructions(text)
                        } catch let validation {
                            error = validation.localizedDescription
                            return
                        }
                        store.setConversationInstructions(conversation.id, text)
                        showing = false
                    }
                    .keyboardShortcut(.defaultAction)
                }
                .controlSize(.small)
            }
            .padding(12)
        }
    }
}

struct PromptLibraryShotView: View {
    let variant: String

    private static let fixtureDate = Date(timeIntervalSince1970: 1_785_000_000)

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            if variant == "prompt-preview" {
                let assembled = NativeContextAssembler.assemble(NativeContextInput(
                    persona: "You are Vera. Answer plainly, admit uncertainty, and keep household matters private.",
                    userScope: "I prefer metric units and 24 hour time.",
                    conversationInstructions: "Keep answers under three paragraphs in this conversation.",
                    timestamp: Self.fixtureDate,
                    timeZone: TimeZone(identifier: "America/Chicago") ?? .current,
                    contracts: []))
                PromptPreviewPane(sections: assembled.sections, scrolls: false)
                    .frame(width: 620)
                    .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
            } else {
                libraryMirror
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .foregroundStyle(Theme.textPrimary)
    }

    private var libraryMirror: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("PROMPT LIBRARY").font(.system(size: 10, weight: .semibold)).tracking(0.5)
                    .foregroundStyle(Theme.textSecondary)
                Spacer()
                Label("Add", systemImage: "plus").font(.system(size: 12, weight: .medium))
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(Theme.surfaceHover).clipShape(RoundedRectangle(cornerRadius: 6))
            }
            Text("Personas, user context, and reusable prompts are stored in Vera's local database with revision history.")
                .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            group("Personas", PromptScope.persona.explanation, [
                entry("Vera", "Persona", active: true, selected: false),
                entry("Brief mode", "Persona", active: false, selected: true),
            ])
            group("User context", PromptScope.user.explanation, [
                entry("About me", "User context", active: nil, selected: false),
            ])
            group("Reusable prompts",
                  "Named snippets you insert into a message on demand. They are sent as your message text, never as system text.", [
                entry("Weekly recap", "Reusable", active: nil, selected: false),
                entry("Plan my day", "Reusable", active: nil, selected: false),
            ])
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Brief mode").font(.system(size: 12))
                        .padding(.horizontal, 8).padding(.vertical, 5).frame(width: 240, alignment: .leading)
                        .background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 6))
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.hairline, lineWidth: 1))
                    Spacer()
                    chip("Persona")
                }
                Text("You are Vera. Answer in as few words as accuracy allows.")
                    .font(.system(size: 13))
                    .padding(10).frame(maxWidth: .infinity, minHeight: 90, alignment: .topLeading)
                    .background(Theme.bg).clipShape(RoundedRectangle(cornerRadius: 8))
                HStack(spacing: 12) {
                    ForEach(["Save", "Duplicate", "Preview", "History", "Export"], id: \.self) { title in
                        Text(title).font(.system(size: 11, weight: .medium))
                            .padding(.horizontal, 10).padding(.vertical, 4)
                            .background(Theme.surfaceHover).clipShape(RoundedRectangle(cornerRadius: 6))
                    }
                    Spacer()
                    Text("Delete").font(.system(size: 11, weight: .medium)).foregroundStyle(.red)
                }
            }
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(22).frame(width: 640)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
    }

    private func group(_ title: String, _ explanation: String, _ rows: [AnyView]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 12, weight: .semibold))
            Text(explanation).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            ForEach(Array(rows.enumerated()), id: \.offset) { $0.element }
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.bg.opacity(0.5)).clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func entry(_ name: String, _ scope: String, active: Bool?, selected: Bool) -> AnyView {
        AnyView(HStack(spacing: 8) {
            if let active {
                Image(systemName: active ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(active ? Theme.accent : Theme.textSecondary)
            }
            Text(name).font(.system(size: 12, weight: selected ? .semibold : .regular))
            Spacer()
            chip(scope)
        })
    }

    private func chip(_ label: String) -> some View {
        Text(label).font(.system(size: 10))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(Theme.surfaceHover, in: Capsule())
    }
}
