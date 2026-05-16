import Foundation

// MARK: - dHash perceptual hash

/// Computes a 64-bit difference hash (dHash) from a 16×16 grayscale pixel buffer.
///
/// The hash is built by comparing adjacent column pairs in each row of an 8×8
/// grid derived from the input. Each of the 64 comparisons contributes one bit:
/// 1 if the left pixel is greater than the right, 0 otherwise.
///
/// - Parameters:
///   - pixels: Pointer to `width * height` grayscale byte values (0–255).
///   - width: Width of the pixel buffer (must be ≥ 9 to produce a useful hash).
///   - height: Height of the pixel buffer (must be ≥ 8 to produce a useful hash).
/// - Returns: A 64-bit hash value. Deterministic: same input → same output.
func dHash(pixels: UnsafePointer<UInt8>, width: Int, height: Int) -> UInt64 {
    // We sample an 8×8 grid of column-pair comparisons.
    // Row step: height / 8, column step: width / 9 (9 columns → 8 pairs per row).
    let rowStep = max(1, height / 8)
    let colStep = max(1, width / 9)

    var hash: UInt64 = 0
    for row in 0..<8 {
        let y = row * rowStep
        for col in 0..<8 {
            let xLeft  = col * colStep
            let xRight = (col + 1) * colStep
            let left  = pixels[y * width + xLeft]
            let right = pixels[y * width + xRight]
            hash = (hash << 1) | (left > right ? 1 : 0)
        }
    }
    return hash
}

/// Returns the number of bit positions at which two hashes differ.
func hammingDistance(_ a: UInt64, _ b: UInt64) -> Int {
    return (a ^ b).nonzeroBitCount
}
