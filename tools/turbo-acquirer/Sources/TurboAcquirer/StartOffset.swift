import Foundation

/// Computes elapsed time in milliseconds from `$TURBO_T0_NS` (a monotonic-clock
/// nanosecond timestamp marking when the parent workflow began) to now.
/// Returns 0 when the env var is unset or malformed.
///
/// Shared by `RecordScreen.computeStartOffsetMs()` and
/// `RecordAudio.computeStartOffsetMs()` — both acquirers derive
/// `start_offset_ms` from the same time origin so screen/audio/manifest
/// timestamps line up in the HUD. The two enums keep their own thin static
/// wrappers (rather than callers using this function directly) so existing
/// call sites and tests don't need to change.
func computeStartOffsetMsFromT0() -> Int {
    let nowNs = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
    if let t0Str = ProcessInfo.processInfo.environment["TURBO_T0_NS"],
       let t0Ns = UInt64(t0Str), nowNs >= t0Ns {
        return Int((nowNs - t0Ns) / 1_000_000)
    }
    return 0
}
