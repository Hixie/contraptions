//! The wire format. PROTOCOL.md defines it.

use std::fmt;
use std::io::{self, Read};

use ring::digest::{Context, SHA256, digest};

pub const RP_ID: &str = "touch-gate";
pub const SECRET_LEN: usize = 32;
pub const CHALLENGE_LEN: usize = 32;
pub const STRING_LIMIT: usize = 1 << 20;
pub const BLOB_LIMIT: usize = 1024;

pub const OK: u32 = 0;
pub const FAILED: u32 = 1;

const NOOP: u32 = 0;
const CREATE_PULL_REQUEST: u32 = 1;
const MERGE_PULL_REQUEST: u32 = 2;

#[derive(Debug, PartialEq)]
pub enum Action {
    Noop,
    CreatePullRequest {
        token: String,
        repository: String,
        head: String,
        base: String,
        title: String,
        body: String,
    },
    MergePullRequest {
        token: String,
        repository: String,
        number: u64,
        method: String,
        sha: String,
    },
}

/// Describes the action for the server's log. Every string is quoted and
/// escaped, so that control characters in it cannot forge log lines.
impl fmt::Display for Action {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            Action::Noop => write!(f, "verify the security key"),
            Action::CreatePullRequest {
                token,
                repository,
                head,
                base,
                title,
                body,
            } => write!(
                f,
                "create a pull request in {repository:?} from {head:?} into {base:?} titled {title:?} \
                 described as {body:?} with token {token:?}"
            ),
            Action::MergePullRequest {
                token,
                repository,
                number,
                method,
                sha,
            } => {
                write!(f, "merge pull request {number} of {repository:?}")?;
                if !method.is_empty() {
                    write!(f, " by {method:?}")?;
                }
                if !sha.is_empty() {
                    write!(f, " if its head is {sha:?}")?;
                }
                write!(f, " with token {token:?}")
            }
        }
    }
}

#[derive(Debug, PartialEq)]
pub struct Request {
    pub key: String,
    pub action: Action,
}

impl Request {
    pub fn encode(&self) -> Vec<u8> {
        let mut out = Vec::new();
        match &self.action {
            Action::Noop => {
                put_u32(&mut out, NOOP);
                put_string(&mut out, &self.key);
            }
            Action::CreatePullRequest {
                token,
                repository,
                head,
                base,
                title,
                body,
            } => {
                put_u32(&mut out, CREATE_PULL_REQUEST);
                for field in [&self.key, token, repository, head, base, title, body] {
                    put_string(&mut out, field);
                }
            }
            Action::MergePullRequest {
                token,
                repository,
                number,
                method,
                sha,
            } => {
                put_u32(&mut out, MERGE_PULL_REQUEST);
                for field in [&self.key, token, repository] {
                    put_string(&mut out, field);
                }
                out.extend(number.to_le_bytes());
                put_string(&mut out, method);
                put_string(&mut out, sha);
            }
        }
        out
    }

    pub fn decode(r: &mut impl Read) -> io::Result<Request> {
        let request = |key, action| Request { key, action };
        Ok(match read_u32(r)? {
            NOOP => request(read_string(r)?, Action::Noop),
            CREATE_PULL_REQUEST => request(
                read_string(r)?,
                Action::CreatePullRequest {
                    token: read_string(r)?,
                    repository: read_string(r)?,
                    head: read_string(r)?,
                    base: read_string(r)?,
                    title: read_string(r)?,
                    body: read_string(r)?,
                },
            ),
            MERGE_PULL_REQUEST => request(
                read_string(r)?,
                Action::MergePullRequest {
                    token: read_string(r)?,
                    repository: read_string(r)?,
                    number: u64::from_le_bytes(read_array(r)?),
                    method: read_string(r)?,
                    sha: read_string(r)?,
                },
            ),
            kind => return Err(invalid(format!("unknown request type {kind}"))),
        })
    }
}

/// Returns the client data hash that the security key signs: the SHA-256
/// hash of the challenge, the SHA-256 hash of the server's certificate, and
/// the encoded request, in that order.
pub fn client_data_hash(challenge: &[u8], cert: &[u8], request: &[u8]) -> [u8; 32] {
    let mut context = Context::new(&SHA256);
    context.update(challenge);
    context.update(digest(&SHA256, cert).as_ref());
    context.update(request);
    context
        .finish()
        .as_ref()
        .try_into()
        .expect("SHA-256 hashes are 32 bytes long")
}

/// Whether `name` can name a key or a token, or be one half of a
/// repository's name. Such names become path segments, both in the server's
/// state directory and in GitHub API URLs.
pub fn valid_name(name: &str) -> bool {
    !name.is_empty()
        && name != "."
        && name != ".."
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

pub fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

pub fn read_array<const N: usize>(r: &mut impl Read) -> io::Result<[u8; N]> {
    let mut bytes = [0; N];
    r.read_exact(&mut bytes)?;
    Ok(bytes)
}

pub fn read_u32(r: &mut impl Read) -> io::Result<u32> {
    Ok(u32::from_le_bytes(read_array(r)?))
}

pub fn read_string(r: &mut impl Read) -> io::Result<String> {
    let mut bytes = Vec::new();
    loop {
        let [byte] = read_array(r)?;
        if byte == 0 {
            return String::from_utf8(bytes).map_err(|_| invalid("a string is not UTF-8"));
        }
        if bytes.len() == STRING_LIMIT {
            return Err(invalid("a string is too long"));
        }
        bytes.push(byte);
    }
}

pub fn read_blob(r: &mut impl Read) -> io::Result<Vec<u8>> {
    let len = read_u32(r)?;
    let len = usize::try_from(len)
        .ok()
        .filter(|&n| n <= BLOB_LIMIT)
        .ok_or_else(|| invalid("a byte string is too long"))?;
    let mut bytes = vec![0; len];
    r.read_exact(&mut bytes)?;
    Ok(bytes)
}

pub fn put_u32(out: &mut Vec<u8>, value: u32) {
    out.extend(value.to_le_bytes());
}

pub fn put_string(out: &mut Vec<u8>, value: &str) {
    assert!(
        !value.contains('\0'),
        "strings sent on the wire cannot contain U+0000"
    );
    out.extend(value.as_bytes());
    out.push(0);
}
