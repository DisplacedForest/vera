import Foundation

enum MessageContentType: String {
    case text
    case pulseCard = "pulse_card"
}

/// A chat message in the UI.
struct Message: Identifiable, Hashable {
    enum Role: String { case user, assistant }
    var id: UUID = UUID()
    var role: Role
    var text: String
    var createdAt: Date = Date()
    var state: MessageState = .complete
    var failure: String? = nil
    var modelID: String? = nil
    var ask: VeraAsk? = nil        // a structured question parsed out of an assistant reply
    var answered: Bool = false     // set once the user taps an answer
    var answerText: String? = nil  // the recorded selection (for recap display)
    var artifacts: [Artifact] = [] // Canvas artifacts parsed out of an assistant reply
    var attachments: [MessageAttachment] = []  // images/docs the user attached to this turn
    var pulse: PulseCard? = nil    // when set, render this turn as the rich Pulse briefing (continued in chat)
    var sources: [PulseSource] = []  // cited sources for this reply — drives the citation chips
    var toolActivities: [NativeToolActivity] = []
    var contentType: MessageContentType = .text
    var routeNote: MessageRouteNote? = nil

    /// Build an assistant message from raw reply text, splitting out artifacts then any `vera:ask` block.
    static func assistant(from raw: String, sources: [PulseSource] = []) -> Message {
        let (afterArtifacts, artifacts) = Artifact.parse(raw)
        let (clean, ask) = VeraAsk.parse(afterArtifacts)
        return Message(role: .assistant, text: clean, ask: ask, artifacts: artifacts, sources: sources)
    }
}

/// A conversation shown in the sidebar.
struct Conversation: Identifiable, Hashable {
    let id: String
    var title: String
    var messages: [Message]
    var createdAt: Date = Date()
    var updatedAt: Date
    var isPersisted: Bool = false
    var serverUpdatedAt: Int = 0
    var pinned: Bool = false
    var memoryExcluded: Bool = false
    var originType: String? = nil
    var originID: String? = nil
    var instructions: String? = nil
    var grounding: [String] = []
}
