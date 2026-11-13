//! The server side of the protocol.

use std::collections::{HashSet, VecDeque};
use std::fs;
use std::io::{self, Read, Write};
use std::net::{IpAddr, Shutdown, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex, PoisonError};
use std::thread;

use ring::digest::{SHA256, digest};
use ring::rand::{SecureRandom, SystemRandom};
use ring::signature::{ECDSA_P256_SHA256_ASN1, UnparsedPublicKey};
use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};

use crate::github::GitHub;
use crate::protocol::*;
use crate::tcp;

/// The number of connections that may be waiting for their secret at once.
pub const PENDING_LIMIT: usize = 64;

/// The number of connections from one network that may be waiting for
/// their secret at once. A network is an IPv4 address or an IPv6 /64.
pub const PENDING_PER_NETWORK_LIMIT: usize = 4;

pub struct Server {
    dir: PathBuf,
    secret: [u8; SECRET_LEN],
    cert: CertificateDer<'static>,
    tls: Arc<ServerConfig>,
    github: GitHub,
    /// The public keys of the keys that have been sent a challenge whose
    /// answer has not yet arrived. Keys are identified by their public keys,
    /// so that two names for one key share one entry.
    awaiting: Mutex<HashSet<Vec<u8>>>,
}

/// Records that a key is waiting for a touch, until it is dropped.
struct AwaitingTouch<'a> {
    awaiting: &'a Mutex<HashSet<Vec<u8>>>,
    public: &'a [u8],
}

impl Drop for AwaitingTouch<'_> {
    fn drop(&mut self) {
        let mut awaiting = self.awaiting.lock().unwrap_or_else(PoisonError::into_inner);
        awaiting.remove(self.public);
    }
}

/// The connections waiting for their secret, oldest first, each with its
/// ID and its peer's network.
type Pending = Mutex<VecDeque<(u64, IpAddr, TcpStream)>>;

impl Server {
    pub fn load(dir: PathBuf, github: GitHub) -> io::Result<Server> {
        let secret = fs::read(dir.join("secret"))?
            .try_into()
            .map_err(|_| invalid(format!("the secret must be {SECRET_LEN} bytes long")))?;
        let cert = CertificateDer::from(fs::read(dir.join("cert.der"))?);
        let key = PrivateKeyDer::try_from(fs::read(dir.join("key.der"))?).map_err(invalid)?;
        let tls = ServerConfig::builder_with_protocol_versions(&[&rustls::version::TLS13])
            .with_no_client_auth()
            .with_single_cert(vec![cert.clone()], key)
            .map_err(io::Error::other)?;
        Ok(Server {
            dir,
            secret,
            cert,
            tls: Arc::new(tls),
            github,
            awaiting: Mutex::default(),
        })
    }

    pub fn serve(self: Arc<Self>, listener: TcpListener) -> ! {
        let pending: Arc<Pending> = Arc::default();
        let mut next_id = 0u64;
        loop {
            let (tcp, peer) = match tcp::accept(&listener) {
                Ok(accepted) => accepted,
                Err(e) => {
                    eprintln!("could not accept a connection: {e}");
                    continue;
                }
            };
            let Ok(handle) = tcp.try_clone() else {
                continue;
            };
            let id = next_id;
            next_id += 1;
            let network = network(peer.ip());
            let mut queue = pending.lock().unwrap();
            let networks: Vec<IpAddr> = queue.iter().map(|(_, network, _)| *network).collect();
            if let Some((_, _, evicted)) =
                eviction(&networks, network).and_then(|i| queue.remove(i))
            {
                let _ = evicted.shutdown(Shutdown::Both);
            }
            queue.push_back((id, network, handle));
            drop(queue);
            let (server, shared_pending) = (self.clone(), pending.clone());
            let spawned =
                thread::Builder::new().spawn(move || server.connection(id, tcp, &shared_pending));
            if let Err(e) = spawned {
                eprintln!("could not start a thread for a connection: {e}");
                pending.lock().unwrap().retain(|(other, _, _)| *other != id);
            }
        }
    }

    fn connection(&self, id: u64, tcp: TcpStream, pending: &Pending) {
        let peer = tcp
            .peer_addr()
            .map_or_else(|e| e.to_string(), |a| a.to_string());
        let connection =
            ServerConnection::new(self.tls.clone()).expect("the TLS configuration is valid");
        let mut tls = StreamOwned::new(connection, tcp);
        let secret = read_array::<SECRET_LEN>(&mut tls);
        pending.lock().unwrap().retain(|(other, _, _)| *other != id);
        // The comparison takes the same time wherever the secrets differ.
        let matches = secret.is_ok_and(|s| {
            s.iter()
                .zip(&self.secret)
                .fold(0, |acc, (a, b)| acc | (a ^ b))
                == 0
        });
        if !matches {
            eprintln!("{peer}: closed the connection: the secret did not arrive or did not match");
            return;
        }
        let (status, message, results) = match self.exchange(&mut tls, &peer) {
            Ok((message, results)) => (OK, message, results),
            Err(message) => (FAILED, message, Vec::new()),
        };
        eprintln!(
            "{peer}: {}: {message}",
            if status == OK { "succeeded" } else { "failed" }
        );
        let message = message.replace('\0', "");
        let mut out = Vec::new();
        put_u32(&mut out, status);
        put_string(
            &mut out,
            &message[..message.floor_char_boundary(STRING_LIMIT)],
        );
        out.extend(results);
        let sent = tls.write_all(&out).and_then(|()| {
            tls.conn.send_close_notify();
            tls.flush()
        });
        // Closing with unread bytes would reset the connection, which can
        // discard the reply, so wait for the client to close first.
        if sent.is_ok() && tls.sock.shutdown(Shutdown::Write).is_ok() {
            let _ = io::copy(&mut tls.sock, &mut io::sink());
        }
    }

    /// Reads a request, challenges the client, and carries out the request.
    /// Returns the reply's message and the encoded results that follow it.
    fn exchange(
        &self,
        tls: &mut (impl Read + Write),
        peer: &str,
    ) -> Result<(String, Vec<u8>), String> {
        let request =
            Request::decode(tls).map_err(|e| format!("could not read the request: {e}"))?;
        eprintln!(
            "{peer}: key {:?} asks to {:?}",
            request.key,
            request.action.to_string()
        );
        let request_bytes = request.encode();
        let key = request.key;
        check(valid_name(&key), "the key name is not valid")?;
        let public = fs::read(self.dir.join("keys").join(format!("{key}.public")))
            .map_err(|e| format!("cannot use key {key}: keys/{key}.public: {e}"))?;
        check(
            public.len() == 65 && public[0] == 0x04,
            format!(
                "cannot use key {key}: keys/{key}.public holds {} bytes that are not an uncompressed P-256 point",
                public.len()
            ),
        )?;

        type Run<'a> = Box<dyn FnOnce() -> Result<(String, Vec<u8>), String> + 'a>;
        let run: Run = match request.action {
            Action::Noop => Box::new(|| Ok((format!("verified key {key}"), Vec::new()))),
            Action::CreatePullRequest {
                token,
                repository,
                head,
                base,
                title,
                body,
            } => {
                let pat = self.pat(&token, &key, &repository)?;
                check(
                    !head.is_empty() && !base.is_empty() && !title.is_empty(),
                    "the head, the base and the title must not be empty",
                )?;
                let github = self.github.session(pat)?;
                Box::new(move || {
                    let (message, number) =
                        github.create_pull_request(&repository, &head, &base, &title, &body)?;
                    Ok((message, number.to_le_bytes().to_vec()))
                })
            }
            Action::MergePullRequest {
                token,
                repository,
                number,
                method,
                sha,
            } => {
                let pat = self.pat(&token, &key, &repository)?;
                check(number != 0, "pull request numbers start at 1")?;
                check(
                    ["", "merge", "squash", "rebase"].contains(&method.as_str()),
                    "the merge method must be merge, squash or rebase",
                )?;
                check(
                    sha.bytes().all(|b| b.is_ascii_hexdigit()),
                    "the expected commit must be hexadecimal",
                )?;
                let github = self.github.session(pat)?;
                Box::new(move || {
                    Ok((
                        github.merge_pull_request(&repository, number, &method, &sha)?,
                        Vec::new(),
                    ))
                })
            }
        };

        let mut challenge = [0; CHALLENGE_LEN];
        let (authenticator_data, signature) = {
            let _awaiting = self.await_touch(&key, &public)?;
            SystemRandom::new()
                .fill(&mut challenge)
                .map_err(|_| "could not generate a challenge")?;
            let mut out = Vec::new();
            put_u32(&mut out, OK);
            out.extend(challenge);
            let mut response = || -> io::Result<(Vec<u8>, Vec<u8>)> {
                tls.write_all(&out)?;
                Ok((read_blob(tls)?, read_blob(tls)?))
            };
            response().map_err(|e| format!("could not read the response: {e}"))?
        };
        let client_data_hash = client_data_hash(&challenge, &self.cert, &request_bytes);
        check(
            verify(&public, &client_data_hash, &authenticator_data, &signature),
            "the security key's response is not valid",
        )?;
        run()
    }

    /// Records that the key named `key`, whose public key is `public`, is
    /// waiting for a touch, unless it already is.
    fn await_touch<'a>(&'a self, key: &str, public: &'a [u8]) -> Result<AwaitingTouch<'a>, String> {
        check(
            self.awaiting.lock().unwrap().insert(public.to_owned()),
            format!("key {key} is already waiting for a touch to approve another request"),
        )?;
        Ok(AwaitingTouch {
            awaiting: &self.awaiting,
            public,
        })
    }

    /// Returns the personal access token named `token` if `key` may use it.
    fn pat(&self, token: &str, key: &str, repository: &str) -> Result<String, String> {
        check(valid_name(token), "the token name is not valid")?;
        check(
            repository
                .split_once('/')
                .is_some_and(|(owner, name)| valid_name(owner) && valid_name(name)),
            "the repository must be OWNER/NAME",
        )?;
        let access = fs::read_to_string(self.dir.join("access"))
            .map_err(|e| format!("cannot read the access file: {e}"))?;
        check(
            may_use(&access, token, key)?,
            format!("key {key} may not use token {token}"),
        )?;
        let pat = fs::read_to_string(self.dir.join("tokens").join(token))
            .map_err(|e| format!("cannot use token {token}: {e}"))?;
        let pat = pat.trim();
        check(
            !pat.is_empty() && pat.bytes().all(|b| b.is_ascii_graphic()),
            format!("token {token} is malformed"),
        )?;
        Ok(pat.to_owned())
    }
}

/// Whether the access file `text` lets the key named `key` use the token
/// named `token`. Each line of the file that is not blank and does not start
/// with "#" holds a token's name followed by the names of the keys that may
/// use it, separated by spaces or tabs. Every name must be valid.
fn may_use(text: &str, token: &str, key: &str) -> Result<bool, String> {
    let mut allowed = false;
    for (number, line) in (1..).zip(text.lines()) {
        let names: Vec<&str> = line.split_whitespace().collect();
        if names.first().is_none_or(|first| first.starts_with('#')) {
            continue;
        }
        if let Some(name) = names.iter().find(|name| !valid_name(name)) {
            return Err(format!(
                "line {number} of the access file has {name:?}, which is not a valid name"
            ));
        }
        allowed |= names[0] == token && names[1..].contains(&key);
    }
    Ok(allowed)
}

/// Returns the network an address belongs to, for limiting the connections
/// that wait for their secret: the address itself for IPv4, and the /64
/// that holds it for IPv6.
fn network(address: IpAddr) -> IpAddr {
    match address.to_canonical() {
        IpAddr::V6(v6) => IpAddr::V6((u128::from(v6) & !u128::from(u64::MAX)).into()),
        v4 => v4,
    }
}

/// Given the networks of the connections waiting for their secret, oldest
/// first, returns which of them to close before accepting another from
/// `network`: the oldest from that network if it has reached its limit,
/// or otherwise, if the total has reached its limit, the oldest from the
/// network with the most.
fn eviction(waiting: &[IpAddr], network: IpAddr) -> Option<usize> {
    let count = |n: &IpAddr| waiting.iter().filter(|w| *w == n).count();
    if count(&network) >= PENDING_PER_NETWORK_LIMIT {
        waiting.iter().position(|w| *w == network)
    } else if waiting.len() >= PENDING_LIMIT {
        let most = waiting.iter().map(count).max()?;
        waiting.iter().position(|w| count(w) == most)
    } else {
        None
    }
}

fn check(ok: bool, message: impl Into<String>) -> Result<(), String> {
    if ok { Ok(()) } else { Err(message.into()) }
}

/// Checks a FIDO2 assertion: the authenticator data must be for RP_ID and
/// have the user-present flag set, and the signature must cover the
/// authenticator data followed by the client data hash.
fn verify(
    public: &[u8],
    client_data_hash: &[u8],
    authenticator_data: &[u8],
    signature: &[u8],
) -> bool {
    let rp_id_hash = digest(&SHA256, RP_ID.as_bytes());
    authenticator_data.len() >= 37
        && authenticator_data[..32] == *rp_id_hash.as_ref()
        && authenticator_data[32] & 0x01 != 0
        && UnparsedPublicKey::new(&ECDSA_P256_SHA256_ASN1, public)
            .verify(&[authenticator_data, client_data_hash].concat(), signature)
            .is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(text: &str) -> IpAddr {
        text.parse().unwrap()
    }

    #[test]
    fn the_access_file_lists_each_token_s_keys() {
        let text = "# token  keys\n\nwork\tlaptop desk\n  other  desk\n  # work stolen\nwork phone\nlonely\n";
        for (token, key, expected) in [
            ("work", "laptop", true),
            ("work", "desk", true),
            ("work", "phone", true),
            ("other", "desk", true),
            ("other", "laptop", false),
            ("work", "work", false),
            ("lonely", "lonely", false),
            ("missing", "laptop", false),
            ("#", "token", false),
            ("work", "stolen", false),
        ] {
            assert_eq!(may_use(text, token, key), Ok(expected), "{token} {key}");
        }
        let error = "line 2 of the access file has \"../x\", which is not a valid name";
        assert_eq!(
            may_use("work laptop\nwork ../x\n", "work", "laptop"),
            Err(error.to_owned())
        );
        let error = "line 1 of the access file has \"laptop#note\", which is not a valid name";
        assert_eq!(
            may_use("work laptop#note\n", "work", "laptop"),
            Err(error.to_owned())
        );
        assert_eq!(may_use("", "work", "laptop"), Ok(false));
    }

    #[test]
    fn networks_are_ipv4_addresses_and_ipv6_slash_64s() {
        assert_eq!(network(ip("192.0.2.7")), ip("192.0.2.7"));
        assert_eq!(network(ip("::ffff:192.0.2.7")), ip("192.0.2.7"));
        assert_eq!(network(ip("2001:db8:1:2:3:4:5:6")), ip("2001:db8:1:2::"));
        assert_ne!(
            network(ip("2001:db8:1:2::1")),
            network(ip("2001:db8:1:3::1"))
        );
    }

    #[test]
    fn eviction_closes_the_crowded_network_first() {
        let (a, b, c) = (ip("192.0.2.1"), ip("192.0.2.2"), ip("192.0.2.3"));
        assert_eq!(eviction(&[], a), None);
        assert_eq!(eviction(&[b, a, a, a], a), None);
        assert_eq!(eviction(&[b, a, a, a, a], a), Some(1));
        // Full: the oldest connection from the network with the most goes,
        // whichever network the new connection comes from.
        let mut full = vec![b, c, c, c];
        full.extend((0..PENDING_LIMIT - 4).map(|i| ip(&format!("198.51.100.{}", i % 100 + 1))));
        full.truncate(PENDING_LIMIT);
        assert_eq!(full.len(), PENDING_LIMIT);
        assert_eq!(eviction(&full, a), Some(1));
        assert_eq!(eviction(&full[..PENDING_LIMIT - 1], a), None);
        // When networks tie for the most, the oldest connection goes.
        let distinct: Vec<IpAddr> = (0..PENDING_LIMIT)
            .map(|i| ip(&format!("203.0.113.{i}")))
            .collect();
        assert_eq!(eviction(&distinct, a), Some(0));
    }
}
