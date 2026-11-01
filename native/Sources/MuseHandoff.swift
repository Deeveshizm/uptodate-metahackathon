import Foundation
#if os(iOS)
import UIKit
import UniformTypeIdentifiers
#else
import AppKit
#endif

@MainActor
enum MuseHandoff {
    static func openChat(onUnavailable: @escaping @MainActor () -> Void) {
        let url = URL(string: "https://muse.ai/chat")!
        #if os(iOS)
        UIApplication.shared.open(url, options: [.universalLinksOnly: true]) { opened in
            if !opened { onUnavailable() }
        }
        #else
        if !NSWorkspace.shared.open(url) { onUnavailable() }
        #endif
    }

    static func copyConnectionKey(_ key: String) {
        #if os(iOS)
        UIPasteboard.general.setItems([[UTType.utf8PlainText.identifier: key]],
            options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(120)])
        #else
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(key, forType: .string)
        #endif
    }
}
