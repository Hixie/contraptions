import AppKit

enum Sample {
    case reply(milliseconds: Double)
    case lost
}

/// The one's complement sum of big-endian 16-bit words used by ICMP.
func internetChecksum(_ bytes: [UInt8]) -> UInt16 {
    var sum = stride(from: 0, to: bytes.count, by: 2).reduce(UInt32(0)) { sum, index in
        let low = index + 1 < bytes.count ? bytes[index + 1] : 0
        return sum + (UInt32(bytes[index]) << 8 | UInt32(low))
    }
    while sum > 0xffff {
        sum = (sum & 0xffff) + (sum >> 16)
    }
    return ~UInt16(sum)
}

/// Sends one ICMP echo request at a time from an unprivileged ICMP datagram
/// socket, reporting each result on the main thread, with a pause between
/// samples. The socket is connected to the host, so it only receives packets
/// from the host, but those include replies to other processes' pings, each
/// with its IPv4 header in front, so a reply only counts when its identifier
/// and sequence number match the outstanding request. A request that cannot
/// be sent, for example because no network is usable, counts as lost at
/// once; one that gets no reply counts as lost at the deadline. All methods
/// run on the main thread, and samples are reported from the main queue,
/// never from within a method.
final class Pinger {
    var onSample: ((Sample) -> Void)?
    private let socket: Int32
    private let reader: DispatchSourceRead
    private let gap: DispatchTimeInterval
    private let deadline: DispatchTimeInterval
    private let identifier = UInt16(truncatingIfNeeded: getpid())
    private var sequence: UInt16 = 0
    private var sentAt: DispatchTime?
    /// The deadline of the outstanding request, or the end of the pause
    /// before the next one; nil exactly when sampling is stopped.
    private var pending: DispatchWorkItem?

    init(host: String, gap: DispatchTimeInterval, deadline: DispatchTimeInterval) {
        let socket = Darwin.socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)
        guard socket >= 0 else {
            fatalError("could not open an ICMP socket: \(String(cString: strerror(errno)))")
        }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        guard inet_pton(AF_INET, host, &address.sin_addr) == 1 else {
            fatalError("\(host) is not an IPv4 address")
        }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(socket, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard connected == 0 else {
            fatalError("could not connect to \(host): \(String(cString: strerror(errno)))")
        }
        self.socket = socket
        self.gap = gap
        self.deadline = deadline
        reader = DispatchSource.makeReadSource(fileDescriptor: socket, queue: .main)
        reader.setEventHandler { [weak self] in
            self?.receive()
        }
        reader.setCancelHandler {
            close(socket)
        }
        reader.resume()
    }

    deinit {
        reader.cancel()
    }

    /// Starts sampling, unless it is already running.
    func start() {
        dispatchPrecondition(condition: .onQueue(.main))
        if pending == nil {
            send()
        }
    }

    /// Stops sampling. A reply to the outstanding request, if any, is
    /// ignored.
    func stop() {
        dispatchPrecondition(condition: .onQueue(.main))
        pending?.cancel()
        pending = nil
        sentAt = nil
    }

    /// Samples only while the displays are awake, since nobody can see the
    /// results while they sleep, and calls `asleep` each time they go to
    /// sleep. Returns the observers of the displays, for removing.
    @discardableResult
    func runWhileDisplaysAwake(asleep: @escaping () -> Void) -> [NSObjectProtocol] {
        if CGDisplayIsAsleep(CGMainDisplayID()) == 0 {
            start()
        }
        let notifications = NSWorkspace.shared.notificationCenter
        return [
            notifications.addObserver(forName: NSWorkspace.screensDidSleepNotification,
                                      object: nil, queue: .main) { [weak self] _ in
                self?.stop()
                asleep()
            },
            notifications.addObserver(forName: NSWorkspace.screensDidWakeNotification,
                                      object: nil, queue: .main) { [weak self] _ in
                self?.start()
            },
        ]
    }

    /// The identifier and sequence number fields, which a reply echoes back.
    private var echoFields: [UInt8] {
        [UInt8(identifier >> 8), UInt8(identifier & 0xff),
         UInt8(sequence >> 8), UInt8(sequence & 0xff)]
    }

    private func send() {
        sequence &+= 1
        var packet: [UInt8] = [8, 0, 0, 0] + echoFields  // type 8: echo request
        let checksum = internetChecksum(packet)
        packet[2] = UInt8(checksum >> 8)
        packet[3] = UInt8(checksum & 0xff)
        sentAt = .now()
        let sent = Darwin.send(socket, packet, packet.count, 0) == packet.count
        schedule(after: sent ? deadline : .seconds(0)) { [weak self] in
            self?.finish(with: .lost)
        }
    }

    private func receive() {
        var buffer = [UInt8](repeating: 0, count: 1500)
        let count = recv(socket, &buffer, buffer.count, 0)
        guard let sentAt, count > 0 else { return }
        let icmpStart = Int(buffer[0] & 0x0f) * 4
        guard count >= icmpStart + 8,
              buffer[icmpStart] == 0,  // type 0: echo reply
              buffer[icmpStart + 4 ..< icmpStart + 8].elementsEqual(echoFields) else {
            return
        }
        let nanoseconds = DispatchTime.now().uptimeNanoseconds - sentAt.uptimeNanoseconds
        finish(with: .reply(milliseconds: Double(nanoseconds) / 1_000_000))
    }

    /// Schedules the next request, then reports the sample, so that a
    /// report that stops sampling also cancels that request.
    private func finish(with sample: Sample) {
        sentAt = nil
        schedule(after: gap) { [weak self] in
            self?.send()
        }
        onSample?(sample)
    }

    private func schedule(after delay: DispatchTimeInterval, _ work: @escaping () -> Void) {
        pending?.cancel()
        let item = DispatchWorkItem(block: work)
        pending = item
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: item)
    }
}
