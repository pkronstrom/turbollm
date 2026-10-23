import Foundation
import Darwin

/// Run only on a background queue. Drain both pipes while the child runs, with
/// bounded memory and TERM/KILL deadlines even if a descendant retains a pipe.
enum BoundedSubprocess {
    struct Result {
        let stdout: Data
        let stderr: Data
        let status: Int32?
        let timedOut: Bool
    }

    static func run(executable: String, arguments: [String], timeout: TimeInterval) throws -> Result {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let output = Pipe(), errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        let done = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in done.signal() }
        let outFD = output.fileHandleForReading.fileDescriptor
        let errFD = errors.fileHandleForReading.fileDescriptor
        for fd in [outFD, errFD] {
            _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)
        }
        defer {
            try? output.fileHandleForReading.close()
            try? errors.fileHandleForReading.close()
        }
        try process.run()
        var stdout = Data(), stderr = Data()
        var buffer = [UInt8](repeating: 0, count: 65536)
        func drain(_ fd: Int32, into data: inout Data) {
            // Bound each sweep so a continuously noisy child cannot defeat the deadline.
            for _ in 0..<16 {
                let count = read(fd, &buffer, buffer.count)
                guard count > 0 else { break }
                let kept = min(count, max(0, 8 * 1024 * 1024 - data.count))
                data.append(contentsOf: buffer.prefix(kept))
            }
        }
        let deadline = ProcessInfo.processInfo.systemUptime + timeout
        var timedOut = false
        while process.isRunning {
            drain(outFD, into: &stdout)
            drain(errFD, into: &stderr)
            if ProcessInfo.processInfo.systemUptime >= deadline {
                timedOut = true
                process.terminate()
                if done.wait(timeout: .now() + 0.25) == .timedOut {
                    kill(process.processIdentifier, SIGKILL)
                    _ = done.wait(timeout: .now() + 0.25)
                }
                break
            }
            Thread.sleep(forTimeInterval: 0.005)
        }
        drain(outFD, into: &stdout)
        drain(errFD, into: &stderr)
        return Result(stdout: stdout, stderr: stderr,
                      status: process.isRunning ? nil : process.terminationStatus, timedOut: timedOut)
    }
}
