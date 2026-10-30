// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "UpToDate",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [.executable(name: "UpToDate", targets: ["UpToDate"])],
    targets: [.executableTarget(name: "UpToDate", path: "Sources", resources: [.process("Assets.xcassets"), .copy("PrivacyInfo.xcprivacy")])]
)
