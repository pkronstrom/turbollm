import XCTest
@testable import TurboAcquirer
import Foundation

final class SignalHandlingTests: XCTestCase {

    override func setUp() {
        super.setUp()
        // Reset stop flag before each test so tests are independent.
        resetStopFlag()
        // No-op the hard force-exit backstop for the whole test process so a
        // signal test's 3s timer can't terminate the runner (including when it
        // fires later, during another test class). Never restore _exit in tests.
        stopForceExit = {}
    }

    func test_isStopRequested_initially_false() {
        XCTAssertFalse(isStopRequested())
    }

    func test_SIGTERM_sets_stop_flag() {
        installStopSignalHandlers()
        // Let the kqueue dispatch source register: with SIG_IGN, a signal raised
        // before registration completes is ignored and lost (a test-only race;
        // production signals arrive long after setup).
        Thread.sleep(forTimeInterval: 0.1)
        kill(getpid(), SIGTERM)
        let deadline = Date().addingTimeInterval(0.5)
        while !isStopRequested() && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.005)
        }
        XCTAssertTrue(isStopRequested(), "isStopRequested() should be true shortly after SIGTERM")
    }

    func test_SIGINT_sets_stop_flag() {
        resetStopFlag()
        installStopSignalHandlers()
        Thread.sleep(forTimeInterval: 0.1)
        kill(getpid(), SIGINT)
        let deadline = Date().addingTimeInterval(0.5)
        while !isStopRequested() && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.005)
        }
        XCTAssertTrue(isStopRequested(), "isStopRequested() should be true shortly after SIGINT")
    }
}
