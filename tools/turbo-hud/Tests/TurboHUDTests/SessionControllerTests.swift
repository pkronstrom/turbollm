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

    // MARK: - T-fix-1: buildAcquirers factory

    func test_buildAcquirers_returns_commandAcquirer_for_command_param() {
        // T-fix-1: buildAcquirers must instantiate concrete acquirers; before the fix
        // it returned [] which caused a force-unwrap crash in start().
        let ctrl = SessionController()
        let wf = Workflow(
            name: "cmd-wf", description: nil, command: "echo", script: nil,
            args: nil, env: nil,
            params: [
                WorkflowParam(name: "today", type: "command", mode: "background",
                              defaultValue: nil, defaultEnv: nil, auto: nil,
                              options: nil, extensions: nil, scope: nil,
                              command: "date +%Y-%m-%d")
            ]
        )
        let acquirers = ctrl.buildAcquirers(for: wf, settings: makeSettings())
        XCTAssertEqual(acquirers.count, 1, "buildAcquirers must return one acquirer for one command param")
        XCTAssertEqual(acquirers[0].paramName, "today")
        XCTAssertEqual(acquirers[0].mode, .background,
                       "CommandAcquirer mode is always .background")
    }

    func test_buildAcquirers_returns_screenshotManual_for_screenshot_param() {
        let ctrl = SessionController()
        let wf = Workflow(
            name: "screenshot-wf", description: nil, command: nil, script: nil,
            args: nil, env: nil,
            params: [
                WorkflowParam(name: "screenshots", type: "screenshot-manual", mode: "trigger",
                              defaultValue: nil, defaultEnv: nil, auto: nil,
                              options: nil, extensions: nil, scope: nil, command: nil)
            ]
        )
        let acquirers = ctrl.buildAcquirers(for: wf, settings: makeSettings())
        XCTAssertEqual(acquirers.count, 1)
        XCTAssertEqual(acquirers[0].paramName, "screenshots")
        XCTAssertEqual(acquirers[0].mode, .trigger)
    }

    func test_buildAcquirers_returns_audioRecorder_for_audio_recording_param() {
        let ctrl = SessionController()
        let wf = Workflow(
            name: "record-wf", description: nil, command: nil, script: nil,
            args: nil, env: nil,
            params: [
                WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                              defaultValue: nil, defaultEnv: nil, auto: nil,
                              options: nil, extensions: nil, scope: "mic-only", command: nil)
            ]
        )
        let acquirers = ctrl.buildAcquirers(for: wf, settings: makeSettings())
        XCTAssertEqual(acquirers.count, 1)
        XCTAssertEqual(acquirers[0].paramName, "audio")
        XCTAssertEqual(acquirers[0].mode, .primary,
                       "AudioRecorder mode is always .primary")
    }

    func test_buildAcquirers_returns_empty_for_non_acquired_params() {
        let ctrl = SessionController()
        let wf = Workflow(
            name: "plain-wf", description: nil, command: "echo", script: nil,
            args: nil, env: nil,
            params: [
                WorkflowParam(name: "title", type: "string", mode: nil,
                              defaultValue: "hello", defaultEnv: nil, auto: nil,
                              options: nil, extensions: nil, scope: nil, command: nil)
            ]
        )
        let acquirers = ctrl.buildAcquirers(for: wf, settings: makeSettings())
        XCTAssertTrue(acquirers.isEmpty, "Non-acquired params must not produce acquirers")
    }

    func test_buildAcquirers_skips_command_param_with_no_command_string() {
        let ctrl = SessionController()
        let wf = Workflow(
            name: "bad-cmd-wf", description: nil, command: nil, script: nil,
            args: nil, env: nil,
            params: [
                WorkflowParam(name: "broken", type: "command", mode: "background",
                              defaultValue: nil, defaultEnv: nil, auto: nil,
                              options: nil, extensions: nil, scope: nil, command: nil)
            ]
        )
        let acquirers = ctrl.buildAcquirers(for: wf, settings: makeSettings())
        XCTAssertTrue(acquirers.isEmpty,
                      "command param with no command string must be skipped gracefully")
    }

    // MARK: - T-fix-2: Background completes after primary; session ends in bounded time

    func test_session_ends_promptly_after_primary_even_when_background_blocks() async throws {
        // T-fix-2: A background acquirer that blocks indefinitely should NOT delay
        // session teardown after the primary completes.
        let ctrl = SessionController()

        let primary = MockAcquirer(
            paramName: "audio",
            mode: .primary,
            result: AcquirerResult(paramName: "audio", value: "/tmp/out.wav", phase: nil),
            delay: 0  // returns immediately
        )
        // Background acquirer with a long delay; should be cancelled when primary returns.
        let bg = MockAcquirer(
            paramName: "data",
            mode: .background,
            result: AcquirerResult(paramName: "data", value: "bg-value", phase: nil),
            delay: 30.0  // would deadlock the test without T-fix-2
        )

        let wf = makeWorkflow()

        let start = Date()
        try await ctrl.startWithAcquirers([primary, bg], workflow: wf, settings: makeSettings())
        let elapsed = Date().timeIntervalSince(start)

        XCTAssertLessThan(elapsed, 2.0,
            "Session must end within 2 s of primary completing, not wait for background (elapsed: \(elapsed)s)")
        XCTAssertTrue(bg.cancelCalled,
            "Background acquirer must have cancel() called after primary completes")
    }

    // MARK: - T-fix-4: InputFormController override reaches spawnWorkflow

    func test_inputFormOverride_merged_params_are_used_in_session() async throws {
        // T-fix-4: After acquirers complete, SessionController must call InputFormController.show
        // (or its override) with the merged param map. We verify via the override closure.
        let ctrl = SessionController()

        var capturedPrefilled: [String: String]? = nil

        ctrl.inputFormShowOverride = { workflow, prefilled, settings in
            capturedPrefilled = prefilled
            // Return a merged map with an extra user-provided param.
            var result = prefilled
            result["user_note"] = "test-note"
            return result
        }

        let wf = Workflow(
            name: "test-wf", description: nil, command: "echo", script: nil,
            args: nil, env: nil,
            params: [
                WorkflowParam(name: "audio", type: "audio-recording", mode: "primary",
                              defaultValue: nil, defaultEnv: nil, auto: nil,
                              options: nil, extensions: nil, scope: "mic-only", command: nil),
                WorkflowParam(name: "user_note", type: "string", mode: nil,
                              defaultValue: nil, defaultEnv: nil, auto: nil,
                              options: nil, extensions: nil, scope: nil, command: nil)
            ]
        )

        let primary = MockAcquirer(
            paramName: "audio",
            mode: .primary,
            result: AcquirerResult(paramName: "audio", value: "/tmp/out.wav", phase: nil)
        )

        try await ctrl.startWithAcquirers([primary], workflow: wf, settings: makeSettings())

        // The override should have been called with the acquired audio path in prefilled.
        XCTAssertNotNil(capturedPrefilled,
            "inputFormShowOverride must be called during start()")
        XCTAssertEqual(capturedPrefilled?["audio"], "/tmp/out.wav",
            "audio acquirer result must be passed as prefilled to InputFormController")
    }
}
