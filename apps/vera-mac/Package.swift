// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Vera",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/socketio/socket.io-client-swift", from: "16.1.0"),
        .package(url: "https://github.com/gonzalezreal/swift-markdown-ui", from: "2.4.0"),
    ],
    targets: [
        .executableTarget(
            name: "Vera",
            dependencies: [
                .product(name: "SocketIO", package: "socket.io-client-swift"),
                .product(name: "MarkdownUI", package: "swift-markdown-ui"),
            ],
            path: "Sources/Vera",
            resources: [.process("Resources")]
        )
    ]
)
