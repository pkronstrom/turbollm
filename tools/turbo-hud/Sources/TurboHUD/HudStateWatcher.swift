import Foundation

final class HudStateWatcher {
    let stateDir: URL
    private var dirSource: DispatchSourceFileSystemObject?
    private(set) var onChange: ((_ activities: [Activity], _ acquirerActivity: Activity?) -> Void)?

    init(stateDir: URL) {
        self.stateDir = stateDir
    }

    /// Age threshold (24 h) past which an activity file is GC'd even if its
    /// owner PID still looks alive — guards against macOS PID reuse and
    /// stuck activity files from crashed-but-respawned processes.
    static let staleAgeSeconds: TimeInterval = 24 * 60 * 60

    /// Load + GC the current activity files. Returns the live set.
    func loadCurrent() -> [Activity] {
        guard FileManager.default.fileExists(atPath: stateDir.path) else { return [] }
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(at: stateDir,
                                                       includingPropertiesForKeys: [.contentModificationDateKey],
                                                       options: [.skipsHiddenFiles]) else {
            return []
        }
        var live: [Activity] = []
        let now = Date()
        for url in entries where url.lastPathComponent.hasPrefix("activity-") && url.pathExtension == "json" {
            guard let data = try? Data(contentsOf: url),
                  let activity = try? JSONDecoder.activity().decode(Activity.self, from: data) else {
                continue
            }
            let pidDead = !Self.isPidAlive(activity.ownerPid)
            let tooOld = now.timeIntervalSince(activity.startedAt) > Self.staleAgeSeconds
            if pidDead || tooOld {
                for childPid in activity.children {
                    kill(pid_t(childPid), SIGTERM)
                }
                try? fm.removeItem(at: url)
                continue
            }
            live.append(activity)
        }
        return live
    }

    func startWatching(onChange: @escaping ([Activity], Activity?) -> Void) {
        self.onChange = onChange
        let fd = open(stateDir.path, O_EVTONLY)
        guard fd >= 0 else { return }
        let src = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd,
            eventMask: [.write, .extend, .delete, .rename],
            queue: .main
        )
        src.setEventHandler { [weak self] in
            guard let self else { return }
            let activities = self.loadCurrent()
            self.onChange?(activities, Self.acquirerActivity(from: activities))
        }
        src.setCancelHandler {
            close(fd)
        }
        src.resume()
        dirSource = src
        // Emit initial snapshot
        let initial = loadCurrent()
        onChange(initial, Self.acquirerActivity(from: initial))
    }

    /// Extracts the single acquirer activity from the live activity set.
    /// Assumption: only one acquirer is in flight at a time; if multiple exist
    /// (e.g. after a crash without cleanup), the newest `startedAt` wins.
    static func acquirerActivity(from activities: [Activity]) -> Activity? {
        activities
            .filter { $0.kind == "acquirer" }
            .sorted { $0.startedAt > $1.startedAt }
            .first
    }

    func stop() {
        dirSource?.cancel()
        dirSource = nil
    }

    private static func isPidAlive(_ pid: Int) -> Bool {
        // kill(pid, 0) returns 0 if process exists; -1 with errno=ESRCH if dead.
        let rc = kill(pid_t(pid), 0)
        if rc == 0 { return true }
        return errno == EPERM   // process exists but we can't signal it
    }
}
