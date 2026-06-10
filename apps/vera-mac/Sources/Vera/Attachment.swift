import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// A pending composer attachment (image or document) being prepared for the next message.
final class Attachment: ObservableObject, Identifiable {
    enum Kind { case image, file }
    enum Status { case processing, ready, failed }

    let id = UUID()
    let url: URL
    let name: String
    let ext: String            // short badge, e.g. "PNG", "PDF", "PPTX"
    let kind: Kind

    @Published var status: Status = .processing
    @Published var thumbnail: NSImage? = nil      // small preview for image chips
    var dataURL: String? = nil                     // images: data:<mime>;base64,...  (downscaled)
    var owuiFile: [String: Any]? = nil             // documents: OWUI /api/v1/files/ response object

    init(url: URL) {
        self.url = url
        self.name = url.lastPathComponent
        self.ext = url.pathExtension.uppercased()
        let t = UTType(filenameExtension: url.pathExtension)
        self.kind = (t?.conforms(to: .image) ?? false) ? .image : .file
    }
}

/// Lightweight, Hashable attachment snapshot stored on a sent Message for display in the bubble.
struct MessageAttachment: Identifiable, Hashable {
    let id = UUID()
    var name: String
    var ext: String
    var isImage: Bool
    var thumbnailData: Data? = nil   // PNG of the thumbnail (Hashable; NSImage isn't)
}

extension NSImage {
    /// PNG encoding of the image (for Hashable storage on a sent Message).
    var pngData: Data? {
        guard let tiff = tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return nil }
        return rep.representation(using: .png, properties: [:])
    }
}

enum ImageEncoder {
    /// Downscale to `maxDim` on the long edge and return a JPEG data URL (small enough for the
    /// vision pipeline, lossy is fine for Qwen3-VL). Returns nil on failure.
    static func dataURL(from url: URL, maxDim: CGFloat = 1568, quality: CGFloat = 0.85) -> (dataURL: String, thumb: NSImage)? {
        guard let img = NSImage(contentsOf: url) else { return nil }
        let scaled = downscale(img, maxDim: maxDim)
        guard let tiff = scaled.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let jpeg = rep.representation(using: .jpeg, properties: [.compressionFactor: quality]) else { return nil }
        let url = "data:image/jpeg;base64," + jpeg.base64EncodedString()
        return (url, downscale(img, maxDim: 96))
    }

    static func downscale(_ img: NSImage, maxDim: CGFloat) -> NSImage {
        let w = img.size.width, h = img.size.height
        guard w > 0, h > 0 else { return img }
        let scale = min(1, maxDim / max(w, h))
        if scale >= 1 { return img }
        let target = NSSize(width: floor(w * scale), height: floor(h * scale))
        let out = NSImage(size: target)
        out.lockFocus()
        NSGraphicsContext.current?.imageInterpolation = .high
        img.draw(in: NSRect(origin: .zero, size: target),
                 from: NSRect(origin: .zero, size: img.size), operation: .copy, fraction: 1)
        out.unlockFocus()
        return out
    }
}
