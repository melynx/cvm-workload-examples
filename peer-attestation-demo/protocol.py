"""CVM portal client, cryptographic utilities, and peer verification logic.

Uses the workload-facing UDS endpoint `POST /sign-message` (secp256k1 session
key) for identity. Signing is keccak256(DOMAIN || message) with
DOMAIN = "ATAKIT_SESSION_SIGN_V1" per
atakit-portal/docs/workload-sign-message.md.

Ephemeral secp256k1 keys provide ECDH key agreement; AES-256-GCM encrypts the
channel.

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

AGENT_SOCKET = os.environ.get("AGENT_SOCKET", "/run/atakit-portal.sock")

# Domain separator the portal prepends before hashing. Must match
# SIGN_MESSAGE_DOMAIN in atakit-portal/crates/atakit-portal-api/src/workload.rs.
SIGN_MESSAGE_DOMAIN = b"ATAKIT_SESSION_SIGN_V1"

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


class PortalHTTPError(RuntimeError):
    """Raised when the portal returns a non-2xx response."""

    def __init__(self, method, path, status, body):
        self.status = status
        self.body = body
        super().__init__(f"portal {method} {path} returned {status}: {body}")


def _portal_request(method, path, body=None):
    """Send an HTTP request to the portal via the workload UDS."""
    conn = _UnixHTTPConnection(AGENT_SOCKET)
    headers = {"Content-Type": "application/json"} if body else {}
    payload = json.dumps(body).encode() if body else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    if resp.status >= 400:
        raise PortalHTTPError(method, path, resp.status, data.decode(errors="replace"))
    return json.loads(data)


# ---------------------------------------------------------------------------
# Portal operations
# ---------------------------------------------------------------------------

def portal_sign_message(message_hex: str) -> dict:
    """POST /sign-message -- sign with the session key.

    Args:
        message_hex: "0x"-prefixed hex of the bytes to sign. The portal will
        compute keccak256(DOMAIN || message) and sign that digest.

    Returns dict with: hash_fn, message_hash, signature, session_id,
    session_pubkey: {type_id, key, fingerprint}.
    """
    return _portal_request("POST", "/sign-message", {"message": message_hex})


# ---------------------------------------------------------------------------
# Keccak-256 (Ethereum-style, NOT SHA3-256)
# ---------------------------------------------------------------------------

def keccak256(data: bytes) -> bytes:
    k = _keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


def sign_message_digest(message_bytes: bytes, hash_fn: str = "keccak256") -> bytes:
    """Compute message_hash = hash_fn(DOMAIN || message_bytes).

    Mirrors atakit-portal-api/src/workload.rs::hash_message.
    """
    payload = SIGN_MESSAGE_DOMAIN + message_bytes
    if hash_fn == "keccak256":
        return keccak256(payload)
    if hash_fn == "sha256":
        return hashlib.sha256(payload).digest()
    raise ValueError(f"unsupported hash_fn: {hash_fn}")


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

def verify_signature(
    session_pubkey: dict,
    message_hex: str,
    signature_hex: str,
    hash_fn: str = "keccak256",
) -> bool:
    """Verify the portal's ECDSA-secp256k1 signature.

    The portal signs `hash_fn(DOMAIN || message_bytes)` where message_bytes is
    the raw bytes of the hex-encoded message. The signature is Ethereum-style:
    r (32 bytes) + s (32 bytes) + v (1 byte). We discard v here -- ECDSA
    verification only needs (r, s).

    Args:
        session_pubkey: {"type_id": 3, "key": "0x04..."} from response.
        message_hex: The "0x"-prefixed hex that was passed to /sign-message.
        signature_hex: "0x"-prefixed 65-byte signature from the portal.
        hash_fn: "keccak256" (default) or "sha256". Must match the portal's
            response `hash_fn`.

    Returns True if valid.
    """
    key_bytes = bytes.fromhex(session_pubkey["key"][2:])
    pub_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), key_bytes)

    sig_bytes = bytes.fromhex(signature_hex[2:])
    r = int.from_bytes(sig_bytes[:32], "big")
    s = int.from_bytes(sig_bytes[32:64], "big")
    der_sig = ec_utils.encode_dss_signature(r, s)

    message_bytes = bytes.fromhex(message_hex[2:])
    digest = sign_message_digest(message_bytes, hash_fn)

    # cryptography uses the hash algorithm only to know the expected digest
    # length (32 bytes). Keccak256 and SHA-256 both produce 32 bytes, so the
    # Prehashed(SHA256()) shim accepts a keccak256 digest unchanged.
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
    """Normalize the /sign-message response into a session_info dict.

    The /sign-message response no longer carries workload_id or
    base_image_id -- those live on-chain in SessionRegistry.getSession.
    See README "On-chain verification".
    """
    return {
        "session_id": sign_response["session_id"],
        "session_pubkey": sign_response["session_pubkey"],
        "hash_fn": sign_response.get("hash_fn", "keccak256"),
    }


def verify_peer_session(peer_info: dict, local_info: dict) -> dict:
    """Build the dashboard's verification panel data.

    What we can prove locally with /sign-message alone: the peer holds the
    private key for the session_pubkey they presented (signature already
    verified by the caller). Confirming the peer's session_id binds to OUR
    workload + baseimage requires an on-chain SessionRegistry.getSession
    query -- see README "On-chain verification".

    The "Peer Signature" row reflects an actual check. The Session ID and
    Session Key rows are informational (`kind == "info"`): they surface the
    values the dashboard's Local/Remote Session panels also display, but
    nothing is compared here -- alpha and beta intentionally have
    different session IDs and fingerprints.

    Returns the same shape the dashboard expects: {verified, checks[]}.
    """
    checks = [
        {
            "name": "Peer Signature",
            "passed": True,
            "value": "verified",
        },
        {
            "name": "Session ID",
            "kind": "info",
            "value": "see Local/Remote panels",
        },
        {
            "name": "Session Key",
            "kind": "info",
            "value": "see Local/Remote panels",
        },
    ]

    return {
        "verified": True,
        "mode": "local-signature-only",
        "note": "Workload + base-image binding requires SessionRegistry.getSession",
        "checks": checks,
    }


def shared_secret_fingerprint(aes_key: bytes) -> str:
    """First 16 hex chars of SHA-256(key) for dashboard display."""
    return hashlib.sha256(aes_key).hexdigest()[:16]


def make_hkdf_salt(session_id_a: str, session_id_b: str) -> bytes:
    """Deterministic salt from both session IDs (sorted for consistency)."""
    ids = sorted([session_id_a, session_id_b])
    return hashlib.sha256((ids[0] + ids[1]).encode()).digest()
