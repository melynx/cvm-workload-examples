import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


CHECKS = {
    "public_measured": Path("/atakit-portal/measured-data/data/public.txt"),
    "private_measured_absent": Path("/atakit-portal/measured-data/data/private.txt"),
    "public_unmeasured": Path("/atakit-portal/unmeasured-data/runtime/public.env"),
    "private_unmeasured_absent": Path("/atakit-portal/unmeasured-data/runtime/private.env"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        result = {
            "public_measured": CHECKS["public_measured"].exists(),
            "private_measured_absent": not CHECKS["private_measured_absent"].exists(),
            "public_unmeasured": CHECKS["public_unmeasured"].exists(),
            "private_unmeasured_absent": not CHECKS["private_unmeasured_absent"].exists(),
            "PUBLIC_TOKEN": os.environ.get("PUBLIC_TOKEN", ""),
        }
        body = json.dumps(result, sort_keys=True).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("SMOKE_PORT", "3120"))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
