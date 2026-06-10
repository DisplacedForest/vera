import Foundation

// Entry point. `Vera --shot <path>` renders the UI to a PNG headlessly (no window) and exits;
// otherwise it launches the normal app.
let arguments = CommandLine.arguments
if let idx = arguments.firstIndex(of: "--shot"), idx + 1 < arguments.count {
    let path = arguments[idx + 1]
    let view = arguments.firstIndex(of: "--view").map { arguments[$0 + 1] } ?? "chat"
    await Shot.render(view: view, to: path)
} else if let idx = arguments.firstIndex(of: "--voice-e2e"), idx + 1 < arguments.count {
    // DEBUG-ONLY: stream a sample wav through the real Wyoming servers, print transcript.
    await SelfTest.voiceE2E(wavPath: arguments[idx + 1])
} else if arguments.contains("--selftest") {
    await SelfTest.run()
} else if arguments.contains("--install-conventions") || arguments.contains("--install-ask-convention") {
    await SelfTest.installConventions()
} else {
    VeraApp.main()
}
