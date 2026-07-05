#!/usr/bin/env python3
import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


EVENTS = []
LOCK = threading.Lock()


def service_from_path(path):
    if not isinstance(path, str):
        return None
    match = re.search(r"/([^/]+)/[^/]+\.log$", path)
    if match:
        return match.group(1)
    return None


def parse_payload(body):
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []

    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            records.append({"message": line})
            continue
        if isinstance(item, list):
            records.extend(x for x in item if isinstance(x, dict))
        elif isinstance(item, dict):
            records.append(item)

    if records:
        return records

    try:
        item = json.loads(text)
    except json.JSONDecodeError:
        return [{"message": text}]
    if isinstance(item, list):
        return [x for x in item if isinstance(x, dict)]
    if isinstance(item, dict):
        return [item]
    return [{"message": item}]


def normalize(record):
    inner = {}
    log = record.get("log")
    if isinstance(log, str):
        try:
            parsed = json.loads(log.strip())
            if isinstance(parsed, dict):
                inner = parsed
        except json.JSONDecodeError:
            inner = parse_log_message(log)
    elif isinstance(log, dict):
        message = log.get("message")
        if isinstance(message, str):
            inner = parse_log_message(message)

    service = inner.get("service") or record.get("service") or service_from_path(record.get("log_path"))
    run_id = record.get("run_id") or inner.get("run_id")
    return {
        "run_id": run_id,
        "service": service,
        "event": inner.get("event") or record.get("event"),
        "log_path": record.get("log_path"),
        "record": record,
        "log": inner,
    }


def parse_log_message(message):
    text = message.rstrip("\n")
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.match(r"^[^ ]+ (?:stdout|stderr) [^ ]+ (?P<payload>.*)$", text)
    if match:
        payload = match.group("payload")
        try:
            parsed = json.loads(payload.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return {"message": text}


class Handler(BaseHTTPRequestHandler):
    server_version = "remote-log-smoke-receiver/1.0"

    def _json(self, status, payload):
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True})
            return
        if parsed.path != "/events":
            self._json(404, {"error": "not found"})
            return

        query = parse_qs(parsed.query)
        run_id = query.get("run_id", [None])[0]
        with LOCK:
            events = list(EVENTS)
        if run_id:
            events = [event for event in events if event.get("run_id") == run_id]
        services = sorted({event["service"] for event in events if event.get("service")})
        self._json(200, {"count": len(events), "services": services, "events": events[-200:]})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/ingest":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        records = [normalize(record) for record in parse_payload(body)]
        with LOCK:
            EVENTS.extend(records)
        self._json(202, {"accepted": len(records)})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("shutting down")


if __name__ == "__main__":
    main()
