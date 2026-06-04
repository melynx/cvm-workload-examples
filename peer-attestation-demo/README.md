# peer-attestation-demo

Two CVM instances running the same workload verify each other's identity via
the CVM agent's session key, perform an authenticated key exchange, and
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
 |    +-- CVM agent            |                |    +-- CVM agent            |
 |        /sign-message        |                |        /sign-message        |
 |        (secp256k1)          |                |        (secp256k1)          |
 +-----------------------------+                +-----------------------------+
```

Both instances run the exact same workload archive (same PCR23). Per-instance
configuration (node name, peer address) is provided as unmeasured-data at
deploy time.

## Protocol

1. **Handshake** -- each node generates an ephemeral secp256k1 keypair and
   asks its CVM agent to sign the public key via `POST /sign-message`. The
   signed ephemeral key and session info (sessionId, workloadId, baseImageId)
   are exchanged.

2. **Verification** -- each node verifies the peer's signature against the
   peer's session public key, confirming the peer holds the corresponding
   private key (which never leaves the TEE). WorkloadId and baseImageId are
   checked to ensure both nodes run the same workload on a valid base image.

3. **Key exchange** -- ECDH on the ephemeral keys produces a shared secret.
   HKDF-SHA256 derives a 32-byte AES-256-GCM key. The salt binds the key to
   both session IDs (sorted for determinism).

4. **Encrypted messaging** -- heartbeat messages and dashboard messages are
   sent over the same established peer TCP connection, encrypted with
   AES-256-GCM (random 12-byte nonce per message).

## What this demonstrates

- CVM agent integration (`cvm_agent = true`, Unix socket at `/app/cvm-agent.sock`)
- Session key signing (`POST /sign-message`, secp256k1/keccak256)
- Cross-CVM identity verification (workload ID + base image match)
- Signed Diffie-Hellman key exchange (ephemeral keys + session key authentication)
- Forward secrecy (ephemeral keys discarded after ECDH)
- AES-256-GCM authenticated encryption
- Unmeasured data for per-instance configuration

## Test locally

Requires Python 3.12+ with `cryptography` and `pycryptodome`:

```bash
pip install cryptography pycryptodome
```

Run a mock CVM agent and workload node in separate terminals:

```bash
# Terminal 1 -- mock agent for alpha
python mock_agent.py /tmp/agent-alpha.sock

# Terminal 2 -- alpha node
AGENT_SOCKET=/tmp/agent-alpha.sock NODE_NAME=alpha DASHBOARD_PORT=3000 PEER_PORT=4000 python node.py

# Terminal 3 -- mock agent for beta
python mock_agent.py /tmp/agent-beta.sock

# Terminal 4 -- beta node
AGENT_SOCKET=/tmp/agent-beta.sock NODE_NAME=beta DASHBOARD_PORT=3001 PEER_PORT=4001 python node.py
```

Open http://localhost:3000, enter `localhost:4001` as the peer address, and
click Connect. Both dashboards will show the attestation, key exchange, and
encrypted message flow.

Note: each mock agent generates its own secp256k1 session key but reports the
same workload ID, so cross-verification passes. The mock agents report
`isEmulation: true` in `/platform`.

## Deploy to CVMs

```bash
cd cvm-workload-examples/peer-attestation-demo

# Build the archive (same for both instances)
atakit workload build -d .

# Deploy alpha
echo '{"node_name": "alpha"}' > peer-config.json
atakit cloud deploy -d . --target <target> --name peer-demo-alpha

# Get alpha's external IP
atakit cloud status peer-demo-alpha --target <target>

# Deploy beta (with auto-connect to alpha)
echo '{"node_name": "beta", "peer_addr": "<alpha-ip>:4000"}' > peer-config.json
atakit cloud deploy -d . --target <target> --name peer-demo-beta
```

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

The workload verifies its peer **on-chain itself** (no web3 dependency — plain
JSON-RPC `eth_call` from `protocol.py`). On connect it queries, for both the
local and the peer session:

1. `SessionRegistry.getSession(sessionId)` — the registered session (workload
   id, base image id, platform profile id, variant id, registered/expires).
2. `SessionRegistry.isSessionActive(sessionId)` — the session is live.
3. `BaseImageRegistry.getPlatformProfile(platformProfileId)` — resolves the
   profile name (e.g. `gcp-tdx`) into human-readable cloud / TEE.

The peer is accepted only if its session is **registered, active, and bound to
the same workload + base image** as ours. The dashboard's "On-Chain
Verification" card shows the pass/fail checks and full session info for both
sides, and lets the operator set the **RPC URL** and **SessionRegistry /
BaseImageRegistry addresses** (pre-filled with the deployment defaults; also
overridable via `ONCHAIN_RPC_URL` / `SESSION_REGISTRY` / `BASE_IMAGE_REGISTRY`
env). It re-runs on demand via the "Verify On-Chain" button.

The peer-signature check (the peer holds the TEE-resident session key) still
runs during the TCP handshake; the on-chain check adds the registry binding.
The CVM needs outbound access to the RPC endpoint.

## Security notes

- **Session key isolation** -- the session private key never leaves the CVM
  agent (runs inside the TEE). The workload only receives signatures.
- **Forward secrecy** -- ephemeral ECDH keys are generated per connection and
  discarded after deriving the shared secret. Compromising a session key does
  not reveal past message content.
- **Replay protection** -- AES-GCM nonces are random per message. The HKDF
  salt binds the derived key to both session IDs, preventing key reuse across
  different session pairs.
