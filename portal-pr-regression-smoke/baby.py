import json
import os
import time
from pathlib import Path


workspace = Path("/workspace")
storage_ok = False
storage_error = ""
try:
    workspace.mkdir(parents=True, exist_ok=True)
    probe = workspace / "baby-storage.txt"
    probe.write_text("baby-storage-ok\n", encoding="utf-8")
    storage_ok = probe.read_text(encoding="utf-8") == "baby-storage-ok\n"
except Exception as exc:
    storage_error = str(exc)

chroot_ok = False
chroot_error = ""
try:
    Path("/sandbox").mkdir(parents=True, exist_ok=True)
    os.chroot("/sandbox")
    os.chdir("/")
    chroot_ok = True
except Exception as exc:
    chroot_error = str(exc)

print(
    json.dumps(
        {
            "kind": "portal-pr-regression-baby",
            "ok": bool(storage_ok and chroot_ok),
            "storage_ok": storage_ok,
            "storage_error": storage_error,
            "chroot_ok": chroot_ok,
            "chroot_error": chroot_error,
        },
        sort_keys=True,
    ),
    flush=True,
)

while True:
    time.sleep(60)
