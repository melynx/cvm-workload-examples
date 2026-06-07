#!/usr/bin/env python3
import json
import os
import time
import urllib.request
from datetime import datetime, timezone


VERSION = os.environ.get("BABY_VERSION", "v1")
PAIR = os.environ.get("FOREX_PAIR", "USD/SGD")
INTERVAL = int(os.environ.get("FOREX_INTERVAL", "15"))
FALLBACK_RATE = "1.3500"


def now():
    return datetime.now(timezone.utc).isoformat()


def fetch_usd_sgd():
    url = "https://api.frankfurter.app/latest?from=USD&to=SGD"
    with urllib.request.urlopen(url, timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return str(payload["rates"]["SGD"]), "frankfurter.app"


print(json.dumps({
    "kind": "forex.worker.started",
    "version": VERSION,
    "pair": PAIR,
    "time": now(),
}), flush=True)

while True:
    try:
        rate, source = fetch_usd_sgd()
    except Exception as exc:
        rate, source = FALLBACK_RATE, f"fallback:{type(exc).__name__}"
    print(json.dumps({
        "kind": "forex.tick",
        "version": VERSION,
        "pair": PAIR,
        "rate": rate,
        "source": source,
        "time": now(),
    }), flush=True)
    time.sleep(INTERVAL)
