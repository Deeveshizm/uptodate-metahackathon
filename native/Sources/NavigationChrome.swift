import SwiftUI
#if os(iOS)
import UIKit
#endif

private struct AppScrollHeaderKey: EnvironmentKey {
    static var defaultValue: AnyView? { nil }
}

extension EnvironmentValues {
    var appScrollHeader: AnyView? {
        get { self[AppScrollHeaderKey.self] }
        set { self[AppScrollHeaderKey.self] = newValue }
    }
}

struct AppScrollView<Content: View>: View {
    @Environment(\.appScrollHeader) private var header
    var showsHeader = true
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                if showsHeader, let header { header }
                content
            }
        }
        .contentMargins(.bottom, 12, for: .scrollContent)
    }
}

struct AppSourceList<Content: View>: View {
    @Environment(\.appScrollHeader) private var header
    @ViewBuilder var content: Content

    var body: some View {
        List {
            if let header {
                header.listRowInsets(EdgeInsets()).listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
            }
            content
        }
        .listStyle(.plain).scrollContentBackground(.hidden)
        .background(.background)
        .contentMargins(.bottom, 12, for: .scrollContent)
    }
}

#if os(iOS)
// UIKit owns tab hit testing, transitions, glass rendering and safe-area layout.
// Its delegate also reports reselection, which keeps Home-to-top reliable.
struct NativeTabNavigation: UIViewControllerRepresentable {
    @Binding var selection: Int
    @Environment(\.colorScheme) private var colorScheme
    let roots: [AnyView]
    let avatarData: Data?
    let onHome: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIViewController(context: Context) -> UITabBarController {
        let controller = UITabBarController()
        controller.overrideUserInterfaceStyle = colorScheme == .dark ? .dark : .light
        context.coordinator.hosts = roots.map { UIHostingController(rootView: $0) }
        let items = [("Home", "house", "house.fill"), ("Saved", "bookmark", "bookmark.fill"),
                     ("Manage", "newspaper", "newspaper.fill"), ("Profile", "person.crop.circle", "person.crop.circle.fill")]
        for (index, host) in context.coordinator.hosts.enumerated() {
            let item = items[index]
            host.tabBarItem = UITabBarItem(title: nil, image: UIImage(systemName: item.1),
                                           selectedImage: UIImage(systemName: item.2))
            host.tabBarItem.accessibilityLabel = item.0
            host.tabBarItem.accessibilityIdentifier = "navigation.\(index)"
            host.view.backgroundColor = .systemBackground
        }
        controller.setViewControllers(context.coordinator.hosts, animated: false)
        controller.selectedIndex = min(selection, 3)
        controller.delegate = context.coordinator
        controller.tabBar.tintColor = .label
        if #available(iOS 26, *) { controller.tabBarMinimizeBehavior = .onScrollDown }
        context.coordinator.updateAvatar(avatarData)
        return controller
    }

    func updateUIViewController(_ controller: UITabBarController, context: Context) {
        context.coordinator.parent = self
        controller.overrideUserInterfaceStyle = colorScheme == .dark ? .dark : .light
        for (index, host) in context.coordinator.hosts.enumerated() {
            host.rootView = roots[index]
        }
        let tab = min(selection, 3)
        if controller.selectedIndex != tab { controller.selectedIndex = tab }
        context.coordinator.updateAvatar(avatarData)
    }

    @MainActor final class Coordinator: NSObject, UITabBarControllerDelegate {
        var parent: NativeTabNavigation
        var hosts: [UIHostingController<AnyView>] = []
        private var lastAvatar: Data?

        init(_ parent: NativeTabNavigation) { self.parent = parent }

        func tabBarController(_ tabBarController: UITabBarController, didSelect viewController: UIViewController) {
            let index = tabBarController.selectedIndex
            if index == 0 { parent.onHome() }
            else { parent.selection = index }
        }

        func updateAvatar(_ data: Data?) {
            guard hosts.count == 4, data != lastAvatar else { return }
            lastAvatar = data
            let item = hosts[3].tabBarItem!
            guard let data, let photo = UIImage(data: data), photo.size.width > 0, photo.size.height > 0 else {
                item.image = UIImage(systemName: "person.crop.circle")
                item.selectedImage = UIImage(systemName: "person.crop.circle.fill")
                return
            }
            let size = CGSize(width: 28, height: 28)
            let image = UIGraphicsImageRenderer(size: size).image { _ in
                UIBezierPath(ovalIn: CGRect(origin: .zero, size: size)).addClip()
                let scale = max(size.width / photo.size.width, size.height / photo.size.height)
                let drawn = CGSize(width: photo.size.width * scale, height: photo.size.height * scale)
                photo.draw(in: CGRect(x: (size.width - drawn.width) / 2, y: (size.height - drawn.height) / 2,
                                      width: drawn.width, height: drawn.height))
            }.withRenderingMode(.alwaysOriginal)
            item.image = image
            item.selectedImage = image
        }
    }
}
#endif
