import SwiftUI

struct VeinBuilderView: View {
    @ObservedObject var model: BuilderModel
    var onCreated: () -> Void = {}
    @Environment(\.dismiss) private var dismiss
    @State private var input = ""
    @State private var confirmDiscard = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(Theme.hairline)
            HStack(spacing: 0) {
                describePane.frame(width: 340)
                Divider().overlay(Theme.hairline)
                structurePane.frame(maxWidth: .infinity)
                Divider().overlay(Theme.hairline)
                previewPane.frame(width: 320)
            }
            .frame(maxHeight: .infinity)
            Divider().overlay(Theme.hairline)
            footer
        }
        .frame(width: 1000, height: 640)
        .background(Theme.bg)
        .confirmationDialog("Discard this draft?", isPresented: $confirmDiscard) {
            Button("Discard", role: .destructive) { dismiss() }
            Button("Keep editing", role: .cancel) {}
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "wand.and.stars").font(.system(size: 16)).foregroundStyle(Theme.accent)
            Text(model.isEditing ? "Edit definition" : "Build a new vein").font(.system(size: 16, weight: .semibold))
            Spacer()
        }
        .padding(16)
    }

    // MARK: Describe

    private var describePane: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if model.transcript.isEmpty {
                        Text("Describe what you want Vera to watch. She drafts a vein you can edit, test, and create.")
                            .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                    }
                    ForEach(model.transcript) { entry in transcriptRow(entry) }
                    if model.sending {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Thinking…").font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        }
                    }
                }
                .padding(14).frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider().overlay(Theme.hairline)
            HStack(spacing: 8) {
                TextField("What should Vera watch?", text: $input, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(1...4)
                    .onSubmit(sendInput)
                Button(action: sendInput) {
                    Image(systemName: "arrow.up.circle.fill").font(.system(size: 22))
                        .foregroundStyle(canSend ? Theme.accent : Theme.textSecondary.opacity(0.5))
                }
                .buttonStyle(.plain).disabled(!canSend)
            }
            .padding(12)
        }
    }

    @ViewBuilder
    private func transcriptRow(_ entry: BuilderTranscriptEntry) -> some View {
        if entry.role == "event" {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "bolt.horizontal").font(.system(size: 9)).padding(.top, 2)
                Text(entry.content).font(.system(size: 11))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .foregroundStyle(Theme.textSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 2)
        } else {
            VStack(alignment: entry.role == "user" ? .trailing : .leading, spacing: 2) {
                Text(entry.role == "user" ? "You" : "Vera")
                    .font(.system(size: 10, weight: .semibold)).foregroundStyle(Theme.textSecondary)
                Text(entry.content).font(.system(size: 13))
                    .textSelection(.enabled)
                    .padding(10)
                    .background(entry.role == "user" ? Theme.userBubble.opacity(0.18) : Theme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: entry.role == "user" ? .trailing : .leading)
        }
    }

    private var canSend: Bool {
        !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !model.sending
    }

    private var saveTitle: String {
        if model.isEditing { return model.creating ? "Saving…" : "Save changes" }
        return model.creating ? "Creating…" : "Create vein"
    }

    private func sendInput() {
        guard canSend else { return }
        model.send(input)
        input = ""
    }

    // MARK: Structure

    @ViewBuilder private var structurePane: some View {
        ScrollView {
            if let draft = model.draft {
                VStack(alignment: .leading, spacing: 18) {
                    identitySection(draft)
                    coreSection(draft)
                    scheduleSection(draft)
                    toolsSection(draft)
                    if !model.problems.isEmpty { problemsBox }
                }
                .padding(16)
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "square.dashed").font(.system(size: 26)).foregroundStyle(Theme.textSecondary.opacity(0.5))
                    Text("The draft will appear here as you describe it.")
                        .font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity).padding(.top, 80)
            }
        }
    }

    private func draftBinding<T>(_ keyPath: WritableKeyPath<VeinDraft, T>) -> Binding<T> {
        Binding(
            get: { model.draft![keyPath: keyPath] },
            set: { model.draft![keyPath: keyPath] = $0 })
    }

    private func identitySection(_ draft: VeinDraft) -> some View {
        SectionBox(title: "Identity") {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    Image(systemName: draft.icon.isEmpty ? "sparkles" : draft.icon)
                        .font(.system(size: 20)).frame(width: 28)
                        .foregroundStyle(Theme.accent)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Name").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                        TextField("Name", text: draftBinding(\.label)).textFieldStyle(.plain)
                            .font(.system(size: 14, weight: .semibold))
                    }
                }
                labeledField("Icon (SF Symbol)", draftBinding(\.icon))
                labeledField("What it watches", draftBinding(\.blurb))
            }
        }
    }

    @ViewBuilder private func coreSection(_ draft: VeinDraft) -> some View {
        if draft.hasBand {
            SectionBox(title: "Trip band") {
                HStack(spacing: 12) {
                    labeledField("Low", draftBinding(\.bandLo)).frame(maxWidth: .infinity)
                    labeledField("High", draftBinding(\.bandHi)).frame(maxWidth: .infinity)
                }
                Text("A card posts when the reading crosses either bound. Leave one blank to watch only one side.")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
        } else if draft.hasBar {
            SectionBox(title: "The bar") {
                labeledField("Keep a finding when it", draftBinding(\.judgeBar))
                Text("Vera keeps only findings that clear this bar, and writes a card for each.")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
        }
    }

    private func scheduleSection(_ draft: VeinDraft) -> some View {
        SectionBox(title: "Schedule") {
            Picker("Runs", selection: Binding(
                get: { SchedulePreset.match(draft.schedule)?.rawValue ?? "Custom schedule" },
                set: { sel in
                    if let p = SchedulePreset.allCases.first(where: { $0.rawValue == sel }) {
                        model.draft!.schedule = p.cron
                    }
                })) {
                ForEach(SchedulePreset.allCases) { Text($0.rawValue).tag($0.rawValue) }
                if SchedulePreset.match(draft.schedule) == nil {
                    Text("Custom schedule").tag("Custom schedule")
                }
            }
            .labelsHidden().pickerStyle(.menu)
        }
    }

    private func toolsSection(_ draft: VeinDraft) -> some View {
        SectionBox(title: "Tools this vein may use") {
            VStack(spacing: 8) {
                ForEach(draft.usedBlocks, id: \.self) { block in toolRow(block) }
            }
            if !model.toolsResolved {
                Text("Confirm or remove each tool before creating.")
                    .font(.system(size: 11)).foregroundStyle(.orange)
            }
        }
    }

    private func toolRow(_ block: String) -> some View {
        let state = model.confirmedTools[block]
        return HStack(spacing: 10) {
            Image(systemName: BlockFacts.icon(block)).font(.system(size: 14)).frame(width: 20)
                .foregroundStyle(Theme.textSecondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(BlockFacts.label(block)).font(.system(size: 12, weight: .medium))
                Text(BlockFacts.reach(block)).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Button("Remove") { model.confirmedTools[block] = false }
                .buttonStyle(.plain).font(.system(size: 11, weight: .medium))
                .foregroundStyle(state == false ? .red : Theme.textSecondary)
            Button(state == true ? "Confirmed" : "Confirm") { model.confirmedTools[block] = true }
                .buttonStyle(.plain).font(.system(size: 11, weight: .semibold))
                .foregroundStyle(state == true ? Color(red: 0.36, green: 0.78, blue: 0.5) : Theme.accent)
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 10))
        .opacity(state == false ? 0.5 : 1)
    }

    private var problemsBox: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(model.problems, id: \.self) { p in
                Text(p).font(.system(size: 12)).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.1)).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func labeledField(_ label: String, _ binding: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            TextField(label, text: binding, axis: .vertical).textFieldStyle(.roundedBorder)
                .autocorrectionDisabled().lineLimit(1...4)
        }
    }

    // MARK: Preview

    private var previewPane: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Preview").font(.system(size: 13, weight: .semibold))
                Spacer()
                Button(model.dryRunning ? "Running…" : "Dry run") { model.runDryRun() }
                    .disabled(model.draft == nil || model.dryRunning)
                    .font(.system(size: 12, weight: .medium))
            }
            .padding(14)
            Divider().overlay(Theme.hairline)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let err = model.error {
                        Text(err).font(.system(size: 12)).foregroundStyle(.red)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if model.dryRanOnce && model.wouldPost.isEmpty && model.error == nil {
                        Text("Nothing would post right now. That is the quiet state.")
                            .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    }
                    ForEach(model.wouldPost) { card in previewCard(card) }
                    if !model.stepTrace.isEmpty { stepTrace }
                    if !model.dryRanOnce && model.error == nil {
                        Text("Run once to see what this vein would post, before you create it.")
                            .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    }
                }
                .padding(14)
            }
        }
    }

    private func previewCard(_ card: WouldPostCard) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(card.title).font(.system(size: 13, weight: .semibold))
                .textSelection(.enabled)
            if !card.summary.isEmpty {
                Text(card.summary).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline, lineWidth: 1))
    }

    private var stepTrace: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(model.stepTrace, id: \.block) { step in
                    HStack {
                        Text(BlockFacts.label(step.block)).font(.system(size: 11))
                        Spacer()
                        Text("\(step.items)").font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
            }
            .padding(.top, 4)
        } label: {
            Text("Step trace").font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textSecondary)
        }
    }

    // MARK: Footer

    private var footer: some View {
        HStack {
            Button("Cancel") {
                if model.draft != nil { confirmDiscard = true } else { dismiss() }
            }
            .keyboardShortcut(.cancelAction)
            Spacer()
            if model.kindConflict && !model.isEditing {
                Text("That name is taken. Rename it above.").font(.system(size: 12)).foregroundStyle(.orange)
            }
            Button(saveTitle) {
                model.onCreated = { dismiss(); onCreated() }
                model.create()
            }
            .keyboardShortcut(.defaultAction)
            .disabled(!model.canCreate)
        }
        .padding(12)
    }
}
