import Foundation

/// What cibar saved in `url` when it last ran, or nothing when it saved
/// nothing. A file that cannot be read is moved aside, with `.unreadable`
/// added to its name, so that saving does not replace it.
func readState(from url: URL) -> WatchState {
    readState(from: url, or: WatchState())
}

/// What cibar saved in `url` when it last ran, or `empty` when it saved
/// nothing or what it saved cannot be read, which is then moved aside.
func readState<State: Decodable>(from url: URL, or empty: State) -> State {
    guard FileManager.default.fileExists(atPath: url.path) else { return empty }
    do {
        return try JSONDecoder().decode(State.self, from: Data(contentsOf: url))
    } catch {
        let aside = url.appendingPathExtension("unreadable")
        try? FileManager.default.removeItem(at: aside)
        try? FileManager.default.moveItem(at: url, to: aside)
        log("cannot read \(url.path), so it is now \(aside.lastPathComponent): \(error)")
        return empty
    }
}

func write<State: Encodable>(_ state: State, to url: URL) {
    do {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try JSONEncoder().encode(state).write(to: url, options: .atomic)
    } catch {
        log("cannot save what cibar knows: \(error)")
    }
}

/// Saves what cibar knows on a queue of its own, one write at a time. Saves
/// asked for while a write is under way become one more write once it
/// ends, of what cibar knows then. Its methods run on the main thread.
final class StateWriter {
    private let snapshot: () -> WatchState
    private let write: (WatchState) -> Void
    private let queue = DispatchQueue(label: "local.cibar.save")
    private var writing = false
    private var again = false

    init(snapshot: @escaping () -> WatchState, write: @escaping (WatchState) -> Void) {
        self.snapshot = snapshot
        self.write = write
    }

    func save() {
        guard !writing else {
            again = true
            return
        }
        writing = true
        again = false
        let state = snapshot()
        queue.async { [write, weak self] in
            write(state)
            DispatchQueue.main.async { self?.written() }
        }
    }

    private func written() {
        writing = false
        if again {
            save()
        }
    }

    /// Waits for any write under way, then writes what cibar knows now.
    func flush() {
        queue.sync {}
        write(snapshot())
    }
}
