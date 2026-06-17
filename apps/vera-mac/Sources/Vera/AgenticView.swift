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

/// Live state for the Agentic tab's Activity section — vera-api's autonomous activity feed.
/// Unreachable/unconfigured states render honestly (clean status cards, never fake data).
@MainActor
final class ActivityStore: ObservableObject {
    enum Phase { case loading, unconfigured, unreachable, unsupported, ready }
    @Published var phase: Phase = .loading
    @Published var events: [ActivityEvent] = []

    private var client: ActivityClient?
    var baseDescription: String { client?.base.absoluteString ?? "vera-api" }

    func configure(base: URL?) {
        client = base.map { ActivityClient(base: $0) }
        if client == nil { phase = .unconfigured }
    }

    func refresh() async {
        guard let client else { phase = .unconfigured; return }
        switch await client.fetch() {
        case .unreachable:
            phase = .unreachable
        case .unsupported:
            phase = .unsupported
        case .ok(let events):
            self.events = events
            phase = .ready
        }
    }
}

/// Live state for the last Pulse run's structured per-item detail (`GET /pulse/run_status`),
/// used by the pulse drill-in's stage expansions. Absent or older runs leave `detail` nil so
/// the canvas degrades to "no detail recorded for this run".
@MainActor
final class PulseRunStore: ObservableObject {
    @Published var detail: PulseRunDetail?

    private var client: PulseRunClient?

    func configure(base: URL?) {
        client = base.map { PulseRunClient(base: $0) }
        if client == nil { detail = nil }
    }

    func refresh() async {
        guard let client else { detail = nil; return }
        if case .ok(let d) = await client.fetch() { detail = d } else { detail = nil }
    }
}

/// The Agentic surface. Canvas: the organism map of every autonomous flow, with
/// drill-ins and the node inspector. Activity: the reverse-chronological feed of
/// everything Vera did on her own in the last day.
struct AgenticView: View {
    @EnvironmentObject var store: ChatStore
    @EnvironmentObject var config: ConfigStore
    @StateObject private var sched = SchedulerStore()
    @StateObject private var activity = ActivityStore()
    @StateObject private var graphStore = GraphStore()
    @StateObject private var pulseRun = PulseRunStore()
    @State private var editing: SchedulerJob?

    var body: some View {
        Group {
            switch store.agenticPane {
            case .canvas:
                AgenticCanvasView(graphStore: graphStore, sched: sched, activity: activity,
                                  pulseRun: pulseRun, onEditSchedule: { editing = $0 })
            case .activity:
                AgenticActivityView(activity: activity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
        .task {
            sched.configure(base: config.resolved?.veraAPIBase)
            activity.configure(base: config.resolved?.veraAPIBase)
            graphStore.configure(base: config.resolved?.veraAPIBase)
            pulseRun.configure(base: config.resolved?.veraAPIBase)
            async let s: Void = sched.refresh()
            async let a: Void = activity.refresh()
            async let g: Void = graphStore.refresh()
            async let p: Void = pulseRun.refresh()
            _ = await (s, a, g, p)
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30 * 1_000_000_000)
                async let s2: Void = sched.refresh()
                async let a2: Void = activity.refresh()
                async let g2: Void = graphStore.refresh()
                async let p2: Void = pulseRun.refresh()
                _ = await (s2, a2, g2, p2)
            }
        }
        .sheet(item: $editing) { job in
            CronEditor(job: job) { cron in await sched.saveCron(job, cron: cron) }
        }
    }
}

/// The Activity pane: the plain reverse-chronological list of autonomous events.
struct AgenticActivityView: View {
    @ObservedObject var activity: ActivityStore

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Activity").font(.system(size: 22, weight: .bold))
                InfoTip(text: "Everything Vera did on her own in the last day: heartbeat ticks, scheduled runs, autonomous actions.", size: 13)
                if case .ready = activity.phase {
                    Text("\(activity.events.count)").font(.system(size: 13, weight: .semibold))
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
                    SectionBox(title: "Last 24 hours") {
                        activitySection
                    }
                }
                .padding(.horizontal, 28).padding(.vertical, 18)
                .frame(maxWidth: 860, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
    }

    @ViewBuilder
    private var activitySection: some View {
        switch activity.phase {
        case .loading:
            RowCard {
                ProgressView().controlSize(.small)
                Text("Loading activity…").font(.system(size: 13)).foregroundStyle(Theme.textSecondary)
            }
        case .unconfigured:
            statusCard(icon: "gearshape", title: "vera-api isn't configured",
                       note: "Set the vera-api URL in Settings to see what Vera does on her own.")
        case .unreachable:
            statusCard(icon: "exclamationmark.triangle", title: "vera-api unreachable",
                       note: "Couldn't load activity from \(activity.baseDescription).", retry: true)
        case .unsupported:
            statusCard(icon: "sparkles.rectangle.stack", title: "Activity feed not available",
                       note: "This vera-api doesn't expose the activity feed yet. Update vera-api to see autonomous activity here.",
                       retry: true)
        case .ready:
            if activity.events.isEmpty {
                statusCard(icon: "moon.zzz", title: "No autonomous activity",
                           note: "Nothing in the last 24 hours. Heartbeat ticks, scheduled runs, and autonomous actions will appear here.",
                           retry: true)
            } else {
                ForEach(activity.events.prefix(100)) { event in
                    ActivityRow(event: event)
                }
            }
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
                Button("Retry") { Task { await activity.refresh() } }
                    .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.accent)
            }
        }
    }
}

/// One autonomous-activity event: source icon, title, detail line, relative time.
struct ActivityRow: View {
    let event: ActivityEvent

    var body: some View {
        RowCard {
            Image(systemName: event.icon)
                .font(.system(size: 14))
                .foregroundStyle(event.failed ? Color(red: 0.92, green: 0.42, blue: 0.38) : Theme.textSecondary)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(event.title).font(.system(size: 13, weight: .semibold))
                    if event.failed {
                        Text("failed").font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Color(red: 0.92, green: 0.42, blue: 0.38))
                    }
                }
                if !event.detail.isEmpty {
                    Text(event.detail).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 12)
            Text(relativeTime(event.ts)).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
        }
    }
}

/// Short relative phrasing for schedule timestamps ("32 min ago", "in 4 hr").
func relativeTime(_ d: Date) -> String {
    let f = RelativeDateTimeFormatter()
    f.unitsStyle = .abbreviated
    return f.localizedString(for: d, relativeTo: Date())
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
            Text("Schedule for \(job.label)").font(.system(size: 15, weight: .semibold))
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
