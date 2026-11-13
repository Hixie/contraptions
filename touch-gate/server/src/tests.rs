//! Tests that run the server with a fake GitHub, talking to it over TLS
//! with a minimal client and a software key standing in for the security
//! key.

use std::collections::VecDeque;
use std::fs;
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;

use ring::digest::{SHA256, digest};
use ring::rand::SystemRandom;
use ring::signature::{ECDSA_P256_SHA256_ASN1_SIGNING, EcdsaKeyPair, KeyPair};
use rustls::pki_types::pem::PemObject;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, ServerName};
use rustls::{
    ClientConfig, ClientConnection, RootCertStore, ServerConfig, ServerConnection, StreamOwned,
};

use crate::github::GitHub;
use crate::protocol::*;
use crate::server::{PENDING_PER_NETWORK_LIMIT, Server};

const SECRET: [u8; SECRET_LEN] = [7; SECRET_LEN];

type Stream = StreamOwned<ClientConnection, TcpStream>;

/// The authenticator data and the signature that answer a challenge.
type Assertion = (Vec<u8>, Vec<u8>);

fn testdata(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("testdata")
        .join(name)
}

fn test_key() -> EcdsaKeyPair {
    let rng = SystemRandom::new();
    let pkcs8 = EcdsaKeyPair::generate_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, &rng).unwrap();
    EcdsaKeyPair::from_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, pkcs8.as_ref(), &rng).unwrap()
}

/// Returns a signer that answers a client data hash the way a security key
/// does.
fn signer(key: &EcdsaKeyPair, rp_id: &str, flags: u8) -> impl FnOnce(&[u8]) -> Assertion {
    move |client_data_hash| {
        let authenticator_data = [
            digest(&SHA256, rp_id.as_bytes()).as_ref(),
            &[flags],
            &[0, 0, 0, 9],
        ]
        .concat();
        let signed = [&authenticator_data, client_data_hash].concat();
        let signature = key.sign(&SystemRandom::new(), &signed).unwrap();
        (authenticator_data, signature.as_ref().to_vec())
    }
}

fn never_sign(_: &[u8]) -> Assertion {
    panic!("the server sent a challenge for a request it should have refused")
}

struct Harness {
    dir: PathBuf,
    port: u16,
    key: EcdsaKeyPair,
    desk_key: EcdsaKeyPair,
    /// The requests the fake GitHub received.
    github_requests: Arc<Mutex<Vec<String>>>,
    /// The replies the fake GitHub sends, in order.
    github_replies: Arc<Mutex<VecDeque<&'static str>>>,
}

impl Harness {
    fn new(name: &str) -> Harness {
        let dir = std::env::temp_dir().join(format!("touch-gate-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let (key, desk_key) = (test_key(), test_key());
        for subdir in ["keys", "tokens"] {
            fs::create_dir_all(dir.join(subdir)).unwrap();
        }
        fs::write(dir.join("secret"), SECRET).unwrap();
        fs::copy(testdata("cert.der"), dir.join("cert.der")).unwrap();
        fs::copy(testdata("key.der"), dir.join("key.der")).unwrap();
        fs::write(dir.join("keys/laptop.public"), key.public_key().as_ref()).unwrap();
        fs::write(dir.join("keys/desk.public"), desk_key.public_key().as_ref()).unwrap();
        fs::write(dir.join("keys/alias.public"), key.public_key().as_ref()).unwrap();
        fs::write(dir.join("tokens/work"), "ghp_work\n").unwrap();
        fs::write(dir.join("tokens/other"), "ghp_other\n").unwrap();
        fs::write(dir.join("access"), "# token keys\nwork laptop\nother\n").unwrap();

        let (github_requests, github_replies) = (Arc::default(), Arc::default());
        let github_port = fake_github(Arc::clone(&github_requests), Arc::clone(&github_replies));
        let github = GitHub::new("localhost", github_port, &testdata("ca.pem"));
        let server = Arc::new(Server::load(dir.clone(), github).unwrap());
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || server.serve(listener));
        Harness {
            dir,
            port,
            key,
            desk_key,
            github_requests,
            github_replies,
        }
    }

    /// Opens a TLS connection to the server, trusting the test certificate
    /// authority.
    fn connect(&self) -> Stream {
        let mut roots = RootCertStore::empty();
        roots
            .add(CertificateDer::from_pem_file(testdata("ca.pem")).unwrap())
            .unwrap();
        let config = ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth();
        let name = ServerName::try_from("localhost").unwrap();
        let connection = ClientConnection::new(Arc::new(config), name).unwrap();
        StreamOwned::new(
            connection,
            TcpStream::connect(("127.0.0.1", self.port)).unwrap(),
        )
    }

    /// Sends `request` on a new connection, and returns the connection and
    /// the server's challenge.
    fn challenge_for(&self, request: &Request) -> (Stream, [u8; CHALLENGE_LEN]) {
        let mut tls = self.connect();
        tls.write_all(&[SECRET.as_slice(), &request.encode()].concat())
            .unwrap();
        assert_eq!(read_u32(&mut tls).unwrap(), OK);
        let challenge = read_array(&mut tls).unwrap();
        (tls, challenge)
    }

    /// Sends `action` for `key`, answering any challenge with `sign`, and
    /// returns the server's reply.
    fn send_for(
        &self,
        key: &str,
        action: Action,
        sign: impl FnOnce(&[u8]) -> Assertion,
    ) -> (u32, String) {
        let (status, message, results) = self.send_for_results(key, action, sign);
        assert!(results.is_empty(), "{results:?}");
        (status, message)
    }

    /// Like `send_for`, but also returns the bytes after the reply's
    /// message.
    fn send_for_results(
        &self,
        key: &str,
        action: Action,
        sign: impl FnOnce(&[u8]) -> Assertion,
    ) -> (u32, String, Vec<u8>) {
        let request = Request {
            key: key.to_owned(),
            action,
        }
        .encode();
        let mut tls = self.connect();
        tls.write_all(&[SECRET.as_slice(), &request].concat())
            .unwrap();
        let mut status = read_u32(&mut tls).unwrap();
        if status == OK {
            let challenge = read_array::<CHALLENGE_LEN>(&mut tls).unwrap();
            let cert = fs::read(testdata("cert.der")).unwrap();
            let assertion = sign(&client_data_hash(&challenge, &cert, &request));
            answer(&mut tls, assertion);
            status = read_u32(&mut tls).unwrap();
        }
        let message = read_string(&mut tls).unwrap();
        let mut results = Vec::new();
        tls.read_to_end(&mut results).unwrap();
        (status, message, results)
    }

    fn send_with(&self, action: Action, sign: impl FnOnce(&[u8]) -> Assertion) -> (u32, String) {
        self.send_for("laptop", action, sign)
    }

    fn send(&self, action: Action) -> (u32, String) {
        self.send_with(action, signer(&self.key, RP_ID, 0x01))
    }

    fn requests(&self) -> Vec<String> {
        self.github_requests.lock().unwrap().clone()
    }
}

impl Drop for Harness {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

/// Sends an assertion on a connection that has received a challenge.
fn answer(tls: &mut Stream, (authenticator_data, signature): Assertion) {
    let mut out = Vec::new();
    for blob in [authenticator_data, signature] {
        put_u32(&mut out, u32::try_from(blob.len()).unwrap());
        out.extend(blob);
    }
    tls.write_all(&out).unwrap();
}

/// Starts an HTTPS server that records each request and answers it with
/// the next reply. Returns its port.
fn fake_github(
    requests: Arc<Mutex<Vec<String>>>,
    replies: Arc<Mutex<VecDeque<&'static str>>>,
) -> u16 {
    let cert = CertificateDer::from(fs::read(testdata("cert.der")).unwrap());
    let key = PrivateKeyDer::try_from(fs::read(testdata("key.der")).unwrap()).unwrap();
    let tls = Arc::new(
        ServerConfig::builder()
            .with_no_client_auth()
            .with_single_cert(vec![cert], key)
            .unwrap(),
    );
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        for tcp in listener.incoming() {
            let mut stream =
                StreamOwned::new(ServerConnection::new(tls.clone()).unwrap(), tcp.unwrap());
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                request.push(read_array::<1>(&mut stream).unwrap()[0]);
            }
            let head = String::from_utf8(request.clone()).unwrap();
            let length: usize = head
                .lines()
                .find_map(|line| line.strip_prefix("Content-Length: "))
                .map_or(0, |n| n.parse().unwrap());
            let mut body = vec![0; length];
            stream.read_exact(&mut body).unwrap();
            request.extend(body);
            requests
                .lock()
                .unwrap()
                .push(String::from_utf8(request).unwrap());
            let reply = replies
                .lock()
                .unwrap()
                .pop_front()
                .expect("a reply for each request");
            stream.write_all(reply.as_bytes()).unwrap();
            stream.conn.send_close_notify();
            stream.flush().unwrap();
        }
    });
    port
}

fn create(token: &str, repository: &str) -> Action {
    Action::CreatePullRequest {
        token: token.to_owned(),
        repository: repository.to_owned(),
        head: "feature".to_owned(),
        base: "main".to_owned(),
        title: "Say \"hi\"".to_owned(),
        body: "line one\nline two".to_owned(),
    }
}

fn merge(token: &str, method: &str, sha: &str) -> Action {
    Action::MergePullRequest {
        token: token.to_owned(),
        repository: "octo/repo".to_owned(),
        number: 12,
        method: method.to_owned(),
        sha: sha.to_owned(),
    }
}

#[test]
fn noop_succeeds_with_the_enrolled_key_only() {
    let h = Harness::new("noop");
    assert_eq!(h.send(Action::Noop), (OK, "verified key laptop".to_owned()));
    let other_key = test_key();
    let reply = h.send_with(Action::Noop, signer(&other_key, RP_ID, 0x01));
    assert_eq!(
        reply,
        (
            FAILED,
            "the security key's response is not valid".to_owned()
        )
    );
}

#[test]
fn responses_without_user_presence_or_for_another_rp_id_fail() {
    let h = Harness::new("presence");
    assert_eq!(
        h.send_with(Action::Noop, signer(&h.key, RP_ID, 0x04)).0,
        FAILED
    );
    assert_eq!(
        h.send_with(Action::Noop, signer(&h.key, "example.com", 0x01))
            .0,
        FAILED
    );
    assert_eq!(
        h.send_with(Action::Noop, |_| (vec![1; 10], vec![2; 10])).0,
        FAILED
    );
}

#[test]
fn create_pull_request_calls_github_and_returns_the_pull_request_url() {
    let h = Harness::new("create");
    h.github_replies.lock().unwrap().push_back(
        "HTTP/1.1 201 Created\r\nLocation: https://api.github.com/repos/octo/repo/pulls/34\r\n\r\n{}",
    );
    let reply = h.send_for_results(
        "laptop",
        create("work", "octo/repo"),
        signer(&h.key, RP_ID, 0x01),
    );
    assert_eq!(
        reply,
        (
            OK,
            "https://github.com/octo/repo/pull/34".to_owned(),
            34u64.to_le_bytes().to_vec()
        )
    );
    let requests = h.requests();
    assert_eq!(requests.len(), 1);
    let start = "POST /repos/octo/repo/pulls HTTP/1.0\r\nHost: localhost\r\n";
    assert!(requests[0].starts_with(start), "{}", requests[0]);
    assert!(requests[0].contains("\r\nAuthorization: Bearer ghp_work\r\n"));
    let body =
        r#"{"head":"feature","base":"main","title":"Say \"hi\"","body":"line one\u000aline two"}"#;
    let end = format!("Content-Length: {}\r\n\r\n{body}", body.len());
    assert!(requests[0].ends_with(&end), "{}", requests[0]);
}

#[test]
fn create_pull_request_reports_a_missing_location() {
    let h = Harness::new("create-nolocation");
    h.github_replies
        .lock()
        .unwrap()
        .push_back("HTTP/1.1 201 Created\r\n\r\n{}");
    let message = "created a pull request in octo/repo, but GitHub did not say which";
    let reply = h.send_for_results(
        "laptop",
        create("work", "octo/repo"),
        signer(&h.key, RP_ID, 0x01),
    );
    assert_eq!(reply, (OK, message.to_owned(), 0u64.to_le_bytes().to_vec()));
}

#[test]
fn merge_pull_request_sends_only_the_options_given() {
    let h = Harness::new("merge");
    for _ in 0..2 {
        h.github_replies
            .lock()
            .unwrap()
            .push_back("HTTP/1.1 200 OK\r\n\r\n{\"merged\":true}");
    }
    let merged = (OK, "merged https://github.com/octo/repo/pull/12".to_owned());
    assert_eq!(h.send(merge("work", "squash", "abc123")), merged);
    assert_eq!(h.send(merge("work", "", "")), merged);
    let requests = h.requests();
    assert!(requests[0].starts_with("PUT /repos/octo/repo/pulls/12/merge HTTP/1.0\r\n"));
    let end = "\r\n\r\n{\"merge_method\":\"squash\",\"sha\":\"abc123\"}";
    assert!(requests[0].ends_with(end), "{}", requests[0]);
    assert!(
        requests[1].ends_with("\r\nContent-Length: 2\r\n\r\n{}"),
        "{}",
        requests[1]
    );
}

#[test]
fn github_errors_reach_the_client() {
    let h = Harness::new("github-error");
    h.github_replies
        .lock()
        .unwrap()
        .push_back("HTTP/1.1 405 Method Not Allowed\r\n\r\n{\"message\":\"not mergeable\"}\n");
    let message = "GitHub replied with status 405: {\"message\":\"not mergeable\"}";
    assert_eq!(h.send(merge("work", "", "")), (FAILED, message.to_owned()));
    h.github_replies
        .lock()
        .unwrap()
        .push_back("HTTP/1.1 422 Unprocessable Entity\r\n\r\n{\"message\":\"exists\"}");
    let message = "GitHub replied with status 422: {\"message\":\"exists\"}";
    assert_eq!(
        h.send(create("work", "octo/repo")),
        (FAILED, message.to_owned())
    );
}

#[test]
fn an_unreachable_github_or_a_missing_roots_file_is_reported() {
    let port = TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port();
    let github = GitHub::new("localhost", port, &testdata("ca.pem"));
    let error = github
        .session("pat".to_owned())
        .unwrap()
        .merge_pull_request("o/r", 1, "", "")
        .unwrap_err();
    assert!(error.starts_with("could not talk to GitHub: "), "{error}");
    let missing = testdata("missing.pem");
    let error = GitHub::new("localhost", port, &missing)
        .session("pat".to_owned())
        .err()
        .unwrap();
    assert!(
        error.starts_with(&format!("cannot read {}: ", missing.display())),
        "{error}"
    );
}

#[test]
fn a_missing_roots_file_is_reported_before_the_challenge() {
    let mut h = Harness::new("roots");
    let server = Arc::new(
        Server::load(
            h.dir.clone(),
            GitHub::new("localhost", 1, &testdata("missing.pem")),
        )
        .unwrap(),
    );
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || server.serve(listener));
    h.port = port;
    let (status, message) = h.send_with(create("work", "octo/repo"), never_sign);
    assert_eq!(status, FAILED);
    assert!(message.starts_with("cannot read "), "{message}");
    assert_eq!(h.send(Action::Noop).0, OK);
}

#[test]
fn refused_requests_fail_before_the_challenge_and_never_reach_github() {
    let h = Harness::new("refused");
    let empty_base = Action::CreatePullRequest {
        token: "work".to_owned(),
        repository: "octo/repo".to_owned(),
        head: "feature".to_owned(),
        base: String::new(),
        title: "t".to_owned(),
        body: String::new(),
    };
    let zero = Action::MergePullRequest {
        token: "work".to_owned(),
        repository: "octo/repo".to_owned(),
        number: 0,
        method: String::new(),
        sha: String::new(),
    };
    let cases = [
        (
            "laptop",
            create("other", "octo/repo"),
            "key laptop may not use token other",
        ),
        (
            "laptop",
            create("missing", "octo/repo"),
            "key laptop may not use token missing",
        ),
        (
            "desk",
            create("work", "octo/repo"),
            "key desk may not use token work",
        ),
        (
            "laptop",
            create("../work", "octo/repo"),
            "the token name is not valid",
        ),
        (
            "laptop",
            create("work", "octo"),
            "the repository must be OWNER/NAME",
        ),
        (
            "laptop",
            create("work", "octo/../x"),
            "the repository must be OWNER/NAME",
        ),
        (
            "laptop",
            empty_base,
            "the head, the base and the title must not be empty",
        ),
        ("laptop", zero, "pull request numbers start at 1"),
        (
            "laptop",
            merge("work", "octopus", ""),
            "the merge method must be merge, squash or rebase",
        ),
        (
            "laptop",
            merge("work", "", "abc\ndef"),
            "the expected commit must be hexadecimal",
        ),
        ("..", Action::Noop, "the key name is not valid"),
    ];
    for (key, action, expected) in cases {
        assert_eq!(
            h.send_for(key, action, never_sign),
            (FAILED, expected.to_owned())
        );
    }
    let (status, message) = h.send_for("desktop", Action::Noop, never_sign);
    assert_eq!(status, FAILED);
    assert!(message.starts_with("cannot use key desktop: "), "{message}");
    fs::write(
        h.dir.join("keys/desk.public"),
        "-----BEGIN PUBLIC KEY-----\n",
    )
    .unwrap();
    let message = "cannot use key desk: keys/desk.public holds 27 bytes that are not an uncompressed P-256 point";
    assert_eq!(
        h.send_for("desk", Action::Noop, never_sign),
        (FAILED, message.to_owned())
    );
    fs::write(h.dir.join("tokens/work"), "two words").unwrap();
    let message = "token work is malformed";
    assert_eq!(
        h.send_with(create("work", "octo/repo"), never_sign),
        (FAILED, message.to_owned())
    );
    fs::write(h.dir.join("access"), "work laptop\nwork lap top!\n").unwrap();
    let message = "line 2 of the access file has \"top!\", which is not a valid name";
    assert_eq!(
        h.send_with(create("work", "octo/repo"), never_sign),
        (FAILED, message.to_owned())
    );
    fs::remove_file(h.dir.join("access")).unwrap();
    let (status, message) = h.send_with(create("work", "octo/repo"), never_sign);
    assert_eq!(status, FAILED);
    assert!(
        message.starts_with("cannot read the access file: "),
        "{message}"
    );
    assert!(h.requests().is_empty());
}

#[test]
fn a_key_waiting_for_a_touch_cannot_be_challenged_again() {
    let h = Harness::new("awaiting");
    let noop = Request {
        key: "laptop".to_owned(),
        action: Action::Noop,
    };
    let (mut first, challenge) = h.challenge_for(&noop);

    // While the first challenge is unanswered, the same key is refused, under
    // its own name or under another name for the same public key, but a
    // different key is not.
    let refused = |name: &str| {
        (
            FAILED,
            format!("key {name} is already waiting for a touch to approve another request"),
        )
    };
    assert_eq!(h.send_with(Action::Noop, never_sign), refused("laptop"));
    assert_eq!(
        h.send_for("alias", Action::Noop, never_sign),
        refused("alias")
    );
    assert_eq!(
        h.send_for("desk", Action::Noop, signer(&h.desk_key, RP_ID, 0x01))
            .0,
        OK
    );

    // Once the first challenge is answered, the key can be challenged again.
    let cert = fs::read(testdata("cert.der")).unwrap();
    let hash = client_data_hash(&challenge, &cert, &noop.encode());
    answer(&mut first, signer(&h.key, RP_ID, 0x01)(&hash));
    assert_eq!(read_u32(&mut first).unwrap(), OK);
    assert_eq!(h.send(Action::Noop).0, OK);

    // A challenge answered with a bad assertion also stops waiting.
    let (mut second, _) = h.challenge_for(&noop);
    answer(&mut second, (vec![0; 37], vec![0; 8]));
    assert_eq!(read_u32(&mut second).unwrap(), FAILED);
    assert_eq!(h.send(Action::Noop).0, OK);

    // So does a client that closes its connection instead of answering. The
    // server releases the key before it replies, so the reply shows that
    // the key has been released.
    let (mut third, _) = h.challenge_for(&noop);
    third.conn.send_close_notify();
    third.flush().unwrap();
    third.sock.shutdown(std::net::Shutdown::Write).unwrap();
    assert_eq!(read_u32(&mut third).unwrap(), FAILED);
    assert!(
        read_string(&mut third)
            .unwrap()
            .starts_with("could not read the response: ")
    );
    assert_eq!(h.send(Action::Noop).0, OK);
}

#[test]
fn unknown_request_types_and_malformed_requests_fail() {
    let h = Harness::new("unknown");
    let reply = |bytes: &[u8]| {
        let mut tls = h.connect();
        tls.write_all(&[SECRET.as_slice(), bytes].concat()).unwrap();
        (read_u32(&mut tls).unwrap(), read_string(&mut tls).unwrap())
    };
    let expected = "could not read the request: unknown request type 7".to_owned();
    assert_eq!(reply(&[7, 0, 0, 0]), (FAILED, expected));
    let expected = "could not read the request: a string is not UTF-8".to_owned();
    assert_eq!(reply(&[0, 0, 0, 0, 0xFF, 0]), (FAILED, expected));
}

#[test]
fn a_wrong_secret_closes_the_connection_without_a_reply() {
    let h = Harness::new("secret");
    let mut tls = h.connect();
    let request = Request {
        key: "laptop".to_owned(),
        action: Action::Noop,
    }
    .encode();
    tls.write_all(&[[8; SECRET_LEN].as_slice(), &request].concat())
        .unwrap();
    let mut rest = Vec::new();
    let _ = tls.read_to_end(&mut rest);
    assert!(rest.is_empty());
    assert_eq!(h.send(Action::Noop).0, OK);
}

#[test]
fn idle_connections_are_closed_when_too_many_from_one_address_wait_for_their_secret() {
    let h = Harness::new("pending");
    let mut idle: Vec<TcpStream> = (0..PENDING_PER_NETWORK_LIMIT)
        .map(|_| TcpStream::connect(("127.0.0.1", h.port)).unwrap())
        .collect();
    // The server registers each connection before accepting the next, so a
    // completed exchange shows that all of the idle ones were registered.
    assert_eq!(h.send(Action::Noop).0, OK);
    let mut byte = [0];
    assert_eq!(idle[0].read(&mut byte).unwrap(), 0);
    idle[1].set_nonblocking(true).unwrap();
    assert_eq!(
        idle[1].read(&mut byte).unwrap_err().kind(),
        io::ErrorKind::WouldBlock
    );
    idle.clear();
}

#[test]
fn server_load_rejects_a_short_secret() {
    let h = Harness::new("load");
    fs::write(h.dir.join("secret"), [1; 31]).unwrap();
    let github = GitHub::new("localhost", 1, &testdata("ca.pem"));
    let error = Server::load(h.dir.clone(), github).err().unwrap();
    assert_eq!(error.to_string(), "the secret must be 32 bytes long");
}

#[test]
fn requests_round_trip_through_the_wire_format() {
    let requests = [
        Action::Noop,
        create("work", "octo/repo"),
        merge("work", "rebase", ""),
    ]
    .map(|action| Request {
        key: "laptop".to_owned(),
        action,
    });
    for request in requests {
        let bytes = request.encode();
        assert_eq!(Request::decode(&mut bytes.as_slice()).unwrap(), request);
        assert!(Request::decode(&mut &bytes[..bytes.len() - 1]).is_err());
    }
    let noop = Request {
        key: "k".to_owned(),
        action: Action::Noop,
    }
    .encode();
    assert_eq!(noop, [0, 0, 0, 0, b'k', 0]);
    let mut long = vec![0, 0, 0, 0];
    long.resize(4 + STRING_LIMIT + 1, b'a');
    long.push(0);
    let error = Request::decode(&mut long.as_slice()).unwrap_err();
    assert_eq!(error.to_string(), "a string is too long");
    let mut blob = Vec::new();
    put_u32(&mut blob, u32::try_from(BLOB_LIMIT + 1).unwrap());
    assert!(read_blob(&mut blob.as_slice()).is_err());
}

#[test]
fn long_messages_are_cut_to_the_string_limit() {
    let h = Harness::new("long-message");
    let body = "é".repeat(STRING_LIMIT);
    let reply = format!("HTTP/1.1 422 Unprocessable Entity\r\n\r\n{body}");
    h.github_replies
        .lock()
        .unwrap()
        .push_back(Box::leak(reply.into_boxed_str()));
    let (status, message) = h.send(merge("work", "", ""));
    assert_eq!(status, FAILED);
    assert!(message.starts_with("GitHub replied with status 422: éé"));
    assert!(
        message.len() <= STRING_LIMIT && message.len() > STRING_LIMIT - 2,
        "{}",
        message.len()
    );
}

#[test]
fn logged_descriptions_escape_every_string() {
    assert_eq!(Action::Noop.to_string(), "verify the security key");
    assert_eq!(
        create("work", "octo/repo").to_string(),
        "create a pull request in \"octo/repo\" from \"feature\" into \"main\" titled \"Say \\\"hi\\\"\" \
         described as \"line one\\nline two\" with token \"work\""
    );
    assert_eq!(
        merge("work", "", "").to_string(),
        "merge pull request 12 of \"octo/repo\" with token \"work\""
    );
    assert_eq!(
        merge("work", "squash", "abc123").to_string(),
        "merge pull request 12 of \"octo/repo\" by \"squash\" if its head is \"abc123\" with token \"work\""
    );
    let mut spoof = create("work", "octo/repo");
    if let Action::CreatePullRequest { head, .. } = &mut spoof {
        *head = "evil\r\u{1b}[2Kfeature".to_owned();
    }
    let shown = spoof.to_string();
    assert!(
        !shown.contains('\r') && !shown.contains('\u{1b}'),
        "{shown:?}"
    );
}

/// Returns the bytes in each code block of PROTOCOL.md's example section.
/// Each line of a block starts with hexadecimal bytes, and any comment
/// after them is separated from them by at least two spaces.
fn specification_example_blocks() -> Vec<Vec<u8>> {
    let spec = include_str!("../../PROTOCOL.md");
    let example = spec
        .split("\n## ")
        .find(|section| section.starts_with("7. Example"))
        .unwrap();
    let blocks = example.split("```").skip(1).step_by(2);
    let line_bytes = |line: &str| {
        let hex = line.split("  ").next().unwrap();
        hex.split_whitespace()
            .map(|b| u8::from_str_radix(b, 16).unwrap())
            .collect::<Vec<u8>>()
    };
    blocks
        .map(|block| block.lines().flat_map(line_bytes).collect())
        .collect()
}

#[test]
fn the_specification_example_matches_the_implementation() {
    let [request, challenge, cert_hash, hash, reply] = &specification_example_blocks()[..] else {
        panic!("the example has the wrong number of code blocks");
    };
    let expected = Request {
        key: "laptop".to_owned(),
        action: merge("work", "squash", ""),
    };
    assert_eq!(request.len(), 42);
    assert_eq!(*request, expected.encode());
    assert_eq!(Request::decode(&mut request.as_slice()).unwrap(), expected);
    let cert = fs::read(testdata("cert.der")).unwrap();
    assert_eq!(cert_hash, digest(&SHA256, &cert).as_ref());
    assert_eq!(hash, &client_data_hash(challenge, &cert, request));
    let mut expected_reply = Vec::new();
    put_u32(&mut expected_reply, OK);
    put_string(
        &mut expected_reply,
        "merged https://github.com/octo/repo/pull/12",
    );
    assert_eq!(*reply, expected_reply);
    let spec = include_str!("../../PROTOCOL.md");
    let (_, rest) = spec
        .split_once("relying party ID, which are the bytes")
        .unwrap();
    let (listed, _) = rest.split_once(';').unwrap();
    let listed: Vec<u8> = listed
        .split_whitespace()
        .map(|b| u8::from_str_radix(b.strip_prefix("0x").unwrap(), 16).unwrap())
        .collect();
    assert_eq!(listed, digest(&SHA256, RP_ID.as_bytes()).as_ref());
}

#[test]
fn an_assertion_approves_only_its_own_request_server_and_connection() {
    let h = Harness::new("binding");
    let cert = fs::read(testdata("cert.der")).unwrap();
    let noop = Request {
        key: "laptop".to_owned(),
        action: Action::Noop,
    };
    let merge = Request {
        key: "laptop".to_owned(),
        action: merge("work", "", ""),
    };
    let refused = (
        FAILED,
        "the security key's response is not valid".to_owned(),
    );
    let sign = |hash: [u8; 32]| signer(&h.key, RP_ID, 0x01)(&hash);
    let reply = |tls: &mut Stream| (read_u32(tls).unwrap(), read_string(tls).unwrap());

    // An assertion for a no-op does not approve a merge sent in its place.
    let (mut tls, challenge) = h.challenge_for(&merge);
    answer(
        &mut tls,
        sign(client_data_hash(&challenge, &cert, &noop.encode())),
    );
    assert_eq!(reply(&mut tls), refused);

    // An assertion made for a server with another certificate does not verify.
    let other_cert = fs::read(testdata("ca.pem")).unwrap();
    let (mut tls, challenge) = h.challenge_for(&merge);
    answer(
        &mut tls,
        sign(client_data_hash(&challenge, &other_cert, &merge.encode())),
    );
    assert_eq!(reply(&mut tls), refused);

    // An assertion for one connection's challenge does not approve another.
    let (mut first, first_challenge) = h.challenge_for(&merge);
    answer(&mut first, (vec![], vec![]));
    assert_eq!(read_u32(&mut first).unwrap(), FAILED);
    let (mut tls, _) = h.challenge_for(&merge);
    answer(
        &mut tls,
        sign(client_data_hash(&first_challenge, &cert, &merge.encode())),
    );
    assert_eq!(reply(&mut tls), refused);
    assert!(h.requests().is_empty());

    // The matching assertion is accepted.
    let (mut tls, challenge) = h.challenge_for(&noop);
    answer(
        &mut tls,
        sign(client_data_hash(&challenge, &cert, &noop.encode())),
    );
    assert_eq!(reply(&mut tls), (OK, "verified key laptop".to_owned()));
}
