//! touch-gate-server carries out requests, such as GitHub actions, once a
//! security key approves them. ../README.md describes how to set it up, and
//! ../PROTOCOL.md defines the protocol it speaks.

mod github;
mod protocol;
mod server;
mod tcp;
#[cfg(test)]
mod tests;

use std::convert::Infallible;
use std::ffi::{OsStr, OsString};
use std::io;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;

fn main() -> ExitCode {
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    let [dir, address] = &args[..] else {
        eprintln!("usage: touch-gate-server STATE_DIR ADDRESS:PORT");
        return ExitCode::FAILURE;
    };
    let Err(e) = serve(dir, address);
    eprintln!("touch-gate-server: {e}");
    ExitCode::FAILURE
}

fn serve(dir: &OsStr, address: &OsStr) -> io::Result<Infallible> {
    let address = address
        .to_str()
        .ok_or_else(|| protocol::invalid("the address is not UTF-8"))?;
    let server = server::Server::load(PathBuf::from(dir), github::GitHub::system())?;
    let listener = TcpListener::bind(address)?;
    eprintln!("listening on {}", listener.local_addr()?);
    Arc::new(server).serve(listener)
}
