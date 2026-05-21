"""Mock portal for local testing.

Listens on a Unix socket and implements the subset of the workload-facing
portal API used by the peer-attestation-demo: POST /sign-message per
atakit-portal/docs/workload-sign-message.md.

Generates a secp256k1 session key at startup and signs with it the same way
the real portal would (keccak256(DOMAIN || message), DOMAIN =
"ATAKIT_SESSION_SIGN_V1").

Usage:
    python mock_agent.py /tmp/agent-alpha.sock
    python mock_agent.py /tmp/agent-beta.sock

Then point the workload at the socket:
    AGENT_SOCKET=/tmp/agent-alpha.sock NODE_NAME=alpha DASHBOARD_PORT=3000 PEER_PORT=4000 python node.py
"""

import hashlib
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler

from Crypto.Hash import keccak as _keccak
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils as ec_utils


# ---------------------------------------------------------------------------
# Session key (generated once at startup)
# ---------------------------------------------------------------------------

_session_private = ec.generate_private_key(ec.SECP256K1())
_session_public = _session_private.public_key()
_session_pub_bytes = _session_public.public_bytes(
    serialization.Encoding.X962,
    serialization.PublicFormat.UncompressedPoint,
)


# Domain separator the real portal prepends before hashing. Must match
# SIGN_MESSAGE_DOMAIN in atakit-portal/crates/atakit-portal-api/src/workload.rs.
_SIGN_MESSAGE_DOMAIN = b"ATAKIT_SESSION_SIGN_V1"

# Solidity literal `keccak256("KEY_RESOLVER_V1")` -- KEY_DOMAIN used by
# LibKey.computeKeyFingerprint. See
# atakit-portal/crates/atakit-portal-chain/src/domains.rs.
_KEY_DOMAIN_LITERAL = b"KEY_RESOLVER_V1"


def _keccak256(data: bytes) -> bytes:
    k = _keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


def _u256(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _compute_key_fingerprint(type_id: int, key_bytes: bytes) -> bytes:
    """Mirror Solidity `LibKey.computeKeyFingerprint`:

        keccak256(abi.encode(KEY_DOMAIN, typeId, key))

    See atakit-portal/crates/atakit-portal-chain/src/hashes.rs::compute_key_fingerprint.
    `abi.encode(bytes32, uint8, bytes)` lays out as:
      slot 0: KEY_DOMAIN (32B)
      slot 1: typeId left-padded to 32B
      slot 2: offset to dynamic bytes = 0x60
      slot 3: bytes length (uint256)
      slot 4+: bytes data, right-padded to a 32B multiple
    """
    key_domain = _keccak256(_KEY_DOMAIN_LITERAL)
    pad = (32 - (len(key_bytes) % 32)) % 32
    buf = (
        key_domain
        + _u256(type_id)
        + _u256(0x60)
        + _u256(len(key_bytes))
        + key_bytes
        + b"\x00" * pad
    )
    return _keccak256(buf)


def _hex0x(data: bytes) -> str:
    return "0x" + data.hex()


# Session key fingerprint per the real portal's formula. The session_id in a
# real deploy is keccak256(abi.encode(SESSION_DOMAIN, tpmSignatureHash,
# teeReportHash)) -- the mock has no TPM/TEE, so we substitute a deterministic
# stand-in derived from the session pubkey.
_SESSION_KEY_FINGERPRINT = _compute_key_fingerprint(3, _session_pub_bytes)
_SESSION_ID = _keccak256(b"MOCK_SESSION_ID_V1" + _session_pub_bytes)


def _sign_message(message_hex: str, hash_fn: str = "keccak256") -> dict:
    """Replicate the portal's /sign-message behaviour.

    Signs `hash_fn(DOMAIN || raw_message_bytes)` with the session key
    (secp256k1 ECDSA). Returns Ethereum-style r+s+v.
    """
    raw = message_hex[2:] if message_hex.startswith("0x") else message_hex
    message_bytes = bytes.fromhex(raw)
    payload = _SIGN_MESSAGE_DOMAIN + message_bytes

    if hash_fn == "keccak256":
        digest = _keccak256(payload)
    elif hash_fn == "sha256":
        digest = hashlib.sha256(payload).digest()
    else:
        raise ValueError(f"unsupported hash_fn: {hash_fn}")

    # Sign the pre-hashed 32-byte digest. cryptography uses Prehashed(SHA256)
    # only to learn the expected digest size; keccak256 also yields 32 bytes
    # so this shim works for both hash_fns.
    der_sig = _session_private.sign(
        digest,
        ec.ECDSA(ec_utils.Prehashed(hashes.SHA256())),
    )
    r, s = ec_utils.decode_dss_signature(der_sig)
    sig_hex = _hex0x(r.to_bytes(32, "big") + s.to_bytes(32, "big") + b"\x1b")

    return {
        "hash_fn": hash_fn,
        "message_hash": _hex0x(digest),
        "signature": sig_hex,
        "session_id": _hex0x(_SESSION_ID),
        "session_pubkey": {
            "type_id": 3,
            "key": _hex0x(_session_pub_bytes),
            "fingerprint": _hex0x(_SESSION_KEY_FINGERPRINT),
        },
    }


# ---------------------------------------------------------------------------
# HTTP handler over raw socket
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def __init__(self, sock, client_address, server):
        self.rfile = sock.makefile("rb")
        self.wfile = sock.makefile("wb")
        self.client_address = client_address
        self.server = server
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if self.raw_requestline and self.parse_request():
                method = getattr(self, "do_" + self.command, None)
                if method:
                    method()
                else:
                    self.send_error(501)
        finally:
            self.wfile.flush()
            self.rfile.close()
            self.wfile.close()

    def do_POST(self):
        if self.path == "/sign-message":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
            except json.JSONDecodeError as e:
                self._json(400, {"error": f"malformed JSON: {e}"})
                return
            message = body.get("message")
            if not isinstance(message, str):
                self._json(400, {"error": "message must be a 0x-prefixed hex string"})
                return
            hash_fn = body.get("hash_fn", "keccak256")
            try:
                self._json(200, _sign_message(message, hash_fn=hash_fn))
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:
                self._json(503, {"error": str(e)})
        else:
            self.send_error(404)

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[mock-portal] {fmt % args}")


# ---------------------------------------------------------------------------
# Unix socket server
# ---------------------------------------------------------------------------

def serve(socket_path):
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(socket_path)
    sock.listen(5)
    os.chmod(socket_path, 0o777)

    print(f"[mock-portal] listening on {socket_path}")
    print(f"[mock-portal] session_id:  {_hex0x(_SESSION_ID)[:18]}...")
    print(f"[mock-portal] fingerprint: {_hex0x(_SESSION_KEY_FINGERPRINT)[:18]}...")

    try:
        while True:
            conn, _ = sock.accept()
            try:
                Handler(conn, ("localhost", 0), None)
            except Exception as e:
                print(f"[mock-portal] request error: {e}")
            finally:
                conn.close()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <socket-path>")
        print(f"Example: {sys.argv[0]} /tmp/agent-alpha.sock")
        sys.exit(1)
    serve(sys.argv[1])
