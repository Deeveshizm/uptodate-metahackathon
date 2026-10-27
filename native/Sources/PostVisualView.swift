import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

struct PostVisualView: View {
    @EnvironmentObject private var model: AppModel
    let post: Post
    @State private var data: Data?
    @State private var zooming = false
    @State private var finished = false
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let data, let image = platformImage(data) {
                image.resizable().scaledToFit()
                    .frame(maxWidth: .infinity)
                    .onTapGesture { zooming = true }
                    .accessibilityLabel(post.visual?.caption ?? "Post illustration")
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction { zooming = true }
            } else if !finished {
                ProgressView("Loading source visual").frame(maxWidth: .infinity, minHeight: 180)
            } else {
                Text("Image unavailable. Open the original source or reconnect to load it.")
                    .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 100)
            }
            Text(post.visual?.kind == "source" ? "SOURCE VISUAL" : "AI-GENERATED ILLUSTRATION")
                .font(.caption2.bold()).foregroundStyle(.secondary)
            if let caption = post.visual?.caption { Text(caption).font(.caption).foregroundStyle(.secondary) }
            if let attribution = post.visual?.attribution { Text(attribution).font(.caption2).foregroundStyle(.secondary) }
        }
        .task(id: post.visual?.assetId) { finished = false; data = await model.visualData(post); finished = true }
        .sheet(isPresented: $zooming) {
            if let data { VisualZoom(data: data, caption: post.visual?.caption ?? "") }
        }
    }
}

private func platformImage(_ data: Data) -> Image? {
    #if os(macOS)
    guard let image = NSImage(data: data) else { return nil }
    return Image(nsImage: image)
    #else
    guard let image = UIImage(data: data) else { return nil }
    return Image(uiImage: image)
    #endif
}

private struct VisualZoom: View {
    @Environment(\.dismiss) private var dismiss
    let data: Data
    let caption: String
    @State private var scale = 1.0
    var body: some View {
        VStack {
            HStack { Text("Source / illustration").font(.headline); Spacer(); Button("Done") { dismiss() } }.padding()
            GeometryReader { geometry in
                ScrollView([.horizontal, .vertical]) {
                    if let image = platformImage(data) {
                        image.resizable().scaledToFit().frame(width: geometry.size.width * scale)
                    }
                }
            }
            Text(caption).font(.caption).padding(.horizontal)
            Slider(value: $scale, in: 1...4).accessibilityLabel("Image zoom").padding()
        }.frame(minWidth: 320, minHeight: 450)
    }
}
