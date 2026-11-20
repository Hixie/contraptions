import Foundation

// Reading chosen values out of a JSON document in one pass over its bytes.
// Everything else in the document is checked to be well formed and then
// passed over, without being kept.

/// Why a document could not be read, or why a value in it is not what was
/// wanted.
struct JSONError: Error, CustomStringConvertible {
    let description: String
}

/// A value at one of the wanted paths. A number is kept as it is written.
/// An object or an array is not kept.
enum JSONValue: Equatable {
    case string(String)
    case number(String)
    case bool(Bool)
    case null
    case container

    var string: String? {
        if case .string(let text) = self { text } else { nil }
    }

    var integer: Int? {
        if case .number(let text) = self { Int(text) } else { nil }
    }

    var boolean: Bool? {
        if case .bool(let value) = self { value } else { nil }
    }

    /// A time as GitHub writes times: in ISO 8601, or in seconds since 1970.
    var date: Date? {
        switch self {
        case .string(let text): try? Date(text, strategy: .iso8601)
        case .number(let text): Double(text).flatMap { $0.isFinite ? Date(timeIntervalSince1970: $0) : nil }
        default: nil
        }
    }
}

/// The values a document is read for. Each is named by its path: the keys
/// from the outermost object inward, joined by dots.
struct JSONPaths {
    fileprivate indirect enum Node {
        case value(path: String)
        case object(path: String, [String: Node])
    }

    fileprivate let root: [String: Node]
    fileprivate let paths: Set<String>

    init(_ paths: [String]) {
        let keys = paths.map { $0.split(separator: ".", omittingEmptySubsequences: false)[...] }
        precondition(!keys.contains { $0.contains("") }, "a path has an empty key")
        self.paths = Set(paths)
        root = Self.children(keys, path: "")
    }

    /// The nodes for the keys that come first in `keys`, which are what
    /// follows `path` in each wanted path.
    private static func children(_ keys: [ArraySlice<Substring>], path: String) -> [String: Node] {
        let byKey = Dictionary(grouping: keys) { String($0.first!) }
        return Dictionary(uniqueKeysWithValues: byKey.map { key, rest in
            let inner = path.isEmpty ? key : "\(path).\(key)"
            let tails = rest.map { $0.dropFirst() }
            guard !tails.contains(where: \.isEmpty) else {
                precondition(tails.count == 1, "\(inner) is wanted twice, or both as a value and as an object")
                return (key, .value(path: inner))
            }
            return (key, .object(path: inner, children(tails, path: inner)))
        })
    }

    /// Reads the wanted values out of a document that is a JSON object.
    func read(_ document: Data) throws(JSONError) -> JSONFields {
        let result = document.withUnsafeBytes { raw in
            Result { () throws(JSONError) -> [String: JSONValue] in
                var reader = JSONReader(bytes: raw)
                var found: [String: JSONValue] = [:]
                guard reader.peek() == "{" else { throw reader.unexpected("where an object should start") }
                try reader.readObject(root, into: &found)
                guard reader.peek() == nil else { throw reader.unexpected("after the document") }
                return found
            }
        }
        return JSONFields(paths: paths, values: try result.get())
    }
}

/// The values found at the wanted paths. A path whose key is missing, or
/// that runs through a null, has no value. Of a key given twice in an
/// object, the last counts.
struct JSONFields {
    fileprivate let paths: Set<String>
    fileprivate let values: [String: JSONValue]

    func value(_ path: String) -> JSONValue? {
        precondition(paths.contains(path), "\(path) was not read")
        return values[path]
    }

    /// The value at `path` as `read` makes it, or nil if there is no value
    /// there or it is null. Throws if `read` cannot make anything of it.
    func optional<T>(_ path: String, _ kind: String, _ read: (JSONValue) -> T?) throws(JSONError) -> T? {
        switch value(path) {
        case nil, .null?:
            return nil
        case let value?:
            guard let read = read(value) else { throw JSONError(description: "\(path) is not \(kind)") }
            return read
        }
    }

    func required<T>(_ path: String, _ kind: String, _ read: (JSONValue) -> T?) throws(JSONError) -> T {
        guard let value = try optional(path, kind, read) else {
            throw JSONError(description: "\(path) has no value")
        }
        return value
    }

    func string(_ path: String) throws(JSONError) -> String { try required(path, "a string", \.string) }
    func optionalString(_ path: String) throws(JSONError) -> String? { try optional(path, "a string", \.string) }
    func int(_ path: String) throws(JSONError) -> Int { try required(path, "a whole number", \.integer) }
    func optionalBool(_ path: String) throws(JSONError) -> Bool? { try optional(path, "true or false", \.boolean) }
    func date(_ path: String) throws(JSONError) -> Date { try required(path, "a time", \.date) }
    func optionalDate(_ path: String) throws(JSONError) -> Date? { try optional(path, "a time", \.date) }
}

/// Reads RFC 8259 JSON, encoded in UTF-8, from the start of `bytes`.
private struct JSONReader {
    let bytes: UnsafeRawBufferPointer
    var at = 0

    func unexpected(_ context: String) -> JSONError {
        JSONError(description: at < bytes.count
                  ? "unexpected byte 0x\(String(bytes[at], radix: 16)) \(context), at offset \(at)"
                  : "the document ends \(context)")
    }

    /// Passes over whitespace, and returns the byte after it as a scalar
    /// without passing over it, or nil at the end of the document.
    mutating func peek() -> Unicode.Scalar? {
        while at < bytes.count {
            switch bytes[at] {
            case 0x20, 0x09, 0x0A, 0x0D: at += 1
            default: return Unicode.Scalar(bytes[at])
            }
        }
        return nil
    }

    mutating func consume(_ expected: Unicode.Scalar, _ context: String) throws(JSONError) {
        guard peek() == expected else { throw unexpected(context) }
        at += 1
    }

    /// Reads the object that starts at the next byte, keeping the values at
    /// the paths `wanted` leads to.
    mutating func readObject(_ wanted: [String: JSONPaths.Node],
                             into found: inout [String: JSONValue]) throws(JSONError) {
        at += 1
        if peek() == "}" {
            at += 1
            return
        }
        while true {
            guard peek() == "\"" else { throw unexpected("where a key should start") }
            // A key holding half of a surrogate pair is no key that is wanted.
            let key = try string(keep: true)
            try consume(":", "after a key")
            switch key.flatMap({ wanted[$0] }) {
            case .value(let path)?:
                found[path] = try value()
            case .object(let path, let inner)?:
                found = found.filter { !$0.key.hasPrefix("\(path).") }
                switch peek() {
                case "{": try readObject(inner, into: &found)
                case "n": try literal("null")
                default:
                    _ = try value()
                    throw JSONError(description: "\(path) is not an object")
                }
            case nil:
                try skipValue()
            }
            let next = peek()
            guard next == "," || next == "}" else { throw unexpected("after a value in an object") }
            at += 1
            if next == "}" { return }
        }
    }

    mutating func value() throws(JSONError) -> JSONValue {
        switch peek() {
        case "\"":
            let start = at
            guard let text = try string(keep: true) else {
                throw JSONError(description: "the string at offset \(start) holds half of a surrogate pair")
            }
            return .string(text)
        case "{", "[":
            try skipValue()
            return .container
        default:
            return try scalar(keep: true)
        }
    }

    /// Reads a value of any kind and keeps nothing of it. Containers are
    /// followed with a stack rather than by recursion, so that no document
    /// can nest deeply enough to exhaust the call stack.
    mutating func skipValue() throws(JSONError) {
        // For each container the reader is inside, whether it is an object.
        var open: [Bool] = []
        while true {
            // A value starts here.
            switch peek() {
            case "{", "[":
                let isObject = bytes[at] == UInt8(ascii: "{")
                let close: Unicode.Scalar = isObject ? "}" : "]"
                at += 1
                if peek() == close {
                    at += 1
                } else {
                    open.append(isObject)
                    if isObject { try skipKey() }
                    continue
                }
            case "\"":
                _ = try string(keep: false)
            default:
                _ = try scalar(keep: false)
            }
            // A value ended here, and so may the containers around it.
            while let isObject = open.last {
                let next = peek()
                if next == "," {
                    at += 1
                    if isObject { try skipKey() }
                    break
                }
                let close: Unicode.Scalar = isObject ? "}" : "]"
                guard next == close else {
                    throw unexpected(isObject ? "after a value in an object" : "after a value in an array")
                }
                at += 1
                open.removeLast()
            }
            if open.isEmpty { return }
        }
    }

    /// Reads a key that is not wanted, and the colon after it.
    private mutating func skipKey() throws(JSONError) {
        guard peek() == "\"" else { throw unexpected("where a key should start") }
        _ = try string(keep: false)
        try consume(":", "after a key")
    }

    /// Reads true, false, null, or a number, which is kept only if `keep`.
    private mutating func scalar(keep: Bool) throws(JSONError) -> JSONValue {
        switch peek() {
        case "t":
            try literal("true")
            return .bool(true)
        case "f":
            try literal("false")
            return .bool(false)
        case "n":
            try literal("null")
            return .null
        case "-", .some("0"..."9"):
            let start = at
            try number()
            return .number(keep ? String(decoding: bytes[start..<at], as: UTF8.self) : "")
        default:
            throw unexpected("where a value should start")
        }
    }

    private mutating func literal(_ word: StaticString) throws(JSONError) {
        let matches = word.withUTF8Buffer { expected in
            bytes.count - at >= expected.count && bytes[at..<at + expected.count].elementsEqual(expected)
        }
        guard matches else { throw unexpected("where \(word) should be") }
        at += word.utf8CodeUnitCount
    }

    private mutating func number() throws(JSONError) {
        if bytes[at] == UInt8(ascii: "-") { at += 1 }
        if at < bytes.count, bytes[at] == UInt8(ascii: "0") {
            at += 1
        } else if digits() == 0 {
            throw unexpected("in a number")
        }
        if at < bytes.count, bytes[at] == UInt8(ascii: ".") {
            at += 1
            guard digits() > 0 else { throw unexpected("in a number") }
        }
        if at < bytes.count, bytes[at] | 0x20 == UInt8(ascii: "e") {
            at += 1
            if at < bytes.count, bytes[at] == UInt8(ascii: "+") || bytes[at] == UInt8(ascii: "-") { at += 1 }
            guard digits() > 0 else { throw unexpected("in a number") }
        }
    }

    /// Passes over decimal digits, and says how many there were.
    private mutating func digits() -> Int {
        let start = at
        while at < bytes.count, (0x30...0x39).contains(bytes[at]) { at += 1 }
        return at - start
    }

    /// Reads the string that starts at the next byte, and returns its text
    /// if `keep` and it has one. A string that holds half of a surrogate
    /// pair, which RFC 8259 allows, has no text, since no string can
    /// represent it.
    mutating func string(keep: Bool) throws(JSONError) -> String? {
        at += 1
        // The text of the string up to `start`, once an escape is found.
        var text: [UInt8] = []
        var whole = true
        var start = at
        while at < bytes.count {
            switch bytes[at] {
            case UInt8(ascii: "\""):
                let end = at
                at += 1
                guard keep, whole else { return nil }
                guard !text.isEmpty else { return String(decoding: bytes[start..<end], as: UTF8.self) }
                text.append(contentsOf: bytes[start..<end])
                return String(decoding: text, as: UTF8.self)
            case UInt8(ascii: "\\"):
                if keep { text.append(contentsOf: bytes[start..<at]) }
                at += 1
                if let scalar = try escape(keep: keep) {
                    if keep { UTF8.encode(scalar) { text.append($0) } }
                } else {
                    whole = false
                }
                start = at
            case 0..<0x20:
                throw unexpected("in a string")
            case 0x80...:
                try utf8Sequence()
            default:
                at += 1
            }
        }
        throw unexpected("in a string")
    }

    /// Reads what follows a backslash in a string, and returns the scalar it
    /// stands for. A \u escape gives nil in a string that is not kept, where
    /// it is only checked to be four hexadecimal digits, and when it stands
    /// for half of a surrogate pair without the other half.
    private mutating func escape(keep: Bool) throws(JSONError) -> Unicode.Scalar? {
        guard at < bytes.count else { throw unexpected("in an escape") }
        let escaped = Unicode.Scalar(bytes[at])
        at += 1
        switch escaped {
        case "\"", "\\", "/": return escaped
        case "b": return "\u{8}"
        case "f": return "\u{C}"
        case "n": return "\n"
        case "r": return "\r"
        case "t": return "\t"
        case "u":
            let unit = try hexUnit()
            guard keep else { return nil }
            if (0xD800...0xDBFF).contains(unit), bytes.count - at >= 2,
               bytes[at] == UInt8(ascii: "\\"), bytes[at + 1] == UInt8(ascii: "u") {
                at += 2
                let low = try hexUnit()
                if (0xDC00...0xDFFF).contains(low) {
                    return Unicode.Scalar(0x10000 + (unit - 0xD800) << 10 + (low - 0xDC00))
                }
            }
            return Unicode.Scalar(unit)
        default:
            at -= 1
            throw unexpected("after a backslash")
        }
    }

    /// Reads the four hexadecimal digits of a \u escape.
    private mutating func hexUnit() throws(JSONError) -> UInt32 {
        var unit: UInt32 = 0
        for _ in 0..<4 {
            guard at < bytes.count else { throw unexpected("in a \\u escape") }
            let byte = bytes[at]
            let digit: UInt8
            switch byte {
            case 0x30...0x39: digit = byte - 0x30
            case 0x41...0x46: digit = byte - 0x37
            case 0x61...0x66: digit = byte - 0x57
            default: throw unexpected("in a \\u escape")
            }
            unit = unit << 4 | UInt32(digit)
            at += 1
        }
        return unit
    }

    /// Passes over one character of more than one byte, checking that it is
    /// UTF-8 as RFC 3629 defines it.
    private mutating func utf8Sequence() throws(JSONError) {
        // How many bytes follow the first, and the range the second may be
        // in; the others may be anything from 0x80 to 0xBF.
        let following: Int
        let second: ClosedRange<UInt8>
        switch bytes[at] {
        case 0xC2...0xDF: (following, second) = (1, 0x80...0xBF)
        case 0xE0: (following, second) = (2, 0xA0...0xBF)
        case 0xE1...0xEC, 0xEE...0xEF: (following, second) = (2, 0x80...0xBF)
        case 0xED: (following, second) = (2, 0x80...0x9F)
        case 0xF0: (following, second) = (3, 0x90...0xBF)
        case 0xF1...0xF3: (following, second) = (3, 0x80...0xBF)
        case 0xF4: (following, second) = (3, 0x80...0x8F)
        default: throw unexpected("that is not UTF-8")
        }
        guard bytes.count - at > following, second.contains(bytes[at + 1]),
              bytes[(at + 2)..<(at + 1 + following)].allSatisfy({ (0x80...0xBF).contains($0) }) else {
            throw unexpected("that starts a sequence that is not UTF-8")
        }
        at += 1 + following
    }
}
