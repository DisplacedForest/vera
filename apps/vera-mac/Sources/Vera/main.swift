import Foundation

// Entry point. `Vera --shot <path>` renders the UI to a PNG headlessly (no window) and exits;
// otherwise it launches the normal app.
let arguments = CommandLine.arguments
if let idx = arguments.firstIndex(of: "--shot"), idx + 1 < arguments.count {
    let path = arguments[idx + 1]
    let view = arguments.firstIndex(of: "--view").map { arguments[$0 + 1] } ?? "chat"
    let appearance = arguments.firstIndex(of: "--appearance").map { arguments[$0 + 1] } ?? "dark"
    await Shot.render(view: view, to: path, appearance: appearance)
} else if let idx = arguments.firstIndex(of: "--voice-e2e"), idx + 1 < arguments.count {
    // DEBUG-ONLY: stream a sample wav through the real Wyoming servers, print transcript.
    await SelfTest.voiceE2E(wavPath: arguments[idx + 1])
} else if arguments.contains("--reminders-serve") {
    // DEBUG-ONLY: start the in-app EventKit bridge and block, to validate the native
    // Reminders path (macOS prompt + HTTP contract) independent of the UI.
    try? RemindersBridge.shared.start()
    print("vera-reminders bridge serving on :\(RemindersBridge.shared.port), ctrl-c to stop")
    while true { try? await Task.sleep(nanoseconds: 3_600_000_000_000) }
} else if arguments.contains("--selftest-recovery") {
    // DEBUG-ONLY: prove 401 session recovery across a server restart (blocks on stdin between sends).
    await SelfTest.recoveryProbe()
} else if arguments.contains("--selftest") {
    await SelfTest.run()
} else if arguments.contains("--install-conventions") || arguments.contains("--install-ask-convention") {
    await SelfTest.installConventions()
} else {
    VeraApp.main()
}
