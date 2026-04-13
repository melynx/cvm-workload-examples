"""Peer attestation demo: dashboard HTTP server plus persistent peer socket.

Each CVM instance runs two listeners:
- a dashboard HTTP server for the web UI and local control API
- a peer TCP server for attestation and encrypted messaging

Two instances connect over the peer TCP socket, verify each other's CVM
session via the agent's session key, perform ECDH to derive a shared AES key,
and then keep using that same socket for bidirectional encrypted messages.
"""

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import protocol

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "3000"))
PEER_PORT = int(os.environ.get("PEER_PORT", "4000"))
UNMEASURED_CONFIG = "/app/unmeasured-data/peer-config.json"
MAX_MESSAGES = 200
MAX_EVENTS = 100
MESSAGE_INTERVAL = 10
MAX_CONSECUTIVE_FAILURES = 3
CONNECT_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_send_lock = threading.Lock()


class NodeState:
    def __init__(self):
        self.node_name = ""
        self.dashboard_port = DASHBOARD_PORT
        self.peer_port = PEER_PORT
        self.peer_addr = None
        self.connection_state = protocol.DISCONNECTED
        self.local_session_info = None
        self.peer_session_info = None
        self.verification_result = None
        self.local_eph_pub_hex = None
        self.peer_eph_pub_hex = None
        self.shared_secret_fingerprint = None
        self.aes_key = None
        self.key_exchange_time = None
        self.messages = []
        self.events = []
        self.message_counter = 0
        self.last_error = None
        self.peer_socket_addr = None
        # Internal: not serialized
        self._connect_token = 0
        self._peer_token = 0
        self._pending_socket = None
        self._peer_socket = None
        self._peer_reader = None
        self._connect_thread = None
        self._message_thread = None
        self._reader_thread = None


STATE = NodeState()


def _add_event(kind, detail):
    with _lock:
        STATE.events.append(
            {
                "kind": kind,
                "detail": detail,
                "time": time.strftime("%H:%M:%S"),
            }
        )
        if len(STATE.events) > MAX_EVENTS:
            STATE.events = STATE.events[-MAX_EVENTS:]


def _add_message(direction, text, nonce_hex, seq):
    with _lock:
        STATE.messages.append(
            {
                "direction": direction,
                "text": text,
                "nonce": nonce_hex,
                "seq": seq,
                "time": time.strftime("%H:%M:%S"),
            }
        )
        if len(STATE.messages) > MAX_MESSAGES:
            STATE.messages = STATE.messages[-MAX_MESSAGES:]


def _close_quietly(obj):
    if obj is None:
        return
    try:
        obj.close()
    except Exception:
        pass


def _reset_connection():
    """Reset all connection-related state. Caller must hold _lock."""
    pending = STATE._pending_socket
    peer_reader = STATE._peer_reader
    peer_socket = STATE._peer_socket

    STATE._connect_token += 1
    STATE._peer_token += 1
    STATE._pending_socket = None
    STATE._peer_reader = None
    STATE._peer_socket = None
    STATE.connection_state = protocol.DISCONNECTED
    STATE.peer_session_info = None
    STATE.verification_result = None
    STATE.local_eph_pub_hex = None
    STATE.peer_eph_pub_hex = None
    STATE.shared_secret_fingerprint = None
    STATE.aes_key = None
    STATE.key_exchange_time = None
    STATE.last_error = None
    STATE.peer_socket_addr = None
    STATE.message_counter = 0

    _close_quietly(peer_reader)
    _close_quietly(peer_socket)
    _close_quietly(pending)


def _state_snapshot():
    """Thread-safe deep copy of state for JSON serialization."""
    with _lock:
        return {
            "node_name": STATE.node_name,
            "dashboard_port": STATE.dashboard_port,
            "peer_port": STATE.peer_port,
            "peer_addr": STATE.peer_addr,
            "connection_state": STATE.connection_state,
            "local_session_info": STATE.local_session_info,
            "peer_session_info": STATE.peer_session_info,
            "verification": STATE.verification_result,
            "local_eph_pub_hex": STATE.local_eph_pub_hex,
            "peer_eph_pub_hex": STATE.peer_eph_pub_hex,
            "shared_secret_fingerprint": STATE.shared_secret_fingerprint,
            "key_exchange_time": STATE.key_exchange_time,
            "messages": list(STATE.messages),
            "events": list(STATE.events),
            "last_error": STATE.last_error,
            "peer_socket_addr": STATE.peer_socket_addr,
        }


# ---------------------------------------------------------------------------
# Peer socket helpers
# ---------------------------------------------------------------------------


def _normalize_peer_addr(value):
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        return f"{value}:{PEER_PORT}"
    if ":" not in value:
        return f"{value}:{PEER_PORT}"
    return value


def _split_peer_addr(peer_addr):
    peer_addr = _normalize_peer_addr(peer_addr)
    if peer_addr is None:
        raise ValueError("peer_addr required")
    if peer_addr.startswith("["):
        end = peer_addr.find("]")
        if end == -1 or end + 2 > len(peer_addr):
            raise ValueError(f"invalid peer address: {peer_addr}")
        host = peer_addr[1:end]
        port = int(peer_addr[end + 2 :])
        return peer_addr, host, port
    host, sep, port_text = peer_addr.rpartition(":")
    if not sep or not port_text.isdigit():
        raise ValueError(f"invalid peer address: {peer_addr}")
    return peer_addr, host, int(port_text)


def _socket_peer_addr(sock):
    try:
        peer = sock.getpeername()
    except Exception:
        return None
    if isinstance(peer, tuple):
        host = peer[0]
        port = peer[1]
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{port}"
    return str(peer)


def _read_frame(reader):
    line = reader.readline()
    if not line:
        raise EOFError("peer connection closed")
    try:
        return json.loads(line.decode())
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid frame: {e}") from e


def _send_frame(sock, frame):
    payload = (json.dumps(frame, separators=(",", ":")) + "\n").encode()
    with _send_lock:
        sock.sendall(payload)


def _send_error_frame(sock, error, detail):
    try:
        _send_frame(sock, {"type": "error", "error": error, "detail": detail})
    except Exception:
        pass


def _open_peer_socket(peer_addr):
    normalized, host, port = _split_peer_addr(peer_addr)
    sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    sock.settimeout(CONNECT_TIMEOUT)
    return normalized, sock


def _prepare_local_handshake():
    _add_event("key", "generating ephemeral secp256k1 keypair")
    eph_private, eph_pub_bytes = protocol.generate_ephemeral_keypair()
    eph_pub_hex = "0x" + eph_pub_bytes.hex()

    _add_event("sign", "requesting CVM agent signature on ephemeral key")
    sign_resp = None
    for attempt in range(6):
        try:
            sign_resp = protocol.agent_sign_message(eph_pub_hex)
            break
        except RuntimeError as e:
            if "session not registered" in str(e) and attempt < 5:
                delay = 2 * (attempt + 1)
                _add_event(
                    "wait",
                    f"agent session not ready, retrying in {delay}s ({attempt + 1}/5)",
                )
                time.sleep(delay)
            else:
                raise

    local_info = protocol.parse_session_info(sign_resp)

    with _lock:
        STATE.local_session_info = local_info
        STATE.local_eph_pub_hex = eph_pub_hex
        if not STATE.node_name:
            STATE.node_name = local_info["session_id"][:10]

    return eph_private, eph_pub_hex, sign_resp, local_info


def _verify_peer_handshake(peer_data, local_info):
    peer_eph_hex = peer_data["ephemeral_public_key"]
    peer_sig = peer_data["signature"]
    peer_info = peer_data["session_info"]

    with _lock:
        STATE.peer_session_info = peer_info
        STATE.peer_eph_pub_hex = peer_eph_hex

    sig_ok = protocol.verify_signature(
        peer_info["session_key_public"], peer_eph_hex, peer_sig
    )
    if not sig_ok:
        raise ValueError("peer signature verification failed")

    verification = protocol.verify_peer_session(peer_info, local_info)
    with _lock:
        STATE.verification_result = verification

    if not verification["verified"]:
        failed = [c["name"] for c in verification["checks"] if not c["passed"]]
        raise ValueError(f"session verification failed: {', '.join(failed)}")

    return peer_eph_hex, peer_info, verification


def _derive_session_key(eph_private, local_info, peer_info, peer_eph_hex):
    peer_pub_bytes = bytes.fromhex(peer_eph_hex[2:])
    salt = protocol.make_hkdf_salt(local_info["session_id"], peer_info["session_id"])
    return protocol.compute_shared_secret(eph_private, peer_pub_bytes, salt)


def _activate_peer_connection(
    sock,
    reader,
    peer_addr,
    configured_peer_addr,
    local_info,
    peer_info,
    verification,
    local_eph_hex,
    peer_eph_hex,
    aes_key,
):
    with _lock:
        previous_reader = STATE._peer_reader
        previous_socket = STATE._peer_socket

        STATE._pending_socket = None
        STATE._peer_socket = sock
        STATE._peer_reader = reader
        STATE._peer_token += 1
        conn_token = STATE._peer_token

        if configured_peer_addr is not None:
            STATE.peer_addr = configured_peer_addr
        STATE.peer_socket_addr = peer_addr
        STATE.local_session_info = local_info
        STATE.peer_session_info = peer_info
        STATE.verification_result = verification
        STATE.local_eph_pub_hex = local_eph_hex
        STATE.peer_eph_pub_hex = peer_eph_hex
        STATE.aes_key = aes_key
        STATE.shared_secret_fingerprint = protocol.shared_secret_fingerprint(aes_key)
        STATE.key_exchange_time = time.strftime("%Y-%m-%d %H:%M:%S")
        STATE.connection_state = protocol.CONNECTED
        STATE.last_error = None
        if not STATE.node_name:
            STATE.node_name = local_info["session_id"][:10]

    if previous_reader is not reader:
        _close_quietly(previous_reader)
    if previous_socket is not sock:
        _close_quietly(previous_socket)

    _add_event(
        "lock",
        f"secure channel established (fingerprint: {STATE.shared_secret_fingerprint})",
    )

    reader_thread = threading.Thread(
        target=_peer_reader_loop,
        args=(reader, conn_token),
        daemon=True,
    )
    reader_thread.start()

    message_thread = threading.Thread(
        target=_message_loop,
        args=(conn_token,),
        daemon=True,
    )
    message_thread.start()

    with _lock:
        STATE._reader_thread = reader_thread
        STATE._message_thread = message_thread


def _transition_connection(conn_token, new_state, detail, event_kind):
    with _lock:
        if conn_token != STATE._peer_token:
            return False
        _reset_connection()
        STATE.connection_state = new_state
        if new_state == protocol.ERROR:
            STATE.last_error = detail
    _add_event(event_kind, detail)
    return True


# ---------------------------------------------------------------------------
# Initiator: connect to peer
# ---------------------------------------------------------------------------


def _do_handshake(peer_addr, connect_token):
    sock = None
    reader = None
    try:
        eph_private, eph_pub_hex, sign_resp, local_info = _prepare_local_handshake()

        with _lock:
            if connect_token != STATE._connect_token:
                return
            STATE.connection_state = protocol.HANDSHAKE

        _add_event("connect", f"opening peer TCP connection to {peer_addr}")
        normalized_peer_addr, sock = _open_peer_socket(peer_addr)

        with _lock:
            if connect_token != STATE._connect_token:
                return
            STATE._pending_socket = sock

        reader = sock.makefile("rb")
        _add_event("send", f"sending handshake to {normalized_peer_addr}")

        _send_frame(
            sock,
            {
                "type": "handshake",
                "ephemeral_public_key": eph_pub_hex,
                "signature": sign_resp["signature"],
                "session_info": local_info,
            },
        )

        frame = _read_frame(reader)
        if frame.get("type") == "error":
            raise RuntimeError(frame.get("detail") or frame.get("error") or "peer rejected")
        if frame.get("type") != "handshake_ack":
            raise RuntimeError(f"unexpected frame type: {frame.get('type')}")

        with _lock:
            if connect_token != STATE._connect_token:
                return
            STATE.connection_state = protocol.VERIFYING

        _add_event("verify", "verifying peer signature and session")
        peer_eph_hex, peer_info, verification = _verify_peer_handshake(frame, local_info)
        _add_event("check", "peer signature verified")
        _add_event("check", "peer session verified (workload + base image match)")

        with _lock:
            if connect_token != STATE._connect_token:
                return
            STATE.connection_state = protocol.DERIVING_KEY

        _add_event("key", "computing ECDH shared secret")
        aes_key = _derive_session_key(eph_private, local_info, peer_info, peer_eph_hex)
        sock.settimeout(None)

        _activate_peer_connection(
            sock,
            reader,
            _socket_peer_addr(sock),
            normalized_peer_addr,
            local_info,
            peer_info,
            verification,
            eph_pub_hex,
            peer_eph_hex,
            aes_key,
        )
        sock = None
        reader = None
    finally:
        _close_quietly(reader)
        _close_quietly(sock)


def connect_to_peer(peer_addr):
    """Start the peer handshake in a background thread."""
    normalized_peer_addr = _normalize_peer_addr(peer_addr)
    if not normalized_peer_addr:
        return

    with _lock:
        if STATE.connection_state != protocol.DISCONNECTED:
            return
        STATE.peer_addr = normalized_peer_addr
        STATE.connection_state = protocol.HANDSHAKE
        STATE._connect_token += 1
        connect_token = STATE._connect_token

    _add_event("connect", f"initiating connection to {normalized_peer_addr}")

    def _run():
        try:
            _do_handshake(normalized_peer_addr, connect_token)
        except Exception as e:
            with _lock:
                if connect_token == STATE._connect_token:
                    _reset_connection()
                    STATE.connection_state = protocol.ERROR
                    STATE.last_error = str(e)
            _add_event("error", str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    with _lock:
        STATE._connect_thread = t


# ---------------------------------------------------------------------------
# Responder: handle incoming handshake
# ---------------------------------------------------------------------------


def _prepare_for_incoming(peer_session_id):
    yielded = False

    with _lock:
        if STATE.connection_state == protocol.CONNECTED:
            return False, "already_connected"

        if STATE.connection_state not in (protocol.DISCONNECTED, protocol.ERROR):
            local_session = (STATE.local_session_info or {}).get("session_id", "")
            if local_session and peer_session_id:
                if local_session > peer_session_id:
                    return False, "already_connecting"
                _reset_connection()
                yielded = True
            else:
                return False, "already_connecting"

    if yielded:
        _add_event("connect", "yielding to peer with higher session ID")

    return True, None


def _handle_incoming_peer(sock, client_addr):
    reader = sock.makefile("rb")
    sock.settimeout(CONNECT_TIMEOUT)

    try:
        frame = _read_frame(reader)
        if frame.get("type") != "handshake":
            raise ValueError("expected handshake frame")

        peer_session_id = frame.get("session_info", {}).get("session_id", "")
        accepted, error = _prepare_for_incoming(peer_session_id)
        if not accepted:
            _send_error_frame(sock, error, error)
            return

        _add_event("recv", "received handshake from peer")
        _add_event("check", "incoming: peer handshake accepted")

        eph_private, eph_pub_hex, sign_resp, local_info = _prepare_local_handshake()

        _add_event("verify", "incoming: verifying peer signature and session")
        peer_eph_hex, peer_info, verification = _verify_peer_handshake(frame, local_info)
        _add_event("check", "incoming: peer signature verified")
        _add_event("check", "incoming: peer session verified")

        _add_event("key", "incoming: computing ECDH shared secret")
        aes_key = _derive_session_key(eph_private, local_info, peer_info, peer_eph_hex)

        _send_frame(
            sock,
            {
                "type": "handshake_ack",
                "ephemeral_public_key": eph_pub_hex,
                "signature": sign_resp["signature"],
                "session_info": local_info,
            },
        )

        sock.settimeout(None)
        peer_addr = _socket_peer_addr(sock) or _socket_peer_addr_from_tuple(client_addr)
        _activate_peer_connection(
            sock,
            reader,
            peer_addr,
            None,
            local_info,
            peer_info,
            verification,
            eph_pub_hex,
            peer_eph_hex,
            aes_key,
        )
        sock = None
        reader = None
    except ValueError as e:
        _send_error_frame(sock, "attestation_failed", str(e))
        _add_event("error", str(e))
    except Exception as e:
        _send_error_frame(sock, "protocol_error", str(e))
        _add_event("error", str(e))
    finally:
        _close_quietly(reader)
        _close_quietly(sock)


def _socket_peer_addr_from_tuple(addr):
    host = addr[0]
    port = addr[1]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def _serve_peer_listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PEER_PORT))
    server.listen()

    while True:
        conn, addr = server.accept()
        threading.Thread(
            target=_handle_incoming_peer,
            args=(conn, addr),
            daemon=True,
        ).start()


# ---------------------------------------------------------------------------
# Bidirectional encrypted messaging on the peer socket
# ---------------------------------------------------------------------------


def _send_encrypted_message(text, expected_peer_token=None):
    with _lock:
        if STATE.connection_state != protocol.CONNECTED or STATE._peer_socket is None:
            raise RuntimeError("not connected")
        if expected_peer_token is not None and expected_peer_token != STATE._peer_token:
            raise RuntimeError("stale connection")
        key = STATE.aes_key
        sock = STATE._peer_socket
        STATE.message_counter += 1
        seq = STATE.message_counter
        name = STATE.node_name

    frame = protocol.encrypt_message(text, key)
    frame["type"] = "message"
    frame["seq"] = seq
    frame["sender"] = name
    _send_frame(sock, frame)
    _add_message("sent", text, frame["nonce"], seq)
    return seq


def _message_loop(peer_token):
    consecutive_failures = 0

    while True:
        with _lock:
            if STATE.connection_state != protocol.CONNECTED:
                break
            if peer_token != STATE._peer_token:
                break
            name = STATE.node_name

        text = f"heartbeat from {name} at {time.strftime('%H:%M:%S')}"

        try:
            _send_encrypted_message(text, expected_peer_token=peer_token)
            consecutive_failures = 0
        except Exception as e:
            if str(e) == "stale connection":
                break
            consecutive_failures += 1
            print(f"[{name}] message send failed ({consecutive_failures}): {e}")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                _transition_connection(
                    peer_token,
                    protocol.ERROR,
                    f"peer unreachable after {MAX_CONSECUTIVE_FAILURES} failures",
                    "error",
                )
                break

        time.sleep(MESSAGE_INTERVAL)


def _peer_reader_loop(reader, peer_token):
    try:
        while True:
            frame = _read_frame(reader)
            frame_type = frame.get("type")

            if frame_type == "message":
                with _lock:
                    if peer_token != STATE._peer_token:
                        return
                    if STATE.connection_state != protocol.CONNECTED:
                        return
                    key = STATE.aes_key

                text = protocol.decrypt_message(frame, key)
                seq = frame.get("seq", 0)
                nonce = frame.get("nonce", "")
                _add_message("received", text, nonce, seq)
                continue

            if frame_type == "disconnect":
                reason = frame.get("reason", "peer disconnected")
                _transition_connection(
                    peer_token,
                    protocol.DISCONNECTED,
                    reason,
                    "connect",
                )
                return

            raise ValueError(f"unexpected frame type: {frame_type}")
    except EOFError:
        _transition_connection(
            peer_token,
            protocol.ERROR,
            "peer connection closed",
            "error",
        )
    except Exception as e:
        _transition_connection(peer_token, protocol.ERROR, str(e), "error")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>peer-attestation-demo</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: monospace; background: #0d1117; color: #c9d1d9; padding: 24px; }
h1 { color: #58a6ff; margin-bottom: 4px; font-size: 22px; }
h2 { color: #8b949e; margin: 16px 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }
.sub { color: #484f58; font-size: 12px; margin-bottom: 16px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 12px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; margin-left: 10px; }
.badge.disconnected { background: #21262d; color: #8b949e; }
.badge.handshake, .badge.verifying, .badge.deriving_key { background: #3d2e00; color: #d29922; }
.badge.connected { background: #0f2d1c; color: #3fb950; }
.badge.error, .badge.attestation_failed, .badge.key_exchange_failed { background: #3d1418; color: #f85149; }
.row { display: flex; gap: 12px; }
.row > * { flex: 1; }
.kv { display: flex; justify-content: space-between; padding: 3px 0; font-size: 12px; }
.kv .k { color: #8b949e; }
.kv .v { color: #c9d1d9; word-break: break-all; max-width: 60%; text-align: right; }
.mono { font-family: monospace; font-size: 11px; }
input[type=text] { background: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 6px 10px;
  border-radius: 6px; font-family: monospace; font-size: 13px; width: 260px; }
.btn { background: #238636; color: #fff; border: none; padding: 6px 14px; border-radius: 6px;
  cursor: pointer; font-family: monospace; font-size: 12px; margin-left: 6px; }
.btn:hover { background: #2ea043; }
.btn:disabled { opacity: 0.4; cursor: default; }
.btn.danger { background: #da3633; }
.btn.danger:hover { background: #f85149; }
.check-ok { color: #3fb950; }
.check-fail { color: #f85149; }
.timeline { max-height: 180px; overflow-y: auto; font-size: 11px; }
.evt { padding: 3px 0; border-bottom: 1px solid #21262d; display: flex; gap: 8px; }
.evt-time { color: #484f58; min-width: 60px; }
.evt-icon { min-width: 16px; text-align: center; }
.evt-detail { color: #c9d1d9; }
.msgs { max-height: 280px; overflow-y: auto; }
.msg { padding: 4px 0; border-bottom: 1px solid #21262d; font-size: 12px; display: flex; gap: 8px; }
.msg-time { color: #484f58; min-width: 60px; }
.msg-dir { min-width: 16px; text-align: center; }
.msg-dir.sent { color: #58a6ff; }
.msg-dir.recv { color: #3fb950; }
.msg-text { color: #c9d1d9; flex: 1; }
.msg-nonce { color: #484f58; font-size: 10px; }
.send-row { display: flex; gap: 6px; margin-top: 8px; }
.send-row input { flex: 1; }
.empty { color: #484f58; font-size: 12px; padding: 8px 0; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.col-title { color: #58a6ff; font-size: 12px; font-weight: bold; margin-bottom: 6px; }
.error-bar { background: #3d1418; border: 1px solid #f85149; border-radius: 6px; padding: 8px 12px;
  color: #f85149; font-size: 12px; margin-bottom: 12px; }
.conn-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 16px; }
.conn-item { padding: 10px 12px; border: 1px solid #21262d; border-radius: 6px; background: #0d1117; }
.conn-label { color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 4px; }
.conn-value { color: #c9d1d9; font-size: 13px; word-break: break-all; }
</style>
</head>
<body>

<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
  <h1>peer-attestation-demo</h1>
  <span class="badge disconnected" id="badge">disconnected</span>
</div>
<div class="sub" id="node-info">loading...</div>

<div id="error-bar" style="display:none" class="error-bar"></div>

<div class="card">
  <h2>Connect</h2>
  <div style="margin-top:6px;">
    <input type="text" id="peer-addr" placeholder="peer-ip:4000" />
    <button class="btn" id="btn-connect" onclick="doConnect()">Connect</button>
    <button class="btn danger" id="btn-disconnect" onclick="doDisconnect()" style="display:none">Disconnect</button>
  </div>
</div>

<h2>Connection</h2>
<div class="card">
  <div class="conn-grid" id="connection-info">
    <div class="conn-item">
      <div class="conn-label">Status</div>
      <div class="conn-value">loading...</div>
    </div>
  </div>
</div>

<h2>Protocol Timeline</h2>
<div class="card">
  <div class="timeline" id="timeline"></div>
</div>

<div class="cols2">
  <div>
    <h2>Attestation</h2>
    <div class="card" id="attestation-card">
      <div class="cols2" id="attest-cols">
        <div>
          <div class="col-title">Local Session</div>
          <div id="attest-local" class="empty">not initialized</div>
        </div>
        <div>
          <div class="col-title">Remote Session</div>
          <div id="attest-remote" class="empty">not connected</div>
        </div>
      </div>
      <div id="attest-checks" style="margin-top:8px;"></div>
    </div>
  </div>
  <div>
    <h2>Key Exchange</h2>
    <div class="card" id="kex-card">
      <div id="kex-info" class="empty">no key exchange yet</div>
    </div>
  </div>
</div>

<h2>Messages</h2>
<div class="card">
  <div class="msgs" id="messages"></div>
  <div class="send-row">
    <input type="text" id="msg-input" placeholder="type a message..." />
    <button class="btn" id="btn-send" onclick="doSend()" disabled>Send</button>
  </div>
</div>

<script>
const ICON = {connect:'\\u2192',send:'\\u2191',recv:'\\u2193',sign:'\\u270D',key:'\\uD83D\\uDD11',
  verify:'\\uD83D\\uDD0D',check:'\\u2713',lock:'\\uD83D\\uDD12',error:'\\u2717'};

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function trunc(s,n){return s && s.length>n ? s.slice(0,n)+'...' : s||'';}
function syncInputValue(id,value){
  const el=document.getElementById(id);
  if(el.dataset.dirty==='true') return;
  el.value=value||'';
}

function renderSessionInfo(info){
  if(!info) return '<div class="empty">--</div>';
  return `
    <div class="kv"><span class="k">session</span><span class="v mono">${trunc(info.session_id,18)}</span></div>
    <div class="kv"><span class="k">workload</span><span class="v mono">${trunc(info.workload_id,18)}</span></div>
    <div class="kv"><span class="k">base image</span><span class="v mono">${trunc(info.base_image_id,18)}</span></div>
    <div class="kv"><span class="k">session key</span><span class="v mono">${trunc(info.session_key_fingerprint,18)}</span></div>`;
}

function render(st){
  const b=document.getElementById('badge');
  b.textContent=st.connection_state;
  b.className='badge '+st.connection_state;

  document.getElementById('node-info').textContent=
    `node: ${st.node_name||'(unknown)'}`;

  const eb=document.getElementById('error-bar');
  if(st.last_error){eb.style.display='block';eb.textContent=st.last_error;}
  else eb.style.display='none';

  const ci=document.getElementById('connection-info');
  ci.innerHTML=`
    <div class="conn-item">
      <div class="conn-label">Connection State</div>
      <div class="conn-value">${esc(st.connection_state)}</div>
    </div>
    <div class="conn-item">
      <div class="conn-label">Dashboard Port</div>
      <div class="conn-value mono">${esc(st.dashboard_port)}</div>
    </div>
    <div class="conn-item">
      <div class="conn-label">Peer Port</div>
      <div class="conn-value mono">${esc(st.peer_port)}</div>
    </div>
    <div class="conn-item">
      <div class="conn-label">Configured Peer</div>
      <div class="conn-value mono">${esc(st.peer_addr||'none')}</div>
    </div>
    <div class="conn-item">
      <div class="conn-label">Active Socket</div>
      <div class="conn-value mono">${esc(st.peer_socket_addr||'none')}</div>
    </div>
    <div class="conn-item">
      <div class="conn-label">Last Error</div>
      <div class="conn-value">${esc(st.last_error||'none')}</div>
    </div>`;

  const bc=document.getElementById('btn-connect');
  const bd=document.getElementById('btn-disconnect');
  const dis=st.connection_state==='disconnected';
  bc.style.display=dis?'inline-block':'none';
  bd.style.display=dis?'none':'inline-block';
  bc.disabled=!dis;
  syncInputValue('peer-addr', st.peer_addr);

  const tl=document.getElementById('timeline');
  if(st.events.length===0){tl.innerHTML='<div class="empty">no events yet</div>';}
  else{
    let h='';
    for(const e of st.events){
      h+=`<div class="evt"><span class="evt-time">${esc(e.time)}</span>`+
        `<span class="evt-icon">${ICON[e.kind]||'•'}</span>`+
        `<span class="evt-detail">${esc(e.detail)}</span></div>`;
    }
    tl.innerHTML=h;
    tl.scrollTop=tl.scrollHeight;
  }

  document.getElementById('attest-local').innerHTML=renderSessionInfo(st.local_session_info);
  document.getElementById('attest-remote').innerHTML=renderSessionInfo(st.peer_session_info);
  const ac=document.getElementById('attest-checks');
  if(st.verification){
    let h='';
    for(const c of st.verification.checks){
      const icon=c.passed?'<span class="check-ok">✓</span>':'<span class="check-fail">✗</span>';
      h+=`<div class="kv"><span class="k">${icon} ${esc(c.name)}</span>`+
        `<span class="v mono">${c.passed?'match':'MISMATCH'}</span></div>`;
    }
    ac.innerHTML=h;
  } else ac.innerHTML='';

  const ki=document.getElementById('kex-info');
  if(st.shared_secret_fingerprint){
    ki.innerHTML=`
      <div class="kv"><span class="k">local ephemeral</span><span class="v mono">${trunc(st.local_eph_pub_hex,24)}</span></div>
      <div class="kv"><span class="k">peer ephemeral</span><span class="v mono">${trunc(st.peer_eph_pub_hex,24)}</span></div>
      <div class="kv"><span class="k">shared secret</span><span class="v mono">${esc(st.shared_secret_fingerprint)}</span></div>
      <div class="kv"><span class="k">derived at</span><span class="v">${esc(st.key_exchange_time)}</span></div>`;
  } else ki.innerHTML='<div class="empty">no key exchange yet</div>';

  const ml=document.getElementById('messages');
  const bs=document.getElementById('btn-send');
  bs.disabled=st.connection_state!=='connected';
  if(st.messages.length===0){ml.innerHTML='<div class="empty">no messages yet</div>';}
  else{
    let h='';
    for(const m of st.messages){
      const cls=m.direction==='sent'?'sent':'recv';
      const arrow=m.direction==='sent'?'→':'←';
      h+=`<div class="msg"><span class="msg-time">${esc(m.time)}</span>`+
        `<span class="msg-dir ${cls}">${arrow}</span>`+
        `<span class="msg-text">${esc(m.text)}</span>`+
        `<span class="msg-nonce">${trunc(m.nonce,14)}</span></div>`;
    }
    ml.innerHTML=h;
    ml.scrollTop=ml.scrollHeight;
  }
}

async function refresh(){
  try{
    const st=await fetch('/api/state').then(r=>r.json());
    render(st);
  }catch(e){console.error(e);}
}

async function doConnect(){
  const addr=document.getElementById('peer-addr').value.trim();
  if(!addr)return;
  await fetch('/api/connect',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({peer_addr:addr})});
  document.getElementById('peer-addr').dataset.dirty='false';
  setTimeout(refresh,300);
}

async function doDisconnect(){
  await fetch('/api/disconnect',{method:'POST'});
  setTimeout(refresh,300);
}

async function doSend(){
  const inp=document.getElementById('msg-input');
  const text=inp.value.trim();
  if(!text)return;
  inp.value='';
  await fetch('/api/send',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text:text})});
  setTimeout(refresh,300);
}

document.getElementById('msg-input').addEventListener('keydown',function(e){
  if(e.key==='Enter')doSend();
});
document.getElementById('peer-addr').addEventListener('keydown',function(e){
  if(e.key==='Enter')doConnect();
});
document.getElementById('peer-addr').addEventListener('input',function(){
  this.dataset.dirty='true';
});

refresh();
setInterval(refresh,1500);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/dashboard":
            self._serve_html(DASHBOARD_HTML)
        elif self.path == "/api/state":
            self._json(200, _state_snapshot())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/connect":
            self._handle_connect()
        elif self.path == "/api/disconnect":
            self._handle_disconnect()
        elif self.path == "/api/send":
            self._handle_send()
        else:
            self.send_error(404)

    # -- Dashboard API -------------------------------------------------------

    def _handle_connect(self):
        body = self._read_json()
        peer_addr = _normalize_peer_addr(body.get("peer_addr"))
        if not peer_addr:
            self._json(400, {"error": "peer_addr required"})
            return
        connect_to_peer(peer_addr)
        self._json(200, {"status": "connecting", "peer_addr": peer_addr})

    def _handle_disconnect(self):
        with _lock:
            peer_token = STATE._peer_token
            peer_socket = STATE._peer_socket

        if peer_socket is not None:
            try:
                _send_frame(
                    peer_socket,
                    {"type": "disconnect", "reason": "peer requested disconnect"},
                )
            except Exception:
                pass

        with _lock:
            _reset_connection()

        if peer_socket is not None:
            _add_event("connect", "disconnected")
        self._json(200, {"status": "disconnected", "peer_token": peer_token})

    def _handle_send(self):
        body = self._read_json()
        text = body.get("text", "").strip()
        if not text:
            self._json(400, {"error": "text required"})
            return

        try:
            seq = _send_encrypted_message(text)
            self._json(200, {"status": "sent", "seq": seq})
        except Exception as e:
            if str(e) == "not connected":
                self._json(409, {"error": str(e)})
                return
            with _lock:
                peer_token = STATE._peer_token
            _transition_connection(peer_token, protocol.ERROR, str(e), "error")
            self._json(502, {"error": str(e)})

    # -- Helpers -------------------------------------------------------------

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _serve_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        with _lock:
            name = STATE.node_name or "node"
        print(f"[{name}] {fmt % args}")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def _load_config():
    """Load config from unmeasured-data file, then env var overrides."""
    if os.path.exists(UNMEASURED_CONFIG):
        try:
            with open(UNMEASURED_CONFIG) as f:
                cfg = json.load(f)
            with _lock:
                if cfg.get("node_name"):
                    STATE.node_name = cfg["node_name"]
                if cfg.get("peer_addr"):
                    STATE.peer_addr = _normalize_peer_addr(cfg["peer_addr"])
            print(f"[node] loaded unmeasured config: {cfg}")
        except Exception as e:
            print(f"[node] warning: failed to read {UNMEASURED_CONFIG}: {e}")
    else:
        print(f"[node] no unmeasured config at {UNMEASURED_CONFIG}")

    env_name = os.environ.get("NODE_NAME")
    env_peer = os.environ.get("PEER_ADDR")
    with _lock:
        if env_name:
            STATE.node_name = env_name
        if env_peer:
            STATE.peer_addr = _normalize_peer_addr(env_peer)


def main():
    _load_config()

    with _lock:
        name = STATE.node_name or "(unnamed)"
        auto_peer = STATE.peer_addr

    print(f"[{name}] dashboard listening on :{DASHBOARD_PORT}")
    print(f"[{name}] peer listener on :{PEER_PORT}")

    threading.Thread(target=_serve_peer_listener, daemon=True).start()

    if auto_peer:
        print(f"[{name}] auto-connecting to {auto_peer} in 5s...")

        def _auto():
            time.sleep(5)
            connect_to_peer(auto_peer)

        threading.Thread(target=_auto, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", DASHBOARD_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
