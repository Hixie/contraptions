#!/usr/bin/env python3
"""The touch-gate command-line client.

It asks a touch-gate server to carry out a request, such as a GitHub
action, and answers the server's challenge with a FIDO2 security key, using
libfido2's command-line tools. ../README.md describes how to use it, and
../PROTOCOL.md defines the protocol.
"""

import base64
import collections
import fcntl
import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RP_ID = "touch-gate"
CREDENTIAL_EXTENSION = ".credential"
PUBLIC_EXTENSION = ".public"

# Where to find open pull requests: a list that github-hookbar keeps, and
# GitHub's REST API.
PULL_REQUEST_LIST = Path.home() / ".cache" / "github-prs.tsv"
GITHUB_API = "https://api.github.com"
GH_COMMAND = "gh"
SECRET_LEN = 32
CHALLENGE_LEN = 32
STRING_LIMIT = 1 << 20
OK = 0

NOOP = 0
CREATE_PULL_REQUEST = 1
MERGE_PULL_REQUEST = 2

USAGE = """usage:
  touch-gate enroll NAME [DIRECTORY]
  touch-gate noop
  touch-gate create-pr TOKEN [OWNER/REPO [HEAD [BASE [TITLE [BODY]]]]]
  touch-gate merge TOKEN [OWNER/REPO [NUMBER [merge|squash|rebase [SHA]]]]"""

# The DER encoding of a P-256 SubjectPublicKeyInfo, up to the point.
P256_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


class GateError(Exception):
    pass


class GitError(GateError):
    """git failed. `returncode` holds its exit status."""

    def __init__(self, message, returncode):
        super().__init__(message)
        self.returncode = returncode


class ToolError(GateError):
    """A libfido2 tool failed. `stderr` holds what it wrote to its standard
    error."""

    def __init__(self, message, stderr):
        super().__init__(message)
        self.stderr = stderr


def valid_name(name):
    """Whether `name` can name a key or a token on the server."""
    return (
        name not in ("", ".", "..")
        and all(c.isascii() and (c.isalnum() or c in "._-") for c in name)
    )


# A request's type and fields, apart from the key name, and a description
# of it for the person approving it. Each field is a str, sent as a string,
# or an int, sent as a u64.
Action = collections.namedtuple("Action", "kind fields description")


def noop():
    return Action(NOOP, [], "verify the security key")


def create_pull_request(token, repository, head, base, title, body):
    return Action(
        CREATE_PULL_REQUEST,
        [token, repository, head, base, title, body],
        f"create a pull request in {repository!r} from {head!r} into {base!r} "
        f"titled {title!r} described as {body!r} with token {token!r}",
    )


def merge_pull_request(token, repository, number, method, sha):
    description = f"merge pull request {number} of {repository!r}"
    if method:
        description += f" by {method!r}"
    if sha:
        description += f" if its head is {sha!r}"
    description += f" with token {token!r}"
    return Action(MERGE_PULL_REQUEST, [token, repository, number, method, sha], description)


def encode_string(value):
    if "\0" in value:
        raise GateError(f"{value!r} contains U+0000, which the protocol cannot send")
    try:
        return value.encode() + b"\0"
    except UnicodeEncodeError:
        raise GateError(f"{value!r} is not valid UTF-8") from None


def encode_request(key, action):
    out = struct.pack("<I", action.kind) + encode_string(key)
    for field in action.fields:
        out += struct.pack("<Q", field) if isinstance(field, int) else encode_string(field)
    return out


def client_data_hash(challenge, cert, request):
    """The hash the security key signs: SHA-256 of the challenge, the
    SHA-256 hash of the server's certificate, and the request bytes."""
    return hashlib.sha256(challenge + hashlib.sha256(cert).digest() + request).digest()


def parse_action(args, directory):
    """Returns the Action that the command-line arguments `args` describe,
    or None. A pull request's repository, head, base, title and body, when
    missing or empty, come from the git checkout in `directory`."""
    if args == ["noop"]:
        return noop()
    if args[:1] == ["create-pr"] and 2 <= len(args) <= 7:
        token, repository, head, base, title, body = (args[1:] + [""] * 6)[:6]
        if title == "" and body != "":
            raise GateError("give a title with the description")
        repository = repository or default_repository(directory)
        if head:
            branch = head.rpartition(":")[2]
        else:
            branch = current_branch(directory)
            head = pushed_head(directory, branch, repository)
        base = base or default_branch(directory, repository)
        if title == "":
            title, body = first_commit(directory, repository, branch, base)
        return create_pull_request(token, repository, head, base, title, body)
    if args[:1] == ["merge"] and 2 <= len(args) <= 6:
        token, repository, number, method, sha = (args[1:] + [""] * 5)[:5]
        if number and not (number.isascii() and number.isdigit() and int(number) < 1 << 64):
            return None
        repository = repository or default_repository(directory)
        if number:
            number = int(number)
        else:
            branch = current_branch(directory)
            number = find_pull_request(repository, pushed_head(directory, branch, repository))
            sha = sha or ref_target(directory, f"refs/heads/{branch}")
        method = method or default_merge_method(repository)
        return merge_pull_request(token, repository, number, method, sha)
    return None


def git(directory, *args):
    """Runs git in `directory` and returns its standard output. Signatures
    are not shown, whatever the person's configuration says, so that they
    do not appear in commit messages."""
    command = [
        "git", "-C", str(directory), "-c", "log.showSignature=false", "-c", "i18n.logOutputEncoding=UTF-8", *args,
    ]
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
    except OSError as e:
        raise GateError(f"could not run git: {e.strerror}") from None
    if result.returncode != 0:
        raise GitError(f"git {args[0]} failed: {result.stderr.strip()}", result.returncode)
    return result.stdout


# GitHub's URLs for a repository, as https://github.com/OWNER/NAME,
# git@github.com:OWNER/NAME or ssh://git@github.com/OWNER/NAME, optionally
# followed by ".git" or "/".
GITHUB_URL = re.compile(
    r"(?:https://|ssh://git@|git@)github\.com[/:]([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?",
    re.IGNORECASE,
)

# The remotes whose repository is the base repository, in the order that
# `gh` prefers them.
BASE_REMOTES = ("upstream", "github", "origin")


def github_remotes(directory):
    """Returns the "owner/name" of the GitHub repository at each remote whose
    configured URL is a GitHub URL, by remote name."""
    try:
        urls = git(directory, "config", "--get-regexp", r"^remote\..*\.url$")
    except GitError as e:
        if e.returncode == 1:
            return {}  # No remote has a URL.
        raise
    remotes = {}
    for line in urls.splitlines():
        key, _, value = line.partition(" ")
        if match := GITHUB_URL.fullmatch(value.strip()):
            remotes[key.removeprefix("remote.").removesuffix(".url")] = match.group(1)
    return remotes


def same_repository(a, b):
    return a.lower() == b.lower()


def remote_for(directory, repository):
    """Returns the name of the remote whose configured URL is GitHub's URL
    for `repository` (as "owner/name"), or None."""
    for remote, name in github_remotes(directory).items():
        if same_repository(name, repository):
            return remote
    return None


def default_repository(directory):
    """Returns the GitHub repository that pull requests from the checkout go
    to: that of the remote named "upstream", "github" or "origin", in that
    order, or of the only GitHub remote."""
    remotes = github_remotes(directory)
    for remote in BASE_REMOTES:
        if remote in remotes:
            return remotes[remote]
    if len(remotes) == 1:
        return next(iter(remotes.values()))
    raise GateError("cannot tell which GitHub repository to use, so give OWNER/REPO")


def current_branch(directory):
    try:
        ref = git(directory, "symbolic-ref", "--quiet", "HEAD").strip()
    except GitError:
        raise GateError("the checkout is not on a branch, so give the head branch") from None
    return ref.removeprefix("refs/heads/")


def ref_target(directory, ref):
    """Returns the commit that `ref` names, or None if there is no such ref."""
    output = git(directory, "for-each-ref", "--format=%(refname) %(objectname)", ref).split()
    return output[1] if output[:1] == [ref] else None


def config_value(directory, key):
    """Returns the value of a git configuration setting, or None."""
    try:
        return git(directory, "config", "--get", key).strip()
    except GitError as e:
        if e.returncode == 1:
            return None
        raise


def push_destination(directory, branch, remotes):
    """Returns the GitHub remote and branch name that `git push` sends the
    local branch `branch` to, or None. The remote is branch.NAME.pushRemote,
    remote.pushDefault or the branch's upstream remote, in that order. When
    it is the upstream's remote and push.default is "simple" or "upstream",
    the branch name is the upstream branch's; otherwise it is `branch`."""
    upstream = config_value(directory, f"branch.{branch}.remote")
    merge = config_value(directory, f"branch.{branch}.merge") or ""
    remote = (
        config_value(directory, f"branch.{branch}.pushRemote")
        or config_value(directory, "remote.pushDefault")
        or upstream
    )
    if remote not in remotes:
        return None
    mode = config_value(directory, "push.default") or "simple"
    if remote == upstream and merge.startswith("refs/heads/") and mode in ("simple", "upstream", "tracking"):
        return remote, merge.removeprefix("refs/heads/")
    return remote, branch


def pushed_head(directory, branch, repository):
    """Returns the head to give GitHub for the local branch `branch`: the
    branch it was pushed to, as "owner:name" if that branch is not in
    `repository`. The candidates are where `git push` would send it, and then
    a branch with the same name on each GitHub remote, starting with
    "origin"; the first whose fetched copy matches the local branch wins."""
    remotes = github_remotes(directory)
    candidates = [(remote, branch) for remote in sorted(remotes, key=lambda r: (r != "origin", r))]
    destination = push_destination(directory, branch, remotes)
    if destination is not None:
        candidates = [destination] + [c for c in candidates if c != destination]
    local = ref_target(directory, f"refs/heads/{branch}")
    pushed = [(c, ref_target(directory, f"refs/remotes/{c[0]}/{c[1]}")) for c in candidates]
    pushed = [(c, target) for c, target in pushed if target is not None]
    if not pushed:
        raise GateError(f"{branch} has not been pushed to GitHub, so push it first")
    matching = [c for c, target in pushed if target == local]
    if not matching:
        remote, name = pushed[0][0]
        raise GateError(f"{branch} differs from {remote}/{name}, so push it first")
    remote, name = matching[0]
    owner = remotes[remote].partition("/")[0]
    return name if same_repository(remotes[remote], repository) else f"{owner}:{name}"


def find_pull_request(repository, head):
    """Returns the number of the open pull request in `repository` whose head
    is `head` ("branch" or "owner:branch"), from github-hookbar's list if it
    names exactly one, or else from GitHub's REST API."""
    owner, _, branch = head.rpartition(":")
    numbers = listed_pull_requests(repository, branch)
    if len(numbers) == 1:
        return numbers[0]
    return api_pull_request(repository, f"{owner or repository.partition('/')[0]}:{branch}")


def listed_pull_requests(repository, branch):
    """Returns the numbers of the pull requests that github-hookbar's list
    has for `branch` in `repository`. Each line of the list holds a
    repository, a branch and a number, separated by tabs."""
    try:
        file = open(PULL_REQUEST_LIST, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    with file:
        fcntl.flock(file, fcntl.LOCK_SH)
        lines = file.read().splitlines()
    numbers = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) == 3 and same_repository(fields[0], repository) and fields[1] == branch:
            if fields[2].isascii() and fields[2].isdigit():
                numbers.append(int(fields[2]))
    return numbers


def checked_repository(repository):
    """Returns `repository`'s owner and name, if it is OWNER/NAME."""
    owner, _, name = repository.partition("/")
    if not (valid_name(owner) and valid_name(name)):
        raise GateError(f"{repository!r} is not a repository's OWNER/NAME")
    return owner, name


def github_token():
    """Returns a token for GitHub's API: GH_TOKEN, GITHUB_TOKEN, or the token
    of a signed-in `gh`; or None."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run([GH_COMMAND, "auth", "token"], capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def github_json(path, what, body=None):
    """Asks GitHub's API for `path`, sending `body` as JSON if it is given,
    and returns the reply's JSON. `what` names what is asked for, for error
    messages."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "touch-gate"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(GITHUB_API + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        e.close()
        raise GateError(f"GitHub replied with status {e.code} when asked for {what}") from None
    except (urllib.error.URLError, ValueError) as e:
        raise GateError(f"could not ask GitHub for {what}: {e}") from None


def api_pull_request(repository, head):
    """Returns the number of the open pull request in `repository` whose head
    is `head` ("owner:branch"), asking GitHub's REST API."""
    checked_repository(repository)
    query = urllib.parse.urlencode({"head": head, "state": "open"})
    pulls = github_json(f"/repos/{repository}/pulls?{query}", f"{repository}'s pull requests")
    try:
        numbers = [int(pull["number"]) for pull in pulls]
    except (TypeError, KeyError, ValueError):
        raise GateError(f"GitHub's list of {repository}'s pull requests was not understood") from None
    if len(numbers) != 1:
        raise GateError(f"{repository} has {len(numbers)} open pull requests from {head}, so give the number")
    return numbers[0]


def default_merge_method(repository):
    """Returns the merge method that GitHub's web page would choose for a pull
    request in `repository`: the one the token's account used last, or else
    the repository's default. GitHub's merge API does not use that default
    itself, so the client asks GitHub's GraphQL API, which needs a token."""
    owner, name = checked_repository(repository)
    if github_token() is None:
        raise GateError(
            "give a merge method (merge, squash or rebase), or set GH_TOKEN or sign in with gh "
            "so that the client can ask GitHub for the repository's default"
        )
    query = "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { viewerDefaultMergeMethod } }"
    reply = github_json("/graphql", f"{repository}'s merge method", {"query": query, "variables": {"owner": owner, "name": name}})
    try:
        method = reply["data"]["repository"]["viewerDefaultMergeMethod"].lower()
    except (TypeError, KeyError, AttributeError):
        raise GateError(f"GitHub did not say {repository}'s merge method: {json.dumps(reply)}") from None
    if method not in ("merge", "squash", "rebase"):
        raise GateError(f"GitHub gave {repository}'s merge method as {method!r}")
    return method


def default_branch(directory, repository):
    """Returns `repository`'s default branch, which its remote's HEAD names,
    asking the remote over the network."""
    remote = remote_for(directory, repository)
    if remote is None:
        raise GateError(f"no git remote names {repository}, so give the base branch")
    for line in git(directory, "ls-remote", "--symref", "--end-of-options", remote, "HEAD").splitlines():
        target, _, name = line.partition("\t")
        if name == "HEAD" and target.startswith("ref: refs/heads/"):
            return target.removeprefix("ref: refs/heads/")
    raise GateError(f"the remote {remote} did not say which branch is its default")


def tracking_branch(directory, repository, branch):
    """Returns the remote-tracking branch for `branch` from the remote whose
    URL names `repository`, or `branch` itself if there is no such
    remote-tracking branch."""
    remote = remote_for(directory, repository)
    if remote is not None:
        ref = f"refs/remotes/{remote}/{branch}"
        if git(directory, "for-each-ref", "--format=%(refname)", ref).strip() == ref:
            return ref
    return branch


def first_commit(directory, repository, branch, base):
    """Returns the subject and body of the oldest commit on the local branch
    `branch` that is not on `base`, leaving out merges and commits whose
    changes `base` already has, as `gh pr create --fill-first` does. `base`
    is looked up as a remote-tracking branch of `repository`, so that a
    stale local copy of it does not matter."""
    base_ref = tracking_branch(directory, repository, base)
    commits = git(
        directory, "rev-list", "--reverse", "--no-merges", "--right-only", "--cherry-pick",
        "--end-of-options", f"{base_ref}...{branch}", "--",
    ).split()
    if not commits:
        raise GateError(f"{branch} has no commits that {base_ref} does not have")
    message = git(directory, "log", "-1", "--format=%s%x00%b", "--end-of-options", commits[0], "--")
    title, _, body = message.partition("\0")
    return title.strip(), body.strip()


class Config:
    """The client's configuration, from a directory holding `server`
    (host:port), `server.der` (the server's certificate), `secret`, and
    `keys/NAME` (the credential ID of each enrolled security key)."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def read(self, name):
        path = self.directory / name
        try:
            return path.read_bytes()
        except OSError as e:
            raise GateError(f"{path}: {e.strerror}") from None

    def address(self):
        try:
            text = self.read("server").decode().strip()
        except UnicodeDecodeError:
            raise GateError("the server address is not UTF-8") from None
        host, separator, port = text.rpartition(":")
        if not separator or not (port.isascii() and port.isdigit()):
            raise GateError("the server address must be host:port")
        return host.strip("[]"), int(port)

    def cert(self):
        return self.read("server.der")

    def secret(self):
        secret = self.read("secret")
        if len(secret) != SECRET_LEN:
            raise GateError(f"the secret must be {SECRET_LEN} bytes long")
        return secret

    def keys(self):
        """Returns the enrolled keys' credential IDs, by name, from the files
        named NAME.credential, ignoring files whose names start with a dot."""
        directory = self.directory / "keys"
        files = sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []
        keys = {}
        for file in files:
            if file.startswith("."):
                continue
            name, extension = os.path.splitext(file)
            if extension != CREDENTIAL_EXTENSION or not valid_name(name):
                raise GateError(f"{directory / file} is not named NAME{CREDENTIAL_EXTENSION} with a valid key name")
            keys[name] = self.read(Path("keys") / file)
            if not keys[name]:
                raise GateError(f"{directory / file} is empty")
        return keys


class Tools:
    """libfido2's command-line tools, from `directory` (None searches
    PATH)."""

    def __init__(self, directory=None):
        self.directory = directory

    def run(self, program, args, input_text):
        """Runs `program` with `input_text` on its standard input, and returns
        its standard output. If it fails, the error includes what it wrote to
        its standard error."""
        path = os.path.join(self.directory, program) if self.directory else program
        try:
            result = subprocess.run(
                [path, *args], input=input_text, capture_output=True, text=True
            )
        except OSError as e:
            raise GateError(f"could not run {program}: {e.strerror}") from None
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise ToolError(f"{program} failed (exit status {result.returncode}) {stderr}", stderr)
        return result.stdout

    def devices(self):
        """Returns the paths of the connected security keys."""
        listing = self.run("fido2-token", ["-L"], "")
        return [line.split(": ", 1)[0] for line in listing.splitlines() if ": " in line]

    def holds(self, device, credential):
        """Whether `device` holds `credential`. Asks without user presence, so
        the key does not wait for a touch. Raises GateError if the security
        key cannot answer."""
        data = f"{b64(bytes(32))}\n{RP_ID}\n{b64(credential)}\n"
        try:
            self.run("fido2-assert", ["-G", "-t", "up=false", device], data)
        except ToolError as e:
            # A key that speaks only U2F reports that it holds the credential
            # by asking for user presence.
            if e.stderr.endswith("FIDO_ERR_USER_PRESENCE_REQUIRED"):
                return True
            if e.stderr.endswith("FIDO_ERR_NO_CREDENTIALS"):
                return False
            raise GateError(f"could not ask the security key {device} about its credentials: {e}") from None
        return True

    def find_key(self, keys):
        """Returns the name, device and credential ID of an enrolled key that
        a connected security key holds. A security key that cannot answer is
        passed over, and its error reported only if no other key holds an
        enrolled credential."""
        if not keys:
            raise GateError("no security key is enrolled")
        error = None
        for device in self.devices():
            try:
                for name, credential in keys.items():
                    if self.holds(device, credential):
                        return name, device, credential
            except GateError as e:
                error = error or e
        if error:
            raise error
        raise GateError("no connected security key holds an enrolled credential")

    def assert_(self, device, client_data_hash, credential):
        """Gets an assertion from `device`, with user presence, and returns
        the authenticator data and the signature."""
        data = f"{b64(client_data_hash)}\n{RP_ID}\n{b64(credential)}\n"
        lines = self.run("fido2-assert", ["-G", "-p", device], data).splitlines()
        if len(lines) < 4:
            raise GateError("fido2-assert printed too few lines")
        return cbor_byte_string(unb64(lines[2])), unb64(lines[3])

    def enroll(self, name, keys):
        """Creates a credential named `name` on the one connected security
        key, and returns its credential ID and its public key as an
        uncompressed P-256 point. Refuses a security key that already holds
        one of the credentials in `keys`."""
        devices = self.devices()
        if len(devices) != 1:
            raise GateError("connect exactly one security key to enroll it")
        for other, credential in keys.items():
            if self.holds(devices[0], credential):
                raise GateError(f"this security key is already enrolled as {other!r}")
        data = f"{b64(secrets.token_bytes(32))}\n{RP_ID}\n{name}\n{b64(name.encode())}\n"
        print("Touch the security key.", file=sys.stderr)
        made = self.run("fido2-cred", ["-M", devices[0], "es256"], data)
        lines = self.run("fido2-cred", ["-V", "es256"], made).splitlines()
        if not lines:
            raise GateError("fido2-cred printed nothing")
        spki = unb64("".join(line for line in lines[1:] if not line.startswith("-----")))
        point = spki[len(P256_SPKI_PREFIX):]
        if not spki.startswith(P256_SPKI_PREFIX) or len(point) != 65 or point[0] != 4:
            raise GateError("fido2-cred printed an unexpected kind of public key")
        return unb64(lines[0]), point


def b64(data):
    return base64.b64encode(data).decode()


def unb64(text):
    try:
        return base64.b64decode(text, validate=True)
    except ValueError:
        raise GateError("a libfido2 tool printed something that is not base64") from None


def cbor_byte_string(data):
    """Decodes a CBOR byte string, as libfido2 wraps authenticator data."""
    if data and 0x40 <= data[0] <= 0x57:
        length, rest = data[0] - 0x40, data[1:]
    elif data[:1] == b"\x58" and len(data) >= 2:
        length, rest = data[1], data[2:]
    elif data[:1] == b"\x59" and len(data) >= 3:
        length, rest = int.from_bytes(data[1:3], "big"), data[3:]
    else:
        raise GateError("the authenticator data is not a CBOR byte string")
    if len(rest) != length:
        raise GateError("the authenticator data's CBOR length is wrong")
    return rest


class Connection:
    """A TLS connection to the server, which must present exactly the
    certificate `cert`."""

    def __init__(self, host, port, cert):
        if not ssl.HAS_TLSv1_3:
            raise GateError(f"this Python's TLS library ({ssl.OPENSSL_VERSION}) cannot use TLS 1.3")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        # The certificate is checked below against the expected one, byte for
        # byte, instead of against certificate authorities, names or dates.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        # A connection that ends without TLS's close_notify alert is an
        # error, not the end of the server's reply.
        self.socket = context.wrap_socket(
            socket.create_connection((host, port)), server_hostname=host, suppress_ragged_eofs=False
        )
        if self.socket.getpeercert(binary_form=True) != cert:
            self.socket.close()
            raise GateError("the server's certificate is not the expected one")
        self.file = self.socket.makefile("rb")

    def send(self, data):
        self.socket.sendall(data)

    def close(self):
        self.file.close()
        self.socket.close()


def read_some(file, length):
    """Reads up to `length` bytes, fewer only if the server closed the
    connection with TLS's close_notify alert."""
    try:
        return file.read(length)
    except ssl.SSLEOFError:
        raise GateError("the server closed the connection") from None


def read_exact(file, length):
    data = read_some(file, length)
    if len(data) != length:
        raise GateError("the server closed the connection")
    return data


def read_u32(file):
    return struct.unpack("<I", read_exact(file, 4))[0]


def read_optional_u64(file):
    """Reads a u64, or returns 0 if the server closes the connection
    instead."""
    first = read_some(file, 1)
    if not first:
        return 0
    return struct.unpack("<Q", first + read_exact(file, 7))[0]


def read_string(file):
    data = bytearray()
    while (byte := read_exact(file, 1)) != b"\0":
        if len(data) == STRING_LIMIT:
            raise GateError("the server sent a string that is too long")
        data += byte
    try:
        return data.decode()
    except UnicodeDecodeError:
        raise GateError("the server sent a string that is not UTF-8") from None


def send(config, tools, action):
    """Sends `action` to the server, approved by a connected security key.
    Returns the server's final status and message, and the results that
    follow the message: for a pull request created, its number, or 0 if the
    server did not say; nothing otherwise. Bytes after the results are
    ignored."""
    address, cert, secret = config.address(), config.cert(), config.secret()
    key, device, credential = tools.find_key(config.keys())
    request = encode_request(key, action)
    connection = Connection(*address, cert)
    try:
        connection.send(secret + request)
        status = read_u32(connection.file)
        if status == OK:
            challenge = read_exact(connection.file, CHALLENGE_LEN)
            data_hash = client_data_hash(challenge, cert, request)
            print(f"Touch the security key {key!r} to {action.description}.", file=sys.stderr)
            authenticator_data, signature = tools.assert_(device, data_hash, credential)
            connection.send(
                struct.pack("<I", len(authenticator_data)) + authenticator_data
                + struct.pack("<I", len(signature)) + signature
            )
            status = read_u32(connection.file)
        message = read_string(connection.file)
        results = ()
        if status == OK and action.kind == CREATE_PULL_REQUEST:
            results = (read_optional_u64(connection.file),)
        return status, message, results
    finally:
        connection.close()


def run(args, config_directory, tools):
    """Runs the command in `args`. Returns whether it succeeded."""
    config = Config(config_directory)
    if args[:1] == ["enroll"] and len(args) in (2, 3):
        name = args[1]
        if not valid_name(name):
            raise GateError(f"{name!r} is not a valid key name")
        credential_path = config.directory / "keys" / (name + CREDENTIAL_EXTENSION)
        public_path = Path(args[2] if len(args) == 3 else ".") / (name + PUBLIC_EXTENSION)
        for path in (credential_path, public_path):
            if path.exists():
                raise GateError(f"{path} already exists")
        if not public_path.parent.is_dir():
            raise GateError(f"{public_path.parent} is not a directory")
        credential, public = tools.enroll(name, config.keys())
        credential_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_new(public_path, public, 0o644)
        try:
            write_new(credential_path, credential, 0o600)
        except Exception:
            public_path.unlink()
            raise
        print(f"Wrote the credential ID to {credential_path}, which stays here.")
        print(f"Wrote the public key to {public_path.resolve()}; copy it into the server's keys directory.")
        return True
    action = parse_action(args, Path.cwd())
    if action is None:
        raise GateError(USAGE)
    status, message, _ = send(config, tools, action)
    print(printable(message), file=sys.stdout if status == OK else sys.stderr)
    return status == OK


def write_new(path, data, mode):
    """Writes `data` to a file at `path`, which must not exist, readable as
    `mode` allows."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        raise GateError(f"{path} already exists") from None
    with os.fdopen(descriptor, "wb") as file:
        file.write(data)


def printable(text):
    """Escapes the characters in `text`, other than line breaks, that could
    change how a terminal shows the rest. A carriage return followed by a
    line feed counts as a line break."""
    text = text.replace("\r\n", "\n")
    return "".join(c if c.isprintable() or c == "\n" else repr(c)[1:-1] for c in text)


def main(args):
    config_directory = Path.home() / ".config" / "touch-gate"
    try:
        return 0 if run(args, config_directory, Tools()) else 1
    except (GateError, OSError) as e:
        print(f"touch-gate: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
