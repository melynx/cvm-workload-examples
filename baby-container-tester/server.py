#!/usr/bin/env python3
import http.client
import json
import os
import socket
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "3000"))
PORTAL_SOCKET = os.environ.get("PORTAL_SOCKET", "/run/atakit-portal.sock")
BABY_SLOT = os.environ.get("BABY_SLOT", "tester")
UPLOAD_SPOOL_DIR = os.environ.get(
    "UPLOAD_SPOOL_DIR", "/var/lib/baby-container-tester/uploads"
)
WORKLOAD_PAYLOAD_PATH = os.environ.get(
    "WORKLOAD_PAYLOAD_PATH", "/app/payload.bin"
)
DASHBOARD_PATH = os.environ.get("DASHBOARD_PATH", "/app/dashboard.html")
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_LOG_BYTES = 1024 * 1024
CHUNK_BYTES = 1024 * 1024

state_lock = threading.Lock()
last_upload = {
    "archive_bytes": None,
    "image_id": None,
}


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__("localhost", timeout=1800)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(1800)
        self.sock.connect(self.socket_path)


def portal_request(method, path, body=None, headers=None):
    connection = UnixHTTPConnection(PORTAL_SOCKET)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {"raw": text}
        return response.status, payload
    finally:
        connection.close()


def workload_payload_bytes():
    try:
        return os.path.getsize(WORKLOAD_PAYLOAD_PATH)
    except FileNotFoundError:
        return None


def list_field(payload, key):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    return []


def parse_reported_payload_bytes(logs):
    reported = None
    for line in logs.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            record = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        value = record.get("payload_bytes")
        if isinstance(value, int) and value >= 0:
            reported = value
    return reported


def send_json(handler, status, payload):
    if status == 204:
        handler.send_response(status)
        handler.send_header("cache-control", "no-store")
        handler.send_header("content-length", "0")
        handler.end_headers()
        return
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("cache-control", "no-store")
    handler.send_header("content-length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def send_dashboard(handler):
    with open(DASHBOARD_PATH, "rb") as dashboard:
        encoded = dashboard.read()
    handler.send_response(200)
    handler.send_header("content-type", "text/html; charset=utf-8")
    handler.send_header("cache-control", "no-store")
    handler.send_header("content-length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def read_json_body(handler):
    raw_length = handler.headers.get("content-length", "0")
    try:
        length = int(raw_length)
    except ValueError as error:
        raise ValueError("invalid content-length") from error
    if length < 0 or length > MAX_JSON_BYTES:
        raise ValueError("JSON body exceeds 64 KiB")
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("invalid JSON body") from error
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def portal_json_post(path, payload):
    body = json.dumps(payload).encode("utf-8")
    return portal_request(
        "POST",
        path,
        body=body,
        headers={
            "content-type": "application/json",
            "content-length": str(len(body)),
        },
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            send_dashboard(self)
            return
        if parsed.path == "/health":
            send_json(
                self,
                200,
                {
                    "ok": True,
                    "kind": "baby-container-tester",
                    "slot": BABY_SLOT,
                    "workload_payload_bytes": workload_payload_bytes(),
                },
            )
            return
        if parsed.path == "/api/state":
            self.send_state(parsed)
            return
        if parsed.path == "/api/logs":
            self.send_logs(parsed)
            return
        send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/upload":
            self.upload_image()
            return
        if parsed.path == "/api/create":
            self.create_instance()
            return
        if parsed.path == "/api/stop":
            self.instance_action("/baby-container/stop")
            return
        if parsed.path == "/api/remove":
            self.instance_action("/baby-container/remove")
            return
        if parsed.path == "/api/image/remove":
            self.remove_image()
            return
        send_json(self, 404, {"error": "not found"})

    def send_state(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        selected_instance = query.get("instance_id", [None])[0]
        try:
            portal_status_code, portal_status = portal_request(
                "GET", "/portal-external-api/status"
            )
            image_status, image_payload = portal_request(
                "GET", "/baby-container/image/list"
            )
            instance_status, instance_payload = portal_request(
                "GET", "/baby-container/list"
            )
        except OSError as error:
            send_json(
                self,
                502,
                {
                    "error": str(error),
                    "portal_available": False,
                    "slot": BABY_SLOT,
                    "workload_payload_bytes": workload_payload_bytes(),
                    "upload_limit_bytes": MAX_UPLOAD_BYTES,
                },
            )
            return

        images = list_field(image_payload, "images")
        instances = list_field(instance_payload, "instances")
        instance = None
        if selected_instance:
            instance = next(
                (
                    item
                    for item in instances
                    if item.get("instance_id") == selected_instance
                ),
                None,
            )
        elif instances:
            instance = instances[0]

        logs = ""
        logs_status = None
        reported_payload_bytes = None
        if instance and instance.get("instance_id"):
            log_query = urllib.parse.urlencode(
                {
                    "instance_id": instance["instance_id"],
                    "max_bytes": MAX_LOG_BYTES,
                }
            )
            logs_status, log_payload = portal_request(
                "GET", "/baby-container/logs?" + log_query
            )
            if logs_status == 200 and isinstance(log_payload, dict):
                logs = str(log_payload.get("logs", ""))
                reported_payload_bytes = parse_reported_payload_bytes(logs)

        with state_lock:
            upload = dict(last_upload)
        healthy = all(
            code == 200
            for code in (portal_status_code, image_status, instance_status)
        )
        send_json(
            self,
            200 if healthy else 502,
            {
                "portal_available": True,
                "portal_status_code": portal_status_code,
                "portal": portal_status,
                "images_status": image_status,
                "images": images,
                "instances_status": instance_status,
                "instances": instances,
                "logs_status": logs_status,
                "logs": logs,
                "baby_reported_payload_bytes": reported_payload_bytes,
                "workload_payload_bytes": workload_payload_bytes(),
                "upload": upload,
                "upload_limit_bytes": MAX_UPLOAD_BYTES,
                "slot": BABY_SLOT,
            },
        )

    def send_logs(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        instance_id = query.get("instance_id", [None])[0]
        if not instance_id:
            send_json(self, 400, {"error": "instance_id is required"})
            return
        path = "/baby-container/logs?" + urllib.parse.urlencode(
            {"instance_id": instance_id, "max_bytes": MAX_LOG_BYTES}
        )
        try:
            status, payload = portal_request("GET", path)
        except OSError as error:
            send_json(self, 502, {"error": str(error)})
            return
        logs = payload.get("logs", "") if isinstance(payload, dict) else ""
        send_json(
            self,
            status,
            {
                "logs": logs,
                "baby_reported_payload_bytes": parse_reported_payload_bytes(
                    str(logs)
                ),
            },
        )

    def create_instance(self):
        try:
            request = read_json_body(self)
            payload = {"slot": BABY_SLOT}
            for key in ("image_id", "instance_id", "cap-add"):
                if key in request:
                    payload[key] = request[key]
            status, response = portal_json_post(
                "/baby-container/create", payload
            )
            send_json(self, status, response)
        except ValueError as error:
            send_json(self, 400, {"error": str(error)})
        except OSError as error:
            send_json(self, 502, {"error": str(error)})

    def instance_action(self, path):
        try:
            request = read_json_body(self)
            instance_id = request.get("instance_id")
            if not isinstance(instance_id, str) or not instance_id:
                raise ValueError("instance_id is required")
            status, response = portal_json_post(
                path, {"instance_id": instance_id}
            )
            send_json(self, status, response)
        except ValueError as error:
            send_json(self, 400, {"error": str(error)})
        except OSError as error:
            send_json(self, 502, {"error": str(error)})

    def remove_image(self):
        try:
            request = read_json_body(self)
            image_id = request.get("image_id")
            if not isinstance(image_id, str) or not image_id:
                raise ValueError("image_id is required")
            status, response = portal_json_post(
                "/baby-container/image/remove",
                {"image_id": image_id, "slot": BABY_SLOT},
            )
            if status < 300:
                with state_lock:
                    last_upload["archive_bytes"] = None
                    last_upload["image_id"] = None
            send_json(self, status, response)
        except ValueError as error:
            send_json(self, 400, {"error": str(error)})
        except OSError as error:
            send_json(self, 502, {"error": str(error)})

    def upload_image(self):
        raw_length = self.headers.get("content-length")
        if raw_length is None:
            send_json(self, 411, {"error": "content-length is required"})
            return
        try:
            length = int(raw_length)
        except ValueError:
            send_json(self, 400, {"error": "invalid content-length"})
            return
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            send_json(
                self,
                413,
                {"error": "upload must be between 1 byte and 1 GiB"},
            )
            return

        os.makedirs(UPLOAD_SPOOL_DIR, exist_ok=True)
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=UPLOAD_SPOOL_DIR,
                prefix="baby-upload-",
                suffix=".tar",
                delete=False,
            ) as spool:
                path = spool.name
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ValueError("upload ended before content-length")
                    spool.write(chunk)
                    remaining -= len(chunk)

            with open(path, "rb") as image:
                status, payload = portal_request(
                    "POST",
                    "/baby-container/image/upload?slot="
                    + urllib.parse.quote(BABY_SLOT),
                    body=image,
                    headers={
                        "content-type": "application/octet-stream",
                        "content-length": str(length),
                    },
                )
            image_id = payload.get("image_id") if isinstance(payload, dict) else None
            if status < 300:
                with state_lock:
                    last_upload["archive_bytes"] = length
                    last_upload["image_id"] = image_id
            send_json(
                self,
                status,
                {"portal": payload, "uploaded_bytes": length},
            )
        except (OSError, ValueError) as error:
            send_json(self, 502, {"error": str(error)})
        finally:
            if path:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


def main():
    os.makedirs(UPLOAD_SPOOL_DIR, exist_ok=True)
    print(
        json.dumps(
            {
                "kind": "baby-container-tester.started",
                "slot": BABY_SLOT,
                "workload_payload_bytes": workload_payload_bytes(),
            }
        ),
        flush=True,
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
