import SwiftUI

/// A small info glyph whose explanation lives in the hover tooltip. The standard home
/// for ambient helper prose on settings surfaces; inline text stays reserved for
/// transient or load-bearing messages (errors, statuses, test results).
struct InfoTip: View {
    let text: String
    var size: CGFloat = 11
    var body: some View {
        Image(systemName: "info.circle")
            .font(.system(size: size))
            .foregroundStyle(Theme.textSecondary.opacity(0.7))
            .help(text)
    }
}

/// A titled group of rows.
struct SectionBox<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                .textCase(.uppercase)
            content
        }
    }
}

/// A single card row (HStack content).
struct RowCard<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        HStack(alignment: .center, spacing: 10) { content }
            .padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }
}

struct DiscoveredModelPicker: View {
    let title: String
    let models: [String]
    @Binding var selection: String

    private var options: [String] {
        var out = models
        if !selection.isEmpty && !out.contains(selection) { out.insert(selection, at: 0) }
        return out
    }

    var body: some View {
        Picker(title, selection: $selection) {
            Text("None").tag("")
            ForEach(options, id: \.self) { Text($0).tag($0) }
        }
    }
}
