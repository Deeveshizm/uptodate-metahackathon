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
                Text(visual.kind == "source" ? "SOURCE VISUAL" : "AI-GENERATED ILLUSTRATION")
                    .font(.caption2.bold()).foregroundStyle(.secondary).padding(.horizontal, inset)
                if let caption = visual.caption {
                    Text(caption).font(.caption).foregroundStyle(.secondary).lineLimit(3).padding(.horizontal, inset)
                }
                if let attribution = visual.attribution {
                    Text(attribution).font(.caption2).foregroundStyle(.secondary).lineLimit(2).padding(.horizontal, inset)
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
            if let data { VisualZoom(data: data, caption: visual.caption ?? "") }
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
            ScrollView { Text(caption).font(.caption).padding(.horizontal) }.frame(maxHeight: 140)
            Slider(value: $scale, in: 1...4).accessibilityLabel("Image zoom").padding()
        }.frame(minWidth: 320, minHeight: 450)
    }
}
