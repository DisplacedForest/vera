import SwiftUI

/// Full-screen voice-mode surface: an animated orb reflecting session state, a status
/// caption, and a close control. Mirrors ChatGPT's voice screen. Icons only — no emojis.
struct VoiceView: View {
    @EnvironmentObject var voice: VoiceSession

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 0) {
                HStack {
                    (Text("Vera ").foregroundStyle(.white)
                        + Text("Voice").foregroundStyle(Theme.textSecondary))
                        .font(.system(size: 17, weight: .semibold))
                    Spacer()
                }
                .padding(.horizontal, 28).padding(.top, 36)

                Spacer()
                VoiceOrb(state: voice.state, level: voice.level)
                    .frame(width: 220, height: 220)
                Text(caption)
                    .font(.system(size: 14))
                    .foregroundStyle(Theme.textSecondary)
                    .padding(.top, 30)
                // Diagnostics (Phase-1 tuning): live input level + frames seen.
                Text(String(format: "in: %.0f  floor: %.0f dBFS  %@  frames: %d",
                             voice.debugDB, voice.debugFloor,
                             voice.debugSpeech ? "SPEECH" : "N/A", voice.debugFrames))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Theme.textSecondary.opacity(0.7))
                    .padding(.top, 8)
                Spacer()

                if let err = voice.lastError {
                    Text(err)
                        .font(.system(size: 12))
                        .foregroundStyle(.red.opacity(0.9))
                        .padding(.bottom, 10)
                }
                Button(action: { voice.stop() }) {
                    Image(systemName: "xmark")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 56, height: 56)
                        .background(Theme.surfaceHover)
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .padding(.bottom, 40)
                .help("End voice session")
            }
        }
    }

    private var caption: String {
        switch voice.state {
        case .idle:         return ""
        case .listening:    return "Listening…"
        case .transcribing: return "Transcribing…"
        case .thinking:     return voice.statusLine ?? "Thinking…"
        case .speaking:     return "Speaking…"
        }
    }
}

/// The orb: a blue/white gradient sphere that pulses with mic level (listening), breathes
/// (thinking), and ripples outward (speaking).
struct VoiceOrb: View {
    let state: VoiceSession.State
    let level: Float
    @State private var phase: CGFloat = 0

    var body: some View {
        ZStack {
            Circle()
                .stroke(Theme.accent.opacity(ringOpacity), lineWidth: 2)
                .scaleEffect(ringScale)
            Circle()
                .fill(
                    RadialGradient(
                        colors: [Color(red: 0.85, green: 0.93, blue: 1.0),
                                 Color(red: 0.35, green: 0.60, blue: 0.95),
                                 Color(red: 0.15, green: 0.35, blue: 0.80)],
                        center: .center, startRadius: 6, endRadius: 130)
                )
                .scaleEffect(coreScale)
                .shadow(color: Theme.accent.opacity(0.5), radius: 30)
        }
        .animation(.easeInOut(duration: 0.18), value: level)
        .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: phase)
        .onAppear { phase = 1 }
    }

    private var coreScale: CGFloat {
        switch state {
        case .listening: return 0.90 + CGFloat(level) * 0.35
        case .thinking:  return 0.85 + phase * 0.08
        case .speaking:  return 0.95 + phase * 0.12
        default:         return 0.90
        }
    }
    private var ringScale: CGFloat {
        switch state {
        case .speaking: return 1.0 + phase * 0.25
        case .thinking: return 1.0 + phase * 0.12
        default:        return 1.0
        }
    }
    private var ringOpacity: Double {
        switch state {
        case .speaking: return 0.5
        case .thinking: return 0.3
        default:        return 0.0
        }
    }
}
