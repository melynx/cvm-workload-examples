"""Mock CVM agent for local testing.

Listens on a Unix socket and implements the subset of the CVM agent API used
by the peer-attestation-demo: POST /sign-message and GET /platform.

Generates a secp256k1 session key at startup and signs messages with it, just
like the real CVM agent would inside a TEE.

Usage:
    python mock_agent.py /tmp/agent-alpha.sock
    python mock_agent.py /tmp/agent-beta.sock

Then point the workload at the socket:
    AGENT_SOCKET=/tmp/agent-alpha.sock NODE_NAME=alpha DASHBOARD_PORT=3000 PEER_PORT=4000 python node.py
"""

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

_WORKLOAD_NAME = "peer-attestation-demo"
_WORKLOAD_VERSION = "v0.1.0"


def _keccak256(data: bytes) -> bytes:
    k = _keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


def _make_id(domain: str, *parts: bytes) -> str:
    payload = domain.encode()
    for p in parts:
        payload += p
    return "0x" + _keccak256(payload).hex()


_SESSION_ID = _make_id("SESSION_DOMAIN", _session_pub_bytes)
_WORKLOAD_ID = _make_id("WORKLOAD_DOMAIN", _WORKLOAD_NAME.encode(), _WORKLOAD_VERSION.encode())
_SESSION_KEY_FP = _make_id("KEY_RESOLVER_V1", b"\x03", _session_pub_bytes)
_BASE_IMAGE_ID = "0x" + _keccak256(b"mock-base-image-v1").hex()
_OWNER_FP = "0x" + _keccak256(b"mock-owner").hex()


def _sign_message(message_hex: str) -> dict:
    """Replicate the CVM agent's /sign-message behavior.

    Signs keccak256(raw_message_bytes) with the session key (secp256k1 ECDSA).
    Returns Ethereum-style r+s+v signature.
    """
    raw = message_hex[2:] if message_hex.startswith("0x") else message_hex
    message_bytes = bytes.fromhex(raw)
    digest = _keccak256(message_bytes)

    # Sign the pre-hashed digest (Prehashed(SHA256) tells the library not to
    # hash again; digest size matches SHA-256 at 32 bytes).
    der_sig = _session_private.sign(
        digest,
        ec.ECDSA(ec_utils.Prehashed(hashes.SHA256())),
    )

    r, s = ec_utils.decode_dss_signature(der_sig)
    sig_hex = "0x" + r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex() + "1b"

    return {
        "signature": sig_hex,
        "sessionId": _SESSION_ID,
        "sessionKeyPublic": {"typeId": 3, "key": "0x" + _session_pub_bytes.hex()},
        "sessionKeyFingerprint": _SESSION_KEY_FP,
        "ownerKeyPublic": {"typeId": 3, "key": "0x" + _session_pub_bytes.hex()},
        "ownerFingerprint": _OWNER_FP,
        "workloadId": _WORKLOAD_ID,
        "baseImageId": _BASE_IMAGE_ID,
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

    def do_GET(self):
        if self.path == "/platform":
            self._json(200, {
                "teeType": 0, "teeName": "tdx",
                "cloudType": 3, "cloudName": "qemu",
                "isEmulation": True,
            })
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/sign-message":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            try:
                self._json(200, _sign_message(body.get("message", "0x")))
            except Exception as e:
                self._json(500, {"error": str(e)})
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
        print(f"[mock-agent] {fmt % args}")


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

    print(f"[mock-agent] listening on {socket_path}")
    print(f"[mock-agent] session_id:  {_SESSION_ID[:18]}...")
    print(f"[mock-agent] workload_id: {_WORKLOAD_ID[:18]}...")

    try:
        while True:
            conn, _ = sock.accept()
            try:
                Handler(conn, ("localhost", 0), None)
            except Exception as e:
                print(f"[mock-agent] request error: {e}")
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
