import XCTest
@testable import TurboAcquirer

final class DHashTests: XCTestCase {

    // MARK: - Fixture builders

    /// Produces a 16×16 solid-gray pixel buffer (all bytes == value).
    private func solidBuffer(value: UInt8, size: Int = 16) -> [UInt8] {
        return [UInt8](repeating: value, count: size * size)
    }

    /// Produces a 16×16 ascending horizontal-gradient pixel buffer (left=0, right=255).
    /// Every column pair has left < right → dHash bits all 0.
    private func ascendingGradientBuffer(size: Int = 16) -> [UInt8] {
        var buf = [UInt8](repeating: 0, count: size * size)
        for row in 0..<size {
            for col in 0..<size {
                buf[row * size + col] = UInt8(col * 255 / (size - 1))
            }
        }
        return buf
    }

    /// Produces a 16×16 descending horizontal-gradient pixel buffer (left=255, right=0).
    /// Every column pair has left > right → dHash bits all 1 → hash = 0xFFFFFFFFFFFFFFFF.
    private func descendingGradientBuffer(size: Int = 16) -> [UInt8] {
        var buf = [UInt8](repeating: 0, count: size * size)
        for row in 0..<size {
            for col in 0..<size {
                buf[row * size + col] = UInt8(255 - col * 255 / (size - 1))
            }
        }
        return buf
    }

    /// Produces a 16×16 checkerboard: black (0) and white (255) alternating.
    private func checkerboardBuffer(size: Int = 16) -> [UInt8] {
        var buf = [UInt8](repeating: 0, count: size * size)
        for row in 0..<size {
            for col in 0..<size {
                buf[row * size + col] = ((row + col) % 2 == 0) ? 255 : 0
            }
        }
        return buf
    }

    // MARK: - Determinism

    func test_same_input_produces_same_hash() {
        let buf = checkerboardBuffer()
        let h1 = buf.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        let h2 = buf.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        XCTAssertEqual(h1, h2)
    }

    func test_gradient_deterministic() {
        let buf = descendingGradientBuffer()
        let h1 = buf.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        let h2 = buf.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        XCTAssertEqual(h1, h2)
    }

    // MARK: - Solid-color edge cases

    func test_solid_black_hash_is_zero() {
        // All pixels equal: no column pair has left > right → all bits 0.
        let buf = solidBuffer(value: 0)
        let h = buf.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        XCTAssertEqual(h, 0)
    }

    func test_solid_white_hash_is_zero() {
        // Same reasoning: all pixels equal.
        let buf = solidBuffer(value: 255)
        let h = buf.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        XCTAssertEqual(h, 0)
    }

    // MARK: - Visually distinct inputs produce large Hamming distance

    func test_solid_vs_descending_gradient_hamming_distance_above_threshold() {
        // Solid black → all column pairs equal → all bits 0 → hash = 0.
        // Descending gradient → left > right in every pair → all bits 1 → hash = 0xFFFF...
        // Hamming distance = 64, well above the default threshold of 12.
        let solidBlack = solidBuffer(value: 0)
        let descending = descendingGradientBuffer()

        let hSolid = solidBlack.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        let hDescending = descending.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        let dist = hammingDistance(hSolid, hDescending)
        XCTAssertGreaterThanOrEqual(dist, 20,
            "Hamming distance \(dist) between solid-black and descending gradient should be ≥ 20")
    }

    func test_checkerboard_vs_solid_hamming_distance_above_threshold() {
        let solid = solidBuffer(value: 128)
        let checker = checkerboardBuffer()

        let hSolid = solid.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        let hChecker = checker.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        let dist = hammingDistance(hSolid, hChecker)
        XCTAssertGreaterThanOrEqual(dist, 20,
            "Hamming distance \(dist) between solid and checkerboard should be ≥ 20")
    }

    // MARK: - Gradient hash values

    func test_ascending_gradient_hash_is_zero() {
        // Ascending gradient: left < right in every column pair → all bits 0 → hash = 0.
        let buf = ascendingGradientBuffer()
        let h = buf.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        XCTAssertEqual(h, 0, "Ascending gradient should hash to 0 (left < right in every pair)")
    }

    func test_descending_gradient_hash_is_all_ones() {
        // Descending gradient: left > right in every column pair → all bits 1 → hash = max UInt64.
        let buf = descendingGradientBuffer()
        let h = buf.withUnsafeBufferPointer { ptr in
            dHash(pixels: ptr.baseAddress!, width: 16, height: 16)
        }
        XCTAssertEqual(h, UInt64.max, "Descending gradient should hash to 0xFFFF... (left > right in every pair)")
    }

    // MARK: - Hamming distance helpers

    func test_hamming_distance_identical_hashes_is_zero() {
        XCTAssertEqual(hammingDistance(0xDEADBEEFCAFEBABE, 0xDEADBEEFCAFEBABE), 0)
    }

    func test_hamming_distance_inverted_hashes_is_64() {
        XCTAssertEqual(hammingDistance(0x0000000000000000, 0xFFFFFFFFFFFFFFFF), 64)
    }

    func test_hamming_distance_one_bit_differs() {
        XCTAssertEqual(hammingDistance(0b0000, 0b0001), 1)
    }

    func test_hamming_distance_symmetric() {
        let a: UInt64 = 0xABCDEF1234567890
        let b: UInt64 = 0x0FEDCBA987654321
        XCTAssertEqual(hammingDistance(a, b), hammingDistance(b, a))
    }
}
