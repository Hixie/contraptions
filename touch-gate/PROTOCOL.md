# The touch-gate protocol

## 1. Introduction

### 1.1 Background

_This section is non-normative._

A touch-gate server carries out requests on behalf of the people who use
it, and carries out each request only when a person approves that request
by touching a FIDO2 security key, such as a YubiKey, that the server knows.
The requests this specification defines are GitHub actions: creating a pull
request from a branch, and merging a pull request. For these, the server
holds GitHub personal access tokens. Other kinds of request can be added as
new request types.

Several people can share one server. Each person has one or more security
keys and one or more tokens. The server's configuration says which keys may
use which tokens.

### 1.2 Protocol overview

_This section is non-normative._

When a person registers a security key, the key creates a credential: a
private key that never leaves the security key, and a public key, which the
server is given. The key identifies the credential by an opaque string of
bytes, its credential ID, which the client keeps. Before it connects, the
client asks the security keys plugged into its computer, without needing a
touch, which of its credentials they hold, and so picks the security key
that will approve the request.

The client opens a TLS connection to the server and sends three things at
once: a 32 byte secret that every client of the server shares, the type of
request, and the request's fields. The fields name the security key that
will approve the request and, for a GitHub action, the token to use.

If the secret is wrong, the server closes the connection without reading
anything more and without sending anything.

Otherwise, the server checks the request. If the request is acceptable, and
no other request is already waiting for the same security key to be
touched, the server replies with a random 32 byte challenge. The client
hashes the challenge, the server's certificate and
the request together, and asks the security key to sign that hash. The
person touches the key. The client sends the signature, and the data the
key signed it with, to the server.

The server makes the same hash, verifies the signature with the key's
public key, carries out the request, and replies with a status, a message,
such as the URL of a new pull request, and any results the request type
defines, such as the new pull request's number. Then the connection
closes.

A connection carries exactly one request.

### 1.3 Design philosophy

_This section is non-normative._

The protocol is designed on the principle that there should be minimal
encoding and framing. Integers are fixed-width little-endian values.
Strings are UTF-8 text ending with a 0x00 byte. Binary values, which can
contain 0x00 bytes, are preceded by their length. There are no field names,
no type tags other than the request type, and no version numbers.

The client sends everything it has before it reads anything. The server
reads only what it needs for the step it is on.

A new kind of request gets a new request type. A server that does not know
a request type refuses it with a message, before it sends a challenge, so
the person is not asked to touch the key for a request that cannot be
carried out. For the same reason, the server checks everything else it can
check about a request before it sends the challenge.

### 1.4 Security model

_This section is non-normative._

TLS protects the request, the challenge and the reply from being read or
changed in transit. The client knows the server's certificate in advance
and accepts no other, so the client does not depend on any certificate
authority, host name or date.

The shared secret limits who can reach the request parser. A peer that
completes the TLS handshake but does not know the secret receives nothing
and learns nothing beyond the server's certificate. The secret does not
identify a person, because every client of the server has it. A peer that
knows the secret can learn, from the server's refusals, which key names and
token names exist and which keys may use which tokens, without a security
key.

The security key identifies the person. The server holds only each key's
public key, so a copy of the server's configuration does not let anyone
sign as a key. With each signature, the key reports whether someone
touched it (the user-present flag) and whether it checked a PIN or a
biometric (the user-verified flag). The server requires the user-present
flag, so each approval needs a person to touch the key. The server does not
require the user-verified flag.

The key signs a hash of three things: the challenge, the server's
certificate, and the request. Each of them limits where a signature can be
used:

- The server chooses a new random challenge for each connection, so a
  signature is useless on any other connection.
- The client hashes the certificate it accepted, so a signature made while
  talking to one server does not verify at another. This matters because a
  person can register one security key with several servers, and the
  operator of one of them could otherwise pass that server's challenges on
  to another.
- The request's bytes are hashed, so a signature approves only the request
  the client sent. A peer that holds the server's TLS private key can
  accept a client's connection in place of the server and open its own
  connection to the server, but it cannot replace the client's request
  with a different one.

FIDO2 ties each credential to a relying party: the service the credential
is for, named by a string called the relying party ID. touch-gate's
relying party ID is "touch-gate". The key signs a hash of the relying party
ID along with the client's hash, so a signature made for any other relying
party, such as a website, does not verify.

A security key has no display. The person relies on the client to say
which request a touch will approve.

A touch approves whichever pending request the security key is asked to
sign when the person touches it. A program on the person's computer could
start its own request for the person's key, wait, and ask the key to sign
for it at the moment the person starts a request of their own, so that the
person's touch approves the program's request. The server therefore sends
a challenge for a key only when no other challenge for that key is
waiting for an answer. In that situation, the person's own request is
refused with a message saying that the key is already waiting for a
touch, before the person's client asks for one. The server identifies a
key by its public key, so two key names for one key do not get around
this.

With this rule, a program that tries to have the person's touch approve
its own request makes the person's request fail with a message, instead of
going unnoticed. The rule matters only against a program that can start a
request and ask the key to sign, which needs the shared secret and the
key's credential ID, but that cannot change the client or what it shows.
The reference client writes the credential ID to a file that only the
person's account can read, and its instructions say to make the secret's
file the same. The rule does not cover these cases:

- A physical security key registered twice has two credentials and two
  public keys, so the server treats it as two keys. The reference client
  refuses to register a security key that already holds one of its
  credentials.
- The rule applies within one server. It does not cover a security key
  registered with several servers, or touches for other relying parties,
  such as websites.
- A program that can read the client's configuration can usually also
  change the client.
- A person who touches a blinking key after their own request was refused
  approves whatever the key was asked to sign.

A peer that holds the shared secret can stop a key from approving anything
for as long as it keeps its own challenge unanswered. The person learns of
this when they next make a request with that key, but the message does not
say whose request is waiting.

The server's configuration lists, for each token, the keys that may use it.
A person who holds the shared secret and their own key therefore cannot use
another person's token through the protocol. Anyone who can read the
server's configuration, however, can use every token in it without any
security key.

### 1.5 Relationship to other protocols

_This section is non-normative._

The protocol runs over TLS 1.3 [RFC8446] over TCP. The signature that a
security key makes, together with the data it covers, is a FIDO2
assertion, as made by the authenticatorGetAssertion operation of CTAP2
[CTAP]. The data it covers, called the authenticator data, has the format
defined by Web Authentication [WEBAUTHN]. The server carries out the
GitHub actions that this specification defines through the GitHub REST API
[GITHUB].

## 2. Conformance requirements

All diagrams, examples, notes and warnings in this specification are
non-normative, as are all sections explicitly marked non-normative.
Everything else in this specification is normative.

The key words "must", "must not", "should", "should not" and "may" in this
specification are to be interpreted as described in RFC 2119 [RFC2119].
They are written in lowercase for readability.

Requirements phrased in the imperative as part of algorithms (such as
"read a byte" or "refuse the request and abort these steps") are to be
interpreted with the meaning of the key word ("must", "should", "may",
etc) used in introducing the algorithm.

Conformance requirements phrased as algorithms or specific steps may be
implemented in any manner, so long as the end result is equivalent. The
algorithms are written to be easy to follow, not to be fast.

The conformance classes defined by this specification are clients and
servers.

The client's rules are exact: every byte a client can receive has a defined
outcome. The server's rules for malformed input are looser: the server
should refuse such input with a message, but it may instead close the
connection.

### 2.1 Terminology

A **valid name** is a string of one or more characters, each of which is in
the range U+0041 LATIN CAPITAL LETTER A to U+005A LATIN CAPITAL LETTER Z
inclusive, in the range U+0061 LATIN SMALL LETTER A to U+007A LATIN SMALL
LETTER Z inclusive, in the range U+0030 DIGIT ZERO to U+0039 DIGIT NINE
inclusive, or is one of U+002E FULL STOP, U+005F LOW LINE and U+002D
HYPHEN-MINUS, and which is neither "." nor "..".

A **valid repository** is a string consisting of a valid name, a U+002F
SOLIDUS character, and a valid name, in that order.

The **relying party ID** is the string "touch-gate".

SHA-256 is the hash function defined in FIPS 180-4 [FIPS180].

## 3. Data formats

### 3.1 Values

The protocol has five kinds of value:

- A **u32** is four bytes, expressing an integer from 0 to 4,294,967,295
  inclusive as a little-endian 32 bit unsigned integer.
- A **u64** is eight bytes, expressing an integer from 0 to
  18,446,744,073,709,551,615 inclusive as a little-endian 64 bit unsigned
  integer.
- A **string** is a sequence of zero or more bytes other than 0x00 that is
  valid UTF-8, followed by a 0x00 byte. The 0x00 byte is not part of the
  string's value. A string therefore cannot contain U+0000 NULL. The
  **length** of a string is the number of bytes in its value.
- A **byte string** is a u32 giving a length *n*, followed by *n* bytes,
  which may have any values. The *n* bytes are the byte string's value, and
  *n* is its **length**.
- A **fixed field** is a number of bytes given where the field is used.

Example: the string "work" is the five bytes 0x77 0x6F 0x72 0x6B 0x00. The
u32 2 is the four bytes 0x02 0x00 0x00 0x00.

### 3.2 Requests

A **request** is a u32 giving the request type, followed by a string giving
the **key name**, followed by the fields for that request type, in the
order listed:

<dl>
<dt>Type 0: no-op</dt>
<dd>No further fields. A no-op request asks the server to verify the
security key and do nothing else.</dd>

<dt>Type 1: create pull request</dt>
<dd>

1. `token`: a string, the name of the token to use.
2. `repository`: a string, the repository's owner and name, as in
   "octo/repo".
3. `head`: a string, the branch the pull request merges from. For a branch
   in a fork, this is "owner:branch".
4. `base`: a string, the branch the pull request merges into.
5. `title`: a string, the pull request's title.
6. `body`: a string, the pull request's description, which may be empty.

</dd>

<dt>Type 2: merge pull request</dt>
<dd>

1. `token`: a string, the name of the token to use.
2. `repository`: a string, the repository's owner and name.
3. `number`: a u64, the pull request's number.
4. `method`: a string, one of "merge", "squash" and "rebase", or the empty
   string to use GitHub's default.
5. `sha`: a string, the commit the pull request's head must be at for the
   merge to happen, or the empty string to merge whatever the head is.

</dd>
</dl>

All other request types are reserved for future use by future versions of
this protocol.

The **request bytes** of a request are the bytes that encode it, from the
first byte of the request type to the last byte of its last field.

### 3.3 Server messages

The server sends two kinds of message:

- A **challenge** is a u32 with the value 0, followed by a fixed field of 32
  bytes giving the challenge.
- A **reply** is a u32 giving the status, followed by a string giving the
  message, followed, if the status is 0, by the request type's
  **results**. A status of 0 means the request was carried out. A status
  of 1 means it was not. All other statuses are reserved for future use by
  future versions of this protocol, and mean that the request was not
  carried out.

The results of a reply to a create pull request request (type 1) are a u64
giving the number of the pull request that the server created, or 0 if the
server cannot tell. The results of a reply to any other request type
defined here are empty.

Note: Future versions of this protocol may add results after the ones
defined here, and section 4 says that clients ignore them.

Note: The first u32 the server sends tells the client which of the two
kinds of message follows. A challenge's u32 is always 0, and a reply that
comes before the challenge always has a status other than 0.

### 3.4 Assertions

The **client data hash** for a challenge, a certificate and a request is
the SHA-256 hash of the following, in this order: the 32 bytes of the
challenge; the 32 byte SHA-256 hash of the certificate's DER encoding, as
sent in the TLS handshake; and the request bytes.

An **assertion** is a byte string giving the authenticator data, followed
by a byte string giving the signature.

The authenticator data has the format that Web Authentication defines
[WEBAUTHN]. Its first 32 bytes are the SHA-256 hash of the relying party
ID. Its 33rd byte holds flags; the flags byte _and_ed with 0x01 returns
0x01 when the user was present. Its next four bytes are a signature
counter, expressed as a big-endian 32 bit unsigned integer, which this
protocol does not use. Any further bytes are extension data, which this
protocol also does not use.

The signature is an ECDSA signature using the P-256 curve and SHA-256
[FIPS186], encoded as an ASN.1 DER `Ecdsa-Sig-Value` [RFC3279], over the
authenticator data followed by the 32 bytes of the client data hash.

## 4. Client requirements

_This section only applies to clients, not to servers._

A client has the server's address, the server's certificate, the 32 byte
secret, and, for each security key registered with the server, the key's
key name and the credential ID the key created when it was registered.

The steps to **send a request** with a request type and fields are as
follows. They return a status, a message and results, or they fail. The
client must
run these steps. If, at any read in these steps, the connection closes
before enough bytes arrive, or TLS reports an error, or a string read is
not valid UTF-8, then fail.

1. If no registered security key is connected to the client's computer,
   then fail.
2. Choose a registered security key that is connected to the client's
   computer, as a *key name* and a *credential ID*.

   Note: A client can find out whether a connected security key holds a
   credential, without a touch, by asking it for an assertion with that
   credential ID in the allow list and user presence not requested. The
   reference client asks each connected security key about each
   registered credential in turn, and chooses the first that holds one.

3. Open a TCP connection to the server's address.
4. Perform a TLS 1.3 handshake on the connection, with this one difference
   from the usual certificate checks: accept the certificate the server
   presents if and only if it is byte-for-byte equal to the server's
   certificate the client has, without checking its names or dates.

   Note: TLS still verifies the server's handshake signature with that
   certificate's public key. The server's certificate can be self-signed
   and need never be renewed.

5. Let *request* be a request (section 3.2) made of the request type,
   *key name*, and the fields.
6. Send the 32 byte secret, followed by the request bytes of *request*.
7. Read a u32. Let *status* be its value.
8. If *status* is not 0, then go to the step labeled _Message_.
9. Read 32 bytes. Let *challenge* be those bytes.
10. Let *hash* be the client data hash (section 3.4) for *challenge*, the
   server's certificate, and *request*.
11. Show the person the request type, *key name*, and every field of
    *request*, with each string shown so that no character in it can change
    how the rest is displayed.

    Note: A security key has no display, so this is the only way the
    person can know what a touch approves. A string can hold control
    characters, such as U+000D CARRIAGE RETURN, that would otherwise let it
    overwrite other text on a terminal.

12. Ask the security key for an assertion with the relying party ID
    "touch-gate", the client data hash *hash*, an allow list holding only
    *credential ID*, and user presence requested.
13. If the security key did not produce an assertion, then fail.
14. Send the assertion's authenticator data as a byte string, followed by
    the assertion's signature as a byte string.
15. Read a u32. Set *status* to its value.
16. _Message_: Read a string. Let *message* be its value.
17. Let *results* be empty.
18. If *status* is 0, then read the request type's results (section 3.3),
    and set *results* to their values.
19. Close the connection, without reading anything more.
20. Return *status*, *message* and *results*.

If *status* is 0, the request was carried out, *message* describes the
result, and *results* holds the request type's results. Any other status
means the request was not carried out, and *message* says why.

In step 18, if the connection closes, with TLS's close_notify alert, before
the first byte of the results arrives, then the client must treat the
results as their zero values: for a create pull request request, a pull
request number of 0.

Note: Servers that implement earlier versions of this protocol send no
results.

Note: When the secret is wrong, the server closes the connection without
sending anything, so these steps fail at step 7.

In step 11, a client may leave out a field whose empty value means that a
default applies, when that field is empty.

A client must support TLS 1.3's ecdsa_secp256r1_sha256 signature scheme.

A client may fail at step 16 if the string's length is greater than a
limit it chooses. The limit must be at least 1,048,576 bytes.

Note: Servers do not send strings longer than that, so a client that uses
exactly that limit fails only when the server does not conform.

## 5. Server requirements

_This section only applies to servers, not to clients._

### 5.1 Configuration

A server has:

- a certificate and its private key, for TLS, which should be an ECDSA
  key on the P-256 curve;
- the 32 byte secret;
- for each key name it knows, a P-256 public key; two key names with equal
  public keys name the same key;
- for each token name it knows, a GitHub personal access token and a set of
  key names that may use it.

### 5.2 Handling a connection

To **end the connection** is to run these steps:

1. Send a TLS close_notify alert.
2. Shut down the sending half of the TCP connection.
3. Read and discard bytes until the client closes the connection.
4. Close the connection.

Note: A TCP implementation that closes a connection while received bytes
remain unread may reset the connection, and a reset can discard a reply
the client has not yet read. Steps 2 and 3 avoid that.

To **refuse the request** is to run these steps:

1. Send a reply with status 1 and a message saying why.
2. End the connection.

When a server accepts a TCP connection, it must run the following steps.
If, at any read in these steps, the connection closes before enough bytes
arrive, or TLS reports an error, then the server may try to send a reply
with status 1, and must then close the connection and abort these steps.
If the server cannot read a value because it is malformed, then the server
should refuse the request with a message saying so, and may instead close
the connection. If these steps stop after step 14 and before step 18, for
any reason, then the server must make the key named *key name* stop
awaiting a touch before it sends anything more or closes the connection.
The server must run steps 13 and 14 as one action, which no other
connection's run of these steps can interleave with.

1. Perform a TLS 1.3 handshake on the connection, presenting the server's
   certificate.
2. Read 32 bytes. Let *candidate* be those bytes.
3. If *candidate* is not equal to the secret, then close the connection and
   abort these steps.

   Note: This step sends nothing, and the server reads nothing more from a
   connection whose secret does not match.

4. Read a u32. Let *type* be its value.
5. If *type* is not one of the request types defined in section 3.2, then
   refuse the request with a message saying that the type is unknown, and
   abort these steps.
6. Read a string. Let *key name* be its value.
7. Read the fields for *type*, in the order section 3.2 lists them. Let
   *request* be the request read in steps 4 to 7.
8. If *key name* is not a valid name, or the server knows no key with that
   name, then refuse the request and abort these steps.
9. If *type* has a `token` field, then run these substeps:
   1. If `token` is not a valid name, then refuse the request and abort
      these steps.
   2. If `repository` is not a valid repository, then refuse the request
      and abort these steps.
   3. If the server knows no token named `token`, or *key name* is not in
      that token's set of key names, then refuse the request and abort
      these steps.
10. If *type* is 1 (create pull request), and `head`, `base` or `title` is
    the empty string, then refuse the request and abort these steps.
11. If *type* is 2 (merge pull request), and `number` is 0, or `method` is
    not the empty string, "merge", "squash" or "rebase", or `sha` contains a
    character outside the ranges U+0030 DIGIT ZERO to U+0039 DIGIT NINE,
    U+0041 LATIN CAPITAL LETTER A to U+0046 LATIN CAPITAL LETTER F, and
    U+0061 LATIN SMALL LETTER A to U+0066 LATIN SMALL LETTER F, all
    inclusive, then refuse the request and abort these steps.
12. If the server can tell now that it will not be able to carry out
    *request*, for example because a file it needs cannot be read, then
    refuse the request and abort these steps.
13. If the key named *key name* is awaiting a touch, then refuse the request
    and abort these steps.
14. The key named *key name* is now **awaiting a touch**.
15. Let *challenge* be 32 bytes from a cryptographically secure random
    number generator.
16. Send a challenge (section 3.3) holding *challenge*.
17. Read an assertion (section 3.4).
18. The key named *key name* stops awaiting a touch.
19. Let *hash* be the client data hash (section 3.4) for *challenge*, the
    server's certificate, and *request*.
20. If the assertion is not a valid assertion for *hash* and the key named
    *key name*, then refuse the request and abort these steps.
21. Carry out *request* as section 5.3 describes. Let *status* be 0 if that
    succeeded and 1 if it did not, and let *message* be the message, and
    *results* the results, that section 5.3 gives.
22. Send a reply with *status*, *message* and, if *status* is 0, *results*.
23. End the connection.

Note: Section 1.4 explains why a key can be awaiting a touch for only one
request at a time.

The comparison in step 3 should take the same time wherever the two values
differ.

Note: A comparison that stops at the first difference would let a peer
learn the secret one byte at a time by measuring how long the server takes
to close the connection.

An assertion is a **valid assertion** for a client data hash and a key if
all of the following are true:

- the authenticator data is at least 37 bytes long;
- the first 32 bytes of the authenticator data are the SHA-256 hash of the
  relying party ID, which are the bytes 0x16 0x2A 0x3B 0x56 0x36 0x7E 0xD7
  0x72 0xF1 0x84 0xB2 0x53 0x3C 0xEF 0x4A 0x22 0xE0 0x5C 0x30 0x80 0x66
  0xA1 0x37 0x21 0xB5 0x6B 0x57 0xEF 0x7B 0xD6 0x2F 0x35;
- the 33rd byte of the authenticator data _and_ed with 0x01 returns 0x01;
- the signature verifies, as section 3.4 describes, with the key's public
  key, over the authenticator data followed by the client data hash.

Before a server sends a message, it must remove every U+0000 NULL character
from it, and then, if the message's UTF-8 encoding is longer than 1,048,576
bytes, cut it to the longest prefix that is at most 1,048,576 bytes long
and ends at the end of a character.

A server may close a connection at any time, without ending it as described
above.

Note: This lets a server bound the resources that peers can hold. The
reference server keeps at most four connections waiting for their secret
from any one IPv4 address or IPv6 /64 network, and 64 in all. When it
accepts a connection that would go over the first limit, it closes the
oldest waiting connection from the same network. When it accepts one that
would go over the second, it closes the oldest waiting connection from the
network with the most. It closes no connection whose secret has arrived.

A server should close a connection within 300 seconds of the client
ceasing to acknowledge TCP segments, whether or not data is waiting to be
acknowledged.

Note: Otherwise a client that vanished while its key was awaiting a touch,
for example because its computer went to sleep or its network changed,
would keep that key from approving anything for as long as the server
waits. The reference server uses TCP keepalive, sending its first probe
after 60 seconds of silence and closing the connection after six
unanswered probes ten seconds apart. On Linux it also closes a connection
whose data has gone unacknowledged for 120 seconds. A client that is alive
acknowledges the probes however long the person takes to touch the key.

A server may impose limits on the lengths of strings and byte strings that
it reads. If a value's length is greater than such a limit, the value is
malformed. A server must not limit strings to fewer than 1,048,576 bytes or
byte strings to fewer than 1,024 bytes.

Note: The limits bound the memory a client can make the server use. The
lower bounds leave room for a pull request description of GitHub's maximum
length and for any authenticator data or signature a P-256 security key
produces.

### 5.3 Carrying out a request

For each request type, the server must do the following, using the
personal access token named `token` as a bearer token where the request has
one.

<dl>
<dt>No-op</dt>
<dd>Nothing. This succeeds. The message says which key was verified.</dd>

<dt>Create pull request</dt>
<dd>Call the GitHub REST API's "create a pull request" operation for
`repository`, with `head`, `base`, `title` and `body`. If GitHub creates the
pull request, this succeeds, and the message is the pull request's URL, or,
if GitHub's response does not say which pull request it created, a message
saying that a pull request was created. Otherwise this fails, and the
message says why, giving GitHub's response if there was one. The results
(section 3.3) give the new pull request's number, or 0 if GitHub's response
does not say which pull request it created.</dd>

<dt>Merge pull request</dt>
<dd>Call the GitHub REST API's "merge a pull request" operation for pull
request `number` of `repository`, with `merge_method` set to `method`
unless `method` is the empty string, and `sha` set to `sha` unless `sha` is
the empty string. If GitHub merges the pull request, this succeeds, and the
message says so. Otherwise this fails, and the message says why, giving
GitHub's response if there was one.</dd>
</dl>

## 6. Security considerations

Only a peer that sends the shared secret reaches the request parser. A
server operator should generate the secret with a cryptographically secure
random number generator and give it only to the server's clients.

Anyone with the server's configuration has every token in it. The server
should run as a user that no one else uses, with its configuration readable
only by that user.

Anyone with the server's TLS private key can impersonate the server to its
clients and read their requests.

## 7. Example

_This section is non-normative._

A client whose key name is "laptop" asks for pull request 12 of
"octo/repo" to be squash-merged with the token named "work", with no
expected commit. After the 32 byte secret, it sends these 42 request
bytes:

```
02 00 00 00                                  type 2: merge pull request
6c 61 70 74 6f 70 00                         key name "laptop"
77 6f 72 6b 00                               token "work"
6f 63 74 6f 2f 72 65 70 6f 00                repository "octo/repo"
0c 00 00 00 00 00 00 00                      number 12
73 71 75 61 73 68 00                         method "squash"
00                                           sha ""
```

The server knows the key, and the key may use the token. The server sends a
u32 0 followed by this challenge:

```
00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f
10 11 12 13 14 15 16 17 18 19 1a 1b 1c 1d 1e 1f
```

The SHA-256 hash of the server's certificate is:

```
f1 33 8f 1d ab a5 7a 8a 5f 1d dd 16 11 7b e4 98
aa b4 39 ea f4 5e d3 e8 e5 ca 70 2d 71 f6 29 08
```

The client data hash, which is the SHA-256 hash of the challenge, the
certificate's hash and the request bytes, in that order, is:

```
52 d2 04 3d 94 99 1e 07 f2 38 d0 23 cd c3 3e 42
09 43 7a 97 c0 e2 9a 62 07 2a de 2c 4d 3f 00 5c
```

The security key returns authenticator data that starts with the 32 byte
hash given in section 5.2, followed by a flags byte such as 0x01 and a four
byte counter, and a signature over the authenticator data followed by the
client data hash. The client sends the two as byte strings. When GitHub
merges the pull request, the server sends this reply:

```
00 00 00 00                                  status 0
6d 65 72 67 65 64 20 68 74 74 70 73 3a 2f    "merged https:/"
2f 67 69 74 68 75 62 2e 63 6f 6d 2f 6f 63    "/github.com/oc"
74 6f 2f 72 65 70 6f 2f 70 75 6c 6c 2f 31    "to/repo/pull/1"
32 00                                        "2", end of string
```

## 8. References

- [CTAP] FIDO Alliance, "Client to Authenticator Protocol (CTAP)".
- [FIPS180] NIST, "Secure Hash Standard (SHS)", FIPS 180-4.
- [FIPS186] NIST, "Digital Signature Standard (DSS)", FIPS 186-5.
- [GITHUB] GitHub, "GitHub REST API documentation", "Pulls" endpoints.
- [RFC2119] S. Bradner, "Key words for use in RFCs to Indicate Requirement
  Levels", RFC 2119.
- [RFC3279] W. Polk, R. Housley, L. Bassham, "Algorithms and Identifiers
  for the Internet X.509 Public Key Infrastructure Certificate and
  Certificate Revocation List (CRL) Profile", RFC 3279.
- [RFC8446] E. Rescorla, "The Transport Layer Security (TLS) Protocol
  Version 1.3", RFC 8446.
- [WEBAUTHN] W3C, "Web Authentication: An API for accessing Public Key
  Credentials".
