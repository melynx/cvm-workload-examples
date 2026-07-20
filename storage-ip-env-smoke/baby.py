import ipaddress
import json
import os
import time
from pathlib import Path


workspace = Path("/workspace")


def valid_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


storage_ok = False
storage_error = ""
try:
    workspace.mkdir(parents=True, exist_ok=True)
    probe = workspace / "baby.txt"
    probe.write_text("baby-storage-ok\n", encoding="utf-8")
    storage_ok = probe.read_text(encoding="utf-8") == "baby-storage-ok\n"
except Exception as exc:
    storage_error = str(exc)

status = {
    "kind": "storage-ip-env-smoke-baby",
    "ok": bool(
        storage_ok
        and valid_ip(os.environ.get("ATAKIT_PUBLIC_IP", ""))
        and valid_ip(os.environ.get("ATAKIT_INTERNAL_IP", ""))
    ),
    "public_ip": os.environ.get("ATAKIT_PUBLIC_IP", ""),
    "internal_ip": os.environ.get("ATAKIT_INTERNAL_IP", ""),
    "storage_ok": storage_ok,
    "storage_error": storage_error,
}
print(json.dumps(status, sort_keys=True), flush=True)

while True:
    time.sleep(60)
