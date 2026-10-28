import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

struct PostVisualView: View {
    let post: Post
    var inset: CGFloat = 0
    @State private var selected: Int? = 0
    private var selection: Int { selected ?? 0 }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            GeometryReader { geometry in
                ScrollView(.horizontal) {
                    LazyHStack(spacing: 0) {
                        ForEach(Array(post.gallery.enumerated()), id: \.offset) { index, visual in
                            VisualPage(post: post, visual: visual)
                                .frame(width: geometry.size.width, height: 300).id(index)
                        }
                    }.scrollTargetLayout()
                }
                .scrollTargetBehavior(.paging)
                .scrollPosition(id: $selected)
                .scrollIndicators(.hidden)
                .accessibilityIdentifier("post.gallery")
            }.frame(height: 300)
            if post.gallery.count > 1 {
                HStack(spacing: 7) {
                    ForEach(post.gallery.indices, id: \.self) { index in
                        Button { withAnimation { selected = index } } label: {
                            Circle().fill(index == selection ? Color.primary : Color.secondary.opacity(0.3))
                                .frame(width: 7, height: 7).frame(width: 28, height: 28).contentShape(Rectangle())
                        }.accessibilityLabel("Show image \(index + 1)")
                    }
                    Spacer()
                    Text("\(min(selection + 1, post.gallery.count)) / \(post.gallery.count)").font(.caption2)
                        .accessibilityIdentifier("post.gallery.page")
                }.buttonStyle(.plain).padding(.horizontal, inset)
            }
            if post.gallery.indices.contains(selection) {
                let visual = post.gallery[selection]
                if visual.kind == "generated" {
                    Text("AI illustration")
                        .font(.caption2).foregroundStyle(.secondary).padding(.horizontal, inset)
                }
            }
        }
    }
}

private struct VisualPage: View {
    @EnvironmentObject private var model: AppModel
    let post: Post
    let visual: PostVisual
    @State private var data: Data?
    @State private var zooming = false
    @State private var finished = false
    var body: some View {
        Group {
            if let data, let image = platformImage(data) {
                image.resizable().scaledToFit().frame(maxWidth: .infinity, maxHeight: .infinity)
                    .contentShape(Rectangle()).onTapGesture { zooming = true }
                    .accessibilityLabel(visual.caption ?? "Post image")
                    .accessibilityHint("Enlarge image and view credits")
                    .accessibilityAddTraits(.isButton).accessibilityAction { zooming = true }
            } else if !finished {
                ProgressView("Loading source visual").frame(maxWidth: .infinity, minHeight: 180)
            } else {
                Text("Image unavailable. Open the original source or reconnect to load it.")
                    .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 100)
            }
        }
        .task(id: visual.assetId) { finished = false; data = await model.visualData(post, visual: visual); finished = true }
        .sheet(isPresented: $zooming) {
            if let data { VisualZoom(data: data, visual: visual) }
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
    let visual: PostVisual
    @State private var scale = 1.0
    var body: some View {
        VStack {
            HStack { Text(visual.kind == "generated" ? "AI illustration" : "Image").font(.headline); Spacer(); Button("Done") { dismiss() } }.padding()
            GeometryReader { geometry in
                ScrollView([.horizontal, .vertical]) {
                    if let image = platformImage(data) {
                        image.resizable().scaledToFit().frame(width: geometry.size.width * scale)
                    }
                }
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    if let caption = visual.caption { Text(caption) }
                    if let attribution = visual.attribution { Text(attribution).foregroundStyle(.secondary) }
                    if let value = visual.sourceUrl, let url = URL(string: value), ["http", "https"].contains(url.scheme ?? "") {
                        Link("Image source", destination: url)
                    }
                }.font(.caption).padding(.horizontal).frame(maxWidth: .infinity, alignment: .leading)
            }.frame(maxHeight: 140)
            Slider(value: $scale, in: 1...4).accessibilityLabel("Image zoom").padding()
        }.frame(minWidth: 320, minHeight: 450)
    }
}
