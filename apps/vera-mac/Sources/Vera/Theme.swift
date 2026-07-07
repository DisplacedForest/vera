import SwiftUI
import AppKit

extension Color {
    /// A dynamic color that resolves per the effective NSAppearance (window or NSApp),
    /// so brand values adapt wherever the view renders — packaged app or headless shots.
    init(light: String, dark: String) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            let hex = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
            return NSColor(hex: hex)
        })
    }
}

extension NSColor {
    /// `#RRGGBB` to an sRGB color.
    convenience init(hex: String) {
        let s = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        var v: UInt64 = 0
        Scanner(string: s).scanHexInt64(&v)
        self.init(srgbRed: CGFloat((v >> 16) & 0xFF) / 255,
                  green: CGFloat((v >> 8) & 0xFF) / 255,
                  blue: CGFloat(v & 0xFF) / 255, alpha: 1)
    }
}

/// The Astrolabe palette — midnight ink structure, copper instrument accent, parchment
/// reading surface in light mode. Brand values are dynamic (dark/light); text and rules
/// defer to system semantics so vibrancy and accessibility behave natively.
enum Theme {
    /// Window ground: midnight ink / warm parchment.
    static let ink = Color(light: "#FAF5EA", dark: "#101623")
    /// Cards and wells: vellum / bezel. Prefer `.regularMaterial` where the surface sits over glass.
    static let surface = Color(light: "#F0E8D8", dark: "#1D2433")
    /// A slightly elevated surface for hover and selection fills.
    static let surfaceHover = Color(light: "#E8DEC9", dark: "#273044")
    /// Copper: selection, buttons, send, links.
    static let accent = Color(light: "#A05A2C", dark: "#C77B4A")
    /// Burnish: the generation shimmer and "new" badges.
    static let accentGlow = Color(light: "#C77B4A", dark: "#E8A97E")
    /// The one filled message container.
    static let userBubble = Color(light: "#A05A2C", dark: "#8A5230")
    static let textPrimary = Color.primary
    static let textSecondary = Color(light: "#75695A", dark: "#96A2B3")
    static let hairline = Color(nsColor: .separatorColor)

    static let bg = ink
    static let sidebar = ink
    static let assistantBubble = surface
}
