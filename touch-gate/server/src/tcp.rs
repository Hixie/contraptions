//! TCP connections that close within about two minutes of their peer
//! vanishing.
//!
//! Keepalive probes start after a minute of silence and repeat every ten
//! seconds, and, on Linux, data left unacknowledged for two minutes also
//! closes the connection. A live peer answers the probes and acknowledges
//! data, however long it takes to send anything. A client that vanished
//! while its key was awaiting a touch would otherwise keep that key from
//! approving anything else, and a vanished GitHub would hold a thread
//! forever.

use std::io;
use std::net::{SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::os::fd::AsRawFd;

/// The TCP option that sets how long a connection is idle before keepalive
/// probes start.
#[cfg(target_vendor = "apple")]
const TCP_KEEPALIVE_IDLE: libc::c_int = libc::TCP_KEEPALIVE;
#[cfg(not(target_vendor = "apple"))]
const TCP_KEEPALIVE_IDLE: libc::c_int = libc::TCP_KEEPIDLE;

pub fn accept(listener: &TcpListener) -> io::Result<(TcpStream, SocketAddr)> {
    let (tcp, peer) = listener.accept()?;
    keep_alive(&tcp);
    Ok((tcp, peer))
}

pub fn connect(address: impl ToSocketAddrs) -> io::Result<TcpStream> {
    let tcp = TcpStream::connect(address)?;
    keep_alive(&tcp);
    Ok(tcp)
}

fn keep_alive(tcp: &TcpStream) {
    let options = [
        ("SO_KEEPALIVE", libc::SOL_SOCKET, libc::SO_KEEPALIVE, 1),
        (
            "the keepalive idle time",
            libc::IPPROTO_TCP,
            TCP_KEEPALIVE_IDLE,
            60,
        ),
        ("TCP_KEEPINTVL", libc::IPPROTO_TCP, libc::TCP_KEEPINTVL, 10),
        ("TCP_KEEPCNT", libc::IPPROTO_TCP, libc::TCP_KEEPCNT, 6),
    ];
    #[cfg(target_os = "linux")]
    let options = [
        options.as_slice(),
        &[(
            "TCP_USER_TIMEOUT",
            libc::IPPROTO_TCP,
            libc::TCP_USER_TIMEOUT,
            120_000,
        )],
    ]
    .concat();
    for (label, level, name, value) in options {
        let value: libc::c_int = value;
        let size =
            libc::socklen_t::try_from(size_of_val(&value)).expect("an int fits in socklen_t");
        // SAFETY: the descriptor is open for the duration of the call, and the
        // option value points at a live c_int of the size given.
        let result = unsafe {
            libc::setsockopt(
                tcp.as_raw_fd(),
                level,
                name,
                (&raw const value).cast(),
                size,
            )
        };
        if result != 0 {
            eprintln!("could not set {label}: {}", io::Error::last_os_error());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn option(tcp: &TcpStream, level: libc::c_int, name: libc::c_int) -> libc::c_int {
        let mut value: libc::c_int = 0;
        let mut size = libc::socklen_t::try_from(size_of_val(&value)).unwrap();
        // SAFETY: the descriptor is open, and the value and size point at live
        // variables of the sizes given.
        let result = unsafe {
            libc::getsockopt(
                tcp.as_raw_fd(),
                level,
                name,
                (&raw mut value).cast(),
                &raw mut size,
            )
        };
        assert_eq!(result, 0);
        value
    }

    fn assert_keeps_alive(tcp: &TcpStream) {
        assert_ne!(option(tcp, libc::SOL_SOCKET, libc::SO_KEEPALIVE), 0);
        assert_eq!(option(tcp, libc::IPPROTO_TCP, TCP_KEEPALIVE_IDLE), 60);
        assert_eq!(option(tcp, libc::IPPROTO_TCP, libc::TCP_KEEPINTVL), 10);
        assert_eq!(option(tcp, libc::IPPROTO_TCP, libc::TCP_KEEPCNT), 6);
        #[cfg(target_os = "linux")]
        assert_eq!(
            option(tcp, libc::IPPROTO_TCP, libc::TCP_USER_TIMEOUT),
            120_000
        );
    }

    #[test]
    fn accepted_and_connected_streams_keep_alive_but_plain_ones_do_not() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let plain = TcpStream::connect(address).unwrap();
        assert_eq!(option(&plain, libc::SOL_SOCKET, libc::SO_KEEPALIVE), 0);
        let (accepted, peer) = accept(&listener).unwrap();
        assert_eq!(peer, plain.local_addr().unwrap());
        assert_keeps_alive(&accepted);
        let connected = connect(address).unwrap();
        assert_keeps_alive(&connected);
    }
}
