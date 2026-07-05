import os
import time
from pathlib import Path


checks = {
    "private_measured": Path("/atakit-portal/measured-data/data/private.txt").exists(),
    "public_measured_absent": not Path("/atakit-portal/measured-data/data/public.txt").exists(),
    "private_unmeasured": Path("/atakit-portal/unmeasured-data/runtime/private.env").exists(),
    "public_unmeasured_absent": not Path("/atakit-portal/unmeasured-data/runtime/public.env").exists(),
    "PRIVATE_TOKEN": bool(os.environ.get("PRIVATE_TOKEN")),
}

print(checks, flush=True)
while True:
    time.sleep(3600)
