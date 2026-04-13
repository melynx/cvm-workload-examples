"""CVM agent client, cryptographic utilities, and peer verification logic.

Uses the CVM agent's /sign-message endpoint (secp256k1 session key, keccak256-
prehashed) for identity. Ephemeral secp256k1 keys provide ECDH key agreement.
AES-256-GCM encrypts the channel.

Dependencies: cryptography, pycryptodome (for keccak256).
"""

import hashlib
import http.client
import json
import os
import socket

from Crypto.Hash import keccak as _keccak
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils as ec_utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AGENT_SOCKET = os.environ.get("AGENT_SOCKET", "/app/cvm-agent.sock")

# Connection state machine
DISCONNECTED = "disconnected"
HANDSHAKE = "handshake"
VERIFYING = "verifying"
DERIVING_KEY = "deriving_key"
CONNECTED = "connected"
ATTESTATION_FAILED = "attestation_failed"
KEY_EXCHANGE_FAILED = "key_exchange_failed"
ERROR = "error"


# ---------------------------------------------------------------------------
# Unix socket HTTP client
# ---------------------------------------------------------------------------

class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP connection over a Unix domain socket."""

    def __init__(self, socket_path):
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._socket_path)


def _agent_request(method, path, body=None):
    """Send an HTTP request to the CVM agent via Unix socket."""
    conn = _UnixHTTPConnection(AGENT_SOCKET)
    headers = {"Content-Type": "application/json"} if body else {}
    payload = json.dumps(body).encode() if body else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    if resp.status >= 400:
        raise RuntimeError(
            f"CVM agent {method} {path} returned {resp.status}: {data.decode()}"
        )
    return json.loads(data)


# ---------------------------------------------------------------------------
# CVM agent operations
# ---------------------------------------------------------------------------

def agent_sign_message(message_hex: str) -> dict:
    """POST /sign-message -- sign with the session key.

    Args:
        message_hex: "0x"-prefixed hex-encoded message bytes.

    Returns dict with: signature, sessionId, sessionKeyPublic, sessionKeyFingerprint,
    ownerKeyPublic, ownerFingerprint, workloadId, baseImageId.
    """
    return _agent_request("POST", "/sign-message", {"message": message_hex})


def agent_get_platform() -> dict:
    """GET /platform -- TEE type and cloud provider."""
    return _agent_request("GET", "/platform")


# ---------------------------------------------------------------------------
# Keccak-256 (Ethereum-style, NOT SHA3-256)
# ---------------------------------------------------------------------------

def keccak256(data: bytes) -> bytes:
    k = _keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


# ---------------------------------------------------------------------------
# Ephemeral secp256k1 key pair
# ---------------------------------------------------------------------------

def generate_ephemeral_keypair():
    """Generate a secp256k1 key pair for ECDH.

    Returns (private_key, public_key_bytes) where public_key_bytes is 65-byte
    SEC1 uncompressed format (04 || x || y).
    """
    private_key = ec.generate_private_key(ec.SECP256K1())
    pub_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return private_key, pub_bytes


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def verify_signature(session_key_public: dict, message_hex: str, signature_hex: str) -> bool:
    """Verify the CVM agent's ECDSA-secp256k1 signature.

    The agent signs keccak256(message_bytes) where message_bytes is the raw
    bytes of the hex-encoded message. The signature is Ethereum-style:
    r (32 bytes) + s (32 bytes) + v (1 byte).

    Args:
        session_key_public: {"typeId": 3, "key": "0x04..."} from agent response.
        message_hex: The "0x"-prefixed hex string that was passed to /sign-message.
        signature_hex: "0x"-prefixed signature from agent.

    Returns True if valid.
    """
    key_bytes = bytes.fromhex(session_key_public["key"][2:])
    pub_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), key_bytes)

    sig_bytes = bytes.fromhex(signature_hex[2:])
    r = int.from_bytes(sig_bytes[:32], "big")
    s = int.from_bytes(sig_bytes[32:64], "big")
    der_sig = ec_utils.encode_dss_signature(r, s)

    # The agent keccak256-hashes the raw bytes of the hex string before signing.
    message_bytes = bytes.fromhex(message_hex[2:])
    digest = keccak256(message_bytes)

    try:
        pub_key.verify(
            der_sig,
            digest,
            ec.ECDSA(ec_utils.Prehashed(hashes.SHA256())),
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ECDH + key derivation
# ---------------------------------------------------------------------------

def compute_shared_secret(
    my_private: ec.EllipticCurvePrivateKey,
    peer_public_bytes: bytes,
    salt: bytes,
) -> bytes:
    """ECDH key agreement followed by HKDF-SHA256 to derive a 32-byte AES key.

    Args:
        my_private: Our ephemeral private key.
        peer_public_bytes: Peer's 65-byte SEC1 uncompressed public key.
        salt: Context binding (e.g. sorted session IDs).

    Returns 32-byte AES-256-GCM key.
    """
    peer_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256K1(), peer_public_bytes
    )
    raw_shared = my_private.exchange(ec.ECDH(), peer_pub)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"peer-attestation-demo-v1",
    ).derive(raw_shared)


# ---------------------------------------------------------------------------
# AES-256-GCM message encryption
# ---------------------------------------------------------------------------

def encrypt_message(plaintext: str, key: bytes) -> dict:
    """Encrypt with AES-256-GCM.

    Returns {"nonce": "0x...", "ciphertext": "0x...", "tag": "0x..."}.
    """
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    # GCM appends the 16-byte tag to the ciphertext
    ciphertext, tag = ct[:-16], ct[-16:]
    return {
        "nonce": "0x" + nonce.hex(),
        "ciphertext": "0x" + ciphertext.hex(),
        "tag": "0x" + tag.hex(),
    }


def decrypt_message(encrypted: dict, key: bytes) -> str:
    """Decrypt AES-256-GCM. Raises ValueError on tampered data."""
    aesgcm = AESGCM(key)
    nonce = bytes.fromhex(encrypted["nonce"][2:])
    ciphertext = bytes.fromhex(encrypted["ciphertext"][2:])
    tag = bytes.fromhex(encrypted["tag"][2:])
    plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)
    return plaintext.decode()


# ---------------------------------------------------------------------------
# Session info helpers
# ---------------------------------------------------------------------------

def parse_session_info(sign_response: dict) -> dict:
    """Normalize the /sign-message response into a session_info dict."""
    return {
        "session_id": sign_response["sessionId"],
        "session_key_public": sign_response["sessionKeyPublic"],
        "session_key_fingerprint": sign_response["sessionKeyFingerprint"],
        "workload_id": sign_response["workloadId"],
        "base_image_id": sign_response["baseImageId"],
    }


def verify_peer_session(peer_info: dict, local_info: dict) -> dict:
    """Verify that a peer belongs to the same workload.

    Checks workload_id and base_image_id. In production you would also query
    SessionRegistry.getSession(peer.session_id) on-chain and call
    verifySessionSignature to confirm the session is active.

    Returns {"verified": bool, "checks": [{"name", "passed", "local", "remote"}]}.
    """
    checks = []
    for field, label in [
        ("workload_id", "Workload ID"),
        ("base_image_id", "Base Image"),
    ]:
        local_val = local_info.get(field, "")
        remote_val = peer_info.get(field, "")
        checks.append({
            "name": label,
            "passed": local_val == remote_val,
            "local": local_val,
            "remote": remote_val,
        })

    # Session key fingerprint: just display, not a pass/fail check
    checks.append({
        "name": "Session Key",
        "passed": True,  # informational
        "local": local_info.get("session_key_fingerprint", ""),
        "remote": peer_info.get("session_key_fingerprint", ""),
    })

    return {
        "verified": all(c["passed"] for c in checks[:2]),
        "checks": checks,
    }


def shared_secret_fingerprint(aes_key: bytes) -> str:
    """First 16 hex chars of SHA-256(key) for dashboard display."""
    return hashlib.sha256(aes_key).hexdigest()[:16]


def make_hkdf_salt(session_id_a: str, session_id_b: str) -> bytes:
    """Deterministic salt from both session IDs (sorted for consistency)."""
    ids = sorted([session_id_a, session_id_b])
    return hashlib.sha256((ids[0] + ids[1]).encode()).digest()
