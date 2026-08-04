import AppKit
import Foundation

enum NativeAttachmentError: LocalizedError, Equatable {
    case unsupportedType(String)
    case overLimit(String)
    case unreadable(String)

    var errorDescription: String? {
        switch self {
        case .unsupportedType(let name):
            return "\(name) isn't a supported image. Use png, jpeg, heic, webp, or gif."
        case .overLimit(let name):
            return "\(name) is larger than the \(NativeAttachmentStore.maxMegabytes) MB attachment limit."
        case .unreadable(let name):
            return "\(name) couldn't be read."
        }
    }
}

final class NativeAttachmentStore: Sendable {
    static let maxMegabytes = 20
    static let maxBytes = maxMegabytes * 1_048_576

    let directory: URL

    static var standardDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Vera", isDirectory: true)
            .appendingPathComponent("attachments", isDirectory: true)
    }

    init(directory: URL = NativeAttachmentStore.standardDirectory) {
        self.directory = directory
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    func save(data: Data, preferredName: String) throws -> MessageAttachment {
        guard !data.isEmpty else { throw NativeAttachmentError.unreadable(preferredName) }
        guard let kind = Self.sniff(data) else {
            throw NativeAttachmentError.unsupportedType(preferredName)
        }
        guard data.count <= Self.maxBytes else {
            throw NativeAttachmentError.overLimit(preferredName)
        }
        let id = UUID()
        let fileName = id.uuidString.lowercased() + "." + kind.ext
        do {
            try data.write(to: directory.appendingPathComponent(fileName), options: .atomic)
        } catch {
            throw NativeAttachmentError.unreadable(preferredName)
        }
        return MessageAttachment(
            id: id, name: preferredName, ext: kind.ext.uppercased(), isImage: true,
            fileName: fileName, mime: kind.mime, byteSize: data.count)
    }

    func save(url: URL) throws -> MessageAttachment {
        guard let data = try? Data(contentsOf: url) else {
            throw NativeAttachmentError.unreadable(url.lastPathComponent)
        }
        return try save(data: data, preferredName: url.lastPathComponent)
    }

    func url(for fileName: String) -> URL? {
        guard !fileName.isEmpty, !fileName.contains("/"), !fileName.contains("..") else { return nil }
        return directory.appendingPathComponent(fileName)
    }

    func data(for fileName: String) -> Data? {
        guard let url = url(for: fileName) else { return nil }
        return try? Data(contentsOf: url)
    }

    func image(for fileName: String) -> NSImage? {
        guard let data = data(for: fileName) else { return nil }
        return NSImage(data: data)
    }

    func requestDataURL(for attachment: MessageAttachment) -> String? {
        guard attachment.isImage, let fileName = attachment.fileName,
              let url = url(for: fileName) else { return nil }
        return ImageEncoder.dataURL(from: url)?.dataURL
    }

    func remove(_ attachment: MessageAttachment) {
        guard let fileName = attachment.fileName, let url = url(for: fileName) else { return }
        try? FileManager.default.removeItem(at: url)
    }

    static func sniff(_ data: Data) -> (ext: String, mime: String)? {
        guard data.count >= 12 else { return nil }
        let head = [UInt8](data.prefix(12))
        if head[0] == 0x89, head[1] == 0x50, head[2] == 0x4E, head[3] == 0x47 {
            return ("png", "image/png")
        }
        if head[0] == 0xFF, head[1] == 0xD8, head[2] == 0xFF {
            return ("jpg", "image/jpeg")
        }
        if head[0] == 0x47, head[1] == 0x49, head[2] == 0x46, head[3] == 0x38 {
            return ("gif", "image/gif")
        }
        if head[0] == 0x52, head[1] == 0x49, head[2] == 0x46, head[3] == 0x46,
           head[8] == 0x57, head[9] == 0x45, head[10] == 0x42, head[11] == 0x50 {
            return ("webp", "image/webp")
        }
        if head[4] == 0x66, head[5] == 0x74, head[6] == 0x79, head[7] == 0x70 {
            let brand = String(decoding: data.subdata(in: 8..<12), as: UTF8.self)
            if ["heic", "heix", "hevc", "heif", "mif1", "msf1"].contains(brand) {
                return ("heic", "image/heic")
            }
        }
        return nil
    }
}
