# peer-attestation-demo

Two CVM instances use `atakit-verifierd` to verify each other's current atakit
session. Each instance then verifies a signed ephemeral secp256k1 key with the
session public key returned by `atakit-verifierd` and derives an AES key.

Current source version: `peer-attestation-demo:v0.0.7`. This source version is
not published yet. The published `peer-attestation-demo:v0.0.5` archive uses
the older hand-written registry check.

## Security warning

This example does not implement a secure messaging channel. Its
encrypted-message frames have three known defects:

- AES-GCM receives no associated data;
- the frame sender and sequence number are not authenticated; and
- the receiver does not reject replayed frames.

Use this example only to test peer session verification and session-key
binding. Do not copy its encrypted-message protocol into an application.

## Verification flow

1. Each node generates an ephemeral secp256k1 key and asks its own portal to
   sign the public key through `POST /sign-message`.
2. Each node sends its claimed session identifier, signed ephemeral key, and
   signature to its peer.
3. The receiver calls `atakit-verifierd` `POST /v1/verify` with the peer portal
   host and port from its per-instance configuration and the expected
   publisher-qualified base-image and workload references.
4. The receiver requires the claimed session identifier to equal the verified
   session identifier.
5. The receiver verifies the ephemeral-key signature with the session public
   key returned by `atakit-verifierd`. It does not trust the key claimed in the
   peer frame.
6. Both nodes derive an AES key through ephemeral ECDH and HKDF-SHA256 for the
   encrypted-message demonstration.

`atakit-verifierd` performs the full portal TLS and current-session evidence
verification. The selected `VERIFIERD_TRUST_MODE` controls whether the trust
authority is the chain, trust packs, or explicit operator inputs. This workload
currently selects `chain` in `atakit-workload.toml`.

## Build inputs

The `atakit-verifierd` dependency image is not committed to this repository.
Package it from the matching `atakit-ng` checkout:

```sh
mkdir -p peer-attestation-demo/images
cd <atakit-ng-checkout>
./crates/atakit-verifierd/package-image.sh \
  <cvm-workload-examples-checkout>/peer-attestation-demo/images/atakit-verifierd.tar
```

Build from the example directory:

```sh
atakit workload build -d .
```

Publishing this new version and deploying it are separate actions. The
workload reference in `EXPECTED_WORKLOAD_REF` must match the reference that is
published and assigned to both sessions.

## Per-instance configuration

Each instance receives one unmeasured `peer-config.json`. The demo TCP address
and the peer portal endpoint may both vary at deployment time. The portal
endpoint is routing input. `atakit-verifierd` accepts it only after checking its
destination policy, then establishes trust through portal TLS attestation and
current-session verification.

Alpha, which verifies beta:

```json
{"node_name":"alpha","peer_addr":"<beta-address>:4000","peer_portal":{"host":"<beta-address>","port":2024}}
```

Beta, which verifies alpha:

```json
{"node_name":"beta","peer_addr":"<alpha-address>:4000","peer_portal":{"host":"<alpha-address>","port":2024}}
```

The dashboard listens on port 3000. The demonstration peer socket listens on
port 4000. The peer portal must be reachable on port 2024 from the
`atakit-verifierd` dependency container.

## Local integration test

The local mock proves the HTTP and session-key binding path only. It does not
verify TEE or TPM evidence.

Install Python 3.12 or newer with `cryptography` and `pycryptodome`, then use
five terminals from this directory:

```sh
# Terminal 1
python mock_agent.py /tmp/agent-alpha.sock /tmp/session-alpha.json

# Terminal 2
python mock_agent.py /tmp/agent-beta.sock /tmp/session-beta.json

# Terminal 3, after both session files exist
python mock_verifierd.py 9100 \
  alpha:2024=/tmp/session-alpha.json beta:2024=/tmp/session-beta.json

# Terminal 4
AGENT_SOCKET=/tmp/agent-alpha.sock NODE_NAME=alpha \
PEER_PORTAL_HOST=beta PEER_PORTAL_PORT=2024 \
VERIFIERD_URL=http://127.0.0.1:9100 \
EXPECTED_BASE_IMAGE_REF=0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/automata-linux:v1 \
EXPECTED_WORKLOAD_REF=0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/peer-attestation-demo:v1 \
DASHBOARD_PORT=3000 PEER_PORT=4000 python node.py

# Terminal 5
AGENT_SOCKET=/tmp/agent-beta.sock NODE_NAME=beta \
PEER_PORTAL_HOST=alpha PEER_PORTAL_PORT=2024 \
VERIFIERD_URL=http://127.0.0.1:9100 \
EXPECTED_BASE_IMAGE_REF=0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/automata-linux:v1 \
EXPECTED_WORKLOAD_REF=0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/peer-attestation-demo:v1 \
DASHBOARD_PORT=3001 PEER_PORT=4001 python node.py
```

Open `http://127.0.0.1:3000`, enter `127.0.0.1:4001`, and select Connect.

## HTTP API

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Web dashboard |
| `GET` | `/api/state` | Current node state |
| `POST` | `/api/connect` | `{"peer_addr":"host:port"}` |
| `POST` | `/api/send` | `{"text":"message"}` |
| `POST` | `/api/disconnect` | Reset the demo connection |
