import Foundation

struct ToolRun {
    let status: Int32
    let killed: Bool
    let output: String
    let errors: String

    /// How the tool ended, and what it reported on its error stream.
    func failure(_ name: String) -> String {
        let reported = errors.trimmingCharacters(in: .whitespacesAndNewlines)
        let ending = killed ? "was killed by signal \(status)" : "exited with status \(status)"
        return "\(name) \(ending)" + (reported.isEmpty ? "" : ": \(reported)")
    }
}

/// Runs a tool to completion and returns what it wrote to each stream. The
/// streams go to unnamed files, which nothing the tool leaves running can
/// hold open once the tool itself has exited, and which disappear with this
/// process however it ends.
func runTool(_ executable: URL, _ arguments: [String]) throws -> ToolRun {
    let output = try TemporaryLog()
    let errors = try TemporaryLog()
    let process = Process()
    process.executableURL = executable
    process.arguments = arguments
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = output.handle
    process.standardError = errors.handle
    let exited = DispatchSemaphore(value: 0)
    process.terminationHandler = { _ in exited.signal() }
    try process.run()
    exited.wait()
    return ToolRun(
        status: process.terminationStatus,
        killed: process.terminationReason == .uncaughtSignal,
        output: try output.contents(),
        errors: try errors.contents())
}

/// A file a tool writes to. Its name is removed as soon as it is made, so
/// the file lasts only as long as the handle.
private final class TemporaryLog {
    let handle: FileHandle

    init() throws {
        var template = Array(
            "\(FileManager.default.temporaryDirectory.path)/\(applicationName).XXXXXX".utf8CString)
        let descriptor = mkstemp(&template)
        guard descriptor >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        unlink(template)
        handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    }

    func contents() throws -> String {
        try handle.seek(toOffset: 0)
        return String(decoding: try handle.readToEnd() ?? Data(), as: UTF8.self)
    }
}
