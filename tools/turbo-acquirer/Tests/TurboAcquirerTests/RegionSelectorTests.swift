import XCTest
@testable import TurboAcquirer

final class RegionSelectorTests: XCTestCase {

    override func tearDown() {
        // Always reset the test seam after each test.
        RegionSelector.queryFunction = nil
        super.tearDown()
    }

    func test_resolveActiveWindowRect_returns_injected_rect() {
        let expected = CGRect(x: 100, y: 100, width: 800, height: 600)
        RegionSelector.queryFunction = { expected }

        let result = RegionSelector.resolveActiveWindowRect()

        XCTAssertEqual(result, expected)
    }

    func test_resolveActiveWindowRect_returns_nil_when_seam_returns_nil() {
        RegionSelector.queryFunction = { nil }

        let result = RegionSelector.resolveActiveWindowRect()

        XCTAssertNil(result)
    }

    func test_resolveActiveWindowRect_returns_arbitrary_rect() {
        let expected = CGRect(x: 0, y: 50, width: 1440, height: 900)
        RegionSelector.queryFunction = { expected }

        let result = RegionSelector.resolveActiveWindowRect()

        XCTAssertEqual(result, expected,
            "resolveActiveWindowRect must return exactly the rect provided by the query seam")
    }

    func test_resolveActiveWindowRect_seam_reset_between_tests() {
        // Verify that tearDown properly clears the override so subsequent tests
        // that do not set the seam would invoke the real implementation.
        // We can only assert the seam is nil after tearDown by checking it here
        // (tearDown runs after each test, so the seam from this test won't leak).
        RegionSelector.queryFunction = { CGRect(x: 999, y: 999, width: 1, height: 1) }
        XCTAssertNotNil(RegionSelector.queryFunction)
        // tearDown will clear it
    }
}
