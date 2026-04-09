"""Coordinator: accepts tasks, delegates to workers, serves a live dashboard."""

import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

SHARED = "/shared"
WORKERS = [("worker-a", 3001), ("worker-b", 3002)]

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>multi-container-example</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: monospace; background: #0d1117; color: #c9d1d9; padding: 24px; }
  h1 { color: #58a6ff; margin-bottom: 16px; }
  h2 { color: #8b949e; margin: 20px 0 8px; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card-title { color: #58a6ff; font-size: 16px; font-weight: bold; margin-bottom: 8px; }
  .status { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
  .status.ok { background: #3fb950; }
  .status.err { background: #f85149; }
  .kv { display: flex; justify-content: space-between; padding: 2px 0; }
  .kv .k { color: #8b949e; }
  .kv .v { color: #c9d1d9; }
  .files { max-height: 300px; overflow-y: auto; font-size: 12px; }
  .file { background: #0d1117; border: 1px solid #21262d; border-radius: 4px; margin: 4px 0; padding: 6px 8px; }
  .file-name { color: #58a6ff; font-size: 11px; }
  .file-content { color: #8b949e; white-space: pre-wrap; font-size: 11px; }
  .btn { background: #238636; color: #fff; border: none; padding: 8px 16px; border-radius: 6px;
         cursor: pointer; font-family: monospace; font-size: 13px; margin: 8px 4px 0 0; }
  .btn:hover { background: #2ea043; }
  .btn.danger { background: #da3633; }
  .btn.danger:hover { background: #f85149; }
  #log { margin-top: 8px; font-size: 12px; color: #8b949e; max-height: 60px; overflow-y: auto; }
  .updated { color: #484f58; font-size: 11px; margin-top: 12px; }
</style>
</head>
<body>
<h1>multi-container-example</h1>

<div style="margin-bottom: 16px;">
  <button class="btn" onclick="sendTask()">Send Task</button>
  <button class="btn danger" onclick="clearDisk()">Clear Disk</button>
  <div id="log"></div>
</div>

<div class="grid" id="cards"></div>

<h2>Shared Disk</h2>
<div class="files" id="files"></div>
<div class="updated" id="updated"></div>

<script>
function render(status, results) {
  // Container cards
  const cards = document.getElementById('cards');
  let html = '';

  // Coordinator
  html += `<div class="card">
    <div class="card-title"><span class="status ok"></span>coordinator (:3000)</div>
    <div class="kv"><span class="k">status</span><span class="v">${status.coordinator}</span></div>
    <div class="kv"><span class="k">shared files</span><span class="v">${(status.shared_files||[]).length}</span></div>
  </div>`;

  // Workers
  for (const [name, w] of Object.entries(status.workers || {})) {
    const ok = w.status === 'ok';
    html += `<div class="card">
      <div class="card-title"><span class="status ${ok?'ok':'err'}"></span>${name} (:${w.name ? {'worker-a':'3001','worker-b':'3002'}[w.name]||'?' : '?'})</div>
      <div class="kv"><span class="k">status</span><span class="v">${w.status||'unknown'}</span></div>
      <div class="kv"><span class="k">processed</span><span class="v">${w.processed||0}</span></div>
      <div class="kv"><span class="k">shared files</span><span class="v">${w.shared_file_count||0}</span></div>
    </div>`;
  }
  cards.innerHTML = html;

  // Files
  const filesEl = document.getElementById('files');
  let fhtml = '';
  for (const [name, content] of Object.entries(results)) {
    fhtml += `<div class="file"><div class="file-name">${name}</div><div class="file-content">${esc(content)}</div></div>`;
  }
  filesEl.innerHTML = fhtml || '<div style="color:#484f58;padding:8px">no files yet</div>';
  document.getElementById('updated').textContent = 'updated ' + new Date().toLocaleTimeString();
}

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function refresh() {
  try {
    const [s, r] = await Promise.all([
      fetch('/status').then(r => r.json()),
      fetch('/results').then(r => r.json()),
    ]);
    render(s, r);
  } catch(e) { console.error(e); }
}

async function sendTask() {
  const msg = 'hello from dashboard at ' + new Date().toLocaleTimeString();
  try {
    const r = await fetch('/task', { method: 'POST', body: msg });
    const j = await r.json();
    document.getElementById('log').textContent = 'sent: ' + j.task_id;
    setTimeout(refresh, 500);
  } catch(e) { document.getElementById('log').textContent = 'error: ' + e; }
}

async function clearDisk() {
  try {
    await fetch('/clear', { method: 'POST' });
    document.getElementById('log').textContent = 'disk cleared';
    setTimeout(refresh, 500);
  } catch(e) { document.getElementById('log').textContent = 'error: ' + e; }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/dashboard":
            self._dashboard()
        elif self.path == "/status":
            self._status()
        elif self.path == "/results":
            self._results()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/task":
            self._create_task()
        elif self.path == "/clear":
            self._clear_disk()
        else:
            self.send_error(404)

    def _dashboard(self):
        body = DASHBOARD_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _status(self):
        """Query each worker's /health endpoint and list shared disk contents."""
        info = {"coordinator": "ok", "workers": {}}
        for name, port in WORKERS:
            try:
                resp = urllib.request.urlopen(
                    f"http://{name}:{port}/health", timeout=2
                )
                info["workers"][name] = json.loads(resp.read())
            except Exception as e:
                info["workers"][name] = {"status": "unreachable", "error": str(e)}

        info["shared_files"] = sorted(os.listdir(SHARED)) if os.path.isdir(SHARED) else []
        self._json(200, info)

    def _results(self):
        """Read all files from the shared disk."""
        results = {}
        if os.path.isdir(SHARED):
            for f in sorted(os.listdir(SHARED)):
                path = os.path.join(SHARED, f)
                if os.path.isfile(path):
                    with open(path) as fh:
                        results[f] = fh.read()
        self._json(200, results)

    def _create_task(self):
        """Write a task file to the shared disk and notify workers to process it."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length else "empty task"

        task_id = f"task-{int(time.time() * 1000)}"
        os.makedirs(SHARED, exist_ok=True)
        with open(os.path.join(SHARED, f"{task_id}.txt"), "w") as f:
            f.write(body)

        # Notify workers over the container network.
        worker_results = {}
        for name, port in WORKERS:
            try:
                req = urllib.request.Request(
                    f"http://{name}:{port}/process",
                    data=json.dumps({"task_id": task_id, "payload": body}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=5)
                worker_results[name] = json.loads(resp.read())
            except Exception as e:
                worker_results[name] = {"status": "error", "error": str(e)}

        self._json(200, {"task_id": task_id, "workers": worker_results})

    def _clear_disk(self):
        """Remove all files from the shared disk."""
        removed = 0
        if os.path.isdir(SHARED):
            for f in os.listdir(SHARED):
                path = os.path.join(SHARED, f)
                if os.path.isfile(path):
                    os.remove(path)
                    removed += 1
        self._json(200, {"removed": removed})

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[coordinator] {fmt % args}")


if __name__ == "__main__":
    print("[coordinator] listening on :3000")
    HTTPServer(("0.0.0.0", 3000), Handler).serve_forever()
