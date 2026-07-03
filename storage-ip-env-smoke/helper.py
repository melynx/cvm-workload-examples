import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PORT = int(os.environ.get("HELPER_PORT", "3101"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/status":
            self.send_response(404)
            self.end_headers()
            return

        path = Path("/mnt/helper/helper.txt")
        storage_ok = False
        storage_error = ""
        try:
            path.write_text("helper\n", encoding="utf-8")
            storage_ok = path.read_text(encoding="utf-8") == "helper\n"
        except Exception as exc:
            storage_error = str(exc)

        body = {
            "ok": bool(
                storage_ok
                and os.environ.get("ATAKIT_PUBLIC_IP") is not None
                and os.environ.get("ATAKIT_INTERNAL_IP") is not None
                and os.environ.get("MEASURED_ENV_VALUE") == "measured-env-ok"
                and os.environ.get("UNMEASURED_ENV_VALUE") == "unmeasured-env-ok"
            ),
            "public_ip": os.environ.get("ATAKIT_PUBLIC_IP", ""),
            "internal_ip": os.environ.get("ATAKIT_INTERNAL_IP", ""),
            "measured_env": os.environ.get("MEASURED_ENV_VALUE", ""),
            "unmeasured_env": os.environ.get("UNMEASURED_ENV_VALUE", ""),
            "storage_ok": storage_ok,
            "storage_error": storage_error,
        }
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        self.send_response(200 if body["ok"] else 500)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
