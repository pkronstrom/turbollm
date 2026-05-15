import Foundation

// MARK: - AcquirerMode

enum AcquirerMode {
    /// Drives session lifetime. Session ends when the primary's `acquire()` returns or throws.
    case primary
    /// Auto-starts with the primary, auto-stops when the primary stops.
    case background
    /// Idles until the user triggers it; appends to an internal list.
    case trigger
}

// MARK: - AcquirerResult

struct AcquirerResult: Equatable {
    let paramName: String
    let value: String       // file path, stdout string, or \n-joined list
    let phase: String?      // e.g. "Recording 0:42" while in-flight
}

// MARK: - AcquirerError

enum AcquirerError: Error {
    case permissionDenied(String)   // human-readable permission name
    case emptyOutput
    case userCancelled
    case underlying(Error)
}

// MARK: - Acquirer

protocol Acquirer: AnyObject {
    var mode: AcquirerMode { get }
    var paramName: String { get }
    func acquire() async throws -> AcquirerResult
    func cancel()
}
