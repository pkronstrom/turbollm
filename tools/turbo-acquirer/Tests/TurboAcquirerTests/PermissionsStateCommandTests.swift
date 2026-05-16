import XCTest
@testable import TurboAcquirer

final class PermissionsStateCommandTests: XCTestCase {

    override func setUp() {
        super.setUp()
        // Remove cache file before each test to ensure clean state.
        try? FileManager.default.removeItem(at: PermissionsStateCommand.cacheURL)
    }

    override func tearDown() {
        super.tearDown()
        try? FileManager.default.removeItem(at: PermissionsStateCommand.cacheURL)
    }

    func test_run_returns_parseable_json_with_expected_keys() throws {
        let output = PermissionsStateCommand.run()
        guard let data = output.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            XCTFail("Output is not valid JSON: \(output)")
            return
        }
        XCTAssertNotNil(json["microphone"], "JSON must have 'microphone' key")
        XCTAssertNotNil(json["screenRecording"], "JSON must have 'screenRecording' key")
    }

    func test_microphone_value_is_one_of_four_expected_strings() throws {
        let output = PermissionsStateCommand.run()
        guard let data = output.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let microphone = json["microphone"] as? String else {
            XCTFail("Could not parse microphone from JSON: \(output)")
            return
        }
        let validValues = Set(["authorized", "denied", "notDetermined", "restricted"])
        XCTAssertTrue(validValues.contains(microphone),
                      "microphone must be one of \(validValues), got: \(microphone)")
    }

    func test_screen_recording_is_bool() throws {
        let output = PermissionsStateCommand.run()
        guard let data = output.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            XCTFail("Output is not valid JSON: \(output)")
            return
        }
        XCTAssertNotNil(json["screenRecording"] as? Bool,
                        "screenRecording must be a boolean")
    }

    func test_second_call_within_1s_hits_cache_and_returns_identical_output() {
        // First call: fresh state, writes cache.
        let first = PermissionsStateCommand.run()
        // Second call immediately: should return cached (byte-identical) result.
        let second = PermissionsStateCommand.run()
        XCTAssertEqual(first, second,
                       "Successive calls within 1s must return byte-identical output (cache hit)")
    }

    func test_json_string_helper_produces_sorted_keys() throws {
        let state = PermissionsStateCommand.cachedOrFreshState()
        let json = PermissionsStateCommand.jsonString(for: state)
        // With .sortedKeys, microphone should come before screenRecording alphabetically.
        XCTAssertTrue(json.contains("\"microphone\""))
        XCTAssertTrue(json.contains("\"screenRecording\""))
        // Verify it's valid JSON
        guard let data = json.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            XCTFail("jsonString produced invalid JSON: \(json)")
            return
        }
        XCTAssertNotNil(obj["microphone"])
        XCTAssertNotNil(obj["screenRecording"])
    }
}
