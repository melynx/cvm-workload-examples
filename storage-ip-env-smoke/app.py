import json
import http.client
import os
import socket
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PORT = int(os.environ.get("SMOKE_PORT", "3100"))
HELPER_URL = os.environ.get("HELPER_URL", "http://helper:3101/status")
PORTAL_SOCKET = os.environ.get("PORTAL_SOCKET", "/run/atakit-portal.sock")
BABY_SLOT = os.environ.get("BABY_SLOT", "smoke-worker")


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


def portal_request(method, path, body=None, headers=None):
    headers = dict(headers or {})
    conn = UnixHTTPConnection(PORTAL_SOCKET)
    try:
        conn.request(method, path, body=body, headers=headers)
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


def probe_storage():
    rw_path = Path("/mnt/rw/probe.txt")
    ro_path = Path("/mnt/ro/should-not-write.txt")
    result = {
        "rw_exists": rw_path.parent.is_dir(),
        "ro_exists": ro_path.parent.is_dir(),
        "rw_write": False,
        "ro_write_blocked": False,
    }
    try:
        rw_path.write_text("storage-ip-env-smoke\n", encoding="utf-8")
        result["rw_write"] = rw_path.read_text(encoding="utf-8") == "storage-ip-env-smoke\n"
    except Exception as exc:
        result["rw_error"] = str(exc)
    try:
        ro_path.write_text("should fail\n", encoding="utf-8")
        result["ro_error"] = "write unexpectedly succeeded"
    except Exception as exc:
        result["ro_write_blocked"] = True
        result["ro_error"] = exc.__class__.__name__
    return result


def probe_helper():
    try:
        with urllib.request.urlopen(HELPER_URL, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def probe_env_and_data():
    measured_path = Path("/atakit-portal/measured-data/measured-data/config.txt")
    tree_path = Path("/atakit-portal/unmeasured-data/runtime/tree/nested/value.txt")
    result = {
        "measured_env": os.environ.get("MEASURED_ENV_VALUE", ""),
        "unmeasured_env": os.environ.get("UNMEASURED_ENV_VALUE", ""),
        "measured_data": "",
        "unmeasured_tree": "",
    }
    try:
        result["measured_data"] = measured_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        result["measured_data_error"] = str(exc)
    try:
        result["unmeasured_tree"] = tree_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        result["unmeasured_tree_error"] = str(exc)
    result["ok"] = bool(
        result["measured_env"] == "measured-env-ok"
        and result["unmeasured_env"] == "unmeasured-env-ok"
        and result["measured_data"] == "measured-data-ok"
        and result["unmeasured_tree"] == "unmeasured-tree-ok"
    )
    return result


def baby_status():
    status, payload = portal_request("GET", "/baby-container/list")
    if status != 200:
        return status, payload
    logs = {}
    for instance in payload.get("instances", []):
        instance_id = instance.get("instance_id", "")
        if not instance_id:
            continue
        log_status, log_payload = portal_request(
            "GET",
            f"/baby-container/logs?instance_id={urllib.parse.quote(instance_id)}&max_bytes=65536",
        )
        logs[instance_id] = log_payload if log_status == 200 else {
            "status": log_status,
            "payload": log_payload,
        }
    payload["logs"] = logs
    return 200, payload


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

        storage = probe_storage()
        helper = probe_helper()
        env_data = probe_env_and_data()
        body = {
            "ok": bool(
                storage.get("rw_write")
                and storage.get("ro_write_blocked")
                and helper.get("ok")
                and env_data.get("ok")
                and os.environ.get("ATAKIT_PUBLIC_IP") is not None
                and os.environ.get("ATAKIT_INTERNAL_IP") is not None
            ),
            "public_ip": os.environ.get("ATAKIT_PUBLIC_IP", ""),
            "internal_ip": os.environ.get("ATAKIT_INTERNAL_IP", ""),
            "env_data": env_data,
            "storage": storage,
            "helper": helper,
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
            if length <= 0:
                json_response(self, 400, {"error": "empty upload"})
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
            status, payload = portal_request(
                "POST",
                "/baby-container/create",
                body=json.dumps({"slot": BABY_SLOT, "instance_id": "smoke-1"}).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
            json_response(self, status, payload)
            return
        json_response(self, 404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
