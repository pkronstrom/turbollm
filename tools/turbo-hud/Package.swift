// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "TurboHUD",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "TurboHUD",
            path: "Sources/TurboHUD"
        ),
        .testTarget(
            name: "TurboHUDTests",
            dependencies: ["TurboHUD"],
            path: "Tests/TurboHUDTests"
        ),
    ]
)
