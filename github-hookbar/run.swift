import Foundation

// Running the commands the settings name.

/// How much of a command's output the menu keeps, from the end.
let keptOutputLength = 2000

func loginShellPath() -> String {
    if let entry = getpwuid(getuid()), let shell = entry.pointee.pw_shell {
        let path = String(cString: shell)
        if !path.isEmpty {
            return path
        }
    }
    return "/bin/sh"
}

/// Runs the program at a path with the given arguments, with nothing on its
/// standard input. Calls back on the main thread with the exit status and
/// the end of the output, standard output and standard error together.
///
/// The output goes to a file whose name is removed as soon as it is made, so
/// a background process that the program leaves running, still holding the
/// file, does not keep the run from finishing.
func runProgram(_ path: String, _ arguments: [String],
                completion: @escaping (Int32, String) -> Void) {
    let output: FileHandle
    do {
        let (file, name) = try makeTemporaryFile()
        unlink(name)
        output = file
    } catch {
        DispatchQueue.main.async { completion(-1, "\(error)") }
        return
    }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: path)
    process.arguments = arguments
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = output
    process.standardError = output
    process.terminationHandler = { process in
        let text = (try? output.seek(toOffset: 0)).flatMap { try? output.readToEnd() }
            .map { String(decoding: $0, as: UTF8.self) } ?? ""
        DispatchQueue.main.async {
            completion(process.terminationStatus, String(text.suffix(keptOutputLength)))
        }
    }
    do {
        try process.run()
    } catch {
        let problem = "could not start \(path): \(error.localizedDescription)"
        DispatchQueue.main.async { completion(-1, problem) }
    }
}

/// The shell that runs a script.
enum Shell {
    /// The user's shell, as an interactive login shell, so that the script
    /// sees the environment a terminal sees.
    case login
    /// /bin/sh, which reads no startup files, so that the script sees the
    /// environment github-hookbar was started with.
    case plain

    /// The shell's path, and the options that make it run a script.
    var invocation: (path: String, options: String) {
        switch self {
        case .login: (loginShellPath(), "-ilc")
        case .plain: ("/bin/sh", "-c")
        }
    }
}

/// Runs a script in a shell, with the given arguments as $1, $2, and so on,
/// as runProgram does.
func runScript(_ script: String, in shell: Shell, arguments: [String],
               completion: @escaping (Int32, String) -> Void) {
    let (path, options) = shell.invocation
    runProgram(path, [options, script, "github-hookbar"] + arguments, completion: completion)
}

/// Runs a command line in a shell, as runScript does, with an argument added
/// at its end.
func runCommand(_ command: String, with argument: String, in shell: Shell,
                completion: @escaping (Int32, String) -> Void) {
    runScript(command + " \"$1\"", in: shell, arguments: [argument], completion: completion)
}

/// Makes a new, empty file that only this user can read, and opens it.
func makeTemporaryFile() throws -> (FileHandle, String) {
    var template = Array("\(NSTemporaryDirectory())github-hookbar.XXXXXX".utf8CString)
    let descriptor = mkstemp(&template)
    guard descriptor >= 0 else {
        throw Problem(description: "could not make a temporary file: "
                      + String(cString: strerror(errno)))
    }
    return (FileHandle(fileDescriptor: descriptor, closeOnDealloc: true), String(cString: template))
}
