import SwiftUI

enum NewsletterGuideTarget: String, CaseIterable, Identifiable {
    case kind, details, schedule, handoff
    var id: String { rawValue }
    var title: String {
        switch self {
        case .kind: "Choose your newsletter"
        case .details: "Make it yours"
        case .schedule: "Choose when it arrives"
        case .handoff: "Finish in Muse"
        }
    }
    var detail: String {
        switch self {
        case .kind: "Pick a newsletter type and give it a name you will recognize. Each type gets a prompt suited to its content."
        case .details: "Describe what it should cover and the posts you want. For research papers, you can also choose the explanation style and supporting links."
        case .schedule: "Choose daily, weekly, every two weeks, or monthly. Add a preferred day or time if you have one."
        case .handoff: "Once the form is filled in, preview the prompt. Your newsletter address is included. Share or copy it into Muse, then finish creating the recurring task there."
        }
    }
}

struct NewsletterGuideAnchors: PreferenceKey {
    static var defaultValue: [NewsletterGuideTarget: Anchor<CGRect>] { [:] }
    static func reduce(value: inout [NewsletterGuideTarget: Anchor<CGRect>], nextValue: () -> [NewsletterGuideTarget: Anchor<CGRect>]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

extension View {
    func newsletterGuideTarget(_ target: NewsletterGuideTarget) -> some View {
        id(target).anchorPreference(key: NewsletterGuideAnchors.self, value: .bounds) { [target: $0] }
    }
}

struct NewsletterFocusGuide: View {
    let target: NewsletterGuideTarget
    let frame: CGRect
    let size: CGSize
    let onSkip: () -> Void
    let onNext: () -> Void
    @State private var cardHeight: CGFloat = 240

    private var highlight: CGRect {
        frame.insetBy(dx: -5, dy: -6).intersection(CGRect(origin: .zero, size: size))
    }
    private var cardY: CGFloat {
        if highlight.isNull || highlight.isEmpty { return size.height / 2 }
        let below = highlight.maxY + 14 + cardHeight / 2
        let above = highlight.minY - 14 - cardHeight / 2
        if below + cardHeight / 2 <= size.height - 14 { return below }
        if above - cardHeight / 2 >= 14 { return above }
        return max(cardHeight / 2 + 14, size.height - cardHeight / 2 - 14)
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Path { path in
                path.addRect(CGRect(origin: .zero, size: size))
                if !highlight.isNull { path.addRoundedRect(in: highlight, cornerSize: CGSize(width: 24, height: 24)) }
            }.fill(.black.opacity(0.48), style: FillStyle(eoFill: true))
                .contentShape(Rectangle()).onTapGesture { }
                .accessibilityHidden(true)
            if !highlight.isNull, !highlight.isEmpty {
                RoundedRectangle(cornerRadius: 24).stroke(.blue, lineWidth: 2)
                    .frame(width: highlight.width, height: highlight.height)
                    .position(x: highlight.midX, y: highlight.midY).allowsHitTesting(false)
            }
            VStack(alignment: .leading, spacing: 14) {
                Text(target.title).font(.title3.weight(.semibold))
                Text(target.detail).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 12) {
                    HStack(spacing: 4) {
                        ForEach(NewsletterGuideTarget.allCases) { step in
                            Circle().fill(step == target ? Color.primary : Color.primary.opacity(0.2)).frame(width: 4, height: 4)
                        }
                    }.accessibilityLabel("Step \(NewsletterGuideTarget.allCases.firstIndex(of: target)! + 1) of 4")
                    Spacer()
                    Button("Skip", action: onSkip).font(.subheadline)
                        .buttonStyle(.bordered).buttonBorderShape(.capsule)
                    Button(target == .handoff ? "Got it" : "Next", action: onNext)
                        .font(.subheadline.weight(.semibold)).buttonStyle(.borderedProminent)
                        .buttonBorderShape(.capsule).tint(.blue)
                        .accessibilityIdentifier("newsletter.guide.next")
                }
            }.padding(18).frame(width: min(size.width - 32, 420))
                .background(.background, in: RoundedRectangle(cornerRadius: 24))
                .overlay(RoundedRectangle(cornerRadius: 24).stroke(.primary.opacity(0.1), lineWidth: 0.5))
                .shadow(color: .black.opacity(0.12), radius: 16, y: 6)
                .onGeometryChange(for: CGFloat.self, of: { $0.size.height }) { cardHeight = $0 }
                .position(x: size.width / 2, y: cardY)
                .accessibilityElement(children: .contain)
                .accessibilityAddTraits(.isModal)
                .accessibilityIdentifier("newsletter.guide.\(target.rawValue)")
        }.frame(width: size.width, height: size.height)
    }
}
