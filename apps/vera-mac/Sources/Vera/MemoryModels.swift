import Foundation

/// A memory entry from OWUI's store. The fact text plus tags/bank parsed out of the
/// "[Tags: ...] ... [Memory Bank: ...]" trailer Adaptive Memory writes.
struct MemoryItem: Identifiable, Hashable {
    let id: String
    var text: String
    var tags: [String]
    var bank: String

    static func parse(id: String, content: String) -> MemoryItem {
        var t = content
        var tags: [String] = []
        var bank = "General"
        if let r = t.range(of: #"\[Tags:[^\]]*\]"#, options: .regularExpression) {
            let inner = t[r].dropFirst(6).dropLast()
            tags = inner.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            t.removeSubrange(r)
        }
        if let r = t.range(of: #"\[Memory Bank:[^\]]*\]"#, options: .regularExpression) {
            bank = String(t[r]).replacingOccurrences(of: "[Memory Bank:", with: "")
                .replacingOccurrences(of: "]", with: "").trimmingCharacters(in: .whitespaces)
            t.removeSubrange(r)
        }
        return MemoryItem(id: id, text: t.trimmingCharacters(in: .whitespaces), tags: tags, bank: bank.isEmpty ? "General" : bank)
    }

}
