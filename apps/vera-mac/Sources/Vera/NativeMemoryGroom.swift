import Foundation

struct NativeMemoryGroomOutcome: Equatable, Sendable {
    var removedCount: Int
    var invalidatedProposalCount: Int
    var dryRun: Bool
    var error: String?
    var finishedAt: Date

    var label: String {
        if let error { return "Expiry groom failed: \(error)" }
        let noun = removedCount == 1 ? "expired memory" : "expired memories"
        if dryRun {
            return "Groom dry run: \(removedCount) \(noun) would be removed"
        }
        return "Removed \(removedCount) \(noun)"
    }
}

enum NativeMemoryGroom {
    static func expired(
        records: [NativeMemoryRecord], now: Date, calendar: Calendar
    ) -> [NativeMemoryRecord] {
        records.filter { isExpired($0, now: now, calendar: calendar) }
    }

    static func isExpired(_ record: NativeMemoryRecord, now: Date, calendar: Calendar) -> Bool {
        guard record.status == .approved, record.durability == .episodic,
              let expiry = record.expiry else { return false }
        var storage = Calendar(identifier: .gregorian)
        storage.timeZone = TimeZone(secondsFromGMT: 0) ?? storage.timeZone
        let expiryDay = storage.dateComponents([.year, .month, .day], from: expiry)
        let today = calendar.dateComponents([.year, .month, .day], from: now)
        guard let expiryYear = expiryDay.year, let expiryMonth = expiryDay.month,
              let expiryDate = expiryDay.day, let todayYear = today.year,
              let todayMonth = today.month, let todayDate = today.day else { return false }
        return (expiryYear, expiryMonth, expiryDate) < (todayYear, todayMonth, todayDate)
    }

    static func danglingProposals(
        pending: [NativeMemoryProposal], removedIDs: Set<String>
    ) -> [NativeMemoryProposal] {
        pending.filter { $0.targetIDs.contains(where: removedIDs.contains) }
    }
}
