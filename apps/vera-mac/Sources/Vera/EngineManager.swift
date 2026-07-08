import SwiftUI
import AppKit
import CryptoKit

enum EngineMode: String, CaseIterable {
    case remote, local, off
}

enum EnginePaths {
    static var home: URL { FileManager.default.homeDirectoryForCurrentUser }
    static var homePath: String { home.path }
    static var veraDir: URL { home.appendingPathComponent(".vera") }
    static var engineRoot: URL { veraDir.appendingPathComponent("engine") }
    static var dataDir: URL { veraDir.appendingPathComponent("data") }
    static var logFile: URL { engineRoot.appendingPathComponent("engine.log") }
    static var launchAgent: URL {
        home.appendingPathComponent("Library/LaunchAgents/\(EngineManager.label).plist")
    }
    static func versionDir(_ v: String) -> URL { engineRoot.appendingPathComponent(v) }
    static func binary(_ v: String) -> URL {
        versionDir(v).appendingPathComponent("vera-api/vera-api")
    }
}

@MainActor
final class EngineManager: ObservableObject {
    nonisolated static let label = "com.vera.engine"
    static let engineZipName = "vera-api-macos-arm64.zip"
    static let engineShaName = "vera-api-macos-arm64.zip.sha256"

    enum Phase: Equatable {
        case idle
        case resolving
        case downloading
        case verifying
        case unpacking
        case launching
        case healthy
        case failed(String)
    }

    @Published var phase: Phase = .idle
    @Published var installedVersion: String?
    @Published var running = false
    @Published var runDetail: String?
    @Published var busy = false

    var port: Int {
        let file = ConfigFile.read()
        if let n = file["engine_port"] as? Int { return n }
        if let s = file["engine_port"] as? String, let n = Int(s.trimmingCharacters(in: .whitespaces)) { return n }
        return 8089
    }

    var currentMode: EngineMode {
        let file = ConfigFile.read()
        if let raw = (file["engine_mode"] as? String), let m = EngineMode(rawValue: raw) { return m }
        let base = (file["vera_api_base"] as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
        return base.isEmpty ? .off : .remote
    }

    var logPathDisplay: String { "~/.vera/engine/engine.log" }
    var dataPathDisplay: String { "~/.vera/data" }

    func reconcileOnLaunch() async {
        guard currentMode == .local else { return }
        await refresh()
        if AppVersion.isSelfBuilt { return }
        if Self.needsEngineUpdate(installed: installedVersion, app: AppVersion.current) || !running {
            await installAndStart()
        }
    }

    func refresh() async {
        installedVersion = discoverInstalled()
        switch await probe(port: port) {
        case .engine(let v):
            running = true
            installedVersion = installedVersion ?? v
            if phase != .launching { phase = .healthy }
        case .conflict, .free:
            running = false
        }
        runDetail = launchdStatusDetail()
    }

    func startEngine() async { await installAndStart() }

    func stopEngine() async {
        busy = true
        defer { busy = false }
        bootout()
        running = false
        phase = .idle
        runDetail = launchdStatusDetail()
    }

    func apply(mode: EngineMode) async {
        var raw = ConfigFile.read()
        raw["engine_mode"] = mode.rawValue
        try? ConfigFile.write(raw)
        switch mode {
        case .local:
            await installAndStart()
        case .remote:
            bootout()
            running = false
            phase = .idle
        case .off:
            bootout()
            try? FileManager.default.removeItem(at: EnginePaths.launchAgent)
            running = false
            phase = .idle
        }
    }

    func removeEngineFiles() {
        bootout()
        try? FileManager.default.removeItem(at: EnginePaths.launchAgent)
        try? FileManager.default.removeItem(at: EnginePaths.engineRoot)
        installedVersion = nil
        running = false
        phase = .idle
    }

    func deleteData() {
        try? FileManager.default.removeItem(at: EnginePaths.dataDir)
    }

    func installAndStart() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }

        let target = AppVersion.current
        if AppVersion.isSelfBuilt {
            phase = .failed("Local engine needs a released app build. This copy was built from source.")
            return
        }

        switch await probe(port: port) {
        case .conflict:
            phase = .failed("Port \(port) is in use by another program. Change the port and try again.")
            running = false
            return
        case .engine(let v) where v == target:
            installedVersion = v
            running = true
            phase = .healthy
            return
        case .engine, .free:
            break
        }

        do {
            let existing = EnginePaths.binary(target)
            if !FileManager.default.fileExists(atPath: existing.path) {
                phase = .resolving
                let release = try await releaseForTag("v\(target)")
                guard let zipAsset = release.asset(named: Self.engineZipName),
                      let shaAsset = release.asset(named: Self.engineShaName) else {
                    throw EngineError("No engine download for \(release.tag_name).")
                }

                phase = .downloading
                let tmp = URL(fileURLWithPath: NSTemporaryDirectory())
                    .appendingPathComponent("vera-engine-\(target)", isDirectory: true)
                try? FileManager.default.removeItem(at: tmp)
                try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
                let (zipTmp, _) = try await URLSession.shared.download(from: URL(string: zipAsset.browser_download_url)!)
                let zip = tmp.appendingPathComponent(Self.engineZipName)
                try FileManager.default.moveItem(at: zipTmp, to: zip)
                let (shaData, _) = try await URLSession.shared.data(from: URL(string: shaAsset.browser_download_url)!)

                phase = .verifying
                guard let expected = Self.expectedSha(fromAsset: String(decoding: shaData, as: UTF8.self)) else {
                    throw EngineError("Checksum file was unreadable.")
                }
                let actual = Self.sha256Hex(try Data(contentsOf: zip))
                guard actual == expected else {
                    try? FileManager.default.removeItem(at: tmp)
                    throw EngineError("Checksum did not match. Nothing was installed.")
                }

                phase = .unpacking
                let dest = EnginePaths.versionDir(target)
                try? FileManager.default.removeItem(at: dest)
                try FileManager.default.createDirectory(at: dest, withIntermediateDirectories: true)
                try runProcess("/usr/bin/ditto", ["-x", "-k", zip.path, dest.path])
                try? runProcess("/usr/bin/find", [dest.path, "-exec", "/usr/bin/xattr", "-c", "{}", "+"])
                try? FileManager.default.removeItem(at: tmp)
                pruneOldVersions()
            }

            guard FileManager.default.fileExists(atPath: EnginePaths.binary(target).path) else {
                throw EngineError("Engine binary missing after unpack.")
            }

            phase = .launching
            try installAgent(binaryPath: EnginePaths.binary(target).path)

            let healthy = await pollHealthy(port: port, target: target)
            installedVersion = target
            if healthy {
                running = true
                phase = .healthy
            } else {
                running = false
                phase = .failed("Engine did not answer on 127.0.0.1:\(port). See the log.")
            }
            runDetail = launchdStatusDetail()
        } catch {
            phase = .failed(error.localizedDescription)
            running = false
        }
    }

    private func installAgent(binaryPath: String) throws {
        guard let template = Self.plistTemplate() else {
            throw EngineError("Engine launch template missing from the app bundle.")
        }
        let rendered = Self.renderPlist(template, home: EnginePaths.homePath,
                                        binaryPath: binaryPath, port: port)
        try FileManager.default.createDirectory(at: EnginePaths.engineRoot, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: EnginePaths.dataDir, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            at: EnginePaths.launchAgent.deletingLastPathComponent(), withIntermediateDirectories: true)
        try rendered.write(to: EnginePaths.launchAgent, atomically: true, encoding: .utf8)
        bootout()
        try runProcess("/bin/launchctl", ["bootstrap", "gui/\(getuid())", EnginePaths.launchAgent.path])
    }

    private func bootout() {
        try? runProcess("/bin/launchctl", ["bootout", "gui/\(getuid())/\(Self.label)"])
    }

    private func discoverInstalled() -> String? {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(atPath: EnginePaths.engineRoot.path) else { return nil }
        let versions = entries.filter { fm.fileExists(atPath: EnginePaths.binary($0).path) }
        return versions.sorted { Semver.compare($0, $1) > 0 }.first
    }

    private func pruneOldVersions() {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(atPath: EnginePaths.engineRoot.path) else { return }
        let dirs = entries.filter { entry in
            var isDir: ObjCBool = false
            return fm.fileExists(atPath: EnginePaths.versionDir(entry).path, isDirectory: &isDir) && isDir.boolValue
        }
        for stale in Self.versionsToPrune(dirs) {
            try? fm.removeItem(at: EnginePaths.versionDir(stale))
        }
    }

    private enum PortProbe { case free, engine(String), conflict }

    private func probe(port: Int) async -> PortProbe {
        guard let url = URL(string: "http://127.0.0.1:\(port)/version") else { return .free }
        var req = URLRequest(url: url)
        req.timeoutInterval = 3
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 200,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let v = obj["version"] as? String {
                return .engine(v)
            }
            return .conflict
        } catch {
            let ns = error as NSError
            if ns.domain == NSURLErrorDomain, ns.code == NSURLErrorCannotConnectToHost {
                return .free
            }
            return .free
        }
    }

    private func pollHealthy(port: Int, target: String) async -> Bool {
        for _ in 0..<30 {
            if case .engine(let v) = await probe(port: port), Semver.compare(v, target) == 0 { return true }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        }
        return false
    }

    private func releaseForTag(_ tag: String) async throws -> ReleaseInfo {
        var req = URLRequest(url: URL(string: "https://api.github.com/repos/\(UpdateChecker.repo)/releases/tags/\(tag)")!)
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { throw EngineError("No release \(tag) on GitHub (HTTP \(code)).") }
        return try JSONDecoder().decode(ReleaseInfo.self, from: data)
    }

    private func launchdStatusDetail() -> String? {
        guard let out = try? runProcessOutput("/bin/launchctl", ["print", "gui/\(getuid())/\(Self.label)"]) else {
            return nil
        }
        func field(_ key: String) -> String? {
            for line in out.split(separator: "\n") {
                let t = line.trimmingCharacters(in: .whitespaces)
                if t.hasPrefix(key) {
                    return t.split(separator: "=").last.map { $0.trimmingCharacters(in: .whitespaces) }
                }
            }
            return nil
        }
        let runs = field("runs =")
        let lastExit = field("last exit code =")
        if let lastExit, lastExit != "0", lastExit != "(never exited)" {
            let n = runs.map { " after \($0) runs" } ?? ""
            return "Engine last exited with code \(lastExit)\(n). See the log."
        }
        return nil
    }

    private func runProcess(_ path: String, _ args: [String]) throws {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        try p.run()
        p.waitUntilExit()
        if p.terminationStatus != 0 {
            throw EngineError("\(URL(fileURLWithPath: path).lastPathComponent) exited \(p.terminationStatus)")
        }
    }

    private func runProcessOutput(_ path: String, _ args: [String]) throws -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = Pipe()
        try p.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(decoding: data, as: UTF8.self)
    }

    static func plistTemplate() -> String? {
        guard let url = VeraResources.url("com.vera.engine.plist", ext: "template"),
              let s = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        return s
    }

    static func renderPlist(_ template: String, home: String, binaryPath: String, port: Int) -> String {
        template
            .replacingOccurrences(of: "@BINARY@", with: binaryPath)
            .replacingOccurrences(of: "@PORT@", with: String(port))
            .replacingOccurrences(of: "@HOME@", with: home)
    }

    static func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func expectedSha(fromAsset text: String) -> String? {
        let token = text.split(whereSeparator: { $0 == " " || $0 == "\n" || $0 == "\t" || $0 == "\r" }).first
        let hex = token.map { String($0).lowercased() }
        guard let hex, hex.count == 64, hex.allSatisfy({ $0.isHexDigit }) else { return nil }
        return hex
    }

    static func needsEngineUpdate(installed: String?, app: String) -> Bool {
        guard let installed else { return true }
        return Semver.compare(installed, app) != 0
    }

    static func versionsToPrune(_ versions: [String]) -> [String] {
        let sorted = versions.sorted { Semver.compare($0, $1) > 0 }
        return sorted.count <= 2 ? [] : Array(sorted[2...])
    }
}

struct EngineError: LocalizedError {
    let message: String
    init(_ m: String) { message = m }
    var errorDescription: String? { message }
}
