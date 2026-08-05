import SwiftUI

struct NativeAdvancedControlsEditor: View {
    @EnvironmentObject var config: ConfigStore
    @EnvironmentObject var store: ChatStore
    @State private var expanded: Bool

    init(initiallyExpanded: Bool = false) {
        _expanded = State(initialValue: initiallyExpanded)
    }

    private var model: String {
        config.nativeResolved?.model
            ?? config.activeNativeProfile?.selectedModel.trimmingCharacters(in: .whitespacesAndNewlines)
            ?? ""
    }
    private var profile: ModelCapabilityProfile {
        config.nativeSettings.resolveCapabilities(model: model).profile
    }
    private var overrides: ModelParameterOverrides {
        config.activeParameterOverrides(model: model)
    }
    private var overrideCount: Int { overrides.values.count + overrides.custom.count }

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            if model.isEmpty {
                Text("Choose a model to tune its request parameters.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    .padding(.top, 8)
            } else {
                VStack(alignment: .leading, spacing: 14) {
                    Text("These settings apply to \(model) on this endpoint only. A control left at its endpoint default is never sent, so the endpoint keeps its own behavior.")
                        .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                    ForEach(
                        [ModelParameterGroup.sampling, .tokens, .reasoning, .streamingContext],
                        id: \.self
                    ) { group in
                        groupView(group)
                    }
                    providerGroup
                    traceView
                }
                .padding(.top, 8)
            }
        } label: {
            HStack(spacing: 8) {
                Text("Advanced controls").font(.system(size: 13, weight: .medium))
                if overrideCount > 0 {
                    Text("\(overrideCount) overridden")
                        .font(.system(size: 11)).foregroundStyle(Theme.accent)
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .background(Theme.accent.opacity(0.15)).clipShape(Capsule())
                }
            }
        }
    }

    @ViewBuilder private func groupView(_ group: ModelParameterGroup) -> some View {
        let declarations = ModelParameterCatalog.all.filter { $0.group == group }
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(group.label).font(.system(size: 12, weight: .semibold))
                Spacer()
                if declarations.contains(where: { overrides.values[$0.id] != nil }) {
                    Button("Reset group") {
                        config.clearParameterGroup(model: model, group: group)
                    }
                    .controlSize(.small)
                }
            }
            ForEach(declarations) { declaration in
                ModelParameterRow(
                    declaration: declaration, model: model, profile: profile,
                    value: overrides.values[declaration.id])
            }
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder private var providerGroup: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(ModelParameterGroup.provider.label).font(.system(size: 12, weight: .semibold))
                Spacer()
                if !overrides.custom.isEmpty {
                    Button("Reset group") {
                        config.clearParameterGroup(model: model, group: .provider)
                    }
                    .controlSize(.small)
                }
            }
            Text("Custom parameters travel in the request's chat template kwargs object, the escape hatch for provider-specific knobs the controls above do not model.")
                .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            if let override = config.envOverride("chat_template_kwargs") {
                Label("\(override) is set and replaces these entries for every request.",
                      systemImage: "exclamationmark.triangle")
                    .font(.system(size: 11)).foregroundStyle(.orange)
            }
            ForEach(overrides.custom) { entry in
                CustomParameterRow(
                    entry: entry, model: model,
                    siblingKeys: Set(
                        overrides.custom.filter { $0.id != entry.id }.map(\.trimmedKey)))
            }
            Button("Add parameter") {
                config.updateParameterOverrides(model: model) { $0.custom.append(.empty()) }
            }
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder private var traceView: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Last request").font(.system(size: 12, weight: .semibold))
            if let trace = store.lastRequestTrace {
                Text("\(trace.model), \(trace.timestamp.formatted(date: .omitted, time: .standard))")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                ForEach(trace.items) { item in
                    HStack(spacing: 8) {
                        Text(item.key).font(.system(size: 11, design: .monospaced))
                        Text(item.value)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.textSecondary).lineLimit(1)
                        Spacer()
                        Text(item.source).font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                        Text(item.destination)
                            .font(.system(size: 10)).foregroundStyle(Theme.textSecondary.opacity(0.7))
                    }
                }
            } else {
                Text("No native request has been sent yet in this session.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            }
        }
        .padding(10).frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface).clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct ModelParameterRow: View {
    @EnvironmentObject var config: ConfigStore
    let declaration: ModelParameterDeclaration
    let model: String
    let profile: ModelCapabilityProfile
    let value: ModelParameterValue?
    @State private var draft = ""
    @State private var entryError: String?

    private var supported: Bool { declaration.capability.supported(by: profile) }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(declaration.name).font(.system(size: 12, weight: .medium))
                Text(declaration.scope.label)
                    .font(.system(size: 9, weight: .medium)).foregroundStyle(Theme.textSecondary)
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .background(Theme.textSecondary.opacity(0.12)).clipShape(Capsule())
                Spacer()
                if !supported {
                    Text("Unavailable").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    if value != nil {
                        Button("Reset") { reset() }.controlSize(.small)
                    }
                } else if value == nil {
                    Text("Endpoint default")
                        .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                    Button("Override") { begin() }.controlSize(.small)
                } else {
                    control
                    Button("Reset") { reset() }.controlSize(.small)
                }
            }
            if !supported {
                Text(declaration.capability.requirement
                     + (value != nil ? " The saved override is ignored until the profile declares support." : ""))
                    .font(.system(size: 11)).foregroundStyle(.orange)
            }
            if let entryError {
                Text(entryError).font(.system(size: 11)).foregroundStyle(.red)
            }
        }
        .help(declaration.explanation)
        .onAppear { syncDraft() }
        .onChange(of: value) { _, _ in syncDraft() }
    }

    @ViewBuilder private var control: some View {
        switch declaration.kind {
        case .number, .integer:
            TextField("", text: $draft)
                .textFieldStyle(.roundedBorder).frame(width: 90)
                .multilineTextAlignment(.trailing)
                .onSubmit { commit() }
        case .list:
            TextField("", text: $draft)
                .textFieldStyle(.roundedBorder).frame(width: 220)
                .onSubmit { commit() }
        case .flag:
            Toggle("", isOn: Binding(
                get: {
                    if case .flag(let flag) = value { return flag }
                    return true
                },
                set: { config.setParameter(model: model, id: declaration.id, value: .flag($0)) }))
                .labelsHidden().toggleStyle(.switch).controlSize(.small)
        case .choice(let options):
            Picker("", selection: Binding(
                get: {
                    if case .choice(let selected) = value { return selected }
                    return options.first ?? ""
                },
                set: { config.setParameter(model: model, id: declaration.id, value: .choice($0)) })) {
                ForEach(options, id: \.self) { option in Text(option).tag(option) }
            }
            .labelsHidden().frame(width: 110)
        }
    }

    private func begin() {
        config.setParameter(model: model, id: declaration.id, value: declaration.seedValue)
        entryError = nil
    }

    private func reset() {
        config.clearParameter(model: model, id: declaration.id)
        entryError = nil
    }

    private func commit() {
        guard let parsed = declaration.parse(draft) else {
            entryError = "This value could not be read as \(kindLabel)."
            syncDraft()
            return
        }
        if let problem = declaration.validate(parsed) {
            entryError = problem
            syncDraft()
            return
        }
        entryError = nil
        config.setParameter(model: model, id: declaration.id, value: parsed)
    }

    private var kindLabel: String {
        switch declaration.kind {
        case .number: return "a number"
        case .integer: return "a whole number"
        case .flag: return "on or off"
        case .list: return "a comma-separated list"
        case .choice: return "one of the listed options"
        }
    }

    private func syncDraft() {
        switch value {
        case .number(let number):
            draft = number == number.rounded() ? String(Int(number)) : String(number)
        case .integer(let integer):
            draft = String(integer)
        case .list(let entries):
            draft = entries.joined(separator: ", ")
        default:
            draft = ""
        }
    }
}

private struct CustomParameterRow: View {
    @EnvironmentObject var config: ConfigStore
    let entry: CustomModelParameter
    let model: String
    let siblingKeys: Set<String>

    private var current: CustomModelParameter {
        config.activeParameterOverrides(model: model).custom.first { $0.id == entry.id } ?? entry
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 8) {
                TextField("key", text: bind(\.key))
                    .textFieldStyle(.roundedBorder).autocorrectionDisabled().frame(width: 160)
                Picker("", selection: bind(\.kind)) {
                    ForEach(CustomModelParameter.Kind.allCases, id: \.self) { kind in
                        Text(kind.label).tag(kind)
                    }
                }
                .labelsHidden().frame(width: 100)
                TextField("value", text: bind(\.raw))
                    .textFieldStyle(.roundedBorder).autocorrectionDisabled()
                    .frame(maxWidth: 220)
                Button {
                    config.updateParameterOverrides(model: model) { overrides in
                        overrides.custom.removeAll { $0.id == entry.id }
                    }
                } label: {
                    Image(systemName: "trash")
                }
                .buttonStyle(.plain).foregroundStyle(Theme.textSecondary)
            }
            if let problem = current.validationError(siblingKeys: siblingKeys) {
                Label(problem, systemImage: "exclamationmark.circle")
                    .font(.system(size: 11)).foregroundStyle(.orange)
            } else {
                Text("Sent as chat_template_kwargs.\(current.trimmedKey)")
                    .font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
            }
        }
    }

    private func bind<T>(_ keyPath: WritableKeyPath<CustomModelParameter, T>) -> Binding<T> {
        Binding(
            get: { current[keyPath: keyPath] },
            set: { value in
                config.updateParameterOverrides(model: model) { overrides in
                    guard let index = overrides.custom.firstIndex(where: { $0.id == entry.id })
                    else { return }
                    overrides.custom[index][keyPath: keyPath] = value
                }
            })
    }
}
