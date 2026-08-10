"""Local-only atakit-verifierd API mock for peer-attestation-demo.

This mock reads public session files written by mock_agent.py. It tests the
example's HTTP integration and session-key binding. It does not verify TEE or
TPM evidence and must never be used as an attestation verifier.
"""

import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SESSIONS = {}


def _hex_id(value):
    return "0x" + hashlib.sha256(value.encode()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/v1/config":
            self._json(
                200,
                {
                    "trust_mode": "mock",
                    "portal_allowed_ports": [2024],
                    "portal_allowed_cidrs": None,
                    "packs": [],
                    "supported_platforms": ["mock"],
                },
            )
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/v1/verify":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            request = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            self._json(400, {"checks": [], "errors": [f"invalid JSON: {error}"]})
            return
        portal = request.get("portal") or {}
        portal_host = str(portal.get("host", "")).lower()
        portal_port = portal.get("port")
        portal_key = f"{portal_host}:{portal_port}"
        session_path = SESSIONS.get(portal_key)
        if not session_path:
            self._json(
                400,
                {"checks": [], "errors": [f"unknown mock portal {portal_key!r}"]},
            )
            return
        try:
            with open(session_path, encoding="utf-8") as source:
                session = json.load(source)
        except (OSError, json.JSONDecodeError) as error:
            self._json(
                422,
                {
                    "checks": [],
                    "errors": [f"read mock session for {portal_key}: {error}"],
                },
            )
            return

        base_image = str(request.get("base_image", ""))
        workload = str(request.get("workload", ""))
        result = {
            "verified": True,
            **session,
            "workload_id": _hex_id("workload:" + workload),
            "base_image_id": _hex_id("base-image:" + base_image),
            "binding_mode": "chain",
            "binding_chain_id": 0,
            "binding_registry": "0x" + "00" * 20,
            "attestation_mode": "emulation",
            "sources": {"inputs": {}},
            "checks": [
                {
                    "name": "Mock only: public session file loaded",
                    "valid": True,
                    "detail": None,
                }
            ],
        }
        self._json(200, result)

    def _json(self, status, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[mock-verifierd] {fmt % args}")


def main():
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <listen-port> "
            "<portal-host>:<portal-port>=<session-file> [...]"
        )
        sys.exit(1)
    for mapping in sys.argv[2:]:
        portal, separator, path = mapping.partition("=")
        if not separator or not portal or not path:
            raise ValueError(f"invalid portal mapping: {mapping!r}")
        SESSIONS[portal.lower()] = path
    port = int(sys.argv[1])
    print(f"[mock-verifierd] listening on 127.0.0.1:{port} for {sorted(SESSIONS)}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
