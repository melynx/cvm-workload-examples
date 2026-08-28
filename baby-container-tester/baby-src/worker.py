#!/usr/bin/env python3
import json
import os
import time


PAYLOAD_PATH = os.environ.get("BABY_PAYLOAD_PATH", "/baby/payload.bin")
print(
    json.dumps(
        {
            "kind": "baby-container-tester.started",
            "payload_bytes": os.path.getsize(PAYLOAD_PATH),
        }
    ),
    flush=True,
)

while True:
    time.sleep(60)
