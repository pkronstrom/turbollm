import XCTest
@testable import TurboHUD

final class AudioSourcePickerTests: XCTestCase {

    func test_inputDevices_returns_at_least_one_device() {
        let devices = AudioSourcePicker.inputDevices()
        XCTAssertFalse(devices.isEmpty, "Expected at least one CoreAudio input device (built-in mic).")
    }

    func test_each_device_has_non_empty_name_and_uid() {
        let devices = AudioSourcePicker.inputDevices()
        for device in devices {
            XCTAssertFalse(device.name.isEmpty, "Device \(device.id) has empty name.")
            XCTAssertFalse(device.uid.isEmpty, "Device \(device.id) has empty uid.")
        }
    }

    func test_deviceID_forUID_returns_nil_for_unknown_uid() {
        let result = AudioSourcePicker.deviceID(forUID: "this-uid-does-not-exist-xyz-12345")
        XCTAssertNil(result)
    }

    func test_deviceID_forUID_round_trips_first_device() throws {
        let devices = AudioSourcePicker.inputDevices()
        guard let first = devices.first else {
            throw XCTSkip("No input devices found — skip round-trip test.")
        }
        let resolved = AudioSourcePicker.deviceID(forUID: first.uid)
        XCTAssertEqual(resolved, first.id, "deviceID(forUID:) should round-trip to the same AudioDeviceID.")
    }

    func test_defaultInputDeviceID_is_non_nil() {
        let id = AudioSourcePicker.defaultInputDeviceID()
        XCTAssertNotNil(id, "Expected a default input device to exist on any Mac.")
    }
}
