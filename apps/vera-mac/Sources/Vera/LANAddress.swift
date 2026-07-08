import Foundation

/// Resolves the local IP address vera-api can reach the app on. A UDP socket "connected"
/// to the vera-api host sends nothing but makes the kernel pick the source interface it
/// would route through — that source IP is, by construction, on the same path vera-api's
/// replies take back, so it is the address to hand the integration.
enum LANAddress {
    static func reaching(host: String, port: UInt16) -> String? {
        var hints = addrinfo(ai_flags: 0, ai_family: AF_UNSPEC, ai_socktype: SOCK_DGRAM,
                             ai_protocol: IPPROTO_UDP, ai_addrlen: 0,
                             ai_canonname: nil, ai_addr: nil, ai_next: nil)
        var res: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, String(port), &hints, &res) == 0, let info = res else { return nil }
        defer { freeaddrinfo(res) }

        let fd = socket(info.pointee.ai_family, SOCK_DGRAM, IPPROTO_UDP)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        guard connect(fd, info.pointee.ai_addr, info.pointee.ai_addrlen) == 0 else { return nil }

        var local = sockaddr_storage()
        var len = socklen_t(MemoryLayout<sockaddr_storage>.size)
        let ok = withUnsafeMutablePointer(to: &local) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &len) == 0 }
        }
        guard ok else { return nil }

        var buf = [CChar](repeating: 0, count: Int(NI_MAXHOST))
        let named = withUnsafePointer(to: &local) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getnameinfo($0, len, &buf, socklen_t(buf.count), nil, 0, NI_NUMERICHOST) == 0
            }
        }
        guard named else { return nil }
        let ip = String(cString: buf)
        return ip.isEmpty || ip.hasPrefix("127.") || ip == "::1" ? nil : ip
    }

    /// The host component of a base URL string, for the reachability probe above.
    static func host(of urlString: String) -> String? {
        URLComponents(string: urlString)?.host ?? URL(string: urlString)?.host
    }

    /// The address to advertise to a vera-api at `base` so it can reach this app back.
    /// Loopback when vera-api is local; the routed LAN source IP when it is remote.
    static func selfHost(for base: URL) -> String? {
        let h = host(of: base.absoluteString) ?? "127.0.0.1"
        if h == "localhost" || h.hasPrefix("127.") { return "127.0.0.1" }
        let port = UInt16(base.port ?? (base.scheme == "https" ? 443 : 80))
        return reaching(host: h, port: port)
    }
}
