"""Tests for the touch-gate command-line client.

The end-to-end tests build the Rust server in ../server with cargo and run
it, and use shell scripts built on openssl in place of libfido2's tools.
"""

import contextlib
import http.server
import hashlib
import json
import io
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

import touch_gate as gate

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "server"
TESTDATA = SERVER / "testdata"
SECRET = bytes([7] * 32)
CREDENTIAL = b"credential\0id"


def setUpModule():
    # The tests never reach GitHub, use the person's gh, or see their tokens.
    patches = [
        unittest.mock.patch.object(gate, "GITHUB_API", "http://127.0.0.1:1"),
        unittest.mock.patch.object(gate, "GH_COMMAND", "/nonexistent/gh"),
        unittest.mock.patch.dict(os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": ""}),
    ]
    for patch in patches:
        patch.start()
        unittest.addModuleCleanup(patch.stop)


def example_blocks():
    """Returns the bytes in each code block of PROTOCOL.md's example
    section."""
    spec = (HERE.parent / "PROTOCOL.md").read_text()
    example = next(s for s in spec.split("\n## ") if s.startswith("7. Example"))
    blocks = example.split("```")[1::2]
    return [
        bytes.fromhex("".join(line.split("  ")[0] for line in block.strip().splitlines()))
        for block in blocks
    ]


def pem_to_der(path):
    text = path.read_text()
    body = text.split("-----")[2]
    return gate.unb64("".join(body.split()))


def write_script(path, body):
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


def fake_tools(directory, devices="ioreg://1: vendor=0x1050 (YubiKey)\n"):
    """Writes stand-ins for libfido2's tools into `directory`. Every device
    other than one named "other" holds the credential in the file
    `credential`, and signs with the P-256 key in `key.der`. Each call's
    arguments are appended to a file named after the tool with ".args"."""
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
         "-outform", "DER", "-out", str(directory / "key.der")],
        check=True, capture_output=True,
    )
    (directory / "credential").write_bytes(CREDENTIAL)
    (directory / "devices").write_text(devices)
    write_script(directory / "fido2-token", 'cat "$(dirname "$0")/devices"')
    write_script(directory / "fido2-assert", r"""
d=$(dirname "$0")
echo "$@" >> "$0.args"
for a; do device=$a; done
read hash; read rp; read credential
if [ "$device" = other ] || [ "$credential" != "$(openssl base64 -A < "$d/credential")" ]; then
  echo FIDO_ERR_NO_CREDENTIALS >&2; exit 1
fi
[ "$2" = -t ] && exit 0
{ printf '\130\045'; printf %s "$rp" | openssl dgst -sha256 -binary; printf '\001\000\000\000\011'; } > "$d/data.cbor"
{ tail -c 37 "$d/data.cbor"; printf %s "$hash" | openssl base64 -d -A; } |
  openssl dgst -sha256 -keyform DER -sign "$d/key.der" -binary > "$d/signature"
echo "$hash"; echo "$rp"
openssl base64 -A < "$d/data.cbor"; echo; openssl base64 -A < "$d/signature"; echo""")
    write_script(directory / "fido2-cred", r"""
d=$(dirname "$0")
echo "$@" >> "$0.args"
if [ "$1" = -M ]; then cat > "$d/cred-input"; echo made; exit 0; fi
cat > "$d/verify-input"
openssl base64 -A < "$d/credential"; echo
openssl pkey -inform DER -in "$d/key.der" -pubout""")
    return gate.Tools(str(directory))


def public_point(tools):
    der = subprocess.run(
        ["openssl", "pkey", "-inform", "DER", "-in", os.path.join(tools.directory, "key.der"),
         "-pubout", "-outform", "DER"],
        check=True, capture_output=True,
    ).stdout
    return der[-65:]


def parse(args):
    return gate.parse_action(args, HERE)


class WireFormatTests(unittest.TestCase):
    def test_the_specification_example_matches(self):
        request, challenge, cert_hash, data_hash, reply = example_blocks()
        action = gate.merge_pull_request("work", "octo/repo", 12, "squash", "")
        self.assertEqual(gate.encode_request("laptop", action), request)
        cert = (TESTDATA / "cert.der").read_bytes()
        self.assertEqual(hashlib.sha256(cert).digest(), cert_hash)
        self.assertEqual(gate.client_data_hash(challenge, cert, request), data_hash)
        self.assertEqual(reply, b"\0\0\0\0merged https://github.com/octo/repo/pull/12\0")

    def test_requests_encode(self):
        self.assertEqual(gate.encode_request("k", gate.noop()), b"\0\0\0\0k\0")
        action = gate.create_pull_request("t", "o/r", "h", "b", "T", "")
        self.assertEqual(gate.encode_request("k", action), b"\1\0\0\0k\0t\0o/r\0h\0b\0T\0\0")
        with self.assertRaisesRegex(gate.GateError, "U\\+0000"):
            gate.encode_request("k\0", gate.noop())
        with self.assertRaisesRegex(gate.GateError, "not valid UTF-8"):
            gate.encode_request("k\udcff", gate.noop())

    def test_descriptions_escape_every_string(self):
        action = gate.create_pull_request("work", "octo/repo", "evil\r\x1b[2Kfeature", "main", "T", "a\nb")
        self.assertNotIn("\r", action.description)
        self.assertNotIn("\x1b", action.description)
        self.assertIn(r"from 'evil\r\x1b[2Kfeature'", action.description)
        self.assertIn(r"described as 'a\nb'", action.description)
        self.assertEqual(
            gate.merge_pull_request("work", "octo/repo", 12, "squash", "abc").description,
            "merge pull request 12 of 'octo/repo' by 'squash' if its head is 'abc' with token 'work'",
        )
        self.assertEqual(
            gate.merge_pull_request("work", "octo/repo", 12, "", "").description,
            "merge pull request 12 of 'octo/repo' with token 'work'",
        )

    def test_command_lines_parse(self):
        self.assertEqual(parse(["noop"]).kind, gate.NOOP)
        action = parse(["create-pr", "w", "o/r", "h", "b", "t"])
        self.assertEqual(action.fields, ["w", "o/r", "h", "b", "t", ""])
        action = parse(["create-pr", "w", "o/r", "h", "b", "t", "body"])
        self.assertEqual(action.fields[-1], "body")
        action = parse(["merge", "w", "o/r", "12", "squash", "abc"])
        self.assertEqual(action.fields, ["w", "o/r", 12, "squash", "abc"])
        self.assertEqual(parse(["merge", "w", "o/r", "12", "rebase"]).fields, ["w", "o/r", 12, "rebase", ""])
        with self.assertRaisesRegex(gate.GateError, "give a merge method"):
            parse(["merge", "w", "o/r", "12"])
        for bad in (["noop", "x"], ["merge", "w", "o/r", "twelve"], ["merge", "w", "o/r", "١"],
                    ["merge", "w", "o/r", str(1 << 64)], ["merge", "w", "o/r", "1", "a", "b", "c"],
                    ["create-pr"], []):
            self.assertIsNone(parse(bad), bad)

    def test_server_values_are_read_with_limits(self):
        reader = io.BytesIO(b"\x2a\0\0\0caf\xc3\xa9\0")
        self.assertEqual((gate.read_u32(reader), gate.read_string(reader)), (42, "café"))
        with self.assertRaisesRegex(gate.GateError, "closed the connection"):
            gate.read_u32(io.BytesIO(b"\1\0"))
        with self.assertRaisesRegex(gate.GateError, "not UTF-8"):
            gate.read_string(io.BytesIO(b"\xff\0"))
        with self.assertRaisesRegex(gate.GateError, "too long"):
            gate.read_string(io.BytesIO(b"a" * (gate.STRING_LIMIT + 1) + b"\0"))
        gate.read_string(io.BytesIO(b"a" * gate.STRING_LIMIT + b"\0"))

    def test_server_messages_are_printed_escaped(self):
        self.assertEqual(gate.printable("line one\nline two é"), "line one\nline two é")
        self.assertEqual(gate.printable("a\rb\x1b[2Kc\u202e"), "a\\rb\\x1b[2Kc\\u202e")
        self.assertEqual(gate.printable('{\r\n  "message": "Bad"\r\n}'), '{\n  "message": "Bad"\n}')

    def test_names(self):
        for name in ("laptop", "a.b_c-1", ".github"):
            self.assertTrue(gate.valid_name(name), name)
        for name in ("", ".", "..", "a b", "a/b", "é"):
            self.assertFalse(gate.valid_name(name), name)

    def test_cbor_byte_strings(self):
        self.assertEqual(gate.cbor_byte_string(b"\x43\1\2\3"), b"\1\2\3")
        self.assertEqual(gate.cbor_byte_string(b"\x58\x25" + b"\7" * 37), b"\7" * 37)
        self.assertEqual(gate.cbor_byte_string(b"\x59\1\0" + b"\x09" * 256), b"\x09" * 256)
        for bad in (b"\x44\1\2\3", b"\x63\1\2\3", b"", b"\x58"):
            with self.assertRaises(gate.GateError):
                gate.cbor_byte_string(bad)
        with self.assertRaisesRegex(gate.GateError, "not base64"):
            gate.unb64("Zm9v!")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.config = gate.Config(self.directory)

    def test_reads_the_configuration(self):
        (self.directory / "server").write_text("gate.example.com:7443\n")
        (self.directory / "secret").write_bytes(SECRET)
        (self.directory / "keys").mkdir()
        (self.directory / "keys" / "laptop.credential").write_bytes(CREDENTIAL)
        (self.directory / "keys" / ".DS_Store").write_bytes(b"")
        self.assertEqual(self.config.address(), ("gate.example.com", 7443))
        self.assertEqual(self.config.secret(), SECRET)
        self.assertEqual(self.config.keys(), {"laptop": CREDENTIAL})
        (self.directory / "server").write_text("[::1]:7443\n")
        self.assertEqual(self.config.address(), ("::1", 7443))

    def test_rejects_a_bad_configuration(self):
        with self.assertRaisesRegex(gate.GateError, re.escape(str(self.directory / "server"))):
            self.config.address()
        self.assertEqual(self.config.keys(), {})
        for bad in (b"gate.example.com\n", "gate.example.com:\u00b2".encode()):
            (self.directory / "server").write_bytes(bad)
            with self.assertRaisesRegex(gate.GateError, "host:port"):
                self.config.address()
        (self.directory / "server").write_bytes(b"\xff:1")
        with self.assertRaisesRegex(gate.GateError, "not UTF-8"):
            self.config.address()
        (self.directory / "secret").write_bytes(SECRET + b"\n")
        with self.assertRaisesRegex(gate.GateError, "32 bytes"):
            self.config.secret()
        (self.directory / "keys").mkdir()
        (self.directory / "keys" / "empty.credential").write_bytes(b"")
        with self.assertRaisesRegex(gate.GateError, "empty"):
            self.config.keys()
        (self.directory / "keys" / "empty.credential").unlink()
        for bad in ("two words.credential", "laptop.public", "laptop"):
            (self.directory / "keys" / bad).write_bytes(CREDENTIAL)
            with self.assertRaisesRegex(gate.GateError, "is not named NAME.credential with a valid key name"):
                self.config.keys()
            (self.directory / "keys" / bad).unlink()


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)

    def test_finds_the_key_holding_an_enrolled_credential(self):
        tools = fake_tools(self.directory, "other: x\nioreg://1: y\n")
        keys = {"desk": b"something else", "laptop": CREDENTIAL}
        self.assertEqual(tools.find_key(keys), ("laptop", "ioreg://1", CREDENTIAL))
        probes = (self.directory / "fido2-assert.args").read_text().splitlines()
        self.assertEqual(probes, [
            "-G -t up=false other", "-G -t up=false other",
            "-G -t up=false ioreg://1", "-G -t up=false ioreg://1",
        ])
        with self.assertRaisesRegex(gate.GateError, "^no connected security key holds an enrolled credential$"):
            tools.find_key({"desk": b"something else"})
        with self.assertRaisesRegex(gate.GateError, "no security key is enrolled"):
            tools.find_key({})
        (self.directory / "devices").write_text("")
        with self.assertRaisesRegex(gate.GateError, "no connected security key holds"):
            tools.find_key(keys)

    def test_a_key_that_cannot_answer_a_probe_is_passed_over_and_reported(self):
        tools = fake_tools(self.directory, "busy: x\nioreg://1: y\n")
        script = (self.directory / "fido2-assert").read_text()
        write_script(self.directory / "fido2-assert", script.replace(
            "#!/bin/sh\n",
            '#!/bin/sh\nfor a; do\n'
            '  [ "$a" = busy ] && { echo "fido2-assert: fido_dev_get_assert: FIDO_ERR_RX" >&2; exit 1; }\n'
            '  [ "$a" = u2f ] && { echo "fido2-assert: fido_dev_get_assert: FIDO_ERR_USER_PRESENCE_REQUIRED" >&2; exit 1; }\n'
            'done\n', 1))
        self.assertEqual(tools.find_key({"laptop": CREDENTIAL}), ("laptop", "ioreg://1", CREDENTIAL))
        expected = "could not ask the security key busy about its credentials: .*FIDO_ERR_RX"
        with self.assertRaisesRegex(gate.GateError, expected):
            tools.find_key({"desk": b"something else"})
        (self.directory / "devices").write_text("busy: x\n")
        with self.assertRaisesRegex(gate.GateError, expected):
            tools.enroll("desk", {"laptop": CREDENTIAL})
        # A key that speaks only U2F holds the credential when it asks for
        # user presence.
        (self.directory / "devices").write_text("u2f: x\n")
        self.assertEqual(tools.find_key({"laptop": CREDENTIAL}), ("laptop", "u2f", CREDENTIAL))

    def test_assertions_are_decoded(self):
        tools = fake_tools(self.directory)
        authenticator_data, signature = tools.assert_("ioreg://1", bytes(32), CREDENTIAL)
        self.assertEqual(authenticator_data[:32], hashlib.sha256(b"touch-gate").digest())
        self.assertEqual(authenticator_data[32:], b"\1\0\0\0\x09")
        self.assertEqual(signature[0], 0x30)
        with self.assertRaisesRegex(gate.GateError, "FIDO_ERR_NO_CREDENTIALS"):
            tools.assert_("other", bytes(32), CREDENTIAL)
        write_script(self.directory / "fido2-assert", "echo one; echo two")
        with self.assertRaisesRegex(gate.GateError, "too few lines"):
            tools.assert_("ioreg://1", bytes(32), CREDENTIAL)
        with self.assertRaisesRegex(gate.GateError, "could not run fido2-assert"):
            gate.Tools(str(self.directory / "missing")).assert_("ioreg://1", bytes(32), CREDENTIAL)

    def test_enrollment_needs_one_key_and_a_p256_credential(self):
        tools = fake_tools(self.directory)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            credential, point = tools.enroll("laptop", {"desk": b"something else"})
        self.assertEqual(err.getvalue(), "Touch the security key.\n")
        self.assertEqual((credential, point), (CREDENTIAL, public_point(tools)))
        lines = (self.directory / "cred-input").read_text().splitlines()
        self.assertEqual(lines[1:], ["touch-gate", "laptop", "bGFwdG9w"])
        self.assertEqual(len(gate.unb64(lines[0])), 32)
        self.assertEqual((self.directory / "fido2-cred.args").read_text(), "-M ioreg://1 es256\n-V es256\n")
        self.assertEqual((self.directory / "verify-input").read_text(), "made\n")
        with self.assertRaisesRegex(gate.GateError, "already enrolled as 'laptop'"):
            tools.enroll("desk", {"laptop": CREDENTIAL})
        (self.directory / "devices").write_text("a: x\nb: y\n")
        with self.assertRaisesRegex(gate.GateError, "exactly one"):
            tools.enroll("laptop", {})
        (self.directory / "devices").write_text("a: x\n")
        for script, expected in (
            ("cat > /dev/null; echo AQID; echo Zm9v", "unexpected kind of public key"),
            ("cat > /dev/null", "printed nothing"),
        ):
            write_script(self.directory / "fido2-cred", script)
            with self.assertRaisesRegex(gate.GateError, expected), contextlib.redirect_stderr(io.StringIO()):
                tools.enroll("laptop", {})


class EndToEndTests(unittest.TestCase):
    """Runs the client against the Rust server."""

    @classmethod
    def setUpClass(cls):
        subprocess.run(
            ["cargo", "build", "--quiet", "--manifest-path", str(SERVER / "Cargo.toml")], check=True
        )
        target = Path(os.environ.get("CARGO_TARGET_DIR", SERVER / "target"))
        cls.directory = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.directory)
        cls.tools = fake_tools(cls.directory / "tools")
        state = cls.directory / "state"
        (state / "keys").mkdir(parents=True)
        (state / "tokens").mkdir()
        (state / "secret").write_bytes(SECRET)
        shutil.copy(TESTDATA / "cert.der", state / "cert.der")
        shutil.copy(TESTDATA / "key.der", state / "key.der")
        (state / "keys" / "laptop.public").write_bytes(public_point(cls.tools))
        (state / "tokens" / "work").write_text("ghp_work\n")
        (state / "access").write_text("work\n")
        cls.state = state
        cls.server = subprocess.Popen(
            [str(target / "debug" / "touch-gate-server"), str(state), "127.0.0.1:0"],
            stderr=subprocess.PIPE, text=True,
        )
        log_reader = threading.Thread(target=cls.server.stderr.read)

        def stop_server():
            cls.server.terminate()
            cls.server.wait()
            if log_reader.is_alive():
                log_reader.join()
            cls.server.stderr.close()

        cls.addClassCleanup(stop_server)
        first = cls.server.stderr.readline()
        cls.port = int(re.fullmatch(r"listening on 127\.0\.0\.1:(\d+)\n", first).group(1))
        log_reader.start()

    def setUp(self):
        self.config = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.config)
        (self.config / "server").write_text(f"localhost:{self.port}\n")
        shutil.copy(TESTDATA / "cert.der", self.config / "server.der")
        (self.config / "secret").write_bytes(SECRET)
        (self.config / "keys").mkdir()
        (self.config / "keys" / "laptop.credential").write_bytes(CREDENTIAL)

    def run_client(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = gate.run(list(args), self.config, self.tools)
        return result, out.getvalue(), err.getvalue()

    def test_noop_is_approved_by_the_connected_key(self):
        result, out, err = self.run_client("noop")
        self.assertEqual((result, out), (True, "verified key laptop\n"))
        self.assertEqual(err, "Touch the security key 'laptop' to verify the security key.\n")

    def test_refused_requests_ask_for_no_touch(self):
        args_file = Path(self.tools.directory) / "fido2-assert.args"
        before = args_file.read_text() if args_file.exists() else ""
        result, out, err = self.run_client("create-pr", "work", "octo/repo", "feature", "main", "Title")
        self.assertEqual((result, out, err), (False, "", "key laptop may not use token work\n"))
        self.assertNotIn("-p", args_file.read_text()[len(before):])

    def test_the_server_must_present_the_expected_certificate(self):
        (self.config / "server.der").write_bytes(pem_to_der(TESTDATA / "ca.pem"))
        with self.assertRaisesRegex(gate.GateError, "not the expected one"):
            self.run_client("noop")

    def test_nothing_is_sent_to_a_server_with_another_certificate(self):
        # A TLS server with its own certificate, which records what it
        # receives.
        directory = self.config / "impostor"
        directory.mkdir()
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-nodes", "-subj", "/CN=impostor", "-keyout", str(directory / "key.pem"),
             "-out", str(directory / "cert.pem")],
            check=True, capture_output=True,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(directory / "cert.pem", directory / "key.pem")
        listener = socket.create_server(("127.0.0.1", 0))
        received = []

        def serve():
            connection, _ = listener.accept()
            try:
                with context.wrap_socket(connection, server_side=True) as tls:
                    while data := tls.recv(4096):
                        received.append(data)
            except ConnectionResetError:
                pass  # The client closed the connection without reading everything.

        server = threading.Thread(target=serve)
        server.start()
        (self.config / "server").write_text(f"127.0.0.1:{listener.getsockname()[1]}\n")
        with self.assertRaisesRegex(gate.GateError, "not the expected one"):
            self.run_client("noop")
        server.join()
        listener.close()
        self.assertEqual(received, [])

    def test_a_created_pull_request_s_number_follows_the_message(self):
        # A stand-in for the server, which approves any assertion and reports
        # pull request 34, with or without the number.
        directory = self.config / "stand-in"
        directory.mkdir()
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-nodes", "-subj", "/CN=stand-in", "-keyout", str(directory / "key.pem"),
             "-outform", "DER", "-out", str(directory / "cert.der")],
            check=True, capture_output=True,
        )
        subprocess.run(["openssl", "x509", "-inform", "DER", "-in", str(directory / "cert.der"),
                        "-out", str(directory / "cert.pem")], check=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(directory / "cert.pem", directory / "key.pem")
        action = gate.create_pull_request("work", "octo/repo", "feature", "main", "Title", "")
        request = SECRET + gate.encode_request("laptop", action)
        shutil.copy(directory / "cert.der", self.config / "server.der")
        message = "https://github.com/octo/repo/pull/34"

        def exchange(results, close_notify=True):
            listener = socket.create_server(("127.0.0.1", 0))

            def serve():
                connection, _ = listener.accept()
                with context.wrap_socket(connection, server_side=True) as tls:
                    reader = tls.makefile("rb")
                    self.assertEqual(gate.read_exact(reader, len(request)), request)
                    tls.sendall(b"\0\0\0\0" + bytes(32))
                    for _ in range(2):
                        gate.read_exact(reader, gate.read_u32(reader))
                    tls.sendall(b"\0\0\0\0" + message.encode() + b"\0" + results)
                    if close_notify:
                        try:
                            tls.unwrap()
                        except OSError:
                            pass  # The client does not answer the close_notify alert.

            server = threading.Thread(target=serve)
            server.start()
            (self.config / "server").write_text(f"127.0.0.1:{listener.getsockname()[1]}\n")
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    return gate.send(gate.Config(self.config), self.tools, action)
            finally:
                server.join()
                listener.close()

        # Bytes after the results are for future versions, and are ignored.
        self.assertEqual(exchange((34).to_bytes(8, "little") + b"more"), (0, message, (34,)))
        # A server that sends no results gives no number.
        self.assertEqual(exchange(b""), (0, message, (0,)))
        with self.assertRaisesRegex(gate.GateError, "closed the connection"):
            exchange(b"\x22\0\0")
        # A connection that ends without close_notify may have lost the
        # results, so it is an error.
        with self.assertRaisesRegex(gate.GateError, "closed the connection"):
            exchange(b"", close_notify=False)

    def test_a_signature_the_server_cannot_verify_is_refused_after_the_touch(self):
        key = self.state / "keys" / "laptop.public"
        original = key.read_bytes()
        self.addCleanup(key.write_bytes, original)
        other = fake_tools(self.config / "other-tools")
        key.write_bytes(public_point(other))
        result, out, err = self.run_client("noop")
        self.assertEqual((result, out), (False, ""))
        self.assertTrue(err.endswith("the security key's response is not valid\n"), err)

    def test_a_python_without_tls_1_3_is_refused(self):
        with unittest.mock.patch.object(gate.ssl, "HAS_TLSv1_3", False):
            with self.assertRaisesRegex(gate.GateError, "cannot use TLS 1.3"):
                self.run_client("noop")

    def test_a_wrong_secret_gets_no_reply(self):
        (self.config / "secret").write_bytes(bytes(32))
        with self.assertRaisesRegex(gate.GateError, "closed the connection"):
            self.run_client("noop")

    def test_enrolling_a_key_lets_it_approve_requests(self):
        shutil.rmtree(self.config / "keys")
        public = self.config / "public"
        public.mkdir()
        # If the credential cannot be written, the public key file is removed
        # too, so that enrolling again is not refused.
        (self.config / "keys").mkdir(mode=0o500)
        with self.assertRaises(PermissionError):
            self.run_client("enroll", "desk", str(public))
        self.assertFalse((public / "desk.public").exists())
        (self.config / "keys").chmod(0o700)
        result, out, _ = self.run_client("enroll", "desk", str(public))
        self.assertTrue(result)
        self.assertEqual((self.config / "keys" / "desk.credential").read_bytes(), CREDENTIAL)
        self.assertEqual((self.config / "keys" / "desk.credential").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.config / "keys").stat().st_mode & 0o777, 0o700)
        self.assertEqual((public / "desk.public").read_bytes(), public_point(self.tools))
        self.assertIn(f"Wrote the public key to {public.resolve() / 'desk.public'};", out)
        with self.assertRaisesRegex(gate.GateError, "already exists"):
            self.run_client("enroll", "desk", str(public))
        with self.assertRaisesRegex(gate.GateError, "not a valid key name"):
            self.run_client("enroll", "../desk")
        with self.assertRaisesRegex(gate.GateError, "is not a directory"):
            self.run_client("enroll", "desk2", str(self.config / "missing"))
        self.assertFalse((self.config / "keys" / "desk2.credential").exists())
        with self.assertRaisesRegex(gate.GateError, "already exists"):
            gate.write_new(public / "desk.public", b"", 0o600)
        shutil.copy(public / "desk.public", self.state / "keys" / "desk.public")
        self.addCleanup((self.state / "keys" / "desk.public").unlink)
        self.assertEqual(self.run_client("noop")[:2], (True, "verified key desk\n"))

    def test_usage_and_errors_from_main(self):
        with self.assertRaisesRegex(gate.GateError, "^usage:"):
            self.run_client("frobnicate")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(gate.main(["frobnicate"]), 1)
        self.assertTrue(err.getvalue().startswith("touch-gate: usage:"))



class FirstCommitTests(unittest.TestCase):
    """Checks that a pull request with no title takes the title and body of
    its branch's first commit, as `gh pr create --fill-first` does."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        # The person's own git configuration must not affect these tests.
        environment = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        patch = unittest.mock.patch.dict(os.environ, environment)
        patch.start()
        self.addCleanup(patch.stop)
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def commit(self, message):
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_the_first_commit_after_the_base_repository_branch_is_used(self):
        self.commit("Start")
        merged = self.commit("Already upstream")
        self.git("checkout", "-q", "-b", "feature")
        self.commit("Fix the\nfrobnicator\n\nIt was broken.\nNow it is not.")
        self.commit("Tidy up")
        # Local main is behind; upstream's main has the second commit.
        self.git("branch", "-f", "main", "HEAD~3")
        self.git("remote", "add", "origin", "git@github.com:someone/repo.git")
        self.git("remote", "add", "lookalike", "https://gitlab.com/octo/repo")
        self.git("update-ref", "refs/remotes/lookalike/main", merged)
        self.git("update-ref", "refs/remotes/origin/main", "HEAD~3")
        self.git("remote", "add", "upstream", "https://github.com/Octo/Repo.git")
        self.git("update-ref", "refs/remotes/upstream/main", merged)
        # A merge, and a commit whose change upstream already has, are left out.
        self.git("checkout", "-q", "-b", "side", "main")
        self.commit("Side work")
        self.git("checkout", "-q", "feature")
        self.git("merge", "-q", "--no-ff", "-m", "Merge side", "side")
        # The subject is the whole first paragraph, joined, as git and gh
        # treat it.
        expected = ("Fix the frobnicator", "It was broken.\nNow it is not.")
        self.assertEqual(gate.first_commit(self.repo, "octo/repo", "feature", "main"), expected)
        action = gate.parse_action(["create-pr", "w", "octo/repo", "someone:feature", "main"], self.repo)
        self.assertEqual(action.fields, ["w", "octo/repo", "someone:feature", "main", *expected])
        action = gate.parse_action(["create-pr", "w", "octo/repo", "feature", "main"], self.repo)
        self.assertEqual(action.fields, ["w", "octo/repo", "feature", "main", *expected])
        # Without a matching remote, the local branch is used.
        self.assertEqual(gate.first_commit(self.repo, "other/repo", "feature", "main"), ("Already upstream", ""))

    def test_signatures_do_not_end_up_in_the_title(self):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.repo / ".key")], check=True)
        self.commit("Start")
        self.git("checkout", "-q", "-b", "feature")
        self.git("-c", "gpg.format=ssh", "-c", f"user.signingkey={self.repo / '.key'}",
                 "commit", "-q", "-S", "--allow-empty", "-m", "Signed title\n\nSigned body")
        public = (self.repo / ".key.pub").read_text().split()
        (self.repo / ".allowed").write_text(f"test@example.com {public[0]} {public[1]}\n")
        self.git("config", "gpg.ssh.allowedSignersFile", str(self.repo / ".allowed"))
        self.git("config", "log.showSignature", "true")
        # Plain git now puts the signature check in front of the subject.
        self.assertIn("Good \"git\" signature", self.git("log", "-1", "--format=%s"))
        self.assertEqual(gate.first_commit(self.repo, "octo/repo", "feature", "main"), ("Signed title", "Signed body"))

    def test_the_default_base_is_the_remote_head(self):
        remote = self.repo / "remotes" / "octo" / "repo.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "trunk", str(remote)], check=True)
        self.commit("Start")
        self.git("config", f"url.{self.repo / 'remotes'}/.insteadOf", "https://github.com/")
        self.git("remote", "add", "upstream", "https://github.com/octo/repo.git")
        self.git("push", "-q", "upstream", "main:trunk")
        self.git("fetch", "-q", "upstream")
        self.assertEqual(gate.default_branch(self.repo, "octo/repo"), "trunk")
        self.git("checkout", "-q", "-b", "feature")
        self.commit("Add the thing")
        for args in (["create-pr", "w", "octo/repo", "feature"], ["create-pr", "w", "octo/repo", "feature", ""]):
            action = gate.parse_action(args, self.repo)
            self.assertEqual(action.fields, ["w", "octo/repo", "feature", "trunk", "Add the thing", ""])
        action = gate.parse_action(["create-pr", "w", "octo/repo", "feature", "", "Title"], self.repo)
        self.assertEqual(action.fields, ["w", "octo/repo", "feature", "trunk", "Title", ""])
        with self.assertRaisesRegex(gate.GateError, "no git remote names other/repo"):
            gate.default_branch(self.repo, "other/repo")
        empty = self.repo / "remotes" / "octo" / "empty.git"
        subprocess.run(["git", "init", "-q", "--bare", str(empty)], check=True)
        self.git("remote", "add", "empty", "https://github.com/octo/empty.git")
        with self.assertRaisesRegex(gate.GateError, "the remote empty did not say which branch"):
            gate.default_branch(self.repo, "octo/empty")

    def change(self, message, **environment):
        """Commits a change to a file named after the message."""
        (self.repo / message.split()[0]).write_text(message)
        self.git("add", "-A")
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "commit", "-q", "-m", message],
            check=True, env={**os.environ, **environment},
        )
        return self.git("rev-parse", "HEAD")

    def test_merges_base_side_commits_and_copies_of_base_commits_are_skipped(self):
        old = {"GIT_AUTHOR_DATE": "2001-01-01T00:00:00", "GIT_COMMITTER_DATE": "2001-01-01T00:00:00"}
        start = self.change("start")
        # Upstream has two commits, dated before everything else, that are not
        # on the branches below.
        copied = self.change("upstream-one", **old)
        self.change("upstream-two", **old)
        self.git("remote", "add", "upstream", "git@github.com:octo/repo")
        self.git("update-ref", "refs/remotes/upstream/main", "HEAD")
        self.git("config", "remote.pushonly.pushurl", "git@github.com:octo/elsewhere")
        # A branch whose first commit copies upstream's first change.
        self.git("checkout", "-q", "-b", "copying", start)
        self.git("cherry-pick", "--allow-empty", copied)
        self.change("copying-work The real first commit")
        self.assertEqual(gate.first_commit(self.repo, "octo/repo", "copying", "main"),
                         ("copying-work The real first commit", ""))
        # A branch whose first new commit is a merge of upstream.
        self.git("checkout", "-q", "-b", "merging", start)
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "merge", "-q", "--no-ff", "-m", "Merge upstream", "upstream/main")
        self.change("merging-work After the merge")
        self.assertEqual(gate.first_commit(self.repo, "octo/repo", "merging", "main"),
                         ("merging-work After the merge", ""))

    def test_an_empty_title_is_filled_in_but_a_lone_description_is_refused(self):
        self.commit("Start")
        self.git("checkout", "-q", "-b", "feature")
        self.commit("Subject\n\nBody")
        action = gate.parse_action(["create-pr", "w", "octo/repo", "feature", "main", ""], self.repo)
        self.assertEqual(action.fields[4:], ["Subject", "Body"])
        with self.assertRaisesRegex(gate.GateError, "give a title with the description"):
            gate.parse_action(["create-pr", "w", "octo/repo", "feature", "main", "", "Body"], self.repo)

    def test_a_branch_with_no_new_commits_or_no_repository_is_refused(self):
        self.commit("Start")
        with self.assertRaisesRegex(gate.GateError, "main has no commits that main does not have"):
            gate.first_commit(self.repo, "octo/repo", "main", "main")
        with self.assertRaisesRegex(gate.GateError, "git rev-list failed"):
            gate.first_commit(self.repo, "octo/repo", "missing", "main")
        with self.assertRaisesRegex(gate.GateError, "git config failed: .*nowhere"):
            gate.first_commit(self.repo / "nowhere", "octo/repo", "main", "main")
        # With no remotes at all, the local base branch is used.
        self.git("checkout", "-q", "-b", "feature")
        self.commit("Only commit")
        self.assertEqual(gate.first_commit(self.repo, "octo/repo", "feature", "main"), ("Only commit", ""))
        with unittest.mock.patch.dict(os.environ, {"PATH": str(self.repo)}):
            with self.assertRaisesRegex(gate.GateError, "could not run git"):
                gate.first_commit(self.repo, "octo/repo", "main", "main")


class CheckoutTests(unittest.TestCase):
    """Checks that the repository and head of a pull request come from the
    git checkout, as `gh pr create` finds them."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        environment = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        patch = unittest.mock.patch.dict(os.environ, environment)
        patch.start()
        self.addCleanup(patch.stop)
        self.repo = self.root / "checkout"
        # Local bare repositories stand in for GitHub's, reached through GitHub
        # URLs that git rewrites.
        for name in ("octo/repo", "alice/repo"):
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.root / "remotes" / f"{name}.git")], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self.git("config", f"url.{self.root / 'remotes'}/.insteadOf", "https://github.com/")
        self.git("config", "--add", f"url.{self.root / 'remotes'}/.insteadOf", "git@github.com:")
        self.commit("Start")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def commit(self, message):
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def test_a_fork_s_branch_becomes_a_pull_request_to_the_upstream_repository(self):
        self.git("remote", "add", "origin", "git@github.com:alice/repo.git")
        self.assertEqual(gate.default_repository(self.repo), "alice/repo")
        self.git("remote", "add", "upstream", "https://github.com/octo/repo")
        self.git("push", "-q", "upstream", "main")
        self.git("fetch", "-q", "upstream")
        self.assertEqual(gate.default_repository(self.repo), "octo/repo")
        self.git("checkout", "-q", "-b", "feature")
        self.commit("Add the thing")
        self.git("push", "-q", "-u", "origin", "feature")
        expected = ["w", "octo/repo", "alice:feature", "main", "Add the thing", ""]
        self.assertEqual(gate.parse_action(["create-pr", "w"], self.repo).fields, expected)
        action = gate.parse_action(["create-pr", "w", "", "", "", "Title"], self.repo)
        self.assertEqual(action.fields, [*expected[:4], "Title", ""])
        # A pull request within the fork needs no owner in the head.
        self.assertEqual(gate.pushed_head(self.repo, "feature", "Alice/Repo"), "feature")
        # A branch pushed without an upstream branch is found by its copy.
        self.git("checkout", "-q", "-b", "other")
        self.git("push", "-q", "origin", "other")
        self.assertEqual(gate.pushed_head(self.repo, "other", "octo/repo"), "alice:other")
        # A branch that tracks upstream's main but is pushed to the fork, as
        # branch.NAME.pushRemote says, is found in the fork.
        self.git("checkout", "-q", "-b", "triangle", "--track", "upstream/main")
        self.commit("Triangular work")
        self.git("push", "-q", "origin", "triangle")
        self.git("config", "branch.triangle.pushRemote", "origin")
        self.assertEqual(gate.pushed_head(self.repo, "triangle", "octo/repo"), "alice:triangle")
        # So is one that remote.pushDefault sends to the fork.
        self.git("config", "--unset", "branch.triangle.pushRemote")
        self.git("config", "remote.pushDefault", "origin")
        self.assertEqual(gate.pushed_head(self.repo, "triangle", "octo/repo"), "alice:triangle")
        self.git("config", "--unset", "remote.pushDefault")
        # With push.default=current, `git push` would send the branch to its
        # upstream's remote, but a branch pushed to the fork explicitly is
        # still found there.
        self.git("config", "push.default", "current")
        self.assertEqual(gate.push_destination(self.repo, "triangle", gate.github_remotes(self.repo)),
                         ("upstream", "triangle"))
        self.assertEqual(gate.pushed_head(self.repo, "triangle", "octo/repo"), "alice:triangle")
        self.git("config", "--unset", "push.default")
        # Without a push destination, an out-of-date copy on one remote does
        # not hide a matching copy on another.
        self.git("checkout", "-q", "-b", "dual", "main")
        self.commit("Dual work")
        self.git("push", "-q", "origin", "dual")
        self.commit("More dual work")
        self.git("push", "-q", "upstream", "dual")
        self.assertEqual(gate.pushed_head(self.repo, "dual", "octo/repo"), "dual")
        # A merge finds the repository and the pull request from the branch,
        # and expects the pull request's head to be the branch's commit.
        self.git("checkout", "-q", "feature")
        listing = self.root / "github-prs.tsv"
        listing.write_text("octo/repo\tfeature\t42\n")
        StandInGitHub(self, 200, b'{"data": {"repository": {"viewerDefaultMergeMethod": "REBASE"}}}')
        with unittest.mock.patch.object(gate, "PULL_REQUEST_LIST", listing), \
                unittest.mock.patch.dict(os.environ, {"GH_TOKEN": "token"}):
            action = gate.parse_action(["merge", "w"], self.repo)
            self.assertEqual(action.fields, ["w", "octo/repo", 42, "rebase", self.git("rev-parse", "HEAD")])
            action = gate.parse_action(["merge", "w", "", "", "squash", "abc"], self.repo)
            self.assertEqual(action.fields, ["w", "octo/repo", 42, "squash", "abc"])
            action = gate.parse_action(["merge", "w", "", "7"], self.repo)
            self.assertEqual(action.fields, ["w", "octo/repo", 7, "rebase", ""])
        self.git("checkout", "-q", "other")
        # A branch pushed under another name uses the pushed name.
        self.git("checkout", "-q", "-b", "local-name")
        self.git("push", "-q", "-u", "origin", "local-name:remote-name")
        self.assertEqual(gate.pushed_head(self.repo, "local-name", "octo/repo"), "alice:remote-name")

    def test_unpushed_or_unknown_branches_and_repositories_are_refused(self):
        self.git("remote", "add", "first", "https://github.com/octo/repo")
        self.assertEqual(gate.default_repository(self.repo), "octo/repo")
        self.git("remote", "add", "second", "https://github.com/alice/repo")
        self.git("remote", "add", "elsewhere", "https://gitlab.com/someone/repo")
        with self.assertRaisesRegex(gate.GateError, "cannot tell which GitHub repository to use"):
            gate.default_repository(self.repo)
        self.git("checkout", "-q", "-b", "feature")
        with self.assertRaisesRegex(gate.GateError, "feature has not been pushed to GitHub"):
            gate.pushed_head(self.repo, "feature", "octo/repo")
        self.git("push", "-q", "-u", "first", "feature")
        self.assertEqual(gate.pushed_head(self.repo, "feature", "octo/repo"), "feature")
        self.commit("Not pushed yet")
        with self.assertRaisesRegex(gate.GateError, "feature differs from first/feature, so push it first"):
            gate.pushed_head(self.repo, "feature", "octo/repo")
        # The same holds for a copy found without a push destination.
        self.git("checkout", "-q", "-b", "solo")
        self.git("push", "-q", "first", "solo")
        self.commit("Not pushed either")
        with self.assertRaisesRegex(gate.GateError, "solo differs from first/solo, so push it first"):
            gate.pushed_head(self.repo, "solo", "octo/repo")
        self.git("checkout", "-q", "feature")
        self.assertEqual(gate.current_branch(self.repo), "feature")
        self.git("checkout", "-q", "--detach")
        with self.assertRaisesRegex(gate.GateError, "not on a branch"):
            gate.current_branch(self.repo)
        # A tag with the branch's name does not change the branch's name.
        self.git("checkout", "-q", "feature")
        self.git("tag", "feature")
        self.assertEqual(gate.current_branch(self.repo), "feature")
        self.assertIsNone(gate.config_value(self.repo, "branch.missing.remote"))
        with self.assertRaisesRegex(gate.GateError, "git config failed"):
            gate.config_value(self.root / "nowhere", "branch.feature.remote")


class StandInGitHub:
    """A local HTTP server that answers every request with `status` and
    `body`, and records each request's path, Authorization header and
    body."""

    def __init__(self, test, status, body):
        self.requests = []
        stand_in = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.answer(b"")

            def do_POST(self):
                self.answer(self.rfile.read(int(self.headers["Content-Length"])))

            def answer(self, received):
                stand_in.requests.append((self.path, self.headers.get("Authorization"), received))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        test.addCleanup(thread.join)
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        patch = unittest.mock.patch.object(gate, "GITHUB_API", f"http://127.0.0.1:{self.server.server_port}")
        patch.start()
        test.addCleanup(patch.stop)


class PullRequestLookupTests(unittest.TestCase):
    """Checks how the client finds a pull request's number."""

    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        self.list = directory / "github-prs.tsv"
        patch = unittest.mock.patch.object(gate, "PULL_REQUEST_LIST", self.list)
        patch.start()
        self.addCleanup(patch.stop)
        environment = unittest.mock.patch.dict(os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": ""})
        environment.start()
        self.addCleanup(environment.stop)
        # No gh command, unless a test provides one.
        gh = unittest.mock.patch.object(gate, "GH_COMMAND", str(directory / "gh"))
        gh.start()
        self.addCleanup(gh.stop)
        self.directory = directory

    def test_the_list_is_used_when_it_names_exactly_one_pull_request(self):
        self.list.write_text(
            "Octo/Repo\tfeature\t42\nocto/repo\tother\t43\nsomeone/else\tfeature\t44\n"
            "not a valid line\nocto/repo\tbroken\tforty\n"
        )
        self.assertEqual(gate.listed_pull_requests("octo/repo", "broken"), [])
        self.assertEqual(gate.find_pull_request("octo/repo", "alice:feature"), 42)

    def test_github_is_asked_when_the_list_does_not_settle_it(self):
        github = StandInGitHub(self, 200, b'[{"number": 9803, "title": "x"}]')
        self.assertEqual(gate.find_pull_request("octo/repo", "alice:feature"), 9803)
        self.list.write_text("octo/repo\tfeature\t1\nocto/repo\tfeature\t2\n")
        with unittest.mock.patch.dict(os.environ, {"GH_TOKEN": "secret-token"}):
            self.assertEqual(gate.find_pull_request("octo/repo", "feature"), 9803)
        self.assertEqual(github.requests, [
            ("/repos/octo/repo/pulls?head=alice%3Afeature&state=open", None, b""),
            ("/repos/octo/repo/pulls?head=octo%3Afeature&state=open", "Bearer secret-token", b""),
        ])

    def test_the_default_merge_method_comes_from_github_s_graphql_api(self):
        with self.assertRaisesRegex(gate.GateError, "give a merge method"):
            gate.default_merge_method("octo/repo")
        # A signed-in gh supplies the token.
        write_script(self.directory / "gh", '[ "$*" = "auth token" ] && echo gh-token')
        github = StandInGitHub(self, 200, b'{"data": {"repository": {"viewerDefaultMergeMethod": "SQUASH"}}}')
        self.assertEqual(gate.default_merge_method("octo/repo"), "squash")
        path, authorization, body = github.requests[0]
        self.assertEqual((path, authorization), ("/graphql", "Bearer gh-token"))
        self.assertEqual(json.loads(body)["variables"], {"owner": "octo", "name": "repo"})
        self.assertIn("viewerDefaultMergeMethod", json.loads(body)["query"])
        write_script(self.directory / "gh", "exit 1")
        with self.assertRaisesRegex(gate.GateError, "give a merge method"):
            gate.default_merge_method("octo/repo")
        with unittest.mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}):
            for body, expected in [
                (b'{"data": null, "errors": [{"message": "Could not resolve"}]}', "did not say octo/repo's merge method"),
                (b'{"data": {"repository": {"viewerDefaultMergeMethod": "OCTOPUS"}}}', "as 'octopus'"),
            ]:
                StandInGitHub(self, 200, body)
                with self.assertRaisesRegex(gate.GateError, expected):
                    gate.default_merge_method("octo/repo")

    def test_github_s_answers_that_do_not_name_one_pull_request_are_refused(self):
        for status, body, expected in [
            (200, b"[]", "octo/repo has 0 open pull requests from alice:feature, so give the number"),
            (200, b'[{"number": 1}, {"number": 2}]', "has 2 open pull requests"),
            (200, b'{"message": "odd"}', "was not understood"),
            (200, b"not json", "could not ask GitHub"),
            (404, b'{"message": "Not Found"}', "GitHub replied with status 404"),
        ]:
            StandInGitHub(self, status, body)
            with self.assertRaisesRegex(gate.GateError, expected):
                gate.api_pull_request("octo/repo", "alice:feature")
        for repository in ("octo/../x", "octo", "octo/repo/pulls"):
            with self.assertRaisesRegex(gate.GateError, "is not a repository's OWNER/NAME"):
                gate.api_pull_request(repository, "alice:feature")
        with unittest.mock.patch.object(gate, "GITHUB_API", "http://127.0.0.1:1"):
            with self.assertRaisesRegex(gate.GateError, "could not ask GitHub"):
                gate.api_pull_request("octo/repo", "alice:feature")


if __name__ == "__main__":
    unittest.main()
