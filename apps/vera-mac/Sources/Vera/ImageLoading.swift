import SwiftUI
import AppKit

extension Color {
    /// "#rrggbb" → Color (nil if malformed).
    init?(hex: String?) {
        guard var h = hex?.trimmingCharacters(in: .whitespaces), h.hasPrefix("#") else { return nil }
        h.removeFirst()
        guard h.count == 6, let v = Int(h, radix: 16) else { return nil }
        self = Color(.sRGB, red: Double((v >> 16) & 0xff) / 255,
                     green: Double((v >> 8) & 0xff) / 255, blue: Double(v & 0xff) / 255)
    }
}

/// In-memory NSImage cache keyed by URL (avoids reloading Pulse art / favicons).
@MainActor
final class RemoteImageCache {
    static let shared = RemoteImageCache()
    private var cache: [String: NSImage] = [:]
    func get(_ k: String) -> NSImage? { cache[k] }
    func set(_ k: String, _ v: NSImage) { cache[k] = v }
}

/// Loads an image URL into an NSImage with an optional Bearer token (OWUI file content needs auth).
/// `natural` mode sizes to the image's own aspect ratio (no cropping) instead of filling a fixed frame.
struct AuthedAsyncImage: View {
    let url: String?
    var token: String? = nil
    var contentMode: ContentMode = .fill
    var natural: Bool = false              // size to the image's aspect ratio, no crop
    var placeholderHeight: CGFloat = 200   // height reserved before load in natural mode
    @State private var image: NSImage?

    var body: some View {
        ZStack {
            if let image {
                Image(nsImage: image).resizable()
                    .aspectRatio(contentMode: natural ? .fit : contentMode)
            } else if natural {
                Theme.surface.frame(height: placeholderHeight)
            } else {
                Theme.surface
            }
        }
        .task(id: url) { await load() }
    }

    private func load() async {
        guard let url, let u = URL(string: url) else { return }
        if let cached = RemoteImageCache.shared.get(url) { image = cached; return }
        var req = URLRequest(url: u)
        if let token, !token.isEmpty { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        guard let (data, _) = try? await URLSession.shared.data(for: req), let img = NSImage(data: data) else { return }
        RemoteImageCache.shared.set(url, img)
        image = img
    }
}

/// A source's favicon (DuckDuckGo's public icon service; no auth).
struct Favicon: View {
    let urlString: String
    private var domain: String { URL(string: urlString)?.host?.replacingOccurrences(of: "www.", with: "") ?? "" }
    var body: some View {
        AuthedAsyncImage(url: domain.isEmpty ? nil : "https://icons.duckduckgo.com/ip3/\(domain).ico")
            .frame(width: 16, height: 16)
            .background(Theme.surface)
            .clipShape(Circle())
            .overlay(Circle().stroke(Theme.bg, lineWidth: 1.5))
    }
}

func sourceHost(_ url: String) -> String {
    URL(string: url)?.host?.replacingOccurrences(of: "www.", with: "") ?? url
}
