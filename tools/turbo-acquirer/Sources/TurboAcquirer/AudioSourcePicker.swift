import CoreAudio
import Foundation

struct AudioDeviceInfo: Equatable {
    let id: AudioDeviceID
    let uid: String
    let name: String
    let manufacturer: String
}

enum AudioSourcePicker {

    // MARK: - Public API

    static func inputDevices() -> [AudioDeviceInfo] {
        allDeviceIDs()
            .compactMap { deviceInfo(for: $0) }
            .filter { hasInputChannels(deviceID: $0.id) }
    }

    static func deviceID(forUID uid: String) -> AudioDeviceID? {
        inputDevices().first(where: { $0.uid == uid })?.id
    }

    static func defaultInputDeviceID() -> AudioDeviceID? {
        var deviceID = kAudioObjectUnknown
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject),
            &address, 0, nil, &size, &deviceID
        )
        guard status == noErr, deviceID != kAudioObjectUnknown else { return nil }
        return deviceID
    }

    // MARK: - Private helpers

    private static func allDeviceIDs() -> [AudioDeviceID] {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        var status = AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size
        )
        guard status == noErr, size > 0 else { return [] }

        let count = Int(size) / MemoryLayout<AudioDeviceID>.size
        var deviceIDs = [AudioDeviceID](repeating: kAudioObjectUnknown, count: count)
        status = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &deviceIDs
        )
        guard status == noErr else { return [] }
        return deviceIDs
    }

    private static func hasInputChannels(deviceID: AudioDeviceID) -> Bool {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreamConfiguration,
            mScope: kAudioDevicePropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        var status = AudioObjectGetPropertyDataSize(deviceID, &address, 0, nil, &size)
        guard status == noErr, size > 0 else { return false }

        let bufferListPtr = UnsafeMutableRawPointer.allocate(
            byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer { bufferListPtr.deallocate() }

        status = AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, bufferListPtr)
        guard status == noErr else { return false }

        // AudioBufferList is a variable-length C struct: `mBuffers` is
        // declared as a 1-element tail array, but the actual allocation
        // (sized by `size` above) can hold more for multi-stream/aggregate
        // devices. Loading `.pointee` into a fixed-size Swift value only
        // copies buffer 0 — indexing buffer i≥1 off that copy reads garbage
        // past the copied struct instead of the real data. Iterate the
        // original allocation via the buffer-list-aware pointer wrapper.
        let bufferListTyped = bufferListPtr.bindMemory(to: AudioBufferList.self, capacity: 1)
        let buffers = UnsafeMutableAudioBufferListPointer(bufferListTyped)
        return buffers.contains { $0.mNumberChannels > 0 }
    }

    private static func deviceInfo(for deviceID: AudioDeviceID) -> AudioDeviceInfo? {
        guard let uid = stringProperty(deviceID: deviceID, selector: kAudioDevicePropertyDeviceUID),
              let name = stringProperty(deviceID: deviceID, selector: kAudioObjectPropertyName) else {
            return nil
        }
        let manufacturer = stringProperty(deviceID: deviceID, selector: kAudioObjectPropertyManufacturer) ?? ""
        return AudioDeviceInfo(id: deviceID, uid: uid, name: name, manufacturer: manufacturer)
    }

    private static func stringProperty(deviceID: AudioDeviceID, selector: AudioObjectPropertySelector) -> String? {
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size = UInt32(MemoryLayout<Unmanaged<CFString>>.size)
        var unmanagedRef: Unmanaged<CFString>?
        let status = withUnsafeMutablePointer(to: &unmanagedRef) {
            $0.withMemoryRebound(to: UInt8.self, capacity: Int(size)) { rawPtr in
                AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, rawPtr)
            }
        }
        guard status == noErr, let unmanaged = unmanagedRef else { return nil }
        return unmanaged.takeRetainedValue() as String
    }
}
