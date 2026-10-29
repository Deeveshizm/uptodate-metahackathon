import SwiftUI

private struct AppScrollHeaderKey: EnvironmentKey {
    static var defaultValue: AnyView? { nil }
}

private struct NavigationScrollHandlerKey: EnvironmentKey {
    static var defaultValue: (@MainActor (CGFloat) -> Void)? { nil }
}

extension EnvironmentValues {
    var appScrollHeader: AnyView? {
        get { self[AppScrollHeaderKey.self] }
        set { self[AppScrollHeaderKey.self] = newValue }
    }
    var navigationScrollHandler: (@MainActor (CGFloat) -> Void)? {
        get { self[NavigationScrollHandlerKey.self] }
        set { self[NavigationScrollHandlerKey.self] = newValue }
    }
}

private struct NavigationOffsetKey: PreferenceKey {
    static var defaultValue: CGFloat { 0 }
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}

struct AppScrollView<Content: View>: View {
    @Environment(\.appScrollHeader) private var header
    @Environment(\.navigationScrollHandler) private var reportOffset
    @Namespace private var coordinate
    var showsHeader = true
    @ViewBuilder var content: Content
    private var bottomMargin: CGFloat { showsHeader && reportOffset != nil ? 84 : 0 }

    var body: some View {
        Group {
            if #available(iOS 18, macOS 15, *) {
                ScrollView { contents }
                    .contentMargins(.bottom, bottomMargin, for: .scrollContent)
                    .onScrollGeometryChange(for: CGFloat.self) { geometry in
                        let maximum = max(0, geometry.contentSize.height - geometry.containerSize.height + geometry.contentInsets.top + geometry.contentInsets.bottom)
                        return min(maximum, max(0, geometry.contentOffset.y + geometry.contentInsets.top))
                    } action: { _, offset in
                        if showsHeader { reportOffset?(offset) }
                    }
            } else {
                GeometryReader { viewport in
                    ScrollView {
                        contents.background {
                            GeometryReader { geometry in
                                Color.clear.preference(key: NavigationOffsetKey.self,
                                    value: min(max(0, geometry.size.height - viewport.size.height + bottomMargin),
                                               max(0, -geometry.frame(in: .named(coordinate)).minY)))
                            }
                        }
                    }
                    .coordinateSpace(name: coordinate)
                    .contentMargins(.bottom, bottomMargin, for: .scrollContent)
                    .onPreferenceChange(NavigationOffsetKey.self) { offset in
                        Task { @MainActor in
                            if showsHeader { reportOffset?(offset) }
                        }
                    }
                }
            }
        }
        .onAppear { if showsHeader { reportOffset?(0) } }
    }

    private var contents: some View {
        VStack(spacing: 0) {
            if showsHeader, let header { header }
            content
        }
    }
}

struct AppSourceList<Content: View>: View {
    @Environment(\.appScrollHeader) private var header
    @Environment(\.navigationScrollHandler) private var reportOffset
    @ViewBuilder var content: Content
    var body: some View {
        Group {
            if #available(iOS 18, macOS 15, *) {
                rows.onScrollGeometryChange(for: CGFloat.self) { geometry in
                    let maximum = max(0, geometry.contentSize.height - geometry.containerSize.height + geometry.contentInsets.top + geometry.contentInsets.bottom)
                    return min(maximum, max(0, geometry.contentOffset.y + geometry.contentInsets.top))
                } action: { _, offset in reportOffset?(offset) }
            } else {
                rows
            }
        }.onAppear { reportOffset?(0) }
    }
    private var rows: some View {
        List {
            if let header {
                header.listRowInsets(EdgeInsets()).listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
            }
            content
        }
        .listStyle(.plain).scrollContentBackground(.hidden)
        .background(.background)
        .contentMargins(.bottom, 84, for: .scrollContent)
    }
}

// Reference storage keeps per-frame measurements from redrawing the app root.
final class NavigationScrollTracker {
    private var previous: CGFloat = 0
    private var travel: CGFloat = 0
    func reset() { previous = 0; travel = 0 }
    func update(_ offset: CGFloat) -> Bool? {
        let delta = offset - previous
        previous = offset
        if offset <= 12 { travel = 0; return false }
        guard abs(delta) > 0.5 else { return nil }
        if delta * travel < 0 { travel = 0 }
        travel += delta
        if offset > 72, travel > 28 { travel = 0; return true }
        if travel < -14 { travel = 0; return false }
        return nil
    }
}

struct FloatingBottomNavigation: View {
    @Binding var selection: Int
    let minimized: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        GeometryReader { geometry in
            HStack(spacing: 4) {
                tab("house", selected: "house.fill", label: "Home", index: 0)
                tab("bookmark", selected: "bookmark.fill", label: "Saved", index: 1)
                tab("newspaper", selected: "newspaper.fill", label: "Manage", index: 2)
                tab("person.crop.circle", selected: "person.crop.circle.fill", label: "Profile", index: 3)
            }
            .padding(.horizontal, minimized ? 8 : 12)
            .padding(.vertical, minimized ? 4 : 8)
            .frame(width: min(max(0, geometry.size.width - 32), minimized ? 244 : 340))
            .modifier(NavigationGlass(reduceTransparency: reduceTransparency))
            .shadow(color: .black.opacity(0.12), radius: 12, y: 4)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            .animation(reduceMotion ? nil : .spring(response: 0.32, dampingFraction: 0.86), value: minimized)
        }.frame(height: 76)
    }

    private func tab(_ symbol: String, selected: String, label: String, index: Int) -> some View {
        Button { selection = index } label: {
            Image(systemName: selection == index ? selected : symbol)
                .font(.system(size: minimized ? 20 : 22, weight: .regular))
            .frame(maxWidth: .infinity).frame(height: minimized ? 44 : 52)
            .foregroundStyle(.primary)
            .background(.primary.opacity(selection == index ? 0.09 : 0), in: Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(.plain).accessibilityLabel(label)
        .accessibilityIdentifier("navigation.\(index)")
        .accessibilityAddTraits(selection == index ? .isSelected : [])
    }
}

private struct NavigationGlass: ViewModifier {
    let reduceTransparency: Bool
    @ViewBuilder func body(content: Content) -> some View {
        if reduceTransparency {
            content.background(.background, in: Capsule())
                .overlay(Capsule().strokeBorder(.primary.opacity(0.14), lineWidth: 0.5))
        } else if #available(iOS 26, macOS 26, *) {
            content.glassEffect(.regular, in: Capsule())
        } else {
            content.background(.ultraThinMaterial, in: Capsule())
                .overlay(Capsule().strokeBorder(.primary.opacity(0.1), lineWidth: 0.5))
        }
    }
}
