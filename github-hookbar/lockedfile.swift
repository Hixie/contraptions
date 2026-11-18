import Foundation

// Changing files that other programs read, without their seeing a file half
// changed.

/// Opens the file at `path`, creating it and its directory when they do not
/// exist yet, and runs `body` with it while holding an exclusive flock(2)
/// lock on it. A reader that holds a shared lock on the file, as `flock -s`
/// does, therefore never sees it half changed.
func withExclusiveLock<T>(onFileAt path: String, _ body: (FileHandle) throws -> T) throws -> T {
    try FileManager.default.createDirectory(
        at: URL(fileURLWithPath: path).deletingLastPathComponent(),
        withIntermediateDirectories: true)
    let descriptor = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0o600)
    guard descriptor >= 0 else {
        throw Problem(description: "cannot open \(path): \(String(cString: strerror(errno)))")
    }
    let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    // Closing the file releases the lock.
    defer { try? file.close() }
    guard flock(descriptor, LOCK_EX) == 0 else {
        throw Problem(description: "cannot lock \(path): \(String(cString: strerror(errno)))")
    }
    return try body(file)
}

/// Replaces what an open file holds, in place, so that it stays the same
/// file for anyone holding a lock on it.
func replaceContents(of file: FileHandle, with data: Data) throws {
    // Writing over the old contents before cutting them short keeps them
    // whole unless the new contents cannot be written.
    try file.seek(toOffset: 0)
    try file.write(contentsOf: data)
    try file.truncate(atOffset: UInt64(data.count))
}
