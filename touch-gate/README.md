# touch-gate

touch-gate carries out requests on behalf of the people who use it, once
they approve each request by touching a FIDO2 security key such as a
YubiKey. The requests it supports today are GitHub actions: creating a pull
request from a branch, and merging a pull request. It has two parts,
written in different languages, which share no code and each implement
[PROTOCOL.md](PROTOCOL.md):

- `server/`: the server, in Rust, which runs on x86-64 Linux and holds
  GitHub personal access tokens.
- `cli-client/`: a command-line client, in Python, which runs on a Mac with
  the security key plugged in.

Several people can share one server, each with their own security keys and
their own tokens. The server's configuration says which security keys may
use which tokens, so no one can use another person's token through the
server. Anyone who can read the server's configuration can use all of the
tokens, though.

## How it works

The client and server talk over TLS. The client first works out which of
its registered security keys is plugged in, and names it in the request.
Every request starts with a secret that all of the server's clients share.
The server closes a connection whose secret is wrong without reading
anything more. Otherwise, if the request passes the server's checks, the
server sends a random challenge. The client shows every field of the
request that a touch will approve, and the person touches the security key,
which signs the challenge, the server's certificate and the request
together. The server checks that signature before it carries out the
request.

The server sends a challenge for a security key only when no other request
is waiting for that key to be touched. Otherwise, a program on the person's
computer could start a request of its own, wait for the person to start
theirs, and ask the key to sign its own challenge, so that the person's
touch approves the program's request. Instead, the person's request is
refused with a message saying that the key is already waiting for a touch.
Section 1.4 of PROTOCOL.md describes what this does and does not cover.
The server uses TCP keepalive to notice within about two minutes that a
client has vanished, for example because its computer went to sleep, so
that the client's key can approve requests again.

The server runs one thread per connection. While there are no connections,
it has one thread and waits for one. Measured on Linux, it then uses about
1.5 MB of memory. At most four connections from one IPv4 address or IPv6
/64 network, and 64 in all, may wait for their secret at once. When another
arrives, the server closes the oldest waiting connection from the same
network, or, if the total is reached, from the network with the most.

## Requests

- **noop**: checks the security key and does nothing else.
- **create-pr**: creates a pull request from a branch.
- **merge**: merges a pull request.

## Setting up the server

The server needs no special privileges, so any user account on the server
machine can run it. The examples below use a server machine named
`gate.example.com`, and keep everything the server uses in one directory,
`touch-gate`, in that account's home directory: the program,
`touch-gate-server`, and the directory it reads its configuration from,
`state`. That directory will hold the tokens, so make `touch-gate` readable
only by the account that runs the server. On the server machine, run:

```sh
mkdir -m 700 ~/touch-gate
```

### Getting the program

To build the server on a Mac, run `server/build-linux.sh`, which needs
rustup and Xcode's command-line tools, and copy the program it builds to
the server machine:

```sh
scp server/target/x86_64-unknown-linux-musl/release/touch-gate-server gate.example.com:touch-gate/
```

To build it on the server machine instead, run `cargo build --release` in
`server/`, which needs rustup and a C compiler, and copy
`server/target/release/touch-gate-server` to `~/touch-gate`.

To reach GitHub, the server trusts the certificate authorities listed in
`/etc/ssl/certs/ca-certificates.crt`. Debian and Ubuntu provide that file in
their `ca-certificates` package.

### The state directory

The server reads its configuration from one directory, which holds these
files:

| File | What it holds |
|--|--|
| `secret` | 32 random bytes. Every client has a copy. |
| `key.der` | The server's private key for TLS. |
| `cert.der` | The server's certificate for TLS. Every client has a copy. |
| `keys/NAME.public` | The public key of the security key named NAME. `touch-gate enroll` creates these files on the client. |
| `tokens/NAME` | A GitHub personal access token, named NAME, as text. |
| `access` | Which security keys may use which tokens. |

The server reads `secret`, `key.der` and `cert.der` when it starts. It
reads `keys`, `tokens` and `access` for each request, so security keys and
tokens can be added and removed while it runs.

Each line of `access` holds a token's name followed by the names of the
security keys that may use it, separated by spaces or tabs. The server
ignores blank lines and lines whose first character other than a space or
tab is `#`; a `#` anywhere else is not allowed. If any line holds a name
that is not valid, the server refuses every request that uses a token, and
says which line is wrong. For example:

```
# token   keys that may use it
alice     alice-yubikey alice-spare
team      alice-yubikey bob-yubikey
```

Names of keys and tokens may contain ASCII letters, digits, `.`, `_` and
`-`, and may not be `.` or `..`.

Anyone who can read this directory can use every token in it, which is
why `touch-gate` is readable only by the account that runs the server. To
create the state directory, run this on the server machine:

```sh
mkdir ~/touch-gate/state
cd ~/touch-gate/state
head -c 32 /dev/urandom > secret
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -outform DER -out key.der
openssl req -new -x509 -key key.der -keyform DER -subj /CN=touch-gate -days 36500 -outform DER -out cert.der
mkdir keys tokens
touch access
```

The certificate's name and expiry date do not matter, because clients
accept exactly this certificate and no other.

### Starting the server

On the server machine, start the server with the state directory and the
address and port to listen on:

```sh
cd ~/touch-gate
./touch-gate-server state 0.0.0.0:7443
```

It prints `listening on 0.0.0.0:7443`, then a line for each connection, and
runs until it is stopped or its terminal closes. Use a port above 1023,
which needs no privileges. If the machine has a firewall, the port must be
open to the clients.

## Setting up a client

### Getting the program

The client is the single file `cli-client/touch_gate.py`. It needs:

- a Python whose TLS library supports TLS 1.3. The Python that comes with
  macOS does not; `brew install python` installs one, which must come
  before `/usr/bin` on PATH;
- libfido2's command-line tools, to talk to the security key, which
  `brew install libfido2` installs.

To run the client as `touch-gate`, link it into a directory on PATH. For
example, in the `touch-gate` directory, run:

```sh
ln -s "$PWD/cli-client/touch_gate.py" /opt/homebrew/bin/touch-gate
```

### The configuration directory

The client reads its configuration from `~/.config/touch-gate/`, which
holds these files:

| File | What it holds |
|--|--|
| `server` | The server's address and port, as `host:port`, for example `gate.example.com:7443`. |
| `server.der` | A copy of the server's `cert.der`. |
| `secret` | A copy of the server's `secret`. |
| `keys/NAME.credential` | The credential ID of the security key named NAME. `touch-gate enroll` creates these files. They stay on the client. |

The secret lets anyone reach the server, so make the directory readable only
by you. To create it, run this on the Mac, copying the two files from the
server machine with `scp` or any other way:

```sh
mkdir -p -m 700 ~/.config/touch-gate
cd ~/.config/touch-gate
echo gate.example.com:7443 > server
scp gate.example.com:touch-gate/state/cert.der server.der
scp gate.example.com:touch-gate/state/secret secret
```

## Adding a security key

Plug the security key into the Mac, with no other security key plugged in,
choose a name for it, and run, in any directory, for example:

```sh
touch-gate enroll alice-yubikey
```

Touch the key when asked. This writes two files:

- `~/.config/touch-gate/keys/alice-yubikey.credential`, which holds the
  key's credential ID. It stays on the Mac.
- `alice-yubikey.public`, in the current directory, which holds the key's
  public key. To write it somewhere else, give a directory after the name.

From the same directory, copy the public key file into the server's `keys`
directory:

```sh
scp alice-yubikey.public gate.example.com:touch-gate/state/keys/
```

The key can now approve no-op requests. To check it, run:

```sh
touch-gate noop
```

The client refuses to enroll a security key that already holds one of the
credentials in `~/.config/touch-gate/keys/`. For each request, the client
asks each plugged-in security key, without needing a touch, whether it
holds one of those credentials. It uses the first key that does, and only
that key waits for a touch.

## Adding a token

On GitHub, create a fine-grained personal access token with read and write
access to the "Pull requests" and "Contents" permissions of the
repositories it should act on.

On the server machine, choose a name for the token, for example `alice`,
and save the token's text in the file `~/touch-gate/state/tokens/alice`,
with any text editor.

Then, in `~/touch-gate/state/access`, add a line naming the token and the
security keys that may use it:

```
alice alice-yubikey
```

To stop a key from using the token, remove the key's name from that line.

## Using it

```sh
touch-gate create-pr alice
touch-gate create-pr alice octo/repo my-branch main 'Fix the frobnicator' 'Optional description'
touch-gate merge alice
touch-gate merge alice octo/repo 1234 squash
touch-gate merge alice octo/repo 1234 squash 3f1c9e0d2b7a5c4e8f6a1b2c3d4e5f6a7b8c9d0e
```

Running `touch-gate` with no arguments lists the commands. The first
argument after `create-pr` or `merge` is the token's name.

For `create-pr`, everything after the token's name is optional, and `''`
in place of the repository, the head branch, the base branch or the title
means the same as leaving it out. Leaving any of them out requires running
the client inside a git checkout of the repository, and the client then
finds them as `gh pr create` does. It only looks at remotes whose
configured URL is one of GitHub's, `https://github.com/OWNER/NAME`,
`git@github.com:OWNER/NAME` or `ssh://git@github.com/OWNER/NAME`,
optionally followed by `.git` or `/`.

- Without a repository, the client uses the repository of the remote named
  `upstream`, `github` or `origin`, in that order, or of the only GitHub
  remote. In a checkout of a fork that has an `upstream` remote for the
  repository the fork was made from, that is the one it uses.
- Without a head branch, the client uses the current branch, as pushed to
  GitHub. It looks first where `git push` would send the branch, following
  `branch.NAME.pushRemote`, `remote.pushDefault` and `push.default`, and
  then at a branch with the same name on each GitHub remote, starting with
  `origin`, and uses the first whose fetched copy matches the local branch.
  So push before running the client. If that branch is in another
  repository, such as a fork, the head is given as `OWNER:BRANCH`.
- Without a base branch, the client asks the repository's remote which
  branch is its default, as `git ls-remote --symref` shows.
- Without a title, the client takes the title and description from the
  oldest commit on the branch that is not on the base branch, leaving out
  merges and commits whose changes the base branch already has. The
  commit's subject becomes the title, and the rest of its message becomes
  the description, in the way `gh pr create --fill-first` chooses them. The
  client compares the branch with the base branch as last fetched from the
  repository's remote, so fetch from that remote first. Without such a
  remote, it uses the checkout's own copy of the base branch.

When `create-pr` succeeds, the client prints the new pull request's URL,
which ends with its number. The server also sends the number on its own,
for programs that use the protocol directly.

For `merge`, everything after the token's name is optional too, and `''`
in place of the repository, the pull request's number or the merge method
means the same as leaving it out:

- Without a repository, the client finds it in the git checkout, as for
  `create-pr`.
- Without a number, the client finds the open pull request whose head is
  the current branch as pushed to GitHub, found as for `create-pr`. It looks
  first in `~/.cache/github-prs.tsv`, the list of open pull requests that
  `github-hookbar` keeps, and, unless that names exactly one pull request,
  asks GitHub's REST API, which needs a token for a private repository.
  The list records only each pull request's repository and branch name,
  not whose fork the branch is in. The client then also gives the branch's
  commit as the commit that the pull request's head must be at, unless a
  commit is given, so GitHub merges the pull request only if it is what the
  checkout has.
- Without a merge method (`merge`, `squash` or `rebase`), the client asks
  GitHub's GraphQL API for the method that GitHub's web page would choose:
  the one last used, or else the repository's default. GitHub's merge API
  itself always uses `merge` unless told otherwise, which repositories that
  allow only squashing or rebasing refuse. Asking needs a token.

When the client asks GitHub's API anything, it uses the token in
`GH_TOKEN` or `GITHUB_TOKEN` if either is set, or else the token of a
signed-in `gh` command, if there is one.

Each command prints the result and exits with status 0 if the server
carried out the request, and prints the reason and exits with status 1 if
not.

## Testing

`cargo test` in `server/` runs the server's tests, and
`python3 -m unittest` in `cli-client/` runs the client's tests, which build
the server with cargo and run the client against it. Both need `openssl` on
PATH, and the client's tests also need `git` and `ssh-keygen`.

## Files

- `PROTOCOL.md`: the protocol.
- `server/src/protocol.rs`: the wire format.
- `server/src/server.rs`: the server.
- `server/src/github.rs`: the GitHub REST API calls.
- `server/src/tcp.rs`: TCP connections that notice when the other end has
  vanished.
- `server/src/tests.rs`: tests that run the server with a fake GitHub.
- `server/testdata/`: a test certificate authority, and a certificate and
  key it signed for `localhost`.
- `server/build-linux.sh`: builds the server for Linux on a Mac.
- `cli-client/touch_gate.py`: the client.
- `cli-client/test_touch_gate.py`: the client's tests, including tests
  that run it against the server.
