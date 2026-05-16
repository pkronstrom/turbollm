import XCTest
@testable import TurboAcquirer

final class RecordScreenTests: XCTestCase {

    // MARK: - T-4: Arg parsing + dispatch scaffold

    func test_parseArgs_full_display_scope() {
        let args = RecordScreen.parseArgs(argv: ["--output-dir", "/tmp/frames", "--scope", "full-display"])
        XCTAssertNotNil(args)
        XCTAssertEqual(args?.outputDir.path, "/tmp/frames")
        XCTAssertEqual(args?.scope, .fullDisplay)
        XCTAssertEqual(args?.threshold, 12)
        XCTAssertEqual(args?.minIntervalMs, 1500)
        XCTAssertEqual(args?.maxKeyframes, 200)
    }

    func test_parseArgs_region_scope() {
        let args = RecordScreen.parseArgs(argv: [
            "--output-dir", "/tmp/frames",
            "--scope", "region",
            "--region", "100,200,800,600"
        ])
        XCTAssertNotNil(args)
        if case .region(let rect) = args?.scope {
            XCTAssertEqual(rect.origin.x, 100)
            XCTAssertEqual(rect.origin.y, 200)
            XCTAssertEqual(rect.size.width, 800)
            XCTAssertEqual(rect.size.height, 600)
        } else {
            XCTFail("Expected .region scope")
        }
    }

    func test_parseArgs_active_window_scope() {
        let args = RecordScreen.parseArgs(argv: ["--output-dir", "/tmp", "--scope", "active-window"])
        XCTAssertNotNil(args)
        XCTAssertEqual(args?.scope, .activeWindow)
    }

    func test_parseArgs_missing_output_dir_returns_nil() {
        let args = RecordScreen.parseArgs(argv: ["--scope", "full-display"])
        XCTAssertNil(args)
    }

    func test_parseArgs_custom_thresholds() {
        let args = RecordScreen.parseArgs(argv: [
            "--output-dir", "/tmp",
            "--keyframe-threshold", "8",
            "--min-interval-ms", "500",
            "--max-keyframes", "50"
        ])
        XCTAssertEqual(args?.threshold, 8)
        XCTAssertEqual(args?.minIntervalMs, 500)
        XCTAssertEqual(args?.maxKeyframes, 50)
    }

    func test_parseRegionString_valid() {
        let rect = RecordScreen.parseRegionString("10,20,300,400")
        XCTAssertEqual(rect, CGRect(x: 10, y: 20, width: 300, height: 400))
    }

    func test_parseRegionString_invalid_returns_nil() {
        XCTAssertNil(RecordScreen.parseRegionString("bad"))
        XCTAssertNil(RecordScreen.parseRegionString("1,2,3"))
    }

    func test_dispatch_routes_to_record_screen_in_fake_mode() throws {
        setenv("TURBO_RECORD_SCREEN_FAKE", "1", 1)
        defer { unsetenv("TURBO_RECORD_SCREEN_FAKE") }

        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("record-screen-scaffold-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let result = App.dispatch(argv: [
            "turbo-acquirer", "record-screen",
            "--output-dir", tmpDir.path,
            "--scope", "full-display"
        ])
        // Result should be parseable manifest JSON.
        XCTAssertTrue(result.contains("frames"), "Manifest must contain 'frames' key, got: \(result)")
        XCTAssertTrue(result.contains("duration_ms"), "Manifest must contain 'duration_ms', got: \(result)")
        XCTAssertTrue(result.contains("dropped_overcap"), "Manifest must contain 'dropped_overcap', got: \(result)")
    }

    func test_fake_mode_manifest_is_valid_json() throws {
        setenv("TURBO_RECORD_SCREEN_FAKE", "1", 1)
        defer { unsetenv("TURBO_RECORD_SCREEN_FAKE") }

        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("record-screen-json-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let result = RecordScreen.run(outputDir: tmpDir, scope: .fullDisplay)
        let data = result.data(using: .utf8)!
        let manifest = try JSONDecoder().decode(RecordScreenManifest.self, from: data)
        XCTAssertEqual(manifest.frames.count, 0)
        XCTAssertEqual(manifest.droppedOvercap, 0)
    }

    // MARK: - T-5: KeyframeDecider pure logic

    func test_keyframe_decider_static_screen_only_first_frame_accepted() {
        let decider = KeyframeDecider(threshold: 12, minIntervalMs: 1500, maxKeyframes: 200)
        let hash: UInt64 = 0xABCD1234

        // First frame: always persist
        XCTAssertEqual(decider.decide(hash: hash, t_ms: 0), .persist)
        // Same hash at t=2000ms (interval ok, but same hash → dropSimilar)
        XCTAssertEqual(decider.decide(hash: hash, t_ms: 2000), .dropSimilar)
        // Same hash at t=4000ms
        XCTAssertEqual(decider.decide(hash: hash, t_ms: 4000), .dropSimilar)
    }

    func test_keyframe_decider_sequence_with_hamming_distances() {
        // threshold=12, minIntervalMs=1500
        let decider = KeyframeDecider(threshold: 12, minIntervalMs: 1500, maxKeyframes: 200)

        // Frame 0: first frame always persists
        let h0: UInt64 = 0x0000000000000000
        XCTAssertEqual(decider.decide(hash: h0, t_ms: 0), .persist)

        // Frame 1 at t=1600ms, distance=5 (below threshold) → dropSimilar
        let h1 = makeHashWithDistance(from: h0, distance: 5)
        XCTAssertEqual(decider.decide(hash: h1, t_ms: 1600), .dropSimilar)

        // Frame 2 at t=3200ms, distance=15 from h0 (above threshold) → persist
        let h2 = makeHashWithDistance(from: h0, distance: 15)
        XCTAssertEqual(decider.decide(hash: h2, t_ms: 3200), .persist)

        // Frame 3 at t=4000ms: interval from h2 (3200) = 800ms < 1500ms → dropMinInterval
        let h3 = makeHashWithDistance(from: h2, distance: 20)
        XCTAssertEqual(decider.decide(hash: h3, t_ms: 4000), .dropMinInterval)

        // Frame 4 at t=4900ms: interval = 4900-3200 = 1700ms ≥ 1500ms, distance=8 < 12 → dropSimilar
        let h4 = makeHashWithDistance(from: h2, distance: 8)
        XCTAssertEqual(decider.decide(hash: h4, t_ms: 4900), .dropSimilar)

        // Frame 5 at t=6800ms: interval = 6800-3200 = 3600ms ≥ 1500ms, distance=30 ≥ 12 → persist
        let h5 = makeHashWithDistance(from: h2, distance: 30)
        XCTAssertEqual(decider.decide(hash: h5, t_ms: 6800), .persist)
    }

    func test_keyframe_decider_min_interval_enforced() {
        let decider = KeyframeDecider(threshold: 12, minIntervalMs: 1500, maxKeyframes: 200)
        let h0: UInt64 = 0x0000000000000000
        let hDist20 = makeHashWithDistance(from: h0, distance: 20)

        // First frame persists
        XCTAssertEqual(decider.decide(hash: h0, t_ms: 0), .persist)

        // 1000ms later, different hash but min-interval not met → dropMinInterval
        XCTAssertEqual(decider.decide(hash: hDist20, t_ms: 1000), .dropMinInterval)

        // 1600ms later, different hash, min-interval met → persist
        XCTAssertEqual(decider.decide(hash: hDist20, t_ms: 1600), .persist)
    }

    func test_keyframe_decider_max_keyframes_cap() {
        let decider = KeyframeDecider(threshold: 12, minIntervalMs: 100, maxKeyframes: 3)

        // Frame 0 → persist
        let h0: UInt64 = 0x0000000000000000
        XCTAssertEqual(decider.decide(hash: h0, t_ms: 0), .persist)

        // Frame 1 → persist (distinct hash, interval ok)
        let h1 = makeHashWithDistance(from: h0, distance: 20)
        XCTAssertEqual(decider.decide(hash: h1, t_ms: 200), .persist)

        // Frame 2 → persist (distinct hash, interval ok)
        let h2 = makeHashWithDistance(from: h1, distance: 20)
        XCTAssertEqual(decider.decide(hash: h2, t_ms: 400), .persist)

        // Frame 3 → dropOvercap (max-keyframes=3 already hit)
        let h3 = makeHashWithDistance(from: h2, distance: 20)
        XCTAssertEqual(decider.decide(hash: h3, t_ms: 600), .dropOvercap)

        // Frame 4 → dropOvercap
        let h4 = makeHashWithDistance(from: h3, distance: 20)
        XCTAssertEqual(decider.decide(hash: h4, t_ms: 800), .dropOvercap)
    }

    // MARK: - T-6: Manifest shape tests (no real SCStream)

    func test_manifest_json_keys_present() throws {
        let manifest = RecordScreenManifest(
            frames: [ScreenFrameEntry(path: "/tmp/001-T+0.png", tOffsetMs: 0)],
            durationMs: 5000,
            droppedOvercap: 2,
            startOffsetMs: 142
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(manifest)
        let json = String(data: data, encoding: .utf8)!
        XCTAssertTrue(json.contains("\"frames\""), "Manifest JSON must contain 'frames'")
        XCTAssertTrue(json.contains("\"duration_ms\""), "Manifest JSON must contain 'duration_ms'")
        XCTAssertTrue(json.contains("\"dropped_overcap\""), "Manifest JSON must contain 'dropped_overcap'")
        XCTAssertTrue(json.contains("\"start_offset_ms\""), "Manifest JSON must contain 'start_offset_ms'")
        XCTAssertTrue(json.contains("\"t_offset_ms\""), "Frame entry must contain 't_offset_ms'")
    }

    func test_manifest_round_trips_through_json() throws {
        let original = RecordScreenManifest(
            frames: [
                ScreenFrameEntry(path: "/tmp/001-T+0.png", tOffsetMs: 0),
                ScreenFrameEntry(path: "/tmp/002-T+1500.png", tOffsetMs: 1500)
            ],
            durationMs: 10000,
            droppedOvercap: 1,
            startOffsetMs: 50
        )
        let encoder = JSONEncoder()
        let data = try encoder.encode(original)
        let decoded = try JSONDecoder().decode(RecordScreenManifest.self, from: data)
        XCTAssertEqual(decoded.frames.count, 2)
        XCTAssertEqual(decoded.frames[0].path, "/tmp/001-T+0.png")
        XCTAssertEqual(decoded.frames[0].tOffsetMs, 0)
        XCTAssertEqual(decoded.frames[1].tOffsetMs, 1500)
        XCTAssertEqual(decoded.durationMs, 10000)
        XCTAssertEqual(decoded.droppedOvercap, 1)
        XCTAssertEqual(decoded.startOffsetMs, 50)
    }

    func test_encode_manifest_produces_valid_json() {
        let manifest = RecordScreenManifest(
            frames: [],
            durationMs: 0,
            droppedOvercap: 0,
            startOffsetMs: 0
        )
        let json = RecordScreen.encodeManifest(manifest)
        XCTAssertFalse(json.isEmpty)
        let data = json.data(using: .utf8)!
        XCTAssertNoThrow(try JSONDecoder().decode(RecordScreenManifest.self, from: data))
    }

    // MARK: - T-7: TURBO_T0_NS start_offset_ms

    func test_compute_start_offset_ms_zero_without_t0_ns() {
        unsetenv("TURBO_T0_NS")
        XCTAssertEqual(RecordScreen.computeStartOffsetMs(), 0)
    }

    func test_compute_start_offset_ms_reflects_delta() {
        // Set TURBO_T0_NS to 200ms before the current monotonic time.
        let nowNs = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
        guard nowNs > 200_000_000 else { return } // skip on very short uptime
        let t0Ns = nowNs - 200_000_000
        setenv("TURBO_T0_NS", String(t0Ns), 1)
        defer { unsetenv("TURBO_T0_NS") }

        let offsetMs = RecordScreen.computeStartOffsetMs()
        // Should be approximately 200ms (allow 100ms jitter for test overhead).
        XCTAssertGreaterThanOrEqual(offsetMs, 100,
            "start_offset_ms should be ≥ 100ms when T0 was 200ms ago, got \(offsetMs)")
        XCTAssertLessThan(offsetMs, 1000,
            "start_offset_ms should be < 1000ms in test context, got \(offsetMs)")
    }

    func test_fake_mode_manifest_includes_start_offset_ms_from_t0_ns() throws {
        // Verify that fake-mode manifest correctly reflects TURBO_T0_NS.
        let nowNs = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
        guard nowNs > 500_000_000 else { return }
        let t0Ns = nowNs - 500_000_000  // 500ms ago
        setenv("TURBO_RECORD_SCREEN_FAKE", "1", 1)
        setenv("TURBO_T0_NS", String(t0Ns), 1)
        defer {
            unsetenv("TURBO_RECORD_SCREEN_FAKE")
            unsetenv("TURBO_T0_NS")
        }

        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("record-screen-t0-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let result = RecordScreen.run(outputDir: tmpDir, scope: .fullDisplay)
        let data = result.data(using: .utf8)!
        let manifest = try JSONDecoder().decode(RecordScreenManifest.self, from: data)
        // With T0 set 500ms ago, start_offset_ms should be ≥ 400ms.
        XCTAssertGreaterThanOrEqual(manifest.startOffsetMs, 400,
            "start_offset_ms should be ≥ 400 when T0 was 500ms ago, got \(manifest.startOffsetMs)")
    }

    func test_fake_mode_start_offset_ms_zero_when_no_t0_ns() throws {
        setenv("TURBO_RECORD_SCREEN_FAKE", "1", 1)
        unsetenv("TURBO_T0_NS")
        defer { unsetenv("TURBO_RECORD_SCREEN_FAKE") }

        let tmpDir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("record-screen-no-t0-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let result = RecordScreen.run(outputDir: tmpDir, scope: .fullDisplay)
        let data = result.data(using: .utf8)!
        let manifest = try JSONDecoder().decode(RecordScreenManifest.self, from: data)
        XCTAssertEqual(manifest.startOffsetMs, 0)
    }

    // MARK: - Helpers

    /// Creates a hash that differs from `base` by exactly `distance` bits.
    private func makeHashWithDistance(from base: UInt64, distance: Int) -> UInt64 {
        var result = base
        for i in 0..<distance {
            result ^= (1 << i)
        }
        return result
    }
}
