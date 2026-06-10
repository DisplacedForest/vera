import SwiftUI

/// ChatGPT-desktop-inspired dark palette. Kept in one place so every surface stays consistent.
enum Theme {
    static let bg = Color(red: 0.13, green: 0.13, blue: 0.14)          // app background
    static let sidebar = Color(red: 0.10, green: 0.10, blue: 0.11)     // left rail
    static let surface = Color(red: 0.17, green: 0.17, blue: 0.19)     // cards / composer
    static let surfaceHover = Color(red: 0.22, green: 0.22, blue: 0.24)
    static let assistantBubble = Color(red: 0.17, green: 0.17, blue: 0.19)
    static let userBubble = Color(red: 0.20, green: 0.31, blue: 0.55)
    static let accent = Color(red: 0.40, green: 0.55, blue: 0.95)
    static let textPrimary = Color(red: 0.93, green: 0.93, blue: 0.94)
    static let textSecondary = Color(red: 0.62, green: 0.62, blue: 0.66)
    static let hairline = Color.white.opacity(0.07)
}
