import Foundation

// Following a stream of server-sent events, as the HTML standard's
// EventSource does, and keeping in a file the value that a path picks out of
// the JSON data of its events.

/// The most bytes an event's unfinished line and data can hold together.
let maximumEventLength = 32 * 1024 * 1024

/// Reads a server-sent event stream from pieces that may split it anywhere,
/// even within a line or a character, and returns the data of each event it
/// completes. Fields other than `data` are ignored. Reading fails once an
/// unfinished event grows past `limit` bytes.
struct EventStreamParser {
    let limit: Int
    private var line: [UInt8] = []
    private var data: String?
    private var atStart = true
    /// Whether the last line ended with a carriage return, in which case a
    /// line feed straight after it is part of the same line ending.
    private var afterCarriageReturn = false

    init(limit: Int = maximumEventLength) {
        self.limit = limit
    }

    mutating func read(_ bytes: Data) throws -> [String] {
        var events: [String] = []
        for byte in bytes {
            if afterCarriageReturn {
                afterCarriageReturn = false
                if byte == 0x0a {
                    continue
                }
            }
            if byte == 0x0a || byte == 0x0d {
                afterCarriageReturn = byte == 0x0d
                if let event = endLine() {
                    events.append(event)
                }
            } else {
                line.append(byte)
                guard line.count + (data?.utf8.count ?? 0) <= limit else {
                    throw Problem(description: "the server sent an event of more than \(limit) bytes")
                }
            }
        }
        return events
    }

    private mutating func endLine() -> String? {
        var text = String(decoding: line, as: UTF8.self)
        line.removeAll(keepingCapacity: true)
        if atStart {
            atStart = false
            if text.unicodeScalars.first == "\u{feff}" {
                text.unicodeScalars.removeFirst()
            }
        }
        if text.isEmpty {
            defer { data = nil }
            return data
        }
        // The field name ends at the first colon, even one that a combining
        // mark follows.
        let scalars = text.unicodeScalars
        let colon = scalars.firstIndex(of: ":") ?? scalars.endIndex
        guard String(scalars[..<colon]) == "data" else { return nil }
        var value = scalars[colon...].dropFirst()
        if value.first == " " {
            value = value.dropFirst()
        }
        if data == nil {
            data = ""
        } else {
            data?.append("\n")
        }
        data?.append(contentsOf: String(value))
        return nil
    }
}

/// One connection to a server-sent event stream, which reports the data of
/// each event. The server must answer with status 200 and the content type
/// `text/event-stream`. A stream that sends nothing for `idleLimit` seconds
/// is taken to have died, as a connection does while the computer sleeps.
/// All methods and callbacks run on the main thread.
final class EventStreamConnection: NSObject, Connection, URLSessionDataDelegate {
    var onReady: (() -> Void)?
    var onEnded: ((String) -> Void)?
    var onEvent: ((String) -> Void)?
    private let url: URL
    private let idleLimit: TimeInterval
    private var session: URLSession?
    private var parser = EventStreamParser()

    init(url: URL, idleLimit: TimeInterval = 60) {
        self.url = url
        self.idleLimit = idleLimit
    }

    func start() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = idleLimit
        configuration.timeoutIntervalForResource = .infinity
        // The session holds on to its delegate until it is invalidated.
        let session = URLSession(configuration: configuration, delegate: self,
                                 delegateQueue: .main)
        self.session = session
        var request = URLRequest(url: url)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        session.dataTask(with: request).resume()
    }

    func cancel() {
        session?.invalidateAndCancel()
        session = nil
    }

    private func end(_ reason: String) {
        guard session != nil else { return }
        cancel()
        onEnded?(reason)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard session === self.session else {
            return completionHandler(.cancel)
        }
        let status = (response as? HTTPURLResponse)?.statusCode
        if status != 200 {
            completionHandler(.cancel)
            end("the server answered with status \(status.map(String.init) ?? "unknown")")
        } else if response.mimeType?.lowercased() != "text/event-stream" {
            completionHandler(.cancel)
            end("the server answered with content type \(response.mimeType ?? "unknown"), "
                + "not text/event-stream")
        } else {
            completionHandler(.allow)
            onReady?()
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        let events: [String]
        do {
            events = try parser.read(data)
        } catch {
            return end("\(error)")
        }
        // An event can cancel the connection, which drops the events after it.
        for event in events where session === self.session {
            onEvent?(event)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard session === self.session else { return }
        end(error?.localizedDescription ?? "the server ended the stream")
    }
}

/// JSON, as far as a path through it needs.
private enum JSON: Decodable {
    case string(String)
    case array([JSON])
    case object([String: JSON])
    case other

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let string = try? container.decode(String.self) {
            self = .string(string)
        } else if let array = try? container.decode([JSON].self) {
            self = .array(array)
        } else if let object = try? container.decode([String: JSON].self) {
            self = .object(object)
        } else {
            self = .other
        }
    }

    func at(_ key: String) -> JSON? {
        switch self {
        case .object(let object):
            return object[key]
        case .array(let array):
            // Only the plain decimal form of an index, so "01" and "+1" name nothing.
            return Int(key).flatMap {
                String($0) == key && array.indices.contains($0) ? array[$0] : nil
            }
        case .string, .other:
            return nil
        }
    }
}

/// The string at a path into JSON text. Each key in the path names a member
/// of an object or, as a number counting from 0, an element of an array.
/// There is none when the text is not JSON, or the path leads to something
/// other than a string, or to nothing.
func string(at path: [String], inJSON text: String) -> String? {
    var value = try? JSONDecoder().decode(JSON.self, from: Data(text.utf8))
    for key in path {
        value = value?.at(key)
    }
    guard case .string(let string)? = value else { return nil }
    return string
}

/// A change to a value file worth logging.
enum ValueFileChange: CustomStringConvertible {
    /// The file now holds a value it did not hold before.
    case wrote(file: String, value: String)
    case failed(problem: String)
    /// Writing the file stopped failing, because the file turned out to
    /// hold the value already.
    case recovered(file: String)

    var description: String {
        switch self {
        case .wrote(let file, _): return "wrote \(file)"
        case .failed(let problem): return "could not write the value file: \(problem)"
        case .recovered(let file): return "\(file) already holds the latest value"
        }
    }
}

/// The file that holds the string a path picks out of the JSON data of a
/// stream's events.
/// The file is changed in order on a queue of its own, so that waiting for
/// a reader's lock does not hold up the menu bar. Its methods, and the
/// callbacks they make, run on the main thread.
final class StreamValueFile {
    let settings: StreamSettings
    /// When the file was last written, if it has been since this was made.
    private(set) var writtenAt: Date?
    /// Why the file could not be written, if the last attempt failed.
    private(set) var problem: String?
    /// The last value an event gave, if one has since this was made.
    private(set) var latest: String?
    /// One queue for every value file, so that a write for settings that
    /// have since changed cannot land after a write for the new ones.
    private static let queue = DispatchQueue(label: "local.github-hookbar.value-file")

    init(_ settings: StreamSettings) {
        self.settings = settings
    }

    /// Writes the string the path picks out of an event's data to the file,
    /// unless the file holds exactly that already. Data without a string at
    /// the path changes nothing. The file is rewritten in place under an
    /// exclusive lock, as `withExclusiveLock` describes. Calls back with
    /// what changed, if anything did: the file, the problem with writing it,
    /// or that the file turned out to hold the value already after a
    /// problem.
    func take(_ data: String, at time: Date = Date(),
              completion: @escaping (ValueFileChange?) -> Void) {
        dispatchPrecondition(condition: .onQueue(.main))
        guard let value = string(at: settings.path, inJSON: data) else {
            return completion(nil)
        }
        latest = value
        let path = settings.file
        Self.queue.async {
            let wrote = Result {
                try withExclusiveLock(onFileAt: path) { file in
                    let bytes = Data(value.utf8)
                    guard try file.readToEnd() ?? Data() != bytes else { return false }
                    try replaceContents(of: file, with: bytes)
                    return true
                }
            }
            DispatchQueue.main.async { [self] in
                completion(record(wrote, value: value, at: time))
            }
        }
    }

    private func record(_ wrote: Result<Bool, Error>, value: String,
                        at time: Date) -> ValueFileChange? {
        let previous = problem
        switch wrote {
        case .success(true):
            writtenAt = time
            problem = nil
            return .wrote(file: settings.file, value: value)
        case .success(false):
            problem = nil
            return previous == nil ? nil : .recovered(file: settings.file)
        case .failure(let error):
            let problem = "\(error)"
            self.problem = problem
            return problem == previous ? nil : .failed(problem: problem)
        }
    }
}
