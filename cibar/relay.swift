import Foundation
import Network

// The connection to a relay of GitHub webhook deliveries.

/// GitHub caps webhook payloads at 25 MB, and the request around one adds a
/// few kilobytes of headers.
let maximumRequestLength = 32 * 1024 * 1024

/// One connection to the relay, which sends each delivery as a frame and
/// nothing in between. A frame is the length of an HTTP request, as a 32-bit
/// big-endian integer, followed by the request exactly as the relay received
/// it. It reports when the connection is up, each delivery, and once that
/// the connection has ended; a frame that the end of the connection cuts
/// short is not a delivery. All methods and callbacks run on the main
/// thread.
final class RelayConnection: Connection {
    var onReady: (() -> Void)?
    var onDelivery: ((Delivery) -> Void)?
    var onEnded: ((String) -> Void)?
    private let connection: NWConnection
    private var buffer = Data()
    private var ended = false

    init(host: String, port: UInt16) {
        // The relay sends nothing while no deliveries arrive, so keepalive
        // probes are what notice a connection that died silently, for
        // example while the computer slept.
        let tcp = NWProtocolTCP.Options()
        tcp.enableKeepalive = true
        tcp.keepaliveIdle = 60
        tcp.keepaliveInterval = 10
        tcp.keepaliveCount = 3
        connection = NWConnection(host: NWEndpoint.Host(host),
                                  port: NWEndpoint.Port(integerLiteral: port),
                                  using: NWParameters(tls: nil, tcp: tcp))
    }

    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                if self?.ended == false {
                    self?.onReady?()
                }
            case .waiting(let error), .failed(let error):
                self?.end(describe(error))
            default:
                break
            }
        }
        connection.start(queue: .main)
        receive()
    }

    func cancel() {
        ended = true
        connection.cancel()
    }

    private func end(_ reason: String) {
        guard !ended else { return }
        cancel()
        onEnded?(reason)
    }

    private func receive() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) {
            [weak self] data, _, isComplete, error in
            guard let self, !ended else { return }
            if let data {
                buffer.append(data)
                readFrames()
            }
            if ended {
                return
            } else if let error {
                end(describe(error))
            } else if isComplete {
                end("the relay closed the connection")
            } else {
                receive()
            }
        }
    }

    /// Takes every complete frame out of the buffer. Consumed bytes are
    /// removed rather than sliced off, so that their storage is freed.
    private func readFrames() {
        while !ended, buffer.count >= 4 {
            let length = buffer.prefix(4).reduce(0) { $0 << 8 | Int($1) }
            guard length <= maximumRequestLength else {
                end("the relay sent a frame of \(length) bytes")
                return
            }
            let frameEnd = buffer.startIndex + 4 + length
            guard buffer.endIndex >= frameEnd else { return }
            if let delivery = Delivery(buffer[(buffer.startIndex + 4)..<frameEnd]) {
                onDelivery?(delivery)
            } else {
                log("ignoring a frame from the relay that is not an HTTP request")
            }
            buffer.removeSubrange(..<frameEnd)
        }
    }
}

func describe(_ error: NWError) -> String {
    switch error {
    case .posix(let code):
        return String(cString: strerror(code.rawValue))
    case .dns(let code):
        return "could not look up the relay's address (\(code))"
    default:
        return error.localizedDescription
    }
}

/// One delivery: the headers of the HTTP request that carried it, and its
/// body, exactly as GitHub sent it.
struct Delivery {
    /// The header values, by header name in lowercase. Proxies on the way
    /// can change the case of header names.
    let headers: [String: String]
    let body: Data

    /// Splits an HTTP request into its headers and body.
    init?(_ request: Data) {
        guard let blank = request.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        var headers: [String: String] = [:]
        // The first line is the request line, not a header. The values are
        // bytes, read as Latin-1, which every byte is.
        for line in request[..<blank.lowerBound].split(separator: UInt8(ascii: "\n")).dropFirst() {
            let text = String(String.UnicodeScalarView(
                (line.last == UInt8(ascii: "\r") ? line.dropLast() : line).map { Unicode.Scalar($0) }))
            let parts = text.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            if parts.count == 2 {
                headers[parts[0].lowercased()] = parts[1].trimmingCharacters(in: .whitespaces)
            }
        }
        self.headers = headers
        body = Data(request[blank.upperBound...])
    }

    var event: String { headers["x-github-event"] ?? "" }

    /// When GitHub made the delivery, from its X-GitHub-Delivery header, or
    /// nothing when the header is not a time-based (version 1) RFC 4122
    /// UUID. Such a UUID holds a count of 100-nanosecond intervals since the
    /// start of the Gregorian calendar, 15 October 1582, split across its
    /// first eight bytes.
    var created: Date? {
        guard let id = headers["x-github-delivery"], let bytes = UUID(uuidString: id)?.uuid,
              bytes.6 >> 4 == 1, bytes.8 >> 6 == 2 else {
            return nil
        }
        let parts: [(UInt8, Int)] = [(bytes.6 & 0x0f, 56), (bytes.7, 48), (bytes.4, 40), (bytes.5, 32),
                                     (bytes.0, 24), (bytes.1, 16), (bytes.2, 8), (bytes.3, 0)]
        let ticks = parts.reduce(UInt64(0)) { $0 | UInt64($1.0) << $1.1 }
        let secondsFromGregorianStartToUnixEpoch: TimeInterval = 12_219_292_800
        return Date(timeIntervalSince1970: TimeInterval(ticks) / 10_000_000
                    - secondsFromGregorianStartToUnixEpoch)
    }
}
