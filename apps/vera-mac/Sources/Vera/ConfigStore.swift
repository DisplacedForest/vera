import SwiftUI

/// Reads and writes `~/.vera/config.json` — the single on-disk config the app and its clients
/// share. Values are flat string keys; unknown keys in the file are preserved on save.
/// Environment variables always win over file values at resolve time (the file is the editable
/// layer underneath).
enum ConfigFile {
    /// `~/.vera/config.json`, or `$VERA_CONFIG_DIR/config.json` when set — the override
    /// supports test sandboxes and non-standard layouts without touching the real config.
    static var defaultURL: URL {
        if let dir = ProcessInfo.processInfo.environment["VERA_CONFIG_DIR"], !dir.isEmpty {
            return URL(fileURLWithPath: dir).appendingPathComponent("config.json")
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".vera/config.json")
    }

    static func read(at url: URL = defaultURL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return obj
    }

    /// Atomic write, creating the parent directory if needed.
    static func write(_ raw: [String: Any], at url: URL = defaultURL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        let data = try JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url, options: .atomic)
    }
}

/// The editable app config, backed by `~/.vera/config.json`. Settings and onboarding write through
/// this; `OWUIConfig.load()` resolves env-over-file on top of the same file.
@MainActor
final class ConfigStore: ObservableObject {
    /// Raw file contents. String values are editable in Settings; unknown keys are preserved.
    @Published private(set) var raw: [String: Any]
    /// Drives the first-run sheet — true when no usable OWUI config exists.
    @Published var showOnboarding: Bool

    /// THE `~/.vera/config.json` key ↔ environment-variable mapping (env wins at resolve
    /// time). Canonical names match the backend's convention (`OWUI_KEY`); deprecated
    /// aliases keep working for one release.
    static let envNames: [String: String] = [
        "base": "OWUI_BASE", "api_key": "OWUI_KEY", "model": "VERA_MODEL",
        "completions_url": "VERA_COMPLETIONS_URL", "voice_base": "VERA_VOICE_BASE",
        "vera_api_base": "VERA_API_BASE", "owui_email": "OWUI_EMAIL",
        "owui_password": "OWUI_PASSWORD", "owner_name": "VERA_OWNER_NAME",
        "chat_template_kwargs": "VERA_CHAT_TEMPLATE_KWARGS",
    ]
    /// canonical env name → deprecated alias still honored this release.
    static let envAliases: [String: String] = ["OWUI_KEY": "OWUI_API_KEY"]

    init() {
        raw = ConfigFile.read()
        showOnboarding = OWUIConfig.load() == nil
    }

    subscript(_ key: String) -> String {
        get { (raw[key] as? String) ?? "" }
        set {
            if newValue.isEmpty { raw.removeValue(forKey: key) } else { raw[key] = newValue }
        }
    }

    /// A SwiftUI binding onto one file key (edits are in-memory until `save()`).
    func binding(_ key: String) -> Binding<String> {
        Binding(get: { self[key] }, set: { self[key] = $0 })
    }

    /// The active environment override for a key, if one is set (the field is then read-only).
    func envOverride(_ key: String) -> String? {
        guard let name = Self.envNames[key] else { return nil }
        let env = ProcessInfo.processInfo.environment
        if let v = env[name], !v.isEmpty { return name }
        if let old = Self.envAliases[name], let v = env[old], !v.isEmpty { return old }
        return nil
    }

    /// Persist the current values to disk.
    func save() throws {
        try ConfigFile.write(raw)
    }

    /// The fully resolved connection config (env over file). Nil until OWUI base + key exist.
    var resolved: OWUIConfig? { OWUIConfig.load() }

    /// The person's name — drives the greeting and the sidebar chip. Nil when unset.
    var ownerName: String? {
        let env = ProcessInfo.processInfo.environment["VERA_OWNER_NAME"]
        let v = (env?.isEmpty == false ? env : nil) ?? (raw["owner_name"] as? String)
        let trimmed = v?.trimmingCharacters(in: .whitespaces)
        return (trimmed?.isEmpty == false) ? trimmed : nil
    }
}

/// One-shot connectivity checks used by Settings and onboarding. Each returns a short
/// human-readable success line or throws with a human-readable failure.
enum ConnectionTest {
    enum TestError: Error, LocalizedError {
        case message(String)
        var errorDescription: String? { switch self { case .message(let m): return m } }
    }

    /// Exercise an OWUI sign-in (the credential path the live socket uses).
    static func owui(base: String, email: String, password: String) async throws -> String {
        guard let url = URL(string: base), url.scheme != nil else {
            throw TestError.message("Enter a valid OWUI URL first")
        }
        guard !email.isEmpty, !password.isEmpty else {
            throw TestError.message("Enter the OWUI email and password")
        }
        var req = URLRequest(url: url.appendingPathComponent("api/v1/auths/signin"))
        req.httpMethod = "POST"
        req.timeoutInterval = 8
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["email": email, "password": password])
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              obj["token"] is String else {
            throw TestError.message("Sign-in failed (HTTP \(code))")
        }
        let name = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["name"] as? String
        return "Signed in" + (name.map { " as \($0)" } ?? "")
    }

    /// Probe a service health endpoint — any 2xx counts as alive.
    static func http(base: String, path: String, label: String) async throws -> String {
        guard let url = URL(string: base)?.appendingPathComponent(path), url.scheme != nil else {
            throw TestError.message("Enter a valid \(label) URL first")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 6
        let (_, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw TestError.message("\(label) answered HTTP \(code)")
        }
        return "\(label) is reachable"
    }

    /// Probe the voice host by opening (and closing) a Wyoming ASR connection.
    static func voice(base: String) async throws -> String {
        guard let url = URL(string: base), url.host != nil else {
            throw TestError.message("Enter a valid voice URL first")
        }
        guard await VoiceClient(base: url).health() else {
            throw TestError.message("Voice host not answering on the Wyoming port")
        }
        return "Voice service is reachable"
    }
}
