import SwiftUI

/// Renders a Vera structured question as tappable option cards (single- or multi-select),
/// with an "Other" free-text escape hatch. `onAnswer` is nil in render-only contexts (ShotView).
struct VeraAskCard: View {
    let message: Message
    var onAnswer: ((UUID, [String], String) -> Void)? = nil

    @State private var selected: [String] = []
    @State private var other: String = ""
    @State private var otherOpen = false

    private var ask: VeraAsk { message.ask ?? VeraAsk(question: "", options: []) }
    private var canSubmit: Bool { !selected.isEmpty || !other.trimmingCharacters(in: .whitespaces).isEmpty }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(ask.question).font(.system(size: 14, weight: .semibold))
            if ask.multiSelect {
                Text("Choose any").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }

            if message.answered {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.accent).font(.system(size: 13))
                    Text(message.answerText ?? "").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                }
            } else {
                ForEach(ask.options, id: \.label) { opt in optionRow(opt) }
                otherRow
                Button(action: submit) {
                    Text("Send").font(.system(size: 13, weight: .semibold))
                        .padding(.horizontal, 16).padding(.vertical, 7)
                        .background(canSubmit ? Theme.accent : Theme.surface)
                        .foregroundStyle(canSubmit ? .white : Theme.textSecondary)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain).disabled(!canSubmit || onAnswer == nil).padding(.top, 2)
            }
        }
        .padding(14)
        .frame(maxWidth: 520, alignment: .leading)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline, lineWidth: 1))
    }

    private func optionRow(_ opt: VeraAskOption) -> some View {
        let on = selected.contains(opt.label)
        return Button { toggle(opt.label) } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: on ? (ask.multiSelect ? "checkmark.square.fill" : "largecircle.fill.circle")
                                     : (ask.multiSelect ? "square" : "circle"))
                    .font(.system(size: 14)).foregroundStyle(on ? Theme.accent : Theme.textSecondary)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text(opt.label).font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.textPrimary)
                    if !opt.description.isEmpty {
                        Text(opt.description).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(on ? Theme.accent.opacity(0.12) : Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(on ? Theme.accent.opacity(0.5) : Color.clear, lineWidth: 1))
        }
        .buttonStyle(.plain)
    }

    private var otherRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { otherOpen.toggle() } label: {
                HStack(spacing: 8) {
                    Image(systemName: otherOpen ? "chevron.down" : "plus").font(.system(size: 11, weight: .semibold))
                    Text("Other").font(.system(size: 12, weight: .medium))
                    Spacer(minLength: 0)
                }.foregroundStyle(Theme.textSecondary)
            }.buttonStyle(.plain)
            if otherOpen {
                TextField("Type your own answer…", text: $other)
                    .textFieldStyle(.roundedBorder).font(.system(size: 12))
            }
        }
    }

    private func toggle(_ label: String) {
        if ask.multiSelect {
            if let i = selected.firstIndex(of: label) { selected.remove(at: i) } else { selected.append(label) }
        } else {
            selected = selected == [label] ? [] : [label]
        }
    }

    private func submit() {
        onAnswer?(message.id, selected, other)
    }
}
