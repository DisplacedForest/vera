import SwiftUI
import AppKit

/// Opens the native file picker for images + documents.
enum FilePicker {
    @MainActor
    static func pick(_ completion: @escaping ([URL]) -> Void) {
        let p = NSOpenPanel()
        p.allowsMultipleSelection = true
        p.canChooseDirectories = false
        p.canChooseFiles = true
        p.message = "Add files or photos"
        completion(p.runModal() == .OK ? p.urls : [])
    }
}

/// Row of pending composer attachments (image thumbnails + file cards), remove-on-hover.
struct AttachmentsBar: View {
    let attachments: [Attachment]
    var onRemove: (UUID) -> Void
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                ForEach(attachments) { att in
                    AttachmentChipView(att: att, onRemove: { onRemove(att.id) })
                }
            }
            .padding(.top, 2).padding(.bottom, 4)
        }
    }
}

struct AttachmentChipView: View {
    @ObservedObject var att: Attachment
    var onRemove: () -> Void
    @State private var hover = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Group { if att.kind == .image { imageChip } else { fileChip } }
            if hover {
                Button(action: onRemove) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 16))
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.white, Color.black.opacity(0.65))
                }
                .buttonStyle(.plain).padding(4)
            }
        }
        .onHover { hover = $0 }
    }

    private var imageChip: some View {
        ZStack {
            if let t = att.thumbnail {
                Image(nsImage: t).resizable().aspectRatio(contentMode: .fill)
            } else {
                Skeleton()
            }
        }
        .frame(width: 76, height: 76)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }

    private var fileChip: some View {
        VStack(alignment: .leading, spacing: 6) {
            if att.status == .processing {
                Skeleton().frame(height: 11)
                Skeleton().frame(width: 92, height: 11)
                Spacer(minLength: 0)
                Skeleton().frame(width: 42, height: 14)
            } else {
                Text(att.name)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.textPrimary)
                    .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Text(att.status == .failed ? "FAILED" : att.ext)
                    .font(.system(size: 9, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
            }
        }
        .padding(10)
        .frame(width: 152, height: 76, alignment: .topLeading)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }
}

/// Read-only attachment chips shown in a sent user bubble.
struct SentAttachmentsBar: View {
    let attachments: [MessageAttachment]
    var body: some View {
        HStack(spacing: 8) {
            ForEach(attachments) { a in
                if a.isImage, let d = a.thumbnailData, let img = NSImage(data: d) {
                    Image(nsImage: img).resizable().aspectRatio(contentMode: .fill)
                        .frame(width: 64, height: 64)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.hairline, lineWidth: 1))
                } else {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(a.name).font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Theme.textPrimary).lineLimit(2)
                        Text(a.ext).font(.system(size: 8, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
                    }
                    .padding(8).frame(width: 130, height: 64, alignment: .topLeading)
                    .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.hairline, lineWidth: 1))
                }
            }
        }
    }
}

/// Animated shimmer placeholder used while an attachment is processing/uploading.
struct Skeleton: View {
    @State private var phase: CGFloat = -1
    var body: some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(Color.primary.opacity(0.08))
            .overlay(
                GeometryReader { geo in
                    LinearGradient(colors: [.clear, Color.white.opacity(0.12), .clear],
                                   startPoint: .leading, endPoint: .trailing)
                        .frame(width: geo.size.width * 0.6)
                        .offset(x: phase * geo.size.width)
                }
            )
            .clipped()
            .onAppear {
                withAnimation(.linear(duration: 1.1).repeatForever(autoreverses: false)) { phase = 1.5 }
            }
    }
}
