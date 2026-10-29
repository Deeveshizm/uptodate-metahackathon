import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

struct PostVisualView: View {
    let post: Post
    var inset: CGFloat = 0
    var onSelect: (() -> Void)? = nil
    @State private var selected: Int? = 0
    private var selection: Int { selected ?? 0 }
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            GeometryReader { geometry in
                ScrollView(.horizontal) {
                    LazyHStack(spacing: 0) {
                        ForEach(Array(post.gallery.enumerated()), id: \.offset) { index, visual in
                            VisualPage(post: post, visual: visual, onSelect: onSelect)
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
                HStack(spacing: 5) {
                    ForEach(post.gallery.indices, id: \.self) { index in
                        Circle().fill(index == selection ? Color.primary : Color.secondary.opacity(0.3))
                            .frame(width: 5, height: 5)
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 12)
                .contentShape(Rectangle()).onTapGesture { onSelect?() }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Post images")
                .accessibilityValue("\(min(selection + 1, post.gallery.count)) of \(post.gallery.count)")
                .accessibilityIdentifier("post.gallery.page")
                .accessibilityAdjustableAction { direction in
                    withAnimation {
                        switch direction {
                        case .increment: selected = min(selection + 1, post.gallery.count - 1)
                        case .decrement: selected = max(selection - 1, 0)
                        @unknown default: break
                        }
                    }
                }
            }
            if post.gallery.indices.contains(selection) {
                let visual = post.gallery[selection]
                if visual.kind == "generated" {
                    Text("AI illustration")
                        .font(.caption2).foregroundStyle(.secondary).padding(.horizontal, inset)
                        .onTapGesture { onSelect?() }
                }
            }
        }
    }
}

private struct VisualPage: View {
    @EnvironmentObject private var model: AppModel
    let post: Post
    let visual: PostVisual
    var onSelect: (() -> Void)? = nil
    @State private var data: Data?
    @State private var finished = false
    @State private var zoomAnchor: UnitPoint = .center
    @GestureState(resetTransaction: Transaction(animation: .spring(response: 0.3, dampingFraction: 0.72))) private var zoomScale: CGFloat = 1
    var body: some View {
        Group {
            if let onSelect {
                page.onTapGesture(perform: onSelect)
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction { onSelect() }
            } else {
                page
            }
        }
        .task(id: visual.assetId) { finished = false; data = await model.visualData(post, visual: visual); finished = true }
    }
    private var page: some View {
        Group {
            if let data, let image = platformImage(data) {
                image.resizable().scaledToFit().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if !finished {
                ProgressView("Loading source visual").frame(maxWidth: .infinity, minHeight: 180)
            } else {
                Text("Image unavailable. Open the original source or reconnect to load it.")
                    .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 100)
            }
        }
        .contentShape(Rectangle())
        .scaleEffect(zoomScale, anchor: zoomAnchor)
        .simultaneousGesture(
            MagnifyGesture()
                .updating($zoomScale) { value, scale, transaction in
                    transaction.animation = nil
                    scale = min(max(CGFloat(value.magnification), 1), 4)
                }
                .onChanged { zoomAnchor = $0.startAnchor }
        )
        .accessibilityLabel(visual.caption ?? "Post image")
        .accessibilityHint(onSelect == nil ? "Pinch to zoom; release to reset" : "Open full post. Pinch to zoom; release to reset")
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
