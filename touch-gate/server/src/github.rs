//! Calls to the GitHub REST API, over HTTP/1.0 and TLS.

use std::fmt::Write as _;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use rustls::pki_types::pem::PemObject;
use rustls::pki_types::{CertificateDer, ServerName};
use rustls::{ClientConfig, ClientConnection, RootCertStore, StreamOwned};

use crate::protocol::invalid;

pub struct GitHub {
    host: String,
    port: u16,
    /// A PEM file of the certificate authorities to trust, read for each
    /// call so that updates to it apply without a restart.
    roots: PathBuf,
}

struct Response {
    status: u16,
    location: Option<String>,
    body: String,
}

impl GitHub {
    pub fn system() -> GitHub {
        GitHub::new(
            "api.github.com",
            443,
            Path::new("/etc/ssl/certs/ca-certificates.crt"),
        )
    }

    pub fn new(host: &str, port: u16, roots: &Path) -> GitHub {
        GitHub {
            host: host.to_owned(),
            port,
            roots: roots.to_owned(),
        }
    }

    /// Prepares to make calls with the personal access token `pat`, reading
    /// the certificate authorities to trust.
    pub fn session(&self, pat: String) -> Result<Session<'_>, String> {
        let tls = self
            .tls()
            .map_err(|e| format!("cannot read {}: {e}", self.roots.display()))?;
        Ok(Session {
            github: self,
            tls,
            pat,
        })
    }

    fn tls(&self) -> io::Result<Arc<ClientConfig>> {
        let mut store = RootCertStore::empty();
        for cert in CertificateDer::pem_file_iter(&self.roots).map_err(io::Error::other)? {
            store
                .add(cert.map_err(io::Error::other)?)
                .map_err(io::Error::other)?;
        }
        let tls = ClientConfig::builder_with_protocol_versions(&[&rustls::version::TLS13])
            .with_root_certificates(store)
            .with_no_client_auth();
        Ok(Arc::new(tls))
    }
}

/// Calls made with one personal access token.
pub struct Session<'a> {
    github: &'a GitHub,
    tls: Arc<ClientConfig>,
    pat: String,
}

impl Session<'_> {
    /// Returns a message and the new pull request's number, or 0 if GitHub
    /// did not say which pull request it created.
    pub fn create_pull_request(
        &self,
        repository: &str,
        head: &str,
        base: &str,
        title: &str,
        body: &str,
    ) -> Result<(String, u64), String> {
        let json = format!(
            "{{\"head\":{},\"base\":{},\"title\":{},\"body\":{}}}",
            json(head),
            json(base),
            json(title),
            json(body)
        );
        let response = self.call("POST", &format!("/repos/{repository}/pulls"), &json)?;
        if response.status != 201 {
            return Err(response.failure());
        }
        let number = response
            .location
            .as_deref()
            .and_then(|l| l.rsplit('/').next()?.parse::<u64>().ok());
        Ok(match number {
            Some(number) => (
                format!("https://github.com/{repository}/pull/{number}"),
                number,
            ),
            None => (
                format!("created a pull request in {repository}, but GitHub did not say which"),
                0,
            ),
        })
    }

    /// Empty `method` and `sha` strings are left out of the call.
    pub fn merge_pull_request(
        &self,
        repository: &str,
        number: u64,
        method: &str,
        sha: &str,
    ) -> Result<String, String> {
        let mut fields = Vec::new();
        for (name, value) in [("merge_method", method), ("sha", sha)] {
            if !value.is_empty() {
                fields.push(format!("\"{name}\":{}", json(value)));
            }
        }
        let json = format!("{{{}}}", fields.join(","));
        let response = self.call(
            "PUT",
            &format!("/repos/{repository}/pulls/{number}/merge"),
            &json,
        )?;
        if response.status != 200 {
            return Err(response.failure());
        }
        Ok(format!(
            "merged https://github.com/{repository}/pull/{number}"
        ))
    }

    fn call(&self, method: &str, path: &str, body: &str) -> Result<Response, String> {
        let (host, pat) = (&self.github.host, &self.pat);
        let exchange = || -> io::Result<Response> {
            let name = ServerName::try_from(host.clone()).map_err(io::Error::other)?;
            let connection =
                ClientConnection::new(self.tls.clone(), name).map_err(io::Error::other)?;
            let tcp = crate::tcp::connect((host.as_str(), self.github.port))?;
            let mut tls = StreamOwned::new(connection, tcp);
            let request = format!(
                "{method} {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: touch-gate\r\n\
                 Authorization: Bearer {pat}\r\nAccept: application/vnd.github+json\r\n\
                 X-GitHub-Api-Version: 2022-11-28\r\nContent-Type: application/json\r\n\
                 Content-Length: {}\r\n\r\n{body}",
                body.len()
            );
            tls.write_all(request.as_bytes())?;
            let mut response = Vec::new();
            tls.read_to_end(&mut response)?;
            parse_response(&response)
        };
        exchange().map_err(|e| format!("could not talk to GitHub: {e}"))
    }
}

impl Response {
    fn failure(&self) -> String {
        format!(
            "GitHub replied with status {}: {}",
            self.status,
            self.body.trim()
        )
    }
}

fn parse_response(bytes: &[u8]) -> io::Result<Response> {
    let text = String::from_utf8_lossy(bytes);
    let (head, body) = text
        .split_once("\r\n\r\n")
        .ok_or_else(|| invalid("GitHub's reply has no end of headers"))?;
    let mut lines = head.split("\r\n");
    let status = lines
        .next()
        .and_then(|line| line.split(' ').nth(1)?.parse().ok())
        .ok_or_else(|| invalid("GitHub's reply has no status"))?;
    let location = lines
        .filter_map(|line| line.split_once(':'))
        .find(|(name, _)| name.eq_ignore_ascii_case("location"))
        .map(|(_, value)| value.trim().to_owned());
    Ok(Response {
        status,
        location,
        body: body.to_owned(),
    })
}

fn json(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if c < ' ' => {
                write!(out, "\\u{:04x}", u32::from(c)).expect("writing to a String succeeds")
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_escapes_quotes_backslashes_and_controls() {
        assert_eq!(json("plain é"), "\"plain é\"");
        assert_eq!(json("a\"b\\c\nd\u{1}"), "\"a\\\"b\\\\c\\u000ad\\u0001\"");
    }

    #[test]
    fn parses_status_location_and_body() {
        let r = parse_response(
            b"HTTP/1.1 201 Created\r\nX: y\r\nlocation: https://h/p/7 \r\n\r\n{\"a\":1}",
        )
        .unwrap();
        assert_eq!(
            (r.status, r.location.as_deref(), r.body.as_str()),
            (201, Some("https://h/p/7"), "{\"a\":1}")
        );
        let r = parse_response(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n").unwrap();
        assert_eq!((r.status, r.location, r.body.as_str()), (404, None, ""));
    }

    #[test]
    fn rejects_malformed_responses() {
        assert!(parse_response(b"HTTP/1.1 200 OK\r\n").is_err());
        assert!(parse_response(b"HTTP/1.1 OK\r\n\r\n").is_err());
        assert!(parse_response(b"").is_err());
    }
}
