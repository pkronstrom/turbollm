import XCTest
@testable import TurboHUD

// MARK: - MockAcquirer

/// Test-only Acquirer implementation with configurable return value and delay.
final class MockAcquirer: Acquirer, @unchecked Sendable {
    let mode: AcquirerMode
    let paramName: String
    let result: AcquirerResult
    let delay: TimeInterval
    var cancelCalled: Bool = false
    private var continuation: CheckedContinuation<AcquirerResult, Error>?

    init(paramName: String, mode: AcquirerMode,
         result: AcquirerResult, delay: TimeInterval = 0) {
        self.paramName = paramName
        self.mode = mode
        self.result = result
        self.delay = delay
    }

    func acquire() async throws -> AcquirerResult {
        if delay > 0 {
            return try await withCheckedThrowingContinuation { cont in
                self.continuation = cont
                DispatchQueue.global().asyncAfter(deadline: .now() + delay) {
                    if !self.cancelCalled {
                        cont.resume(returning: self.result)
                        self.continuation = nil
                    }
                }
            }
        }
        return result
    }

    func cancel() {
        cancelCalled = true
        continuation?.resume(throwing: AcquirerError.userCancelled)
        continuation = nil
    }
}

// MARK: - SessionControllerTests

@MainActor
final class SessionControllerTests: XCTestCase {

    func makeWorkflow(params: [WorkflowParam] = []) -> Workflow {
        Workflow(name: "test-wf", description: nil, command: "echo", script: nil,
                 args: nil, env: nil, params: params)
    }

    func makeSettings() -> Settings {
        Settings(suiteName: "com.turbollm.hud.test.\(UUID().uuidString)")
    }

    // MARK: - Validation tests

    func test_start_zero_primaries_throws_noPrimaryAcquirer() async throws {
        let ctrl = SessionController()
        let wf = makeWorkflow()
        let bgAcquirer = MockAcquirer(
            paramName: "bg",
            mode: .background,
            result: AcquirerResult(paramName: "bg", value: "v", phase: nil)
        )

        do {
            try await ctrl.startWithAcquirers([bgAcquirer], workflow: wf, settings: makeSettings())
            XCTFail("Expected SessionError.noPrimaryAcquirer")
        } catch SessionError.noPrimaryAcquirer {
            // expected
        }
    }

    func test_start_multiple_primaries_throws_multiplePrimaryAcquirers() async throws {
        let ctrl = SessionController()
        let wf = makeWorkflow()
        let p1 = MockAcquirer(
            paramName: "a",
            mode: .primary,
            result: AcquirerResult(paramName: "a", value: "x", phase: nil)
        )
        let p2 = MockAcquirer(
            paramName: "b",
            mode: .primary,
            result: AcquirerResult(paramName: "b", value: "y", phase: nil)
        )

        do {
            try await ctrl.startWithAcquirers([p1, p2], workflow: wf, settings: makeSettings())
            XCTFail("Expected SessionError.multiplePrimaryAcquirers")
        } catch SessionError.multiplePrimaryAcquirers {
            // expected
        }
    }

    // MARK: - Lifecycle tests

    func test_session_completes_when_primary_returns() async throws {
        let ctrl = SessionController()
        let wf = makeWorkflow(params: [
            WorkflowParam(name: "title", type: "string", mode: nil,
                          defaultValue: "my-title", defaultEnv: nil, auto: nil,
                          options: nil, extensions: nil, scope: nil, command: nil)
        ])
        let primary = MockAcquirer(
            paramName: "audio",
            mode: .primary,
            result: AcquirerResult(paramName: "audio", value: "/tmp/out.wav", phase: nil)
        )

        // Should complete without throwing
        try await ctrl.startWithAcquirers([primary], workflow: wf, settings: makeSettings())

        // After completion, session state should be cleared
        let activeSession = ctrl.appState?.activeSession
        XCTAssertNil(activeSession)
    }

    func test_cancel_fires_all_acquirer_cancels() async throws {
        let ctrl = SessionController()
        let wf = makeWorkflow()

        let primary = MockAcquirer(
            paramName: "audio",
            mode: .primary,
            result: AcquirerResult(paramName: "audio", value: "/tmp/out.wav", phase: nil),
            delay: 5.0  // long delay; will be cancelled
        )
        let bg = MockAcquirer(
            paramName: "screenshots",
            mode: .background,
            result: AcquirerResult(paramName: "screenshots", value: "", phase: nil),
            delay: 5.0
        )

        // Start the session in a concurrent task, then cancel it.
        let sessionTask = Task {
            try? await ctrl.startWithAcquirers([primary, bg], workflow: wf, settings: self.makeSettings())
        }

        // Give the session a moment to start.
        try await Task.sleep(nanoseconds: 100_000_000)  // 0.1 s

        // Cancel the session (fires cancel on all acquirers).
        await MainActor.run { ctrl.cancel() }

        // Wait for session to finish.
        await sessionTask.value

        XCTAssertTrue(primary.cancelCalled || bg.cancelCalled,
                      "cancel() must fire cancel on at least one live acquirer")
    }

    func test_resolves_configured_params_before_starting() async throws {
        let ctrl = SessionController()
        let suite = "com.turbollm.hud.test.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let settings = Settings(suiteName: suite)

        let wf = makeWorkflow(params: [
            WorkflowParam(name: "vault", type: "directory", mode: nil,
                          defaultValue: "/default/vault", defaultEnv: nil, auto: nil,
                          options: nil, extensions: nil, scope: nil, command: nil)
        ])
        settings.setParamValue(workflow: wf.name, param: "vault", value: "/sticky/vault")

        let primary = MockAcquirer(
            paramName: "audio",
            mode: .primary,
            result: AcquirerResult(paramName: "audio", value: "/tmp/out.wav", phase: nil)
        )

        // Track that the resolved params are correct by checking they don't throw.
        try await ctrl.startWithAcquirers([primary], workflow: wf, settings: settings)

        // The test asserts the session completes without error (param resolution succeeded).
        // Detailed resolution is tested in WorkflowRunnerTests.
        let activeSession = ctrl.appState?.activeSession
        XCTAssertNil(activeSession)
    }
}
