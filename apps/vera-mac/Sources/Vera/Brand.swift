import SwiftUI
import AppKit

/// Resolves the SwiftPM resource bundle WITHOUT the generated `Bundle.module` accessor.
/// That accessor's search paths vary by toolchain — some emit one that probes only the app
/// root and the machine-specific build directory, which fatalErrors in a packaged app built
/// on another machine. This probes every real layout and degrades to nil instead of crashing.
enum VeraResources {
    static let bundle: Bundle? = {
        let name = "Vera_Vera.bundle"
        let candidates = [
            Bundle.main.resourceURL,                                  // packaged .app (Contents/Resources)
            Bundle.main.executableURL?.deletingLastPathComponent(),   // swift run / .build binaries
            Bundle.main.bundleURL,                                    // app root
        ]
        for c in candidates {
            if let u = c?.appendingPathComponent(name), let b = Bundle(url: u) { return b }
        }
        return nil
    }()

    static func url(_ name: String, ext: String) -> URL? {
        bundle?.url(forResource: name, withExtension: ext)
    }
}

/// The Vera astrolabe mark — a copper navigation star in a graduated ring on a transparent
/// background (`vera-mark.png`), used inside the app. The dock / app icon is the full ink
/// tile `vera-icon.png`, generated into `Vera.icns` by `scripts/package.sh`. Falls back to
/// the accent circle so headless renders / missing-resource builds never break.
enum Brand {
    /// The bare mark on transparency — used inside the app UI.
    static let glyph: NSImage? = load("vera-mark")
    /// The full ink-tile app icon — used as the Dock icon when running unbundled
    /// (`swift run`); the packaged `.app` uses `Vera.icns` instead.
    static let icon: NSImage? = load("vera-icon")

    private static func load(_ name: String) -> NSImage? {
        guard let url = VeraResources.url(name, ext: "png"),
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

/// The Vera astrolabe mark at a given size — no background tile, so it sits directly on the UI.
/// Falls back to a filled accent circle. When `animated` is true it breathes in place — scale +
/// opacity + a soft accent glow only, never rotation or layout movement. The loop is a
/// phaseAnimator that exists only on the animated branch, so it is structurally incapable of
/// outliving `animated == false`: the instant generation ends the static glyph renders at
/// identity transform.
struct VeraMark: View {
    var size: CGFloat = 26
    var animated: Bool = false

    private var glyph: some View {
        Group {
            if let glyph = Brand.glyph {
                Image(nsImage: Brand.mark(glyph, size: size)).frame(width: size, height: size)
            } else {
                Circle().fill(Theme.accent).frame(width: size, height: size)
            }
        }
        .frame(width: size, height: size)
    }

    var body: some View {
        if animated {
            glyph
                .phaseAnimator([false, true]) { view, phase in
                    view
                        .scaleEffect(phase ? 1.06 : 0.94)
                        .opacity(phase ? 1.0 : 0.7)
                        .shadow(color: Theme.accentGlow.opacity(phase ? 0.55 : 0.15), radius: 7)
                } animation: { _ in .easeInOut(duration: 0.85) }
        } else {
            glyph
        }
    }
}
