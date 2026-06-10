import SwiftUI
import AppKit

/// The Vera flame mark — the bare flame on a transparent background (`vera-flame.png`),
/// used inside the app. The dock / app icon is the full tiled `vera-logo.png`, generated
/// into `Vera.icns` by `scripts/package.sh`. Falls back to the accent circle so headless
/// renders / missing-resource builds never break.
enum Brand {
    /// The bare flame on transparency — used inside the app UI.
    static let flame: NSImage? = load("vera-flame")
    /// The full tiled app icon (dark background baked in) — used as the Dock icon when running
    /// unbundled (`swift run`); the packaged `.app` uses `Vera.icns` instead.
    static let icon: NSImage? = load("vera-logo")

    private static func load(_ name: String) -> NSImage? {
        guard let url = Bundle.module.url(forResource: name, withExtension: "png"),
              let img = NSImage(contentsOf: url) else { return nil }
        return img
    }

    /// A point-sized mark that redraws the source through Core Graphics at the display's backing
    /// resolution. Handing SwiftUI the full ~1000px source instead lets the GPU minify it ~16× in a
    /// single bilinear step, which aliases the curved edges into jaggies; a resolution-independent
    /// `drawingHandler` image is area-sampled crisply at whatever scale the layer actually needs.
    static func mark(_ source: NSImage, size: CGFloat) -> NSImage {
        let img = NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
            NSGraphicsContext.current?.imageInterpolation = .high
            source.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1)
            return true
        }
        return img
    }
}

/// The Vera flame mark at a given size — no background tile, so it sits directly on the UI.
/// Falls back to a filled accent circle. When `animated` is true it "breathes" (scale + opacity +
/// a soft accent glow, gently rotating) to signal Vera is thinking — like Claude's living mark.
struct VeraMark: View {
    var size: CGFloat = 26
    var animated: Bool = false
    @State private var pulse = false

    private var glyph: some View {
        Group {
            if let flame = Brand.flame {
                Image(nsImage: Brand.mark(flame, size: size)).frame(width: size, height: size)
            } else {
                Circle().fill(Theme.accent).frame(width: size, height: size)
            }
        }
    }

    var body: some View {
        glyph
            .scaleEffect(animated ? (pulse ? 1.10 : 0.90) : 1.0)
            .opacity(animated ? (pulse ? 1.0 : 0.65) : 1.0)
            .rotationEffect(.degrees(animated ? (pulse ? 6 : -6) : 0))
            .shadow(color: Theme.accent.opacity(animated && pulse ? 0.6 : 0.0), radius: animated ? 7 : 0)
            .animation(animated ? .easeInOut(duration: 0.85).repeatForever(autoreverses: true) : .easeOut(duration: 0.2),
                       value: pulse)
            .onChange(of: animated) { _, on in pulse = on }
            .onAppear { if animated { pulse = true } }
    }
}
