import XCTest
@testable import TurboHUD

final class AcquirerTypesTests: XCTestCase {

    func test_acquirerError_cases_are_distinct() {
        // Ensure userCancelled and emptyOutput can be pattern-matched distinctly.
        let cancelled = AcquirerError.userCancelled
        let empty = AcquirerError.emptyOutput

        var matchedCancelled = false
        var matchedEmpty = false

        if case .userCancelled = cancelled { matchedCancelled = true }
        if case .emptyOutput = empty { matchedEmpty = true }

        XCTAssertTrue(matchedCancelled, "AcquirerError.userCancelled must be a distinct case")
        XCTAssertTrue(matchedEmpty, "AcquirerError.emptyOutput must be a distinct case")

        // Confirm they are not the same case
        if case .userCancelled = empty {
            XCTFail("emptyOutput should not match userCancelled")
        }
        if case .emptyOutput = cancelled {
            XCTFail("userCancelled should not match emptyOutput")
        }
    }

    func test_acquirerMode_primary_not_equal_background() {
        let primary = AcquirerMode.primary
        let background = AcquirerMode.background

        var matchedPrimary = false
        var matchedBackground = false

        if case .primary = primary { matchedPrimary = true }
        if case .background = background { matchedBackground = true }

        XCTAssertTrue(matchedPrimary)
        XCTAssertTrue(matchedBackground)

        // primary should not match background
        if case .background = primary {
            XCTFail("primary should not match background")
        }
    }

    func test_acquirerResult_is_equatable() {
        let r1 = AcquirerResult(paramName: "audio", value: "/tmp/out.wav", phase: nil)
        let r2 = AcquirerResult(paramName: "audio", value: "/tmp/out.wav", phase: nil)
        let r3 = AcquirerResult(paramName: "audio", value: "/tmp/other.wav", phase: nil)

        XCTAssertEqual(r1, r2)
        XCTAssertNotEqual(r1, r3)
    }

    func test_acquirerResult_phase_participates_in_equality() {
        let r1 = AcquirerResult(paramName: "x", value: "v", phase: "Recording 0:05")
        let r2 = AcquirerResult(paramName: "x", value: "v", phase: nil)

        XCTAssertNotEqual(r1, r2)
    }
}
