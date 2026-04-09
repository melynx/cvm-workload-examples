"""Worker: writes heartbeats to the shared disk, processes tasks from the coordinator."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

SHARED = "/shared"
NAME = os.environ.get("WORKER_NAME", "worker")
PORT = int(os.environ.get("WORKER_PORT", "3001"))
processed_count = 0


def heartbeat_loop():
    """Write a heartbeat file to the shared disk every 5 seconds."""
    while True:
        os.makedirs(SHARED, exist_ok=True)
        with open(os.path.join(SHARED, f"{NAME}-heartbeat.txt"), "w") as f:
            f.write(f"{NAME} alive at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"processed: {processed_count}\n")

        # Also read other workers' heartbeats to demonstrate cross-container disk reads.
        peers = []
        for entry in os.listdir(SHARED):
            if entry.endswith("-heartbeat.txt") and not entry.startswith(NAME):
                with open(os.path.join(SHARED, entry)) as fh:
                    peers.append(f"{entry}: {fh.readline().strip()}")
        if peers:
            print(f"[{NAME}] peers: {', '.join(peers)}")

        time.sleep(5)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._health()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/process":
            self._process()
        else:
            self.send_error(404)

    def _health(self):
        """Return worker status and count of files on shared disk."""
        files = []
        if os.path.isdir(SHARED):
            files = [f for f in os.listdir(SHARED) if os.path.isfile(os.path.join(SHARED, f))]
        self._json(200, {
            "name": NAME,
            "status": "ok",
            "processed": processed_count,
            "shared_file_count": len(files),
        })

    def _process(self):
        """Process a task: read the task file from disk, write a result file."""
        global processed_count
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        task_id = body.get("task_id", "unknown")
        payload = body.get("payload", "")

        # Read the original task file written by the coordinator.
        task_file = os.path.join(SHARED, f"{task_id}.txt")
        original = ""
        if os.path.exists(task_file):
            with open(task_file) as f:
                original = f.read()

        # Write a result file (demonstrates per-worker disk writes).
        os.makedirs(SHARED, exist_ok=True)
        result_path = os.path.join(SHARED, f"{task_id}.{NAME}.result")
        with open(result_path, "w") as f:
            f.write(f"worker: {NAME}\n")
            f.write(f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"original_length: {len(original)}\n")
            f.write(f"payload_length: {len(payload)}\n")

        processed_count += 1
        self._json(200, {"status": "processed", "worker": NAME, "task_id": task_id})

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{NAME}] {fmt % args}")


if __name__ == "__main__":
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    print(f"[{NAME}] listening on :{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
