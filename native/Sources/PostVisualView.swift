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
    @State private var aspectRatios: [String: CGFloat] = [:]
    private var selection: Int { selected ?? 0 }
    private var aspectRatio: CGFloat {
        let ratio = post.gallery.first?.assetId.flatMap { aspectRatios[$0] }
        return CGFloat(PostGalleryLayout.aspectRatio(firstImage: ratio.map(Double.init)))
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            GeometryReader { geometry in
                ScrollView(.horizontal) {
                    LazyHStack(spacing: 0) {
                        ForEach(Array(post.gallery.enumerated()), id: \.offset) { index, visual in
                            VisualPage(post: post, visual: visual, size: geometry.size, onSelect: onSelect) { ratio in
                                if let id = visual.assetId { aspectRatios[id] = ratio }
                            }
                                .frame(width: geometry.size.width, height: geometry.size.height)
                                .clipped().id(index)
                        }
                    }.scrollTargetLayout()
                }
                .scrollTargetBehavior(.paging)
                .scrollPosition(id: $selected)
                .scrollIndicators(.hidden)
                .accessibilityIdentifier("post.gallery")
            }.aspectRatio(aspectRatio, contentMode: .fit).clipped()
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
    let size: CGSize
    var onSelect: (() -> Void)? = nil
    var onAspectRatio: (CGFloat) -> Void
    @State private var data: Data?
    #if os(iOS)
    @State private var renderedImage: UIImage?
    #else
    @State private var renderedImage: Image?
    #endif
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
        .task(id: visual.assetId) {
            finished = false
            data = await model.visualData(post, visual: visual)
            renderedImage = data.flatMap(platformImage)
            if let data, let ratio = imageAspectRatio(data) { onAspectRatio(ratio) }
            finished = true
        }
    }
    private var page: some View {
        Group {
            if let image = renderedImage {
                #if os(iOS)
                PinchPanImage(image: image).frame(width: size.width, height: size.height)
                #else
                image.resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: size.width, height: size.height)
                    .clipped()
                #endif
            } else if !finished {
                ProgressView("Loading source visual").frame(maxWidth: .infinity, minHeight: 180)
            } else {
                Text("Image unavailable. Open the original source or reconnect to load it.")
                    .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 100)
            }
        }
        .contentShape(Rectangle())
        .overlay(alignment: .bottomLeading) {
            if data != nil, let id = visual.assetId, let credit = model.photoCredits[id] {
                GooglePhotoCreditView(credit: credit).padding(8)
            }
        }
        #if os(macOS)
        .scaleEffect(zoomScale, anchor: zoomAnchor)
        .simultaneousGesture(
            MagnifyGesture()
                .updating($zoomScale) { value, scale, transaction in
                    transaction.animation = nil
                    scale = min(max(CGFloat(value.magnification), 1), 4)
                }
                .onChanged { zoomAnchor = $0.startAnchor }
        )
        #endif
        .accessibilityLabel(visual.caption ?? "Post image")
        .accessibilityHint(onSelect == nil ? "Pinch to zoom, drag to move; release to reset" : "Open full post. Pinch to zoom, drag to move; release to reset")
    }
}
private struct GooglePhotoCreditView: View {
    let credit: PhotoCredit
    private func webURL(_ value: String?) -> URL? {
        guard let value, let url = URL(string: value.hasPrefix("//") ? "https:" + value : value),
              url.scheme == "https", url.host != nil else { return nil }
        return url
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            if let source = webURL(credit.sourceUrl) {
                Link("Google Maps", destination: source)
            } else { Text("Google Maps") }
            ForEach(Array(credit.authors.enumerated()), id: \.offset) { _, author in
                HStack(spacing: 4) {
                    if let avatar = webURL(author.photoUri) {
                        AsyncImage(url: avatar) { image in image.resizable().scaledToFill() }
                            placeholder: { Color.clear }
                            .frame(width: 16, height: 16).clipShape(Circle())
                    }
                    if let profile = webURL(author.uri) {
                        Link(author.displayName, destination: profile)
                    } else { Text(author.displayName) }
                }
            }
        }
        .font(.system(size: 12, weight: .regular)).foregroundStyle(.primary)
        .padding(.horizontal, 9).padding(.vertical, 6)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .contain)
    }
}
private func imageAspectRatio(_ data: Data) -> CGFloat? {
    #if os(macOS)
    guard let image = NSImage(data: data) else { return nil }
    #else
    guard let image = UIImage(data: data) else { return nil }
    #endif
    guard image.size.width > 0, image.size.height > 0 else { return nil }
    return image.size.width / image.size.height
}

#if os(iOS)
private func platformImage(_ data: Data) -> UIImage? { UIImage(data: data) }

private struct PinchPanImage: UIViewRepresentable {
    let image: UIImage

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> ZoomingImageView {
        let view = ZoomingImageView()
        view.delegate = context.coordinator
        view.minimumZoomScale = 1
        view.maximumZoomScale = 4
        view.bounces = false
        view.bouncesZoom = true
        view.showsHorizontalScrollIndicator = false
        view.showsVerticalScrollIndicator = false
        view.contentInsetAdjustmentBehavior = .never
        view.panGestureRecognizer.isEnabled = false
        view.clipsToBounds = true
        view.imageView.contentMode = .scaleAspectFit
        view.imageView.image = image
        view.addSubview(view.imageView)
        let hold = ZoomHoldGesture(target: context.coordinator, action: #selector(Coordinator.holdChanged(_:)))
        hold.cancelsTouchesInView = false
        hold.delegate = context.coordinator
        view.addGestureRecognizer(hold)
        context.coordinator.view = view
        return view
    }

    func updateUIView(_ view: ZoomingImageView, context: Context) {
        if view.imageView.image !== image {
            view.setZoomScale(1, animated: false)
            view.imageView.image = image
            view.setNeedsLayout()
        }
    }

    static func dismantleUIView(_ view: ZoomingImageView, coordinator: Coordinator) {
        coordinator.restoreScrolling()
    }

    final class Coordinator: NSObject, UIScrollViewDelegate, UIGestureRecognizerDelegate {
        weak var view: ZoomingImageView?
        private var pausedScrollViews: [UIScrollView] = []
        func viewForZooming(in scrollView: UIScrollView) -> UIView? { view?.imageView }
        func scrollViewDidZoom(_ scrollView: UIScrollView) {
            scrollView.panGestureRecognizer.isEnabled = scrollView.zoomScale > 1.001
            if scrollView.zoomScale > 1.001, pausedScrollViews.isEmpty {
                var ancestor = scrollView.superview
                while let current = ancestor {
                    if let parent = current as? UIScrollView, parent.panGestureRecognizer.isEnabled {
                        pausedScrollViews.append(parent)
                        parent.panGestureRecognizer.isEnabled = false
                    }
                    ancestor = current.superview
                }
            }
        }
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }
        @objc func holdChanged(_ gesture: ZoomHoldGesture) {
            guard gesture.state == .ended || gesture.state == .cancelled || gesture.state == .failed else { return }
            guard let view else { return }
            UIView.animate(withDuration: 0.3, delay: 0, usingSpringWithDamping: 0.8, initialSpringVelocity: 0, options: [.beginFromCurrentState, .allowUserInteraction]) {
                view.setZoomScale(1, animated: false)
                view.contentOffset = view.restingOffset
            }
            restoreScrolling()
        }
        func restoreScrolling() {
            for parent in pausedScrollViews { parent.panGestureRecognizer.isEnabled = true }
            pausedScrollViews.removeAll()
        }
    }
}

private final class ZoomingImageView: UIScrollView {
    let imageView = UIImageView()
    private var previousSize = CGSize.zero
    private var previousImage: UIImage?
    var restingOffset: CGPoint {
        CGPoint(x: max(0, (imageView.bounds.width - bounds.width) / 2),
                y: max(0, (imageView.bounds.height - bounds.height) / 2))
    }
    override func layoutSubviews() {
        super.layoutSubviews()
        guard zoomScale == 1, bounds.width > 0, bounds.height > 0,
              let image = imageView.image, image.size.width > 0, image.size.height > 0,
              previousSize != bounds.size || previousImage !== image else { return }
        previousSize = bounds.size
        previousImage = image
        let scale = max(bounds.width / image.size.width, bounds.height / image.size.height)
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        imageView.frame = CGRect(origin: .zero, size: size)
        contentSize = size
        contentOffset = restingOffset
    }
}

// Keep zoom active when one finger remains after the pinch, then reset only
// when every finger lifts. The scroll view handles focal-point zoom and panning.
private final class ZoomHoldGesture: UIGestureRecognizer {
    private var held = Set<UITouch>()
    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        held.formUnion(touches)
        if held.count >= 2, state == .possible { state = .began }
    }
    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        if state == .began || state == .changed { state = .changed }
    }
    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        held.subtract(touches)
        if held.isEmpty { state = state == .possible ? .failed : .ended }
    }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) { state = .cancelled }
    override func reset() { held.removeAll(); super.reset() }
}
#else
private func platformImage(_ data: Data) -> Image? {
    guard let image = NSImage(data: data) else { return nil }
    return Image(nsImage: image)
}
#endif
