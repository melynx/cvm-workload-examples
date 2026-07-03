#!/usr/bin/env python3
import http.client
import gzip
import json
import os
import socket
import tempfile
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("DASHBOARD_PORT", "3000"))
PORTAL_SOCKET = os.environ.get("PORTAL_SOCKET", "/run/atakit-portal.sock")
BABY_SLOT = os.environ.get("BABY_SLOT", "forex-worker")
BABY_SLOTS = [
    slot.strip()
    for slot in os.environ.get("BABY_SLOTS", BABY_SLOT).split(",")
    if slot.strip()
]
UPLOAD_SPOOL_DIR = os.environ.get(
    "UPLOAD_SPOOL_DIR",
    "/var/lib/baby-dashboard/uploads",
)
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_COMPRESSED_UPLOAD_BYTES = MAX_UPLOAD_BYTES
UPLOAD_CHUNK_BYTES = 1024 * 1024
UPLOAD_TEMP_PREFIX = "baby-upload-"


class UploadTooLarge(ValueError):
    pass


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
        resp = conn.getresponse()
        data = resp.read()
        text = data.decode("utf-8", errors="replace")
        content_type = resp.getheader("content-type", "")
        if "application/json" in content_type and text:
            payload = json.loads(text)
        else:
            payload = {"raw": text}
        return resp.status, payload
    finally:
        conn.close()


def prepare_upload_spool():
    os.makedirs(UPLOAD_SPOOL_DIR, exist_ok=True)
    for name in os.listdir(UPLOAD_SPOOL_DIR):
        if not name.startswith(UPLOAD_TEMP_PREFIX):
            continue
        path = os.path.join(UPLOAD_SPOOL_DIR, name)
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.unlink(path)
        except FileNotFoundError:
            pass


def new_upload_temp(suffix):
    temp = tempfile.NamedTemporaryFile(
        dir=UPLOAD_SPOOL_DIR,
        prefix=UPLOAD_TEMP_PREFIX,
        suffix=suffix,
        delete=False,
    )
    return temp


def remove_upload_temp(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def parse_content_length(handler):
    raw = handler.headers.get("content-length")
    if raw is None:
        raise ValueError("missing content-length")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("invalid content-length") from exc


def receive_upload_to_file(handler, length):
    temp = new_upload_temp(".upload")
    path = temp.name
    received = 0
    remaining = length
    try:
        with temp:
            while remaining > 0:
                chunk_size = min(UPLOAD_CHUNK_BYTES, remaining)
                chunk = handler.rfile.read(chunk_size)
                if chunk == b"":
                    break
                temp.write(chunk)
                received += len(chunk)
                remaining -= len(chunk)
        if received != length:
            raise ValueError(
                f"incomplete upload: expected {length} bytes, got {received}"
            )
        return path, received
    except Exception:
        remove_upload_temp(path)
        raise


def decompress_gzip_to_file(src_path):
    output = new_upload_temp(".tar")
    output_path = output.name
    decoded = 0
    try:
        with output, gzip.open(src_path, "rb") as source:
            while True:
                chunk = source.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                decoded += len(chunk)
                if decoded > MAX_UPLOAD_BYTES:
                    raise UploadTooLarge("decompressed upload exceeds 1 GiB")
                output.write(chunk)
        return output_path, decoded
    except (OSError, EOFError) as exc:
        remove_upload_temp(output_path)
        raise ValueError(f"invalid gzip upload: {exc}") from exc
    except Exception:
        remove_upload_temp(output_path)
        raise


def portal_upload_file(slot, path, length):
    with open(path, "rb") as body:
        return portal_request(
            "POST",
            f"/baby-container/image/upload?slot={urllib.parse.quote(slot)}",
            body=body,
            headers={
                "content-type": "application/octet-stream",
                "content-length": str(length),
            },
        )


def json_response(handler, status, payload):
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get("content-length", "0"))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def request_slot(parsed, body=None):
    qs = urllib.parse.parse_qs(parsed.query)
    slot = qs.get("slot", [None])[0] or (body or {}).get("slot") or BABY_SLOT
    if slot not in BABY_SLOTS:
        raise ValueError(f"unknown baby-container slot: {slot}")
    return slot


def latest_log_excerpt(instances):
    logs = {}
    for instance in instances:
        instance_id = instance.get("instance_id")
        if not instance_id:
            continue
        status, payload = portal_request(
            "GET",
            f"/baby-container/logs?instance_id={urllib.parse.quote(instance_id)}&max_bytes=8192",
        )
        logs[instance_id] = payload.get("logs", payload) if status == 200 else payload
    return logs


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Baby Container Forex Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f9;
      color: #111827;
    }
    body { margin: 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 28px; }
    header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; margin-bottom: 24px; }
    h1 { margin: 0; font-size: 28px; line-height: 1.15; }
    h2 { margin: 0 0 12px; font-size: 16px; }
    p { margin: 4px 0 0; color: #4b5563; }
    button, .file-button {
      border: 1px solid #1f2937;
      background: #111827;
      color: white;
      border-radius: 6px;
      padding: 9px 12px;
      font: inherit;
      cursor: pointer;
    }
    button.secondary { background: white; color: #111827; border-color: #cbd5e1; }
    button.danger { background: #991b1b; border-color: #991b1b; }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    input[type="file"] { width: 100%; }
    .grid { display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }
    .panel {
      background: white;
      border: 1px solid #d7dde6;
      border-radius: 8px;
      padding: 16px;
    }
    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .kv { display: grid; grid-template-columns: 120px 1fr; gap: 8px; font-size: 14px; }
    .key { color: #64748b; }
    .value { overflow-wrap: anywhere; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { padding: 9px 8px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; }
    th { color: #475569; font-weight: 600; background: #f8fafc; }
    code, pre { font-family: "SFMono-Regular", Consolas, monospace; font-size: 13px; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #0f172a; color: #dbeafe; padding: 12px; border-radius: 6px; min-height: 96px; }
    .pill { display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; background: #e0f2fe; color: #075985; font-size: 12px; font-weight: 600; }
    .error { background: #fef2f2; border-color: #fecaca; color: #7f1d1d; }
    @media (max-width: 860px) {
      main { padding: 18px; }
      header { display: block; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Baby Container Forex Dashboard</h1>
        <p>The workload owns this dashboard and updates its <code>forex-worker</code> slot at runtime.</p>
      </div>
      <div class="pill" id="portalStatus">checking portal</div>
    </header>

    <section class="grid">
      <div class="panel">
        <h2>Runtime Update</h2>
        <div class="kv">
          <div class="key">Slot</div><div class="value"><code id="slotName">forex-worker</code></div>
          <div class="key">Upload format</div><div class="value">Docker archive image tar</div>
        </div>
        <div style="margin-top:14px">
          <input id="imageFile" type="file" accept=".tar,.tar.gz,.tgz,application/x-tar,application/gzip,application/octet-stream">
        </div>
        <div class="toolbar">
          <button id="uploadBtn">Upload image</button>
          <button id="createBtn" class="secondary">Create instance</button>
          <button id="refreshBtn" class="secondary">Refresh</button>
        </div>
        <p id="lastAction"></p>
      </div>

      <div class="panel">
        <h2>Loaded Baby Image</h2>
        <table>
          <thead><tr><th>Slot</th><th>Image ID</th><th>Retention</th><th></th></tr></thead>
          <tbody id="images"></tbody>
        </table>
      </div>
    </section>

    <section class="panel" style="margin-top:18px">
      <h2>Instances</h2>
      <table>
        <thead><tr><th>Instance</th><th>Status</th><th>Image</th><th>Container</th><th></th></tr></thead>
        <tbody id="instances"></tbody>
      </table>
    </section>

    <section class="panel" style="margin-top:18px">
      <h2>Forex Worker Logs</h2>
      <pre id="logs">No baby container logs yet.</pre>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);

    async function api(path, options = {}) {
      const resp = await fetch(path, options);
      const text = await resp.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
      if (!resp.ok) throw new Error(JSON.stringify(data));
      return data;
    }

    function cell(text) {
      const td = document.createElement('td');
      td.textContent = text || '';
      return td;
    }

    async function refresh() {
      try {
        const state = await api('/api/state');
        $('slotName').textContent = state.slot;
        $('portalStatus').textContent = state.portal_available ? 'portal connected' : 'portal unavailable';
        $('portalStatus').className = state.portal_available ? 'pill' : 'pill error';

        const images = $('images');
        images.replaceChildren();
        for (const image of state.images || []) {
          const tr = document.createElement('tr');
          tr.append(cell(image.slot));
          tr.append(cell(image.image_id));
          tr.append(cell(image.retention));
          const action = document.createElement('td');
          const btn = document.createElement('button');
          btn.className = 'danger';
          btn.textContent = 'Remove';
          btn.onclick = () => removeImage(image.image_id);
          action.append(btn);
          tr.append(action);
          images.append(tr);
        }
        if (!images.children.length) images.innerHTML = '<tr><td colspan="4">No image staged.</td></tr>';

        const instances = $('instances');
        instances.replaceChildren();
        for (const instance of state.instances || []) {
          const tr = document.createElement('tr');
          tr.append(cell(instance.instance_id));
          tr.append(cell(instance.status));
          tr.append(cell(instance.image_id));
          tr.append(cell(instance.container_name));
          const action = document.createElement('td');
          const stop = document.createElement('button');
          stop.className = 'secondary';
          stop.textContent = 'Stop';
          stop.onclick = () => stopInstance(instance.instance_id);
          const remove = document.createElement('button');
          remove.className = 'danger';
          remove.textContent = 'Remove';
          remove.style.marginLeft = '6px';
          remove.onclick = () => removeInstance(instance.instance_id);
          action.append(stop, remove);
          tr.append(action);
          instances.append(tr);
        }
        if (!instances.children.length) instances.innerHTML = '<tr><td colspan="5">No baby instances.</td></tr>';

        const logs = state.logs || {};
        const merged = Object.entries(logs).map(([id, log]) => `# ${id}\\n${typeof log === 'string' ? log : JSON.stringify(log, null, 2)}`).join('\\n\\n');
        $('logs').textContent = merged || 'No baby container logs yet.';
      } catch (err) {
        $('portalStatus').textContent = 'error';
        $('portalStatus').className = 'pill error';
        $('lastAction').textContent = err.message;
      }
    }

    async function uploadImage() {
      const file = $('imageFile').files[0];
      if (!file) {
        $('lastAction').textContent = 'Select a baby image tar first.';
        return;
      }
      $('uploadBtn').disabled = true;
      try {
        const headers = {};
        if (file.name.endsWith('.gz')) headers['content-encoding'] = 'gzip';
        const result = await api('/api/upload', { method: 'POST', headers, body: file });
        $('lastAction').textContent = `Uploaded ${result.image_id}`;
        await refresh();
      } finally {
        $('uploadBtn').disabled = false;
      }
    }

    async function createInstance() {
      const result = await api('/api/create', { method: 'POST', headers: {'content-type': 'application/json'}, body: '{}' });
      $('lastAction').textContent = `Created ${result.instance.instance_id}`;
      await refresh();
    }

    async function stopInstance(instance_id) {
      await api('/api/stop', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({ instance_id }) });
      await refresh();
    }

    async function removeInstance(instance_id) {
      await api('/api/remove', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({ instance_id }) });
      await refresh();
    }

    async function removeImage(image_id) {
      await api('/api/image/remove', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({ image_id }) });
      await refresh();
    }

    $('uploadBtn').onclick = uploadImage;
    $('createBtn').onclick = createInstance;
    $('refreshBtn').onclick = refresh;
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "baby-container-dashboard/0.1"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/":
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/state":
                status, payload = portal_request("GET", "/baby-container/list")
                if status != 200:
                    json_response(self, 200, {
                        "slot": BABY_SLOT,
                        "slots": BABY_SLOTS,
                        "portal_available": False,
                        "portal_error": payload,
                        "images": [],
                        "instances": [],
                        "logs": {},
                    })
                    return
                instances = payload.get("instances", [])
                json_response(self, 200, {
                    "slot": BABY_SLOT,
                    "slots": BABY_SLOTS,
                    "portal_available": True,
                    "images": payload.get("images", []),
                    "instances": instances,
                    "logs": latest_log_excerpt(instances),
                })
                return
            if parsed.path == "/api/logs":
                qs = urllib.parse.parse_qs(parsed.query)
                instance_id = qs.get("instance_id", [""])[0]
                status, payload = portal_request(
                    "GET",
                    f"/baby-container/logs?instance_id={urllib.parse.quote(instance_id)}&max_bytes=65536",
                )
                json_response(self, status, payload)
                return
            json_response(self, 404, {"error": "not found"})
        except Exception:
            json_response(self, 500, {"error": traceback.format_exc()})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/upload":
                try:
                    length = parse_content_length(self)
                except ValueError as exc:
                    json_response(self, 400, {"error": str(exc)})
                    return
                if length <= 0:
                    json_response(self, 400, {"error": "empty upload"})
                    return
                encoding = self.headers.get("content-encoding", "").strip().lower()
                if encoding not in ("", "gzip"):
                    json_response(
                        self,
                        400,
                        {"error": f"unsupported content-encoding: {encoding}"},
                    )
                    return
                if length > MAX_COMPRESSED_UPLOAD_BYTES:
                    json_response(self, 413, {"error": "upload exceeds 1 GiB"})
                    return
                temp_paths = []
                try:
                    upload_path, upload_length = receive_upload_to_file(self, length)
                    temp_paths.append(upload_path)
                    forward_path = upload_path
                    forward_length = upload_length
                    if encoding == "gzip":
                        forward_path, forward_length = decompress_gzip_to_file(upload_path)
                        temp_paths.append(forward_path)
                    slot = request_slot(parsed)
                    status, payload = portal_upload_file(slot, forward_path, forward_length)
                except UploadTooLarge as exc:
                    json_response(self, 413, {"error": str(exc)})
                    return
                except ValueError as exc:
                    json_response(self, 400, {"error": str(exc)})
                    return
                finally:
                    for path in temp_paths:
                        remove_upload_temp(path)
                json_response(self, status, payload)
                return
            if parsed.path == "/api/create":
                req = read_json(self)
                body = {"slot": request_slot(parsed, req)}
                if req.get("instance_id"):
                    body["instance_id"] = req["instance_id"]
                status, payload = portal_request(
                    "POST",
                    "/baby-container/create",
                    body=json.dumps(body).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
                json_response(self, status, payload)
                return
            if parsed.path == "/api/stop":
                status, payload = portal_request(
                    "POST",
                    "/baby-container/stop",
                    body=json.dumps(read_json(self)).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
                json_response(self, status, payload)
                return
            if parsed.path == "/api/remove":
                status, payload = portal_request(
                    "POST",
                    "/baby-container/remove",
                    body=json.dumps(read_json(self)).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
                json_response(self, status, payload)
                return
            if parsed.path == "/api/image/remove":
                req = read_json(self)
                req["slot"] = request_slot(parsed, req)
                status, payload = portal_request(
                    "POST",
                    "/baby-container/image/remove",
                    body=json.dumps(req).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
                json_response(self, status, payload)
                return
            json_response(self, 404, {"error": "not found"})
        except Exception:
            json_response(self, 500, {"error": traceback.format_exc()})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


if __name__ == "__main__":
    prepare_upload_spool()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(
        f"dashboard listening on 0.0.0.0:{PORT}, slot={BABY_SLOT}, "
        f"upload_spool={UPLOAD_SPOOL_DIR}",
        flush=True,
    )
    server.serve_forever()
