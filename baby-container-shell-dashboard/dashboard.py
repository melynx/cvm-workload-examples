#!/usr/bin/env python3
import gzip
import html
import http.client
import json
import os
import shlex
import socket
import subprocess
import tempfile
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("DASHBOARD_PORT", "3000"))
PORTAL_SOCKET = os.environ.get("PORTAL_SOCKET", "/run/atakit-portal.sock")
BABY_SLOT = os.environ.get("BABY_SLOT", "ssh-shell")
BABY_INSTANCE_ID = os.environ.get("BABY_INSTANCE_ID", "ssh-shell-1")
BABY_SSH_HOST = os.environ.get("BABY_SSH_HOST", "127.0.0.1")
BABY_SSH_PORT = int(os.environ.get("BABY_SSH_PORT", "2222"))
BABY_TEST_PASSWORD = os.environ.get("BABY_TEST_PASSWORD", "atakit-test")
ATAKIT_PUBLIC_IP = os.environ.get("ATAKIT_PUBLIC_IP", "")
UPLOAD_SPOOL_DIR = os.environ.get(
    "UPLOAD_SPOOL_DIR",
    "/var/lib/shell-dashboard/uploads",
)
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
UPLOAD_TEMP_PREFIX = "baby-shell-upload-"
SSH_USERS = {"tester", "root"}
BABY_CAP_ADD = ["CHOWN", "SETUID", "SETGID", "SYS_CHROOT"]


NORMAL_USER_CHECKS = r"""
set -eu
test "$(id -u)" = "1000"
test "$(whoami)" = "tester"
printf 'normal-user-write\n' > /tmp/tester-check.txt
test "$(cat /tmp/tester-check.txt)" = "normal-user-write"
python3 -c 'import json; print(json.dumps({"python_user": "tester"}))'
jq -n --arg user "$(whoami)" '{jq_user: $user}'
curl -fsS http://127.0.0.1:3000/api/health
test "$(sudo -n id -u)" = "0"
if test -r /etc/shadow; then
    echo "tester unexpectedly read /etc/shadow" >&2
    exit 1
fi
if touch /etc/tester-write 2>/dev/null; then
    echo "tester unexpectedly wrote to the read-only root filesystem" >&2
    exit 1
fi
echo "normal user checks passed"
""".strip()


ROOT_USER_CHECKS = r"""
set -eu
test "$(id -u)" = "0"
test "$(whoami)" = "root"
test -r /etc/shadow
printf 'root-user-write\n' > /tmp/root-check.txt
chmod 0644 /tmp/root-check.txt
chown tester:tester /tmp/root-check.txt
test "$(stat -c '%u:%g' /tmp/root-check.txt)" = "1000:1000"
su -s /bin/bash tester -c 'test -r /tmp/root-check.txt; test -w /tmp/root-check.txt; test "$(id -u)" = "1000"'
python3 -c 'import os; print({"python_uid": os.getuid()})'
ip -brief address show lo
ps -o pid,user,comm
if ip link add baby-test type dummy 2>/dev/null; then
    echo "root unexpectedly created a network interface without NET_ADMIN" >&2
    exit 1
fi
if python3 -c 'import socket; socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)' 2>/dev/null; then
    echo "root unexpectedly opened a raw socket without NET_RAW" >&2
    exit 1
fi
if touch /etc/root-write 2>/dev/null; then
    echo "root unexpectedly wrote to the read-only root filesystem" >&2
    exit 1
fi
echo "root user checks passed"
""".strip()


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
    conn = UnixHTTPConnection(PORTAL_SOCKET)
    try:
        conn.request(method, path, body=body, headers=dict(headers or {}))
        response = conn.getresponse()
        data = response.read()
        text = data.decode("utf-8", errors="replace")
        if "application/json" in response.getheader("content-type", "") and text:
            payload = json.loads(text)
        else:
            payload = {"raw": text}
        return response.status, payload
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
    return tempfile.NamedTemporaryFile(
        dir=UPLOAD_SPOOL_DIR,
        prefix=UPLOAD_TEMP_PREFIX,
        suffix=suffix,
        delete=False,
    )


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
    except ValueError as error:
        raise ValueError("invalid content-length") from error


def receive_upload_to_file(handler, length):
    temp = new_upload_temp(".upload")
    path = temp.name
    received = 0
    remaining = length
    try:
        with temp:
            while remaining > 0:
                chunk = handler.rfile.read(min(UPLOAD_CHUNK_BYTES, remaining))
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


def decompress_gzip_to_file(source_path):
    output = new_upload_temp(".tar")
    output_path = output.name
    decoded = 0
    try:
        with output, gzip.open(source_path, "rb") as source:
            while True:
                chunk = source.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                decoded += len(chunk)
                if decoded > MAX_UPLOAD_BYTES:
                    raise UploadTooLarge("decompressed upload exceeds 1 GiB")
                output.write(chunk)
        return output_path, decoded
    except (OSError, EOFError) as error:
        remove_upload_temp(output_path)
        raise ValueError(f"invalid gzip upload: {error}") from error
    except Exception:
        remove_upload_temp(output_path)
        raise


def portal_upload_file(path, length):
    with open(path, "rb") as body:
        return portal_request(
            "POST",
            f"/baby-container/image/upload?slot={urllib.parse.quote(BABY_SLOT)}",
            body=body,
            headers={
                "content-type": "application/octet-stream",
                "content-length": str(length),
            },
        )


def baby_create_payload():
    return {
        "slot": BABY_SLOT,
        "instance_id": BABY_INSTANCE_ID,
        "cap_add": list(BABY_CAP_ADD),
    }


def run_ssh_command(user, command, timeout=30):
    if user not in SSH_USERS:
        raise ValueError("user must be tester or root")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    if len(command.encode("utf-8")) > 16 * 1024:
        raise ValueError("command exceeds 16 KiB")

    environment = os.environ.copy()
    environment["SSHPASS"] = BABY_TEST_PASSWORD
    ssh_command = [
        "sshpass",
        "-e",
        "ssh",
        "-p",
        str(BABY_SSH_PORT),
        "-o",
        "BatchMode=no",
        "-o",
        "PreferredAuthentications=password",
        "-o",
        "PubkeyAuthentication=no",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=5",
        f"{user}@{BABY_SSH_HOST}",
        "bash -lc " + shlex.quote(command),
    ]
    result = subprocess.run(
        ssh_command,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
        check=False,
    )
    return {
        "user": user,
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def latest_log_excerpt(instances):
    logs = {}
    for instance in instances:
        instance_id = instance.get("instance_id")
        if not instance_id:
            continue
        status, payload = portal_request(
            "GET",
            "/baby-container/logs?instance_id="
            + urllib.parse.quote(instance_id)
            + "&max_bytes=16384",
        )
        logs[instance_id] = payload.get("logs", payload) if status == 200 else payload
    return logs


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


INDEX_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Baby Container Shell Dashboard</title>
  <style>
    :root { font-family: ui-sans-serif, system-ui, sans-serif; color: #172033; background: #edf1f7; }
    body { margin: 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 28px; }
    header { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; margin-bottom: 20px; }
    h1 { margin: 0 0 5px; font-size: 28px; }
    h2 { margin: 0 0 12px; font-size: 17px; }
    p { margin: 5px 0; color: #506079; }
    .warning { border: 1px solid #ef9f32; background: #fff7e8; color: #713f12; padding: 12px; border-radius: 8px; margin-bottom: 18px; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    .panel { background: white; border: 1px solid #ced7e6; border-radius: 9px; padding: 16px; }
    .wide { grid-column: 1 / -1; }
    .kv { display: grid; grid-template-columns: 135px 1fr; gap: 8px; font-size: 14px; }
    .key { color: #66758d; }
    code, pre, input { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #101827; color: #d9e7ff; padding: 13px; min-height: 110px; border-radius: 7px; }
    input[type=file], input[type=text], textarea, select { box-sizing: border-box; width: 100%; margin: 5px 0 10px; padding: 9px; border: 1px solid #bdc8d9; border-radius: 6px; background: white; }
    textarea { min-height: 100px; resize: vertical; }
    button { border: 1px solid #183e85; background: #2456a6; color: white; border-radius: 6px; padding: 9px 12px; cursor: pointer; margin: 3px 5px 3px 0; }
    button.secondary { color: #172033; background: white; border-color: #9aabc2; }
    button.danger { background: #a62d2d; border-color: #8c2222; }
    button:disabled { opacity: .5; cursor: wait; }
    .pill { border-radius: 999px; background: #d9fbe8; color: #17653a; padding: 5px 10px; font-size: 13px; white-space: nowrap; }
    .pill.error { background: #fde2e2; color: #8c1d1d; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; border-bottom: 1px solid #e3e8f0; padding: 8px; vertical-align: top; }
    @media (max-width: 780px) { main { padding: 16px; } header { display: block; } .grid { grid-template-columns: 1fr; } .wide { grid-column: auto; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Baby Container Shell Dashboard</h1>
      <p>Upload one SSH-enabled baby image, start it, and test normal-user and root-user actions.</p>
    </div>
    <div id="portalStatus" class="pill">Checking portal</div>
  </header>

  <div class="warning"><strong>Test only.</strong> Port <code>2222</code> allows password login. Anyone who can reach it can log in as <code>tester</code> or <code>root</code>.</div>

  <section class="grid">
    <div class="panel">
      <h2>1. Upload and start</h2>
      <label>Docker archive image tar</label>
      <input id="imageFile" type="file" accept=".tar,.tar.gz,.tgz,application/x-tar,application/gzip,application/octet-stream">
      <button id="uploadBtn">Upload image</button>
      <button id="createBtn">Start baby container</button>
      <button id="refreshBtn" class="secondary">Refresh state</button>
      <p id="lastAction"></p>
    </div>

    <div class="panel">
      <h2>SSH debugging</h2>
      <div class="kv">
        <div class="key">Normal user</div><div><code>tester</code></div>
        <div class="key">Root user</div><div><code>root</code></div>
        <div class="key">Password</div><div><code>atakit-test</code></div>
        <div class="key">Port</div><div><code>2222</code></div>
        <div class="key">Public IP</div><div><code>__ATAKIT_PUBLIC_IP__</code></div>
        <div class="key">Command</div><div><code>ssh -t -p 2222 tester@__ATAKIT_PUBLIC_IP__ bash -i</code></div>
        <div class="key">Root command</div><div><code>ssh -t -p 2222 root@__ATAKIT_PUBLIC_IP__ bash -i</code></div>
      </div>
    </div>

    <div class="panel wide">
      <h2>Portal state</h2>
      <table>
        <thead><tr><th>Instance</th><th>Status</th><th>Image</th><th>Capabilities</th><th>Actions</th></tr></thead>
        <tbody id="instances"></tbody>
      </table>
      <p id="imageState">No baby image staged.</p>
    </div>

    <div class="panel">
      <h2>2. Fixed checks</h2>
      <button id="normalChecksBtn">Run normal-user checks</button>
      <button id="rootChecksBtn">Run root-user checks</button>
      <p>The normal-user checks also run <code>sudo -n id -u</code> to test switching to root.</p>
    </div>

    <div class="panel">
      <h2>3. Custom command over SSH</h2>
      <select id="commandUser"><option value="tester">tester</option><option value="root">root</option></select>
      <textarea id="commandText">id; whoami; pwd; ls -la /tmp</textarea>
      <button id="commandBtn">Run command</button>
    </div>

    <div class="panel wide">
      <h2>Command result</h2>
      <pre id="commandResult">No command has run.</pre>
    </div>

    <div class="panel wide">
      <h2>Baby-container logs</h2>
      <pre id="logs">No baby-container logs yet.</pre>
    </div>
  </section>
</main>
<script>
  const $ = id => document.getElementById(id);
  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = {raw: text}; }
    if (!response.ok) throw new Error(JSON.stringify(data));
    return data;
  }
  function cell(text) { const td = document.createElement('td'); td.textContent = text || ''; return td; }
  async function refresh() {
    try {
      const state = await api('/api/state');
      $('portalStatus').textContent = state.portal_available ? 'Portal connected' : 'Portal unavailable';
      $('portalStatus').className = state.portal_available ? 'pill' : 'pill error';
      $('imageState').textContent = state.images.length ? `Staged image: ${state.images.map(image => image.image_id).join(', ')}` : 'No baby image staged.';
      const table = $('instances'); table.replaceChildren();
      for (const instance of state.instances) {
        const row = document.createElement('tr');
        row.append(cell(instance.instance_id), cell(instance.status), cell(instance.image_id), cell((instance.cap_add || []).join(', ')));
        const actions = document.createElement('td');
        const stop = document.createElement('button'); stop.className = 'secondary'; stop.textContent = 'Stop'; stop.onclick = () => instanceAction('/api/stop');
        const remove = document.createElement('button'); remove.className = 'danger'; remove.textContent = 'Remove'; remove.onclick = () => instanceAction('/api/remove');
        actions.append(stop, remove); row.append(actions); table.append(row);
      }
      if (!table.children.length) table.innerHTML = '<tr><td colspan="5">No baby container is running.</td></tr>';
      const merged = Object.entries(state.logs || {}).map(([id, value]) => `# ${id}\n${typeof value === 'string' ? value : JSON.stringify(value, null, 2)}`).join('\n\n');
      $('logs').textContent = merged || 'No baby-container logs yet.';
    } catch (error) { $('portalStatus').textContent = 'Error'; $('portalStatus').className = 'pill error'; $('lastAction').textContent = error.message; }
  }
  async function upload() {
    const file = $('imageFile').files[0]; if (!file) { $('lastAction').textContent = 'Select the baby image tar first.'; return; }
    const headers = {}; if (file.name.endsWith('.gz') || file.name.endsWith('.tgz')) headers['content-encoding'] = 'gzip';
    const result = await api('/api/upload', {method: 'POST', headers, body: file});
    $('lastAction').textContent = `Uploaded ${result.image_id}`; await refresh();
  }
  async function create() {
    const result = await api('/api/create', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'});
    $('lastAction').textContent = `Started ${result.instance.instance_id}`; await refresh();
  }
  async function instanceAction(path) {
    await api(path, {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'}); await refresh();
  }
  function showResult(result) {
    $('commandResult').textContent = `user: ${result.user}\nexit code: ${result.exit_code}\ncommand:\n${result.command}\n\nstdout:\n${result.stdout}\n\nstderr:\n${result.stderr}`;
  }
  async function runCommand(body) {
    try { showResult(await api('/api/command', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)})); }
    catch (error) { $('commandResult').textContent = error.message; }
  }
  $('uploadBtn').onclick = upload;
  $('createBtn').onclick = create;
  $('refreshBtn').onclick = refresh;
  $('normalChecksBtn').onclick = () => runCommand({suite: 'normal'});
  $('rootChecksBtn').onclick = () => runCommand({suite: 'root'});
  $('commandBtn').onclick = () => runCommand({user: $('commandUser').value, command: $('commandText').value});
  refresh(); setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def render_index_html(public_ip):
    display_ip = public_ip or "public IP unavailable"
    return INDEX_HTML_TEMPLATE.replace(
        "__ATAKIT_PUBLIC_IP__",
        html.escape(display_ip, quote=True),
    )


INDEX_HTML = render_index_html(ATAKIT_PUBLIC_IP)


class Handler(BaseHTTPRequestHandler):
    server_version = "baby-container-shell-dashboard/0.1"

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
            if parsed.path == "/api/health":
                json_response(self, 200, {"status": "ok"})
                return
            if parsed.path == "/api/state":
                status, payload = portal_request("GET", "/baby-container/list")
                if status != 200:
                    json_response(
                        self,
                        200,
                        {
                            "slot": BABY_SLOT,
                            "instance_id": BABY_INSTANCE_ID,
                            "portal_available": False,
                            "portal_error": payload,
                            "images": [],
                            "instances": [],
                            "logs": {},
                        },
                    )
                    return
                instances = payload.get("instances", [])
                json_response(
                    self,
                    200,
                    {
                        "slot": BABY_SLOT,
                        "instance_id": BABY_INSTANCE_ID,
                        "portal_available": True,
                        "images": payload.get("images", []),
                        "instances": instances,
                        "logs": latest_log_excerpt(instances),
                    },
                )
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
                except ValueError as error:
                    json_response(self, 400, {"error": str(error)})
                    return
                if length <= 0:
                    json_response(self, 400, {"error": "empty upload"})
                    return
                if length > MAX_UPLOAD_BYTES:
                    json_response(self, 413, {"error": "upload exceeds 1 GiB"})
                    return
                encoding = self.headers.get("content-encoding", "").strip().lower()
                if encoding not in ("", "gzip"):
                    json_response(
                        self,
                        400,
                        {"error": f"unsupported content-encoding: {encoding}"},
                    )
                    return
                temp_paths = []
                try:
                    upload_path, upload_length = receive_upload_to_file(self, length)
                    temp_paths.append(upload_path)
                    forward_path, forward_length = upload_path, upload_length
                    if encoding == "gzip":
                        forward_path, forward_length = decompress_gzip_to_file(
                            upload_path
                        )
                        temp_paths.append(forward_path)
                    status, payload = portal_upload_file(forward_path, forward_length)
                except UploadTooLarge as error:
                    json_response(self, 413, {"error": str(error)})
                    return
                except ValueError as error:
                    json_response(self, 400, {"error": str(error)})
                    return
                finally:
                    for path in temp_paths:
                        remove_upload_temp(path)
                json_response(self, status, payload)
                return
            if parsed.path == "/api/create":
                status, payload = portal_request(
                    "POST",
                    "/baby-container/create",
                    body=json.dumps(baby_create_payload()).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
                json_response(self, status, payload)
                return
            if parsed.path in ("/api/stop", "/api/remove"):
                status, payload = portal_request(
                    "POST",
                    "/baby-container/" + parsed.path.rsplit("/", 1)[1],
                    body=json.dumps({"instance_id": BABY_INSTANCE_ID}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
                json_response(self, status, payload)
                return
            if parsed.path == "/api/command":
                request = read_json(self)
                suite = request.get("suite")
                if suite == "normal":
                    user, command = "tester", NORMAL_USER_CHECKS
                elif suite == "root":
                    user, command = "root", ROOT_USER_CHECKS
                elif suite is None:
                    user, command = request.get("user"), request.get("command")
                else:
                    json_response(self, 400, {"error": "suite must be normal or root"})
                    return
                try:
                    result = run_ssh_command(user, command)
                except ValueError as error:
                    json_response(self, 400, {"error": str(error)})
                    return
                except subprocess.TimeoutExpired:
                    json_response(self, 504, {"error": "SSH command timed out"})
                    return
                json_response(self, 200 if result["exit_code"] == 0 else 422, result)
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
        f"dashboard listening on 0.0.0.0:{PORT}; "
        f"baby slot={BABY_SLOT}; SSH target={BABY_SSH_HOST}:{BABY_SSH_PORT}",
        flush=True,
    )
    server.serve_forever()
