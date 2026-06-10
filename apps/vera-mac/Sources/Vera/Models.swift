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

    /// Build an assistant message from raw reply text, splitting out artifacts then any `vera:ask` block.
    static func assistant(from raw: String) -> Message {
        let (afterArtifacts, artifacts) = Artifact.parse(raw)
        let (clean, ask) = VeraAsk.parse(afterArtifacts)
        return Message(role: .assistant, text: clean, ask: ask, artifacts: artifacts)
    }
}

/// A conversation shown in the sidebar.
struct Conversation: Identifiable, Hashable {
    let id: String          // stable UI id (OWUI chat id once persisted, else a local UUID)
    var title: String
    var messages: [Message]
    var updatedAt: Date
    var owuiID: String? = nil   // set once the chat is saved in OWUI; nil = not yet persisted
    var pinned: Bool = false
}
