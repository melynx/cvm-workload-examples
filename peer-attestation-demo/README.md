# peer-attestation-demo

Two CVM instances running the same workload verify each other's identity via
the portal's per-boot session key, perform an authenticated key exchange, and
communicate over an AES-256-GCM encrypted channel. A web dashboard visualizes
the entire protocol flow in real time.

## Architecture

```
 +---------------------------+                  +---------------------------+
 |  CVM Instance (alpha)       |                |  CVM Instance (beta)        |
 |                             |  peer TCP      |                             |
 |  dashboard HTTP (:3000)     |  handshake +   |  dashboard HTTP (:3000)     |
 |  peer socket TCP (:4000)    |  encrypted <-> |  peer socket TCP (:4000)    |
 |    |                        |  messages      |    |                        |
 |    +-- atakit-portal UDS    |                |    +-- atakit-portal UDS    |
 |        /sign-message        |                |        /sign-message        |
 |        (secp256k1)          |                |        (secp256k1)          |
 +-----------------------------+                +-----------------------------+
```

Both instances run the exact same workload archive (same PCR23). Per-instance
configuration (node name, peer address) is provided as unmeasured-data at
deploy time.

## Protocol

1. **Handshake** -- each node generates an ephemeral secp256k1 keypair and
   asks the portal to sign the public key via `POST /sign-message` on the
   workload UDS (`/run/atakit-portal.sock`). The portal signs
   `keccak256("ATAKIT_SESSION_SIGN_V1" || ephemeral_pub)` with the per-boot
   session key. The signed ephemeral key, the session id, and the session
   public key are exchanged with the peer.

2. **Verification** -- each node recomputes the same domain-prefixed digest
   from the peer's ephemeral key and verifies the signature against the
   peer's session public key, confirming the peer holds the corresponding
   private key (which never leaves the TEE). Binding the peer's session id
   to a specific workload + base image requires an on-chain
   `SessionRegistry.getSession` lookup -- see "On-chain verification" below.

3. **Key exchange** -- ECDH on the ephemeral keys produces a shared secret.
   HKDF-SHA256 derives a 32-byte AES-256-GCM key. The salt binds the key to
   both session IDs (sorted for determinism).

4. **Encrypted messaging** -- heartbeat messages and dashboard messages are
   sent over the same established peer TCP connection, encrypted with
   AES-256-GCM (random 12-byte nonce per message).

## What this demonstrates

- Portal integration (`atakit-portal = true`, Unix socket at `/run/atakit-portal.sock`)
- Session key signing (`POST /sign-message`, secp256k1, domain-prefixed keccak256)
- Domain-separated message hashing (`ATAKIT_SESSION_SIGN_V1 || message`)
- Per-boot stable session identity (`session_id`, `session_pubkey`) returned
  alongside every signature
- Signed Diffie-Hellman key exchange (ephemeral keys + session key authentication)
- Forward secrecy (ephemeral keys discarded after ECDH)
- AES-256-GCM authenticated encryption
- Unmeasured data for per-instance configuration

## Test locally

Requires Python 3.12+ with `cryptography` and `pycryptodome`:

```bash
pip install cryptography pycryptodome
```

Run a mock portal and workload node in separate terminals:

```bash
# Terminal 1 -- mock portal for alpha
python mock_agent.py /tmp/agent-alpha.sock

# Terminal 2 -- alpha node
AGENT_SOCKET=/tmp/agent-alpha.sock NODE_NAME=alpha DASHBOARD_PORT=3000 PEER_PORT=4000 python node.py

# Terminal 3 -- mock portal for beta
python mock_agent.py /tmp/agent-beta.sock

# Terminal 4 -- beta node
AGENT_SOCKET=/tmp/agent-beta.sock NODE_NAME=beta DASHBOARD_PORT=3001 PEER_PORT=4001 python node.py
```

Open http://localhost:3000, enter `localhost:4001` as the peer address, and
click Connect. Both dashboards will show the attestation, key exchange, and
encrypted message flow.

Note: each mock portal generates its own secp256k1 session key and signs
domain-prefixed message hashes exactly like the real portal. The mock does
not implement on-chain `SessionRegistry` lookups, so cross-CVM
workload-binding checks are deferred to a production deployment.

## Deploy to CVMs

Both instances run the **same** pulled archive (same PCR23). Per-instance
config (node name, peer address) is supplied at deploy time via
`--unmeasured-data-dir`. This repo ships ready-made `peer-config.json` files
under `alpha/` and `beta/`, so run these commands from your checkout of this
directory (or point the flag at your own directory containing a
`peer-config.json`).

See the [repo README](../README.md) for one-time setup (configuring this repo
as a workload repository, a cloud target, and a base image).

```bash
# Pull the pre-built, on-chain-published archive into your local store.
atakit workload pull peer-attestation-demo:v0.0.1-alpha7

# Run from this directory so alpha/ and beta/ resolve.
# (If you don't have the repo checked out:
#   git clone https://github.com/melynx/cvm-workload-examples)
cd cvm-workload-examples/peer-attestation-demo

# Deploy alpha (uses alpha/peer-config.json verbatim).
atakit cloud deploy peer-attestation-demo:v0.0.1-alpha7 --unmeasured-data-dir alpha \
    --target <target> --image <base-image>:<version> --name peer-demo-alpha

# Get alpha's external IP.
atakit cloud status peer-demo-alpha --target <target>

# Edit beta/peer-config.json: replace "<alpha-ip>" with the value above,
# then deploy beta (which will auto-connect to alpha after startup).
atakit cloud deploy peer-attestation-demo:v0.0.1-alpha7 --unmeasured-data-dir beta \
    --target <target> --image <base-image>:<version> --name peer-demo-beta
```

`--unmeasured-data-dir` resolves each entry declared in
`[package] unmeasured-data` against the named directory, packs them into a
tar.gz, and ships it as part of `/init`. The directory you pick is purely a
lookup base — the files arrive on the CVM at `/atakit-portal/unmeasured-data/`
(read-only) regardless of the host-side parent dir, which is where `node.py`
expects to find them.

## Dashboard

Open `http://<instance-ip>:3000/` in a browser. The peer channel listens on
`<instance-ip>:4000`. The dashboard shows:

- **Connection status** -- current state of the handshake protocol
- **Protocol timeline** -- chronological events (signing, verification, key exchange)
- **Attestation panel** -- local and remote session info with verification checks
- **Key exchange panel** -- ephemeral keys, shared secret fingerprint
- **Message feed** -- sent and received messages with encryption details
- **Send panel** -- type custom messages to the peer

If `peer_addr` is set in `peer-config.json`, the node auto-connects 5 seconds
after startup. Otherwise, enter the peer's address in the dashboard and click
Connect.

## Configuration

Per-instance config lives in `peer-config.json` (unmeasured-data, not part of
the workload measurement):

```json
{
  "node_name": "alpha",
  "peer_addr": "10.0.0.2:4000"
}
```

Both fields are optional. If `node_name` is omitted, the first 10 characters of
the session ID are used. If `peer_addr` is omitted, connect manually via the
dashboard. If the port is omitted, the node defaults to `PEER_PORT` (`4000`).

## API

### Dashboard

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web dashboard |
| GET | `/api/state` | Full node state (JSON, polled by dashboard) |
| POST | `/api/connect` | `{"peer_addr": "host[:port]"}` |
| POST | `/api/send` | `{"text": "message"}` |
| POST | `/api/disconnect` | Reset connection |

### Peer-to-peer

The peer channel is a persistent TCP socket on `PEER_PORT`. It uses newline-
delimited JSON frames for:

- handshake
- handshake acknowledgement
- encrypted message delivery
- disconnect

## On-chain verification

In the current demo, only the peer's signature is verified locally. The
workload-facing `/sign-message` endpoint deliberately returns just the
session identity (`session_id`, `session_pubkey`) plus the signature, so
binding that session to a specific workload + base image requires the
on-chain registry. For full production security, also:

1. Query `SessionRegistry.getSession(peer.session_id)` on-chain
2. Verify the session is active (`isSessionActive`)
3. Confirm `session.sessionKeyFingerprint == peer.session_pubkey.fingerprint`
4. Check `workloadId`, `baseImageId`, expiry, and revocation as required

The dashboard displays the `session_id` and `session_pubkey.fingerprint`
needed to drive these checks.

## Security notes

- **Session key isolation** -- the session private key never leaves the CVM
  agent (runs inside the TEE). The workload only receives signatures.
- **Forward secrecy** -- ephemeral ECDH keys are generated per connection and
  discarded after deriving the shared secret. Compromising a session key does
  not reveal past message content.
- **Replay protection** -- AES-GCM nonces are random per message. The HKDF
  salt binds the derived key to both session IDs, preventing key reuse across
  different session pairs.
