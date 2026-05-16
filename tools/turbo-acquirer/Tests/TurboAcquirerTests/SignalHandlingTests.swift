import XCTest
@testable import TurboAcquirer
import Foundation

final class SignalHandlingTests: XCTestCase {

    override func setUp() {
        super.setUp()
        // Reset stop flag before each test so tests are independent.
        resetStopFlag()
    }

    func test_isStopRequested_initially_false() {
        XCTAssertFalse(isStopRequested())
    }

    func test_SIGTERM_sets_stop_flag() {
        installStopSignalHandlers()
        raise(SIGTERM)
        // Poll for up to 200 ms.
        let deadline = Date().addingTimeInterval(0.2)
        while !isStopRequested() && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.005)
        }
        XCTAssertTrue(isStopRequested(), "isStopRequested() should be true within 200 ms of SIGTERM")
    }

    func test_SIGINT_sets_stop_flag() {
        resetStopFlag()
        installStopSignalHandlers()
        raise(SIGINT)
        let deadline = Date().addingTimeInterval(0.2)
        while !isStopRequested() && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.005)
        }
        XCTAssertTrue(isStopRequested(), "isStopRequested() should be true within 200 ms of SIGINT")
    }
}
