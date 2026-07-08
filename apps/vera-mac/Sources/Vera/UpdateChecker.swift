import SwiftUI
import AppKit

/// The released-app update loop: check GitHub Releases for a newer version, download the
/// Vera.app.zip asset, validate it, and swap the installed bundle in place. Self-built copies
/// (dev version, or VERA_NO_UPDATE=1) are told to update from source instead.

enum AppVersion {
    /// The version stamped into Info.plist from the repo VERSION at package time.
    /// Unbundled runs (`swift run`, `.build` binaries) have no plist value — they are dev builds.
    static var current: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0-dev"
    }

    /// Self-built copies never offer the installer: a dev version means there is no bundle to
    /// swap, and VERA_NO_UPDATE=1 is the explicit opt-out.
    static var isSelfBuilt: Bool {
        current == "0.0.0-dev" || ProcessInfo.processInfo.environment["VERA_NO_UPDATE"] == "1"
    }
}

enum Semver {
    /// Numeric dotted-version compare; tolerates a leading "v" and ragged lengths ("1.2" == "1.2.0").
    /// Non-numeric components count as 0, so a garbage tag can never look newer than a real version.
    static func compare(_ a: String, _ b: String) -> Int {
        func parts(_ s: String) -> [Int] {
            s.trimmingCharacters(in: .whitespaces)
                .replacingOccurrences(of: "^v", with: "", options: .regularExpression)
                .split(separator: ".").map { Int($0.prefix(while: \.isNumber)) ?? 0 }
        }
        let (pa, pb) = (parts(a), parts(b))
        for i in 0..<max(pa.count, pb.count) {
            let (x, y) = (i < pa.count ? pa[i] : 0, i < pb.count ? pb[i] : 0)
            if x != y { return x < y ? -1 : 1 }
        }
        return 0
    }

    /// "0.1.0" -> 1 — used for the app/server minor-version mismatch notice.
    static func minor(_ s: String) -> Int {
        let p = s.replacingOccurrences(of: "^v", with: "", options: .regularExpression)
            .split(separator: ".")
        return p.count > 1 ? (Int(p[1].prefix(while: \.isNumber)) ?? 0) : 0
    }
}

struct ReleaseInfo: Decodable, Equatable {
    struct Asset: Decodable, Equatable {
        let name: String
        let browser_download_url: String
    }
    let tag_name: String
    let html_url: String
    let body: String?
    let assets: [Asset]

    var version: String { tag_name.hasPrefix("v") ? String(tag_name.dropFirst()) : tag_name }
    var appZip: Asset? { assets.first { $0.name == "Vera.app.zip" } }
    func asset(named: String) -> Asset? { assets.first { $0.name == named } }
}

@MainActor
final class UpdateChecker: ObservableObject {
    /// The public repo — a compile-time constant, not user config.
    static let repo = "DisplacedForest/vera"

    @Published var available: ReleaseInfo?      // set only when strictly newer than the running build
    @Published var checking = false
    @Published var installing = false
    @Published var lastResult: String?          // manual-check feedback ("Up to date", errors)

    private var timer: Timer?

    /// Launch cadence: one check now, then every 24h. Silent on failure.
    func start() {
        guard !AppVersion.isSelfBuilt else { return }
        Task { await check(manual: false) }
        timer = Timer.scheduledTimer(withTimeInterval: 24 * 3600, repeats: true) { [weak self] _ in
            Task { await self?.check(manual: false) }
        }
    }

    func check(manual: Bool) async {
        if AppVersion.isSelfBuilt {
            if manual { lastResult = "Built from source. Update with git pull." }
            return
        }
        checking = true
        defer { checking = false }
        do {
            var req = URLRequest(url: URL(string: "https://api.github.com/repos/\(Self.repo)/releases/latest")!)
            req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
            let (data, _) = try await URLSession.shared.data(for: req)
            let release = try JSONDecoder().decode(ReleaseInfo.self, from: data)
            if Semver.compare(AppVersion.current, release.version) < 0 {
                available = release
                if manual { lastResult = "Update available (\(release.tag_name))" }
            } else {
                available = nil
                if manual { lastResult = "Up to date (\(AppVersion.current))" }
            }
        } catch {
            // Network failure never interrupts the app; a manual check reports it honestly.
            if manual { lastResult = "Check failed: \(error.localizedDescription)" }
            NSLog("UpdateChecker: \(error.localizedDescription)")
        }
    }

    /// Download → unzip → validate → clear quarantine → staged swap of /Applications/Vera.app →
    /// relaunch. The swap runs in a detached shell that waits for this process to exit, so the
    /// running bundle is never replaced underneath itself.
    func install() async {
        guard let release = available, let asset = release.appZip else { return }
        installing = true
        defer { installing = false }
        do {
            let tmp = URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("vera-update-\(release.version)", isDirectory: true)
            try? FileManager.default.removeItem(at: tmp)
            try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)

            let (zipTmp, _) = try await URLSession.shared.download(from: URL(string: asset.browser_download_url)!)
            let zip = tmp.appendingPathComponent("Vera.app.zip")
            try FileManager.default.moveItem(at: zipTmp, to: zip)

            try runProcess("/usr/bin/ditto", ["-x", "-k", zip.path, tmp.path])
            let staged = tmp.appendingPathComponent("Vera.app")

            // The downloaded bundle must be the app it claims to be before it replaces anything.
            guard let plist = NSDictionary(contentsOf: staged.appendingPathComponent("Contents/Info.plist")),
                  plist["CFBundleIdentifier"] as? String == Bundle.main.bundleIdentifier ?? "app.vera.mac",
                  plist["CFBundleShortVersionString"] as? String == release.version else {
                throw UpdateError("Downloaded bundle failed validation")
            }

            // We chose this download; clear quarantine so the ad-hoc-signed bundle launches.
            try? runProcess("/usr/bin/find", [staged.path, "-exec", "/usr/bin/xattr", "-c", "{}", "+"])

            let installed = Bundle.main.bundleURL
            let script = """
            while /bin/kill -0 \(ProcessInfo.processInfo.processIdentifier) 2>/dev/null; do /bin/sleep 0.2; done
            /bin/rm -rf '\(installed.path)'
            /bin/cp -R '\(staged.path)' '\(installed.path)'
            /bin/rm -rf '\(tmp.path)'
            /usr/bin/open '\(installed.path)'
            """
            let swap = Process()
            swap.executableURL = URL(fileURLWithPath: "/bin/sh")
            swap.arguments = ["-c", script]
            try swap.run()
            NSApp.terminate(nil)
        } catch {
            // Permissions/translocation can defeat the in-place swap — hand over the artifact.
            lastResult = "Install failed: \(error.localizedDescription). Opening the download instead."
            if let url = URL(string: release.appZip?.browser_download_url ?? release.html_url) {
                NSWorkspace.shared.open(url)
            }
        }
    }

    private func runProcess(_ path: String, _ args: [String]) throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        try p.run()
        p.waitUntilExit()
        if p.terminationStatus != 0 { throw UpdateError("\(URL(fileURLWithPath: path).lastPathComponent) exited \(p.terminationStatus)") }
    }
}

struct UpdateError: LocalizedError {
    let message: String
    init(_ m: String) { message = m }
    var errorDescription: String? { message }
}

/// The sidebar update affordance — visible only when a newer release exists.
struct UpdateBanner: View {
    @EnvironmentObject var updates: UpdateChecker

    var body: some View {
        if let release = updates.available {
            HStack(spacing: 8) {
                Image(systemName: "arrow.down.circle.fill")
                    .font(.system(size: 13)).foregroundStyle(Theme.accent)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Update available (\(release.tag_name))")
                        .font(.system(size: 12, weight: .medium))
                    Button("View release notes") { openExternal(release.html_url) }
                        .buttonStyle(.plain)
                        .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                        .pointerCursor()
                }
                Spacer()
                Button(updates.installing ? "Installing…" : "Install") {
                    Task { await updates.install() }
                }
                .disabled(updates.installing)
                .controlSize(.small)
            }
            .padding(.horizontal, 10).padding(.vertical, 8)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.hairline, lineWidth: 1))
            .padding(.horizontal, 10).padding(.bottom, 6)
        }
    }
}
