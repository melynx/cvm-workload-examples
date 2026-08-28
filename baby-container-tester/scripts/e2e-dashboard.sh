#!/usr/bin/env sh
set -eu

BASE_URL="${BASE_URL:-}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BABY_PAYLOAD_BYTES="${BABY_PAYLOAD_BYTES:-1073741824}"

if [ -z "$BASE_URL" ]; then
  echo "Set BASE_URL, for example BASE_URL=http://<cvm-ip>:3000 $0" >&2
  exit 1
fi

BABY_PAYLOAD_BYTES="$BABY_PAYLOAD_BYTES" "${ROOT}/scripts/build-sample-baby.sh"
ARCHIVE="${ROOT}/dist/baby-container-tester-sample-${BABY_PAYLOAD_BYTES}.tar"

echo "Uploading ${ARCHIVE}"
curl -fsS -X POST \
  -H 'content-type: application/octet-stream' \
  --data-binary "@${ARCHIVE}" \
  "${BASE_URL}/api/upload"
echo

echo "Creating baby-container instance"
curl -fsS -X POST \
  -H 'content-type: application/json' \
  -d '{}' \
  "${BASE_URL}/api/create"
echo

sleep 2
curl -fsS "${BASE_URL}/api/state" | python3 -c '
import json, sys
state = json.load(sys.stdin)
expected = int(sys.argv[1])
actual = state.get("baby_reported_payload_bytes")
if actual != expected:
    raise SystemExit(f"baby payload mismatch: expected {expected}, got {actual}")
print(json.dumps(state, indent=2, sort_keys=True))
print(f"baby payload verified: {actual} bytes")
' "$BABY_PAYLOAD_BYTES"
