import SwiftUI

/// Live state for the Agentic tab's Default Schedules section — vera-api's built-in scheduler.
/// Unreachable/unconfigured states render honestly (N/A, never fake data).
@MainActor
final class SchedulerStore: ObservableObject {
    enum Phase { case loading, unconfigured, unreachable, unsupported, ready }
    @Published var phase: Phase = .loading
    @Published var masterEnabled = true
    @Published var jobs: [SchedulerJob] = []
    @Published var busy: Set<String> = []            // job ids with an in-flight PUT/POST
    @Published var rowNote: [String: String] = [:]   // job id → transient outcome note

    private var client: SchedulerClient?
    var baseDescription: String { client?.base.absoluteString ?? "vera-api" }

    func configure(base: URL?) {
        client = base.map { SchedulerClient(base: $0) }
        if client == nil { phase = .unconfigured }
    }

    func refresh() async {
        guard let client else { phase = .unconfigured; return }
        switch await client.fetch() {
        case .unreachable:
            phase = .unreachable
        case .unsupported:
            phase = .unsupported
        case .ok(let state):
            masterEnabled = state.masterEnabled
            jobs = state.jobs
            phase = .ready
        }
    }

    /// Toggle a job on/off — optimistic, reverted if the server refuses.
    func setEnabled(_ job: SchedulerJob, _ on: Bool) {
        guard let client, let i = jobs.firstIndex(where: { $0.id == job.id }) else { return }
        jobs[i].enabled = on
        busy.insert(job.id)
        Task {
            let ok = await client.update(id: job.id, enabled: on)
            if !ok, let j = jobs.firstIndex(where: { $0.id == job.id }) {
                jobs[j].enabled = !on
                rowNote[job.id] = "Change refused by the server"
            }
            busy.remove(job.id)
            await refresh()
        }
    }

    /// Persist an edited cron. Returns whether the server accepted it (the editor stays open on failure).
    func saveCron(_ job: SchedulerJob, cron: String) async -> Bool {
        guard let client else { return false }
        busy.insert(job.id)
        defer { busy.remove(job.id) }
        let ok = await client.update(id: job.id, cron: cron)
        if ok { await refresh() }
        return ok
    }

    /// Fire a job immediately.
    func runNow(_ job: SchedulerJob) {
        guard let client else { return }
        busy.insert(job.id)
        Task {
            let ok = await client.runNow(id: job.id)
            rowNote[job.id] = ok ? "Run triggered" : "Trigger refused"
            busy.remove(job.id)
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            rowNote[job.id] = nil
            await refresh()
        }
    }
}

/// The Agentic surface. First section: vera-api's default schedules — every autonomous job with
/// its cadence, last outcome, next fire, an on/off toggle, and a run-now affordance. Built as the
/// first of several sections (custom recurring tasks come later).
struct AgenticView: View {
    @EnvironmentObject var config: ConfigStore
    @StateObject private var sched = SchedulerStore()
    @State private var editing: SchedulerJob?

    var body: some View {
        VStack(spacing: 0) {
            // Header rides the same centered column as the cards so wide windows keep them aligned.
            HStack {
                Text("Agentic").font(.system(size: 22, weight: .bold))
                InfoTip(text: "What Vera runs on her own: every autonomous schedule, editable to taste.", size: 13)
                if case .ready = sched.phase {
                    Text("\(sched.jobs.count)").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Theme.surface).clipShape(Capsule())
                }
                Spacer()
            }
            .padding(.horizontal, 28).padding(.top, 36).padding(.bottom, 8)
            .frame(maxWidth: 860, alignment: .leading)
            .frame(maxWidth: .infinity)

            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    SectionBox(title: "Default Schedules") {
                        switch sched.phase {
                        case .loading:
                            RowCard {
                                ProgressView().controlSize(.small)
                                Text("Loading schedules…").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
                            }
                        case .unconfigured:
                            statusCard(icon: "gearshape", title: "vera-api isn't configured",
                                       note: "Set the vera-api URL in Settings to manage Vera's autonomous schedules.")
                        case .unreachable:
                            statusCard(icon: "exclamationmark.triangle", title: "vera-api unreachable",
                                       note: "Couldn't load schedules from \(sched.baseDescription) — last/next runs are N/A.",
                                       retry: true)
                        case .unsupported:
                            statusCard(icon: "clock.badge.exclamationmark", title: "Scheduler not available",
                                       note: "This vera-api doesn't expose the built-in scheduler yet — update vera-api to manage schedules here.",
                                       retry: true)
                        case .ready:
                            if !sched.masterEnabled { masterBanner }
                            if sched.jobs.isEmpty {
                                statusCard(icon: "clock.badge.questionmark", title: "No schedules reported",
                                           note: "The scheduler answered but listed no jobs.", retry: true)
                            }
                            ForEach(sched.jobs) { job in
                                ScheduleRow(job: job, sched: sched, onEdit: { editing = job })
                            }
                        }
                    }
                }
                .padding(.horizontal, 28).padding(.vertical, 18)
                .frame(maxWidth: 860, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
        .task {
            sched.configure(base: config.resolved?.veraAPIBase)
            await sched.refresh()
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
                await sched.refresh()
            }
        }
        .sheet(item: $editing) { job in
            CronEditor(job: job) { cron in await sched.saveCron(job, cron: cron) }
        }
    }

    private var masterBanner: some View {
        RowCard {
            Image(systemName: "pause.circle").font(.system(size: 16)).foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("Scheduler paused").font(.system(size: 13, weight: .semibold))
                Text("The server's master switch is off (SCHEDULER_ENABLED) — no jobs will fire.")
                    .font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
        }
    }

    private func statusCard(icon: String, title: String, note: String, retry: Bool = false) -> some View {
        RowCard {
            Image(systemName: icon).font(.system(size: 16)).foregroundStyle(Theme.textSecondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(note).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 0)
            if retry {
                Button("Retry") { Task { await sched.refresh() } }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.accent)
            }
        }
    }
}

/// Short relative phrasing for schedule timestamps ("32 min ago", "in 4 hr").
func relativeTime(_ d: Date) -> String {
    let f = RelativeDateTimeFormatter()
    f.unitsStyle = .abbreviated
    return f.localizedString(for: d, relativeTo: Date())
}

/// One scheduled job: status dot, label, cadence + last/next runs, run-now, edit, toggle.
/// Click the row to expand the last run's detail. Env-locked jobs render locked.
private struct ScheduleRow: View {
    let job: SchedulerJob
    @ObservedObject var sched: SchedulerStore
    var onEdit: () -> Void
    @State private var expanded = false

    private var statusColor: Color {
        guard let ok = job.lastRunOK else { return Theme.textSecondary.opacity(0.5) }
        return ok ? Color(red: 0.36, green: 0.78, blue: 0.5) : Color(red: 0.92, green: 0.42, blue: 0.38)
    }

    private var subline: String {
        var parts = [cronSummary(job.cron)]
        if let last = job.lastRunAt {
            parts.append("last \(relativeTime(last)) · \(job.lastRunOK == false ? "failed" : "ok")")
        } else {
            parts.append("never run")
        }
        if job.enabled, let next = job.nextRun { parts.append("next \(relativeTime(next))") }
        return parts.joined(separator: "  ·  ")
    }

    var body: some View {
        RowCard {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .center, spacing: 10) {
                    Circle().fill(statusColor).frame(width: 7, height: 7)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(job.label).font(.system(size: 14, weight: .semibold))
                            if job.envLocked {
                                InfoTip(text: "Fixed by the server's environment. Edit its env vars to change.", size: 10)
                            }
                            if let note = sched.rowNote[job.id] {
                                Text(note).font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.accent)
                            }
                        }
                        Text(subline).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                            .help(job.lastRunDetail.isEmpty ? "No last-run detail" : job.lastRunDetail)
                    }
                    Spacer(minLength: 12)
                    if sched.busy.contains(job.id) {
                        ProgressView().controlSize(.small)
                    } else {
                        Button { sched.runNow(job) } label: {
                            Image(systemName: "play.circle").font(.system(size: 15))
                                .foregroundStyle(Theme.textSecondary)
                        }
                        .buttonStyle(.plain).help("Run now")
                        Button(action: onEdit) {
                            Image(systemName: "pencil").font(.system(size: 13))
                                .foregroundStyle(Theme.textSecondary)
                        }
                        .buttonStyle(.plain).help("Edit schedule")
                    }
                    Toggle("", isOn: Binding(get: { job.enabled }, set: { sched.setEnabled(job, $0) }))
                        .toggleStyle(.switch).controlSize(.small).labelsHidden()
                        .tint(Theme.accent)
                        .disabled(sched.busy.contains(job.id))
                }
                if expanded {
                    Text(detailLine).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        .padding(.leading, 17)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() } }
    }

    private var detailLine: String {
        guard let last = job.lastRunAt else { return "no runs recorded" }
        let outcome = job.lastRunOK == false ? "failed" : "ok"
        let detail = job.lastRunDetail.isEmpty ? "" : ": \(job.lastRunDetail)"
        return "last run \(relativeTime(last)) \(outcome)\(detail)"
    }
}

/// Schedule editor: a simple time/interval picker with raw cron as the advanced escape hatch.
private struct CronEditor: View {
    let job: SchedulerJob
    var onSave: (String) async -> Bool
    @Environment(\.dismiss) private var dismiss

    private enum Mode: String, CaseIterable, Identifiable {
        case daily = "Daily", hours = "Hourly interval", minutes = "Minute interval", custom = "Custom cron"
        var id: String { rawValue }
    }
    @State private var mode: Mode = .daily
    @State private var time = Calendar.current.date(from: DateComponents(hour: 5, minute: 0)) ?? Date()
    @State private var everyHours = 6
    @State private var everyMinutes = 20
    @State private var custom = ""
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Schedule — \(job.label)").font(.system(size: 15, weight: .semibold))
            Picker("", selection: $mode) {
                ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented).labelsHidden()

            switch mode {
            case .daily:
                DatePicker("At", selection: $time, displayedComponents: .hourAndMinute)
                    .datePickerStyle(.field).frame(maxWidth: 180)
            case .hours:
                Stepper("Every \(everyHours) hour\(everyHours == 1 ? "" : "s")", value: $everyHours, in: 1...23)
            case .minutes:
                Stepper("Every \(everyMinutes) min", value: $everyMinutes, in: 1...59)
            case .custom:
                TextField("m h dom mon dow", text: $custom)
                    .textFieldStyle(.roundedBorder).font(.system(size: 13, design: .monospaced))
            }

            HStack(spacing: 8) {
                Image(systemName: "clock").font(.system(size: 11)).foregroundStyle(Theme.textSecondary)
                Text(cronSummary(built)).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                Spacer()
            }

            if let error {
                Text(error).font(.system(size: 12)).foregroundStyle(.red)
            }

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(saving ? "Saving…" : "Save") {
                    guard valid else { error = "Enter a 5-field cron expression"; return }
                    saving = true
                    Task {
                        let ok = await onSave(built)
                        saving = false
                        if ok { dismiss() } else { error = "The server refused the schedule" }
                    }
                }
                .keyboardShortcut(.defaultAction).disabled(saving)
            }
        }
        .padding(20).frame(width: 420)
        .onAppear { seed() }
    }

    private var built: String {
        switch mode {
        case .daily:
            let c = Calendar.current.dateComponents([.hour, .minute], from: time)
            return "\(c.minute ?? 0) \(c.hour ?? 0) * * *"
        case .hours: return "0 */\(everyHours) * * *"
        case .minutes: return "*/\(everyMinutes) * * * *"
        case .custom: return custom.trimmingCharacters(in: .whitespaces)
        }
    }

    private var valid: Bool { built.split(separator: " ").count == 5 }

    /// Pre-select the editor mode from the job's current cron.
    private func seed() {
        custom = job.cron
        let f = job.cron.split(separator: " ").map(String.init)
        guard f.count == 5 else { mode = .custom; return }
        if f[1] == "*", f[0].hasPrefix("*/"), let n = Int(f[0].dropFirst(2)),
           f[2] == "*", f[3] == "*", f[4] == "*" {
            mode = .minutes; everyMinutes = n; return
        }
        if f[1].hasPrefix("*/"), let n = Int(f[1].dropFirst(2)), Int(f[0]) != nil,
           f[2] == "*", f[3] == "*", f[4] == "*" {
            mode = .hours; everyHours = n; return
        }
        if let mm = Int(f[0]), let hh = Int(f[1]), f[2] == "*", f[3] == "*", f[4] == "*" {
            mode = .daily
            time = Calendar.current.date(from: DateComponents(hour: hh, minute: mm)) ?? time
            return
        }
        mode = .custom
    }
}
