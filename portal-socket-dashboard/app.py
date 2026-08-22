import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("DASHBOARD_PORT", "3300"))
PORTAL_SOCKET = os.environ.get("PORTAL_SOCKET", "/run/atakit-portal.sock")
STATUS_ROUTE = "/portal-external-api/status"
EVIDENCE_ROUTE = "/portal-external-api/session/evidence-bundle"
SIGN_ROUTE = "/sign-message"
PORTAL_HTTPS_HOST = os.environ.get("PORTAL_HTTPS_HOST", "host.containers.internal")
PORTAL_HTTPS_PORT = int(os.environ.get("PORTAL_HTTPS_PORT", "2024"))
VERIFIERD_CHAIN_URL = os.environ.get(
    "VERIFIERD_CHAIN_URL", "http://verifierd-chain:9100"
)
VERIFIERD_TRUST_PACK_URL = os.environ.get(
    "VERIFIERD_TRUST_PACK_URL", "http://verifierd-trust-pack:9100"
)
CHAIN_AUTHORITY_RPC_URL = os.environ.get("CHAIN_AUTHORITY_RPC_URL", "")
CHAIN_AUTHORITY_CHAIN_ID = os.environ.get("CHAIN_AUTHORITY_CHAIN_ID", "")
CHAIN_AUTHORITY_SESSION_REGISTRY = os.environ.get(
    "CHAIN_AUTHORITY_SESSION_REGISTRY", ""
)
TRUST_PACK_COLLATERAL_PUBLISHER_PUBKEY = os.environ.get(
    "TRUST_PACK_COLLATERAL_PUBLISHER_PUBKEY", ""
)
TRUST_PACK_WORKLOAD_PUBLISHER_PUBKEY = os.environ.get(
    "TRUST_PACK_WORKLOAD_PUBLISHER_PUBKEY", ""
)
TDX_COLLATERAL = {
    "source": "Automata on-chain PCCS",
    "chain": "hoodi",
    "rpc_url": "https://ethereum-hoodi-rpc.publicnode.com",
    "read_strategy": "direct-concurrent",
    "basis": (
        "atakit-verifierd default; the measured workload manifest does not set "
        "VERIFIERD_PCCS_URL"
    ),
}
EXPECTED_BASE_IMAGE_REF = os.environ.get("EXPECTED_BASE_IMAGE_REF", "")
EXPECTED_WORKLOAD_REF = os.environ.get("EXPECTED_WORKLOAD_REF", "")
ISOLATION_PEER_URL = os.environ.get("ISOLATION_PEER_URL", "")
SERVICE_ROLE = os.environ.get("SERVICE_ROLE", "primary")

SESSION_NOT_READY_BODY = {
    "code": "session_not_ready",
    "state": "initializing_session",
    "retryable": True,
}
STARTUP_LOCK = threading.Lock()
STARTUP_OBSERVATIONS = {
    "complete": False,
    "started_at": datetime.now(timezone.utc).isoformat(),
    "responses": {},
    "errors": [],
}
SNAPSHOT_LOCK = threading.Lock()
SNAPSHOT_CACHE = {"at": 0.0, "value": None}


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__("localhost", timeout=30)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def decode_response(response):
    raw = response.read()
    body = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(body) if body else None
    except json.JSONDecodeError:
        payload = {"raw": body}
    return {
        "status": response.status,
        "headers": {key.lower(): value for key, value in response.getheaders()},
        "body": payload,
    }


def portal_request(path, method="GET", payload=None):
    connection = UnixHTTPConnection(PORTAL_SOCKET)
    try:
        body = None
        headers = {"accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        return decode_response(connection.getresponse())
    finally:
        connection.close()


def portal_https_request(path):
    connection = http.client.HTTPSConnection(
        PORTAL_HTTPS_HOST,
        PORTAL_HTTPS_PORT,
        timeout=30,
        context=ssl._create_unverified_context(),
    )
    try:
        connection.request("GET", path, headers={"accept": "application/json"})
        return decode_response(connection.getresponse())
    finally:
        connection.close()


def json_http_request(base_url, path, method="GET", payload=None, timeout=30):
    body = None
    headers = {"accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            decoded = json.loads(text) if text else None
        except json.JSONDecodeError:
            decoded = {"raw": text}
        return {
            "status": response.status,
            "headers": {key.lower(): value for key, value in response.headers.items()},
            "body": decoded,
        }


def safe_request(call):
    try:
        return call()
    except Exception as error:
        return {
            "status": None,
            "headers": {},
            "body": None,
            "error": f"{error.__class__.__name__}: {error}",
        }


def exact_session_not_ready(response):
    return (
        response.get("status") == 503
        and response.get("headers", {}).get("retry-after") == "1"
        and response.get("body") == SESSION_NOT_READY_BODY
    )


def json_shape(value):
    if isinstance(value, dict):
        return {key: json_shape(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [json_shape(value[0])] if value else []
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def socket_identity():
    socket_stat = os.stat(PORTAL_SOCKET)
    return {
        "role": SERVICE_ROLE,
        "path": PORTAL_SOCKET,
        "device": socket_stat.st_dev,
        "inode": socket_stat.st_ino,
        "is_socket": stat.S_ISSOCK(socket_stat.st_mode),
    }


def new_challenge():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def get_path(value, *parts):
    current = value
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def signature_is_valid_shape(signature):
    if not isinstance(signature, str) or not signature.startswith("0x"):
        return False
    if len(signature) != 132:
        return False
    try:
        bytes.fromhex(signature[2:])
        return True
    except ValueError:
        return False


def evidence_summary(response):
    body = response.get("body") if isinstance(response, dict) else None
    bundle = get_path(body, "evidence_bundle") or {}
    binding = get_path(body, "request_binding") or {}
    signature = binding.get("signature")
    return {
        "format": bundle.get("format"),
        "binding_mode": get_path(bundle, "binding", "mode"),
        "chain_id": get_path(bundle, "binding", "chain_id"),
        "registry": get_path(bundle, "binding", "registry"),
        "cloud": get_path(bundle, "platform", "cloud"),
        "tee": get_path(bundle, "platform", "tee"),
        "tee_evidence_kind": get_path(bundle, "tee_evidence", "kind"),
        "ak_evidence_kind": get_path(bundle, "ak_evidence", "kind"),
        "session_key_fingerprint": get_path(bundle, "session_key", "fingerprint"),
        "challenge": binding.get("challenge"),
        "signature": signature,
        "signature_bytes": (len(signature) - 2) // 2
        if isinstance(signature, str) and signature.startswith("0x")
        else None,
    }


def source_inputs(verification):
    inputs = get_path(verification.get("body") or {}, "sources", "inputs")
    return inputs if isinstance(inputs, dict) else {}


def source_kinds(verification):
    return sorted(
        {
            source.get("kind")
            for source in source_inputs(verification).values()
            if isinstance(source, dict) and source.get("kind")
        }
    )


def loaded_packs(config):
    packs = get_path(config.get("body") or {}, "packs")
    return packs if isinstance(packs, list) else []


def collect_verifier(url, evidence, challenge, ready):
    health = safe_request(lambda: json_http_request(url, "/v1/health"))
    config = safe_request(lambda: json_http_request(url, "/v1/config"))
    if ready:
        verification = safe_request(
            lambda: json_http_request(
                url,
                "/v1/verify-session-bundle",
                "POST",
                {
                    "session_evidence": evidence,
                    "expected_challenge": challenge,
                    "base_image": EXPECTED_BASE_IMAGE_REF,
                    "workload": EXPECTED_WORKLOAD_REF,
                },
                timeout=360,
            )
        )
    else:
        verification = {
            "status": None,
            "body": None,
            "error": "evidence bundle is not ready",
        }
    return {
        "endpoint": url,
        "health": health,
        "config": config,
        "verification": verification,
        "trust_input_sources": source_inputs(verification),
        "trust_input_source_kinds": source_kinds(verification),
        "tdx_dcap_collateral": TDX_COLLATERAL,
    }


def build_trust_assumptions(chain_verifier, pack_verifier, binding_mode):
    packs = loaded_packs(pack_verifier["config"])
    digest_pins_configured = False
    return [
        {
            "name": "Chain authority",
            "authority": (
                "The measured RPC URL, chain ID, and SessionRegistry select the "
                "Solidity registry graph that supplies measurement, workload, and "
                "GCP AK-root policy."
            ),
            "coordinates": {
                "rpc_url": CHAIN_AUTHORITY_RPC_URL,
                "chain_id": CHAIN_AUTHORITY_CHAIN_ID,
                "session_registry": CHAIN_AUTHORITY_SESSION_REGISTRY,
            },
            "observed_trust_inputs": chain_verifier["trust_input_sources"],
            "remaining_assumptions": [
                "The configured chain endpoint returns the canonical state for chain ID 31337.",
                "Authorized registry updates can change the policy accepted by later requests.",
                "Session registration is separate from using the chain as the policy authority.",
            ],
        },
        {
            "name": "Trust-pack authority",
            "authority": (
                "The measured publisher public keys authorize the signed "
                "collateral-trust and workload-trust packs."
            ),
            "publisher_keys": {
                "collateral": TRUST_PACK_COLLATERAL_PUBLISHER_PUBKEY,
                "workload": TRUST_PACK_WORKLOAD_PUBLISHER_PUBKEY,
            },
            "loaded_packs": packs,
            "observed_trust_inputs": pack_verifier["trust_input_sources"],
            "digest_pins_configured": digest_pins_configured,
            "remaining_assumptions": [
                "The publisher keys belong to the intended policy authorities.",
                (
                    "No pack digest pins are configured. A correctly signed, unexpired "
                    "replacement pack from either publisher is accepted."
                ),
                "The unmeasured pack files are available at daemon startup.",
                "Missing pack inputs fail closed and are not filled from the registry.",
            ],
        },
        {
            "name": "Intel TDX vendor collateral",
            "authority": (
                "Both daemons use Automata's Hoodi on-chain PCCS as the transport "
                "for Intel-signed TDX DCAP collateral. Intel signatures are checked locally."
            ),
            "configuration": TDX_COLLATERAL,
            "remaining_assumptions": [
                "The Hoodi RPC is available and returns the requested PCCS contract data.",
                "Freshness and revocation are limited to the vendor collateral and verification rules.",
                "Automata supplies collateral bytes; it is not the measurement-policy authority.",
            ],
        },
        {
            "name": "Session binding",
            "authority": (
                f"The evidence reports a {binding_mode or 'missing'} binding. This E2E "
                "uses local binding so chain and trust-pack policy authorities can verify "
                "the same evidence without sharing chain-binding coordinates."
            ),
            "remaining_assumptions": [
                "The caller must require chain binding separately when it relies on chain registration.",
                "A successful verifier response does not itself prove that a session was registered on chain.",
                "The request challenge proves freshness only for this verification request.",
            ],
        },
    ]


def capture_startup_observations():
    challenge = new_challenge()
    path = f"{EVIDENCE_ROUTE}?challenge={urllib.parse.quote(challenge)}"
    probes = {
        "socket_evidence": lambda: portal_request(path),
        "socket_sign_message": lambda: portal_request(
            SIGN_ROUTE, "POST", {"message": "0x01"}
        ),
        "https_session": lambda: portal_https_request("/session"),
    }
    observed = {}
    last = {}
    errors = []
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and len(observed) < len(probes):
        for name, probe in probes.items():
            if name in observed:
                continue
            response = safe_request(probe)
            last[name] = response
            if response.get("status") is not None:
                observed[name] = response
            elif response.get("error") and response["error"] not in errors:
                errors.append(response["error"])
        if len(observed) < len(probes):
            time.sleep(0.05)
    for name in probes:
        observed.setdefault(name, last.get(name, {"status": None, "body": None}))
    with STARTUP_LOCK:
        STARTUP_OBSERVATIONS.update(
            {
                "complete": True,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "challenge": challenge,
                "responses": observed,
                "errors": errors[-5:],
            }
        )


def startup_snapshot():
    with STARTUP_LOCK:
        return json.loads(json.dumps(STARTUP_OBSERVATIONS))


def workload_measurement_projection(bundle):
    event_entry = next(
        (
            entry
            for entry in bundle.get("event_log_hashes") or []
            if entry.get("pcr_index") == 23
        ),
        None,
    )
    pcr_entry = next(
        (
            entry
            for entry in bundle.get("pcr_values") or []
            if entry.get("index") == 23
        ),
        None,
    )
    digests = (event_entry or {}).get("sha256") or []
    projected = bytes(32)
    try:
        for digest in digests:
            if not isinstance(digest, str) or not digest.startswith("0x"):
                raise ValueError("invalid event digest")
            digest_bytes = bytes.fromhex(digest[2:])
            if len(digest_bytes) != 32:
                raise ValueError("invalid event digest length")
            projected = hashlib.sha256(projected + digest_bytes).digest()
    except ValueError:
        projected_value = None
    else:
        projected_value = "0x" + projected.hex() if digests else None
    quoted_value = (pcr_entry or {}).get("sha256")
    return {
        "event_log": event_entry,
        "quoted_pcr23": quoted_value,
        "projected_pcr23": projected_value,
        "valid": bool(digests) and projected_value == quoted_value,
    }


def _collect_snapshot():
    challenge = new_challenge()
    quoted_challenge = urllib.parse.quote(challenge)
    evidence_path = f"{EVIDENCE_ROUTE}?challenge={quoted_challenge}"
    https_evidence_path = f"/session/evidence-bundle?challenge={quoted_challenge}"
    generated_at = datetime.now(timezone.utc).isoformat()

    status_response = safe_request(lambda: portal_request(STATUS_ROUTE))
    https_status_response = safe_request(lambda: portal_https_request("/status"))
    https_session_response = safe_request(lambda: portal_https_request("/session"))
    invalid_response = safe_request(lambda: portal_request(f"{EVIDENCE_ROUTE}?challenge=AA"))
    https_invalid_response = safe_request(
        lambda: portal_https_request("/session/evidence-bundle?challenge=AA")
    )
    evidence_response = safe_request(lambda: portal_request(evidence_path))
    https_evidence_response = safe_request(
        lambda: portal_https_request(https_evidence_path)
    )

    status = status_response.get("body") or {}
    evidence = evidence_response.get("body") or {}
    bundle = evidence.get("evidence_bundle") or {}
    binding = evidence.get("request_binding") or {}
    session_id = bundle.get("session_id")
    workload_measurement = workload_measurement_projection(bundle)

    omitted_sign = safe_request(
        lambda: portal_request(SIGN_ROUTE, "POST", {"message": "0x01"})
    )
    matching_sign = safe_request(
        lambda: portal_request(
            SIGN_ROUTE,
            "POST",
            {"message": "0x02", "expected_session_id": session_id},
        )
    )
    mismatch_id = "0x" + ("ff" if session_id != "0x" + "ff" * 32 else "00") * 32
    mismatch_sign = safe_request(
        lambda: portal_request(
            SIGN_ROUTE,
            "POST",
            {"message": "0x03", "expected_session_id": mismatch_id},
        )
    )
    invalid_sign = safe_request(
        lambda: portal_request(
            SIGN_ROUTE,
            "POST",
            {"message": "0x04", "expected_session_id": "0x11"},
        )
    )
    null_sign = safe_request(
        lambda: portal_request(
            SIGN_ROUTE,
            "POST",
            {"message": "0x05", "expected_session_id": None},
        )
    )

    evidence_ready = evidence_response.get("status") == 200
    with ThreadPoolExecutor(max_workers=2) as executor:
        chain_future = executor.submit(
            collect_verifier,
            VERIFIERD_CHAIN_URL,
            evidence,
            challenge,
            evidence_ready,
        )
        pack_future = executor.submit(
            collect_verifier,
            VERIFIERD_TRUST_PACK_URL,
            evidence,
            challenge,
            evidence_ready,
        )
        chain_verifier = chain_future.result()
        pack_verifier = pack_future.result()

    chain_verification = chain_verifier["verification"]
    pack_verification = pack_verifier["verification"]
    chain_config = chain_verifier["config"]
    pack_config = pack_verifier["config"]
    pack_summaries = loaded_packs(pack_config)
    pack_kinds = sorted(
        pack.get("kind")
        for pack in pack_summaries
        if isinstance(pack, dict) and isinstance(pack.get("kind"), str)
    )
    now_unix = int(time.time())
    packs_are_current_and_identified = bool(pack_summaries) and all(
        isinstance(pack, dict)
        and isinstance(pack.get("digest"), str)
        and pack["digest"].startswith("0x")
        and len(pack["digest"]) == 66
        and isinstance(pack.get("not_before"), int)
        and isinstance(pack.get("not_after"), int)
        and pack["not_before"] <= now_unix <= pack["not_after"]
        for pack in pack_summaries
    )

    own_identity = safe_request(socket_identity)
    peer_identity_response = (
        safe_request(lambda: json_http_request(ISOLATION_PEER_URL, "/api/identity"))
        if ISOLATION_PEER_URL
        else {"status": None, "body": None, "error": "isolation peer is not configured"}
    )
    peer_identity = peer_identity_response.get("body") or {}
    startup = startup_snapshot()
    startup_responses = startup.get("responses") or {}

    socket_and_https_status_shapes_match = (
        status_response.get("status") == 200
        and https_status_response.get("status") == 200
        and json_shape(status_response.get("body"))
        == json_shape(https_status_response.get("body"))
    )
    socket_and_https_evidence_shapes_match = (
        evidence_response.get("status") == 200
        and https_evidence_response.get("status") == 200
        and json_shape(evidence_response.get("body"))
        == json_shape(https_evidence_response.get("body"))
        and bundle == (https_evidence_response.get("body") or {}).get("evidence_bundle")
    )
    committed_ids = {
        "evidence": session_id,
        "https_session": get_path(https_session_response.get("body") or {}, "session_id"),
        "sign_omitted": get_path(omitted_sign.get("body") or {}, "session_id"),
        "sign_pinned": get_path(matching_sign.get("body") or {}, "session_id"),
        "verifierd_chain": get_path(chain_verification.get("body") or {}, "session_id"),
        "verifierd_trust_pack": get_path(
            pack_verification.get("body") or {}, "session_id"
        ),
    }

    checks = [
        {
            "group": "1 · Unix socket routes",
            "name": "status route matches the HTTPS response schema",
            "ok": socket_and_https_status_shapes_match,
            "actual": {
                "socket": status_response.get("status"),
                "https": https_status_response.get("status"),
            },
        },
        {
            "group": "1 · Unix socket routes",
            "name": "evidence route matches the HTTPS schema and bundle",
            "ok": socket_and_https_evidence_shapes_match,
            "actual": {
                "socket": evidence_response.get("status"),
                "https": https_evidence_response.get("status"),
            },
        },
        {
            "group": "1 · Unix socket routes",
            "name": "invalid challenges return HTTP 400 on both routes",
            "ok": invalid_response.get("status") == 400
            and https_invalid_response.get("status") == 400,
            "actual": {
                "socket": invalid_response.get("status"),
                "https": https_invalid_response.get("status"),
            },
        },
        {
            "group": "1 · Unix socket routes",
            "name": "services receive different portal socket files",
            "ok": (
                own_identity.get("is_socket") is True
                and peer_identity.get("is_socket") is True
                and (own_identity.get("device"), own_identity.get("inode"))
                != (peer_identity.get("device"), peer_identity.get("inode"))
            ),
            "actual": {"primary": own_identity, "peer": peer_identity},
        },
        {
            "group": "2 · Session readiness",
            "name": "pre-session sign-message returns the exact shared 503 response",
            "ok": exact_session_not_ready(startup_responses.get("socket_sign_message") or {}),
            "actual": startup_responses.get("socket_sign_message"),
        },
        {
            "group": "2 · Session readiness",
            "name": "pre-session socket evidence returns the exact shared 503 response",
            "ok": exact_session_not_ready(startup_responses.get("socket_evidence") or {}),
            "actual": startup_responses.get("socket_evidence"),
        },
        {
            "group": "2 · Session readiness",
            "name": "pre-session HTTPS session returns the exact shared 503 response",
            "ok": exact_session_not_ready(startup_responses.get("https_session") or {}),
            "actual": startup_responses.get("https_session"),
        },
        {
            "group": "3 · expected_session_id",
            "name": "omitted and matching session IDs sign successfully",
            "ok": omitted_sign.get("status") == 200 and matching_sign.get("status") == 200,
            "actual": {
                "omitted": omitted_sign.get("status"),
                "matching": matching_sign.get("status"),
            },
        },
        {
            "group": "3 · expected_session_id",
            "name": "mismatched session ID fails closed with HTTP 409",
            "ok": mismatch_sign.get("status") == 409
            and mismatch_sign.get("body") == {"error": "session_id_mismatch"},
            "actual": mismatch_sign,
        },
        {
            "group": "3 · expected_session_id",
            "name": "invalid and null session IDs return HTTP 400",
            "ok": invalid_sign.get("status") == 400 and null_sign.get("status") == 400,
            "actual": {
                "invalid": invalid_sign.get("status"),
                "null": null_sign.get("status"),
            },
        },
        {
            "group": "4 · Committed session context",
            "name": "evidence, HTTPS session, signing, and verifierd agree on one session",
            "ok": bool(session_id)
            and all(value == session_id for value in committed_ids.values()),
            "actual": committed_ids,
        },
        {
            "group": "4 · Committed session context",
            "name": "post-launch Quote contains this workload's PCR23 measurement",
            "ok": workload_measurement["valid"],
            "actual": workload_measurement,
        },
        {
            "group": "5 · verifierd",
            "name": "chain verifier starts with the measured Solidity-registry authority",
            "ok": chain_verifier["health"].get("status") == 200
            and get_path(chain_verifier["health"].get("body") or {}, "status") == "ok"
            and chain_config.get("status") == 200
            and get_path(chain_config.get("body") or {}, "trust_mode") == "chain",
            "actual": chain_config,
        },
        {
            "group": "5 · verifierd",
            "name": "trust-pack verifier loads one current pack for each required role",
            "ok": pack_verifier["health"].get("status") == 200
            and get_path(pack_verifier["health"].get("body") or {}, "status") == "ok"
            and pack_config.get("status") == 200
            and get_path(pack_config.get("body") or {}, "trust_mode") == "trust-pack"
            and pack_kinds == ["collateral-trust", "workload-trust"]
            and packs_are_current_and_identified,
            "actual": {
                "http": pack_config.get("status"),
                "kinds": pack_kinds,
                "packs": pack_summaries,
            },
        },
        {
            "group": "5 · verifierd",
            "name": "both authorities cryptographically verify the same socket evidence",
            "ok": chain_verification.get("status") == 200
            and pack_verification.get("status") == 200
            and get_path(chain_verification.get("body") or {}, "verified") is True
            and get_path(pack_verification.get("body") or {}, "verified") is True
            and get_path(chain_verification.get("body") or {}, "session_id") == session_id
            and get_path(pack_verification.get("body") or {}, "session_id") == session_id,
            "actual": {
                "chain": {
                    "http": chain_verification.get("status"),
                    "session": get_path(
                        chain_verification.get("body") or {}, "session_id"
                    ),
                    "error": chain_verification.get("error"),
                },
                "trust_pack": {
                    "http": pack_verification.get("status"),
                    "session": get_path(
                        pack_verification.get("body") or {}, "session_id"
                    ),
                    "error": pack_verification.get("error"),
                },
            },
        },
        {
            "group": "5 · verifierd",
            "name": "trust inputs come only from the selected authority",
            "ok": bool(chain_verifier["trust_input_sources"])
            and chain_verifier["trust_input_source_kinds"] == ["chain"]
            and bool(pack_verifier["trust_input_sources"])
            and pack_verifier["trust_input_source_kinds"] == ["pack"],
            "actual": {
                "chain": chain_verifier["trust_input_sources"],
                "trust_pack": pack_verifier["trust_input_sources"],
            },
        },
        {
            "group": "5 · verifierd",
            "name": "both verifiers validate GCP TDX evidence with on-chain DCAP collateral",
            "ok": get_path(bundle, "platform", "cloud") == "gcp"
            and get_path(bundle, "platform", "tee") == "tdx"
            and chain_verification.get("status") == 200
            and pack_verification.get("status") == 200,
            "actual": TDX_COLLATERAL,
        },
        {
            "group": "Platform",
            "name": "portal reached Running on GCP Intel TDX hardware",
            "ok": status.get("state") == "Running"
            and get_path(status, "platform", "cloud_type") == "gcp"
            and get_path(status, "platform", "tee_type") == "tdx"
            and get_path(status, "platform", "attestation_mode") == "hardware",
            "actual": {
                "state": status.get("state"),
                "cloud": get_path(status, "platform", "cloud_type"),
                "tee": get_path(status, "platform", "tee_type"),
                "attestation": get_path(status, "platform", "attestation_mode"),
            },
        },
        {
            "group": "Platform",
            "name": "local session binding keeps policy authority separate from registration",
            "ok": get_path(bundle, "binding", "mode") == "local"
            and get_path(status, "chain", "status") == "off",
            "actual": {
                "binding": get_path(bundle, "binding", "mode"),
                "registration": get_path(status, "chain", "status"),
            },
        },
        {
            "group": "Evidence",
            "name": "format-2 evidence has the requested 65-byte binding signature",
            "ok": bundle.get("format") == 2
            and binding.get("challenge") == challenge
            and signature_is_valid_shape(binding.get("signature")),
            "actual": evidence_summary(evidence_response),
        },
    ]
    result = {
        "ok": all(check["ok"] for check in checks),
        "generated_at": generated_at,
        "role": SERVICE_ROLE,
        "socket": PORTAL_SOCKET,
        "routes": {
            "status": STATUS_ROUTE,
            "evidence": EVIDENCE_ROUTE,
            "sign_message": SIGN_ROUTE,
            "https": f"https://{PORTAL_HTTPS_HOST}:{PORTAL_HTTPS_PORT}",
            "verifierd_chain": VERIFIERD_CHAIN_URL,
            "verifierd_trust_pack": VERIFIERD_TRUST_PACK_URL,
        },
        "checks": checks,
        "startup": startup,
        "status": status,
        "status_http": status_response.get("status"),
        "https_status_http": https_status_response.get("status"),
        "evidence": evidence_summary(evidence_response),
        "signing": {
            "omitted": omitted_sign.get("status"),
            "matching": matching_sign.get("status"),
            "mismatch": mismatch_sign,
            "invalid": invalid_sign.get("status"),
            "null": null_sign.get("status"),
        },
        "verifierd": {
            "chain": chain_verifier,
            "trust_pack": pack_verifier,
            "comparison": {
                "same_session": bool(session_id)
                and get_path(chain_verification.get("body") or {}, "session_id")
                == session_id
                and get_path(pack_verification.get("body") or {}, "session_id")
                == session_id,
                "chain_input_source_kinds": chain_verifier[
                    "trust_input_source_kinds"
                ],
                "trust_pack_input_source_kinds": pack_verifier[
                    "trust_input_source_kinds"
                ],
                "shared_tdx_dcap_collateral": TDX_COLLATERAL,
            },
            "trust_assumptions": build_trust_assumptions(
                chain_verifier,
                pack_verifier,
                get_path(bundle, "binding", "mode"),
            ),
        },
        "socket_isolation": {
            "primary": own_identity,
            "peer": peer_identity_response,
        },
    }
    return result


def collect_snapshot():
    with SNAPSHOT_LOCK:
        cached = SNAPSHOT_CACHE.get("value")
        age = time.monotonic() - SNAPSHOT_CACHE.get("at", 0.0)
        ttl = 60 if cached and cached.get("ok") else 5
        if cached is not None and age < ttl:
            return cached
        value = _collect_snapshot()
        SNAPSHOT_CACHE.update({"at": time.monotonic(), "value": value})
        return value


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Portal socket and verifierd proof</title>
  <style>
    :root { color-scheme: dark; --ink:#f7f4ea; --muted:#9ba7a5; --line:#273330; --lime:#a7f95b; --red:#ff806d; --panel:#111916; --blue:#7ed7ff; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background:radial-gradient(circle at 85% 5%,#17352c 0,transparent 32rem),#08100e; color:var(--ink); font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
    main { width:min(1180px,calc(100% - 32px)); margin:auto; padding:52px 0 72px; }
    header { display:grid; grid-template-columns:1fr auto; align-items:end; gap:24px; border-bottom:1px solid var(--line); padding-bottom:28px; }
    .eyebrow { color:var(--lime); letter-spacing:.16em; text-transform:uppercase; font-size:12px; }
    h1 { margin:.25rem 0 0; max-width:820px; font:600 clamp(32px,6vw,68px)/.98 system-ui,sans-serif; letter-spacing:-.055em; }
    .stamp { text-align:right; color:var(--muted); }
    .verdict { display:flex; align-items:center; gap:12px; margin:28px 0; padding:18px 22px; background:var(--panel); border:1px solid var(--line); }
    .dot { width:13px; height:13px; border-radius:50%; background:#65706d; box-shadow:0 0 0 5px #65706d22; }
    .pass .dot { background:var(--lime); box-shadow:0 0 18px var(--lime); }
    .fail .dot { background:var(--red); box-shadow:0 0 18px var(--red); }
    .verdict strong { font:650 22px system-ui,sans-serif; }
    .verdict span { color:var(--muted); margin-left:auto; }
    .grid { display:grid; grid-template-columns:repeat(12,1fr); gap:14px; }
    section { grid-column:span 4; padding:20px; background:var(--panel); border:1px solid var(--line); min-width:0; }
    section.half { grid-column:span 6; }
    section.wide { grid-column:span 8; }
    section.full { grid-column:1/-1; }
    h2 { margin:0 0 16px; color:var(--muted); font-size:12px; font-weight:500; letter-spacing:.14em; text-transform:uppercase; }
    dl { margin:0; display:grid; gap:12px; }
    dt { color:var(--muted); font-size:11px; text-transform:uppercase; }
    dd { margin:2px 0 0; overflow-wrap:anywhere; }
    code { color:var(--blue); }
    .checks { list-style:none; margin:0; padding:0; display:grid; grid-template-columns:1fr 1fr; gap:0 24px; }
    .checks li { display:grid; grid-template-columns:20px 1fr auto; gap:10px; align-items:start; padding:11px 0; border-bottom:1px solid var(--line); }
    .checks b { color:var(--lime); }
    .checks .bad { color:var(--red); }
    .actual { color:var(--muted); font-size:12px; max-width:270px; text-align:right; overflow-wrap:anywhere; }
    .pack-grid,.assumption-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
    .pack,.assumption { padding:16px; border:1px solid var(--line); background:#0b1311; min-width:0; }
    .pack h3,.assumption h3 { margin:0 0 8px; color:var(--lime); font:650 17px system-ui,sans-serif; }
    .assumption p { margin:0 0 12px; color:#d5dfdb; }
    .assumption h4 { margin:16px 0 6px; color:var(--muted); font-size:11px; letter-spacing:.1em; text-transform:uppercase; }
    .assumption ul { margin:0; padding-left:20px; color:var(--muted); }
    .source { margin-top:14px; color:var(--muted); font-size:12px; }
    button { border:1px solid var(--lime); color:#07100c; background:var(--lime); padding:10px 15px; font:600 13px/1 ui-monospace,monospace; cursor:pointer; }
    button:disabled { opacity:.5; cursor:wait; }
    pre { margin:0; max-height:360px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; color:#cdd8d4; font-size:12px; }
    @media (max-width:850px) { header { grid-template-columns:1fr; } .stamp { text-align:left; } section,section.half,section.wide { grid-column:1/-1; } .checks,.pack-grid,.assumption-grid { grid-template-columns:1fr; } }
  </style>
</head>
<body><main>
  <header><div><div class="eyebrow">GCP · Intel TDX · Chain + trust pack</div><h1>Two trust authorities. One verified session.</h1></div><div class="stamp"><button id="refresh">Run live test</button><div id="time">waiting</div></div></header>
  <div id="verdict" class="verdict"><i class="dot"></i><strong>Collecting evidence…</strong><span>/run/atakit-portal.sock</span></div>
  <div class="grid">
    <section><h2>Platform</h2><dl id="platform"></dl></section>
    <section><h2>Portal session</h2><dl id="chain"></dl></section>
    <section><h2>Request binding</h2><dl id="binding"></dl></section>
    <section class="half"><h2>Chain-authority verifier</h2><dl id="verifier-chain"></dl><div class="source" id="chain-sources"></div></section>
    <section class="half"><h2>Trust-pack-authority verifier</h2><dl id="verifier-pack"></dl><div class="source" id="pack-sources"></div></section>
    <section class="full"><h2>Loaded trust packs</h2><div id="packs" class="pack-grid"></div></section>
    <section class="full"><h2>Trust assumptions and proof limits</h2><div id="assumptions" class="assumption-grid"></div></section>
    <section class="full"><h2>Items 1–5 and dual-verifier live checks</h2><ul id="checks" class="checks"></ul></section>
    <section class="full"><h2>Test paths</h2><dl id="routes"></dl></section>
    <section class="full"><h2>Complete machine-readable result</h2><pre id="raw"></pre></section>
  </div>
</main><script>
const el=id=>document.getElementById(id);
const esc=value=>String(value??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short=value=>{const s=String(value??'—');return s.length>30?s.slice(0,14)+'…'+s.slice(-12):s};
const rows=items=>items.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
const sourceText=value=>Object.keys(value||{}).length?JSON.stringify(value):'No trust-input provenance reported';
const verifierRows=verifier=>{const config=verifier?.config?.body||{}, verification=verifier?.verification||{}, body=verification.body||{};return rows([['Endpoint',verifier?.endpoint],['Trust mode',config.trust_mode],['HTTP',verification.status],['Verified',body.verified],['Session',short(body.session_id)],['Binding',body.binding_mode],['Platforms',(config.supported_platforms||[]).join(', ')],['Allowed ports',(config.portal_allowed_ports||[]).join(', ')]]);};
const packCards=packs=>(packs||[]).map(pack=>`<article class="pack"><h3>${esc(pack.kind)}</h3>${rows([['Issuer',pack.issuer],['Revision',pack.revision],['Digest',pack.digest],['Not before',new Date((pack.not_before||0)*1000).toISOString()],['Not after',new Date((pack.not_after||0)*1000).toISOString()]])}</article>`).join('')||'<article class="pack">No packs reported.</article>';
const assumptionCards=items=>(items||[]).map(item=>{const details=item.coordinates||item.publisher_keys||item.configuration||{};return `<article class="assumption"><h3>${esc(item.name)}</h3><p>${esc(item.authority)}</p>${rows(Object.entries(details))}<h4>Still assumed or outside this proof</h4><ul>${(item.remaining_assumptions||[]).map(value=>`<li>${esc(value)}</li>`).join('')}</ul></article>`}).join('');
async function run(){
  el('refresh').disabled=true;
  try {
    const response=await fetch('/api/snapshot',{cache:'no-store'});
    const data=await response.json();
    el('time').textContent=data.generated_at||'request failed';
    const verdict=el('verdict'); verdict.className='verdict '+(data.ok?'pass':'fail');
    verdict.querySelector('strong').textContent=data.ok?'All portal and verifier checks passed':'Waiting for every check to pass';
    const status=data.status||{}, platform=status.platform||{}, chain=status.chain||{}, evidence=data.evidence||{};
    el('platform').innerHTML=rows([['Cloud',platform.cloud_type],['TEE',platform.tee_type],['Attestation',platform.attestation_mode],['Machine',platform.machine_type],['Portal state',status.state]]);
    el('chain').innerHTML=rows([['Backend',chain.tee_backend],['Status',chain.status],['Chain ID',chain.chain_id],['Session',short(chain.session_id)],['Transaction',short(chain.tx_hash)],['Block',chain.block_number]]);
    el('binding').innerHTML=rows([['Evidence format',evidence.format],['Mode',evidence.binding_mode],['Chain ID',evidence.chain_id],['Registry',short(evidence.registry)],['TEE evidence',evidence.tee_evidence_kind],['Challenge',short(evidence.challenge)],['Signature',short(evidence.signature)],['Signature bytes',evidence.signature_bytes]]);
    el('routes').innerHTML=rows([['Socket','Unix socket (no TLS)'],['Status',data.routes?.status],['Evidence',data.routes?.evidence],['Sign',data.routes?.sign_message],['HTTPS parity',data.routes?.https],['Chain verifier',data.routes?.verifierd_chain],['Trust-pack verifier',data.routes?.verifierd_trust_pack]]);
    const chainVerifier=data.verifierd?.chain||{}, packVerifier=data.verifierd?.trust_pack||{};
    el('verifier-chain').innerHTML=verifierRows(chainVerifier);
    el('verifier-pack').innerHTML=verifierRows(packVerifier);
    el('chain-sources').textContent='Observed trust inputs: '+sourceText(chainVerifier.trust_input_sources);
    el('pack-sources').textContent='Observed trust inputs: '+sourceText(packVerifier.trust_input_sources);
    el('packs').innerHTML=packCards(packVerifier?.config?.body?.packs);
    el('assumptions').innerHTML=assumptionCards(data.verifierd?.trust_assumptions);
    el('checks').innerHTML=(data.checks||[]).map(c=>`<li><b class="${c.ok?'':'bad'}">${c.ok?'✓':'×'}</b><span><small>${esc(c.group||'')}</small><br>${esc(c.name)}</span><span class="actual">${esc(typeof c.actual==='object'?JSON.stringify(c.actual):c.actual)}</span></li>`).join('');
    el('raw').textContent=JSON.stringify(data,null,2);
  } catch(error) { el('verdict').className='verdict fail'; el('verdict').querySelector('strong').textContent=error.message; }
  finally { el('refresh').disabled=false; }
}
el('refresh').addEventListener('click',run); run(); setInterval(run,15000);
</script></body></html>"""


def json_response(handler, status, payload):
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("cache-control", "no-store")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/healthz":
            json_response(self, 200, {"ok": True})
            return
        if path == "/api/identity":
            identity = safe_request(socket_identity)
            status = safe_request(lambda: portal_request(STATUS_ROUTE))
            identity["status_http"] = status.get("status")
            identity["session_id"] = get_path(
                status.get("body") or {}, "chain", "session_id"
            )
            json_response(self, 200 if identity.get("is_socket") else 503, identity)
            return
        if path == "/api/snapshot":
            snapshot = collect_snapshot()
            json_response(self, 200 if snapshot["ok"] else 503, snapshot)
            return
        json_response(self, 404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return


def main():
    threading.Thread(target=capture_startup_observations, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
