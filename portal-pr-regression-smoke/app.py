import http.client
import json
import os
import socket
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PORT = int(os.environ.get("SMOKE_PORT", "3200"))
PORTAL_SOCKET = os.environ.get("PORTAL_SOCKET", "/run/atakit-portal.sock")
BABY_SLOT = os.environ.get("BABY_SLOT", "regression-worker")


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


def portal_request(method, path, body=None, headers=None):
    conn = UnixHTTPConnection(PORTAL_SOCKET)
    try:
        conn.request(method, path, body=body, headers=dict(headers or {}))
        response = conn.getresponse()
        data = response.read()
        text = data.decode("utf-8", errors="replace")
        content_type = response.getheader("content-type", "")
        if "application/json" in content_type and text:
            payload = json.loads(text)
        else:
            payload = {"raw": text}
        return response.status, payload
    finally:
        conn.close()


def json_response(handler, status, payload):
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def probe_diskroot():
    probe = Path("/mnt/diskroot/service-root-probe.txt")
    result = {"exists": probe.parent.is_dir(), "write": False}
    try:
        probe.write_text("service-storage-ok\n", encoding="utf-8")
        result["write"] = probe.read_text(encoding="utf-8") == "service-storage-ok\n"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def baby_status():
    status, payload = portal_request("GET", "/baby-container/list")
    if status != 200:
        return status, payload

    logs = {}
    ok = False
    for instance in payload.get("instances", []):
        instance_id = instance.get("instance_id", "")
        if not instance_id:
            continue
        log_status, log_payload = portal_request(
            "GET",
            f"/baby-container/logs?instance_id={urllib.parse.quote(instance_id)}&max_bytes=65536",
        )
        logs[instance_id] = log_payload
        raw = log_payload.get("logs", "") if isinstance(log_payload, dict) else json.dumps(log_payload)
        if '"chroot_ok": true' in raw and '"storage_ok": true' in raw:
            ok = True

    payload["logs"] = logs
    payload["ok"] = ok
    return 200 if ok else 503, payload


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/baby/status":
            status, payload = baby_status()
            json_response(self, status, payload)
            return
        if parsed.path not in ("/", "/status"):
            json_response(self, 404, {"error": "not found"})
            return

        diskroot = probe_diskroot()
        body = {
            "ok": bool(diskroot.get("write")),
            "diskroot": diskroot,
        }
        json_response(self, 200 if body["ok"] else 500, body)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/baby/upload":
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                json_response(self, 400, {"error": "invalid content-length"})
                return
            body = self.rfile.read(length)
            status, payload = portal_request(
                "POST",
                f"/baby-container/image/upload?slot={urllib.parse.quote(BABY_SLOT)}",
                body=body,
                headers={
                    "content-type": "application/octet-stream",
                    "content-length": str(len(body)),
                },
            )
            json_response(self, status, payload)
            return

        if parsed.path == "/baby/create":
            body = json.dumps(
                {
                    "slot": BABY_SLOT,
                    "instance_id": "regression-1",
                    "cap_add": ["SYS_CHROOT"],
                }
            ).encode("utf-8")
            status, payload = portal_request(
                "POST",
                "/baby-container/create",
                body=body,
                headers={"content-type": "application/json"},
            )
            json_response(self, status, payload)
            return

        json_response(self, 404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
