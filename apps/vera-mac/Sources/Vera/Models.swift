import Foundation

/// A chat message in the UI.
struct Message: Identifiable, Hashable {
    enum Role: String { case user, assistant }
    let id = UUID()
    var role: Role
    var text: String
    var ask: VeraAsk? = nil        // a structured question parsed out of an assistant reply
    var answered: Bool = false     // set once the user taps an answer
    var answerText: String? = nil  // the recorded selection (for recap display)
    var artifacts: [Artifact] = [] // Canvas artifacts parsed out of an assistant reply
    var attachments: [MessageAttachment] = []  // images/docs the user attached to this turn
    var pulse: PulseCard? = nil    // when set, render this turn as the rich Pulse briefing (continued in chat)
    var sources: [PulseSource] = []  // cited sources for this reply — drives the citation chips

    /// Build an assistant message from raw reply text, splitting out artifacts then any `vera:ask` block.
    static func assistant(from raw: String, sources: [PulseSource] = []) -> Message {
        let (afterArtifacts, artifacts) = Artifact.parse(raw)
        let (clean, ask) = VeraAsk.parse(afterArtifacts)
        return Message(role: .assistant, text: clean, ask: ask, artifacts: artifacts, sources: sources)
    }
}

/// A conversation shown in the sidebar.
struct Conversation: Identifiable, Hashable {
    let id: String          // the OWUI chat id; a local UUID only until first persisted
    var title: String
    var messages: [Message]
    var updatedAt: Date
    var isPersisted: Bool = false   // false = local draft OWUI doesn't know about yet
    var serverUpdatedAt: Int = 0    // OWUI's own updated_at stamp — the reconcile freshness baseline
    var pinned: Bool = false
}
