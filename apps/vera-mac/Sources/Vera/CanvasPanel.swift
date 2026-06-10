import SwiftUI
import WebKit
import MarkdownUI

/// The Canvas — a side panel that renders/edits a Vera artifact. HTML/SVG/Mermaid get a live
/// WKWebView preview; markdown renders; code shows as source. Editable source live-updates.
struct CanvasPanel: View {
    @EnvironmentObject var store: ChatStore
    @State private var mode: Mode = .preview
    @State private var draft: String = ""
    enum Mode { case preview, source }

    private var artifact: Artifact? { store.activeArtifact }

    var body: some View {
        VStack(spacing: 0) {
            header
            Rectangle().fill(Theme.hairline).frame(height: 1)
            if let a = artifact {
                Group {
                    switch mode {
                    case .source: sourceEditor
                    case .preview: ArtifactPreview(artifact: a)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Spacer()
                Text("No artifact open").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                Spacer()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
        .onAppear { draft = artifact?.content ?? "" }
        .onChange(of: artifact?.id) { _, _ in draft = artifact?.content ?? ""; mode = .preview }
    }

    private var header: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(artifact?.title ?? "Canvas").font(.system(size: 14, weight: .semibold)).lineLimit(1)
                if let a = artifact {
                    Text(a.type == .code && !a.language.isEmpty ? a.language : a.type.rawValue)
                        .font(.system(size: 10, weight: .medium)).foregroundStyle(Theme.textSecondary)
                }
            }
            Spacer()
            if artifact != nil {
                Picker("", selection: $mode) {
                    Text("Preview").tag(Mode.preview); Text("Source").tag(Mode.source)
                }.pickerStyle(.segmented).fixedSize().labelsHidden()
                Button { copy() } label: { Image(systemName: "doc.on.doc") }.buttonStyle(.plain)
            }
            Button { store.closeCanvas() } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
        }
        .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
        // 36 top — the panel runs under the hidden title bar.
        .padding(.horizontal, 14).padding(.top, 36).padding(.bottom, 10)
    }

    private var sourceEditor: some View {
        TextEditor(text: $draft)
            .font(.system(size: 12, design: .monospaced))
            .padding(8)
            .onChange(of: draft) { _, new in store.updateActiveArtifact(content: new) }
    }

    private func copy() {
        guard let a = artifact else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(a.content, forType: .string)
    }
}

/// Renders an artifact by type. html/svg/mermaid via WKWebView; markdown rendered; code as text.
struct ArtifactPreview: View {
    let artifact: Artifact

    var body: some View {
        switch artifact.type {
        case .html:    ArtifactWebView(html: artifact.content)
        case .svg:     ArtifactWebView(html: Self.wrapSVG(artifact.content))
        case .mermaid: ArtifactWebView(html: Self.wrapMermaid(artifact.content))
        case .markdown:
            ScrollView {
                Markdown(artifact.content)
                    .markdownTextStyle { ForegroundColor(Theme.textPrimary); FontSize(14) }
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(16)
            }
        case .code, .unknown:
            ScrollView([.vertical, .horizontal]) {
                Text(artifact.content).font(.system(size: 12, design: .monospaced))
                    .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(12)
            }
        }
    }

    static func wrapSVG(_ svg: String) -> String {
        "<!doctype html><html><body style='margin:0;display:flex;align-items:center;justify-content:center;height:100vh;background:#fff'>\(svg)</body></html>"
    }

    static func wrapMermaid(_ src: String) -> String {
        let js = (try? String(contentsOf: Bundle.module.url(forResource: "mermaid.min", withExtension: "js")!, encoding: .utf8)) ?? ""
        let escaped = src.replacingOccurrences(of: "</", with: "<\\/")
        return """
        <!doctype html><html><head><meta charset="utf-8"><script>\(js)</script></head>
        <body style="margin:0;background:#fff;display:flex;justify-content:center">
        <pre class="mermaid">\(escaped)</pre>
        <script>mermaid.initialize({startOnLoad:true,theme:'default'});</script>
        </body></html>
        """
    }
}

/// Minimal WKWebView wrapper that loads an HTML string and reloads when it changes.
struct ArtifactWebView: NSViewRepresentable {
    let html: String
    func makeNSView(context: Context) -> WKWebView {
        let v = WKWebView()
        v.setValue(false, forKey: "drawsBackground")
        return v
    }
    func updateNSView(_ v: WKWebView, context: Context) {
        v.loadHTMLString(html, baseURL: nil)
    }
}
