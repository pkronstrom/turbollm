// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "TurboAcquirer",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "turbo-acquirer", targets: ["TurboAcquirer"]),
    ],
    targets: [
        .executableTarget(
            name: "TurboAcquirer",
            path: "Sources/TurboAcquirer"
        ),
        .testTarget(
            name: "TurboAcquirerTests",
            dependencies: ["TurboAcquirer"],
            path: "Tests/TurboAcquirerTests"
        ),
    ]
)
