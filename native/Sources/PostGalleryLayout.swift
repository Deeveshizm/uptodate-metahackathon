import Foundation

enum PostGalleryLayout {
    static func aspectRatio(firstImage: Double?) -> Double {
        guard let firstImage, firstImage.isFinite, firstImage > 0 else { return 4.0 / 3.0 }
        // One frame for the whole gallery; portraits stop at four-by-five.
        return max(4.0 / 5.0, firstImage)
    }
}
