import Foundation

// Following a stream of server-sent events, as the HTML standard's
// EventSource does, and keeping in a file the value that a path picks out of
// the JSON data of its events.

/// The most bytes the string at the path in an event's data can hold.
let maximumValueLength = 32 * 1024 * 1024

/// Finds the string at a path in JSON text that arrives in pieces, and stops
/// as soon as it knows whether there is one. Each key in the path names a
/// member of an object or, as a number counting from 0 written plainly, an
/// element of an array. Nothing off the path is kept: the reader only looks
/// for where each other member or element ends. Given text that is not JSON,
/// it may find a string or not, but it does not fail. Reading fails once the
/// string grows past `limit` bytes.
struct JSONPathReader {
    enum Outcome: Equatable {
        case reading
        case found(String)
        case absent
    }

    private enum State {
        /// Before a value, which is on the path or is to be skipped.
        case value(onPath: Bool)
        /// Before a member's key, or the end of an object.
        case member
        case key
        /// After a key, which names the next path step or not.
        case colon(matches: Bool)
        /// In the string at the path.
        case target
        /// In an object, array or string off the path.
        case skipping(inString: Bool, escaped: Bool)
        /// In a number, true, false or null off the path.
        case scalar
        /// After a member or element, before a comma or the end.
        case afterValue
    }

    private enum Escape {
        case none
        case backslash
        case unicode(digits: Int, value: UInt16)
    }

    private(set) var outcome = Outcome.reading
    private let keys: [[UInt8]]
    /// Each key's index, when it is a number written plainly.
    private let indices: [Int?]
    private let limit: Int
    private var state = State.value(onPath: true)
    /// How many objects and arrays on the path the reader is inside.
    private var depth = 0
    /// Whether the innermost of those is an object.
    private var inObject = false
    /// Which element the reader is at, when the innermost of those is an array.
    private var element = 0
    /// How many objects and arrays deep the reader is in a value it skips.
    private var skipped = 0
    /// The key or string being read, as UTF-8, up to `kept` bytes.
    private var text: [UInt8] = []
    private var kept = 0
    private var escape = Escape.none
    private var highSurrogate: UInt16?

    init(path: [String], limit: Int = maximumValueLength) {
        keys = path.map { Array($0.utf8) }
        indices = path.map { key in Int(key).flatMap { $0 >= 0 && String($0) == key ? $0 : nil } }
        self.limit = limit
    }

    mutating func read(_ bytes: UnsafeRawBufferPointer) throws {
        for byte in bytes {
            guard outcome == .reading else { return }
            try read(byte)
        }
    }

    private mutating func read(_ byte: UInt8) throws {
        switch state {
        case .value(let onPath):
            if isJSONWhitespace(byte) {
                return
            } else if !onPath {
                switch byte {
                case quote: state = .skipping(inString: true, escaped: false)
                case openBrace, openBracket:
                    skipped = 1
                    state = .skipping(inString: false, escaped: false)
                case closeBracket: outcome = .absent
                default: state = .scalar
                }
            } else if depth == keys.count, byte == quote {
                startString(keeping: limit + 1)
                state = .target
            } else if depth < keys.count, byte == openBrace {
                depth += 1
                inObject = true
                state = .member
            } else if depth < keys.count, byte == openBracket, let index = indices[depth] {
                depth += 1
                inObject = false
                element = 0
                state = .value(onPath: index == 0)
            } else {
                outcome = .absent
            }
        case .member:
            if byte == quote {
                startString(keeping: keys[depth - 1].count + 1)
                state = .key
            } else if !isJSONWhitespace(byte) {
                outcome = .absent
            }
        case .key:
            if readString(byte) {
                state = .colon(matches: text == keys[depth - 1])
            }
        case .colon(let matches):
            if byte == colon {
                state = .value(onPath: matches)
            }
        case .target:
            if readString(byte) {
                outcome = .found(String(decoding: text, as: UTF8.self))
            } else if text.count > limit {
                throw Problem(description: "the server sent a value of more than \(limit) bytes")
            }
        case .skipping(true, let escaped):
            if escaped {
                state = .skipping(inString: true, escaped: false)
            } else if byte == backslash {
                state = .skipping(inString: true, escaped: true)
            } else if byte == quote {
                state = skipped == 0 ? .afterValue : .skipping(inString: false, escaped: false)
            }
        case .skipping(false, _):
            switch byte {
            case quote: state = .skipping(inString: true, escaped: false)
            case openBrace, openBracket: skipped += 1
            case closeBrace, closeBracket:
                skipped -= 1
                if skipped == 0 {
                    state = .afterValue
                }
            default: break
            }
        case .scalar:
            if byte == comma || byte == closeBrace || byte == closeBracket
                || isJSONWhitespace(byte) {
                state = .afterValue
                try read(byte)
            }
        case .afterValue:
            if byte == comma {
                if inObject {
                    state = .member
                } else {
                    element += 1
                    state = .value(onPath: indices[depth - 1] == element)
                }
            } else if !isJSONWhitespace(byte) {
                // The object or array on the path ended without the next step.
                outcome = .absent
            }
        }
    }

    private mutating func startString(keeping count: Int) {
        text.removeAll(keepingCapacity: true)
        kept = count
    }

    /// Reads a byte of a string, keeping what it stands for, and returns
    /// whether it ended the string.
    private mutating func readString(_ byte: UInt8) -> Bool {
        switch escape {
        case .none:
            if byte == quote {
                keepHighSurrogate()
                return true
            } else if byte == backslash {
                escape = .backslash
            } else {
                keep(byte)
            }
        case .backslash:
            escape = .none
            switch byte {
            case UInt8(ascii: "u"): escape = .unicode(digits: 0, value: 0)
            case UInt8(ascii: "b"): keep(0x08)
            case UInt8(ascii: "f"): keep(0x0c)
            case UInt8(ascii: "n"): keep(0x0a)
            case UInt8(ascii: "r"): keep(0x0d)
            case UInt8(ascii: "t"): keep(0x09)
            default: keep(byte)
            }
        case .unicode(let digits, let value):
            let unit = value << 4 | hexValue(byte)
            if digits < 3 {
                escape = .unicode(digits: digits + 1, value: unit)
            } else {
                escape = .none
                keep(unit: unit)
            }
        }
        return false
    }

    private mutating func keep(_ byte: UInt8) {
        keepHighSurrogate()
        if text.count < kept {
            text.append(byte)
        }
    }

    /// Keeps a UTF-16 code unit from a \u escape, pairing surrogates.
    private mutating func keep(unit: UInt16) {
        if let high = highSurrogate, (0xdc00...0xdfff).contains(unit) {
            highSurrogate = nil
            keep(scalar: 0x10000 + (UInt32(high - 0xd800) << 10 | UInt32(unit - 0xdc00)))
        } else {
            keepHighSurrogate()
            if (0xd800...0xdbff).contains(unit) {
                highSurrogate = unit
            } else {
                keep(scalar: UInt32(unit))
            }
        }
    }

    /// Keeps a high surrogate that no low surrogate followed as U+FFFD.
    private mutating func keepHighSurrogate() {
        if highSurrogate != nil {
            highSurrogate = nil
            keep(scalar: 0xfffd)
        }
    }

    private mutating func keep(scalar: UInt32) {
        for byte in (Unicode.Scalar(scalar) ?? "\u{fffd}").utf8 where text.count < kept {
            text.append(byte)
        }
    }
}

private let quote = UInt8(ascii: "\""), backslash = UInt8(ascii: "\\"), colon = UInt8(ascii: ":")
private let comma = UInt8(ascii: ","), space = UInt8(ascii: " ")
private let openBrace = UInt8(ascii: "{"), closeBrace = UInt8(ascii: "}")
private let openBracket = UInt8(ascii: "["), closeBracket = UInt8(ascii: "]")
private let lineFeed = UInt8(ascii: "\n"), carriageReturn = UInt8(ascii: "\r")
private let dataField = Array("data".utf8), byteOrderMarkBytes: [UInt8] = [0xef, 0xbb, 0xbf]

private func isJSONWhitespace(_ byte: UInt8) -> Bool {
    byte == space || byte == lineFeed || byte == carriageReturn || byte == UInt8(ascii: "\t")
}

/// The value of a hexadecimal digit, or 0 for any other byte.
private func hexValue(_ byte: UInt8) -> UInt16 {
    switch byte {
    case UInt8(ascii: "0")...UInt8(ascii: "9"): return UInt16(byte - UInt8(ascii: "0"))
    case UInt8(ascii: "a")...UInt8(ascii: "f"): return UInt16(byte - UInt8(ascii: "a") + 10)
    case UInt8(ascii: "A")...UInt8(ascii: "F"): return UInt16(byte - UInt8(ascii: "A") + 10)
    default: return 0
    }
}

/// Reads a server-sent event stream from pieces that may split it anywhere,
/// even within a line or a character, and returns, for each event it
/// completes, the string at a path in the JSON of the event's data, when
/// there is one. Fields other than `data` are ignored. Each event's data goes
/// to a `JSONPathReader` as it arrives, so it is never held whole, and the
/// rest of it is skipped once the reader knows the answer.
struct EventStreamParser {
    private enum Line {
        /// In the field name, of which this many bytes match "data".
        case name(matched: Int)
        /// In a data line's value, at its start or not.
        case value(atStart: Bool)
        /// In a line that is not a data line.
        case ignored
    }

    /// A reader that has read nothing, which each event starts with.
    private let newReader: JSONPathReader
    private var reader: JSONPathReader
    private var line = Line.name(matched: 0)
    /// Whether the event has had a data line.
    private var hasData = false
    /// How many bytes of a byte order mark the stream has started with, until
    /// a byte that is not part of one.
    private var byteOrderMark: Int? = 0
    /// Whether the last line ended with a carriage return, in which case a
    /// line feed straight after it is part of the same line ending.
    private var afterCarriageReturn = false

    init(path: [String], limit: Int = maximumValueLength) {
        newReader = JSONPathReader(path: path, limit: limit)
        reader = newReader
    }

    mutating func read(_ bytes: Data) throws -> [String] {
        var values: [String] = []
        try bytes.withUnsafeBytes { buffer in
            var index = 0
            while index < buffer.count {
                let byte = buffer[index]
                if afterCarriageReturn {
                    afterCarriageReturn = false
                    if byte == lineFeed {
                        index += 1
                        continue
                    }
                }
                if byte == lineFeed || byte == carriageReturn {
                    afterCarriageReturn = byte == carriageReturn
                    byteOrderMark = nil
                    if let value = try endLine() {
                        values.append(value)
                    }
                    index += 1
                    continue
                }
                if let matched = byteOrderMark {
                    if byte == byteOrderMarkBytes[matched] {
                        byteOrderMark = matched == 2 ? nil : matched + 1
                        index += 1
                        continue
                    }
                    byteOrderMark = nil
                    if matched > 0 {
                        line = .ignored
                    }
                }
                switch line {
                case .name(let matched):
                    if byte == colon, matched == 4 {
                        try startData()
                        line = .value(atStart: true)
                    } else if matched < 4, byte == dataField[matched] {
                        line = .name(matched: matched + 1)
                    } else {
                        line = .ignored
                    }
                    index += 1
                case .value(let atStart):
                    let end = buffer[index...].firstIndex { $0 == lineFeed || $0 == carriageReturn }
                        ?? buffer.count
                    line = .value(atStart: false)
                    try reader.read(UnsafeRawBufferPointer(
                        rebasing: buffer[(atStart && byte == space ? index + 1 : index)..<end]))
                    index = end
                case .ignored:
                    index = buffer[index...].firstIndex { $0 == lineFeed || $0 == carriageReturn }
                        ?? buffer.count
                }
            }
        }
        return values
    }

    /// Starts a data line, which follows any earlier one in the event after a
    /// line feed.
    private mutating func startData() throws {
        if hasData {
            try withUnsafeBytes(of: lineFeed) { try reader.read($0) }
        }
        hasData = true
    }

    /// Ends a line, and returns the value of the event that an empty line
    /// completes, if it has one.
    private mutating func endLine() throws -> String? {
        defer { line = .name(matched: 0) }
        switch line {
        case .name(0):
            defer {
                reader = newReader
                hasData = false
            }
            if hasData, case .found(let value) = reader.outcome {
                return value
            }
        case .name(4):
            // A line of just "data" is a data line with an empty value.
            try startData()
        default:
            break
        }
        return nil
    }
}

/// One connection to a server-sent event stream, which reports the string
/// at a path in the JSON data of each event that has one. The server must
/// answer with status 200 and the content type `text/event-stream`. A stream
/// that sends nothing for `idleLimit` seconds is taken to have died, as a
/// connection does while the computer sleeps.
/// All methods and callbacks run on the main thread.
final class EventStreamConnection: NSObject, Connection, URLSessionDataDelegate {
    var onReady: (() -> Void)?
    var onEnded: ((String) -> Void)?
    var onValue: ((String) -> Void)?
    private let url: URL
    private let idleLimit: TimeInterval
    private var session: URLSession?
    private var parser: EventStreamParser

    init(url: URL, path: [String], idleLimit: TimeInterval = 60) {
        self.url = url
        self.idleLimit = idleLimit
        parser = EventStreamParser(path: path)
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
        let values: [String]
        do {
            values = try parser.read(data)
        } catch {
            return end("\(error)")
        }
        // A value can cancel the connection, which drops the values after it.
        for value in values where session === self.session {
            onValue?(value)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard session === self.session else { return }
        end(error?.localizedDescription ?? "the server ended the stream")
    }
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

/// The file that holds the latest value from a stream's events.
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

    /// Writes a value from an event to the file, unless the file holds
    /// exactly that already. The file is rewritten in place under an
    /// exclusive lock, as `withExclusiveLock` describes. Calls back with
    /// what changed, if anything did: the file, the problem with writing it,
    /// or that the file turned out to hold the value already after a
    /// problem.
    func take(_ value: String, at time: Date = Date(),
              completion: @escaping (ValueFileChange?) -> Void) {
        dispatchPrecondition(condition: .onQueue(.main))
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
