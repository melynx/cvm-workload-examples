#!/usr/bin/env sh
set -eu

BASE_URL="${BASE_URL:-}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [ -z "$BASE_URL" ]; then
  echo "Set BASE_URL to the dashboard URL, for example:" >&2
  echo "  BASE_URL=http://<cvm-ip>:3000 $0" >&2
  exit 1
fi

"${ROOT}/scripts/build-baby-images.sh"

upload_and_create() {
  version="$1"
  tar="${ROOT}/dist/baby-forex-${version}.tar"
  echo "Uploading ${tar}"
  curl -fsS -X POST --data-binary "@${tar}" "${BASE_URL}/api/upload"
  echo
  echo "Creating baby container"
  curl -fsS -X POST -H 'content-type: application/json' -d '{}' "${BASE_URL}/api/create"
  echo
  sleep 3
  echo "Dashboard state"
  curl -fsS "${BASE_URL}/api/state"
  echo
}

upload_and_create v1

echo
echo "Remove the v1 instance from the dashboard or with /api/remove before creating v2 if the slot cap is reached."
echo "Then run:"
echo "  curl -fsS -X POST --data-binary @${ROOT}/dist/baby-forex-v2.tar ${BASE_URL}/api/upload"
echo "  curl -fsS -X POST -H 'content-type: application/json' -d '{}' ${BASE_URL}/api/create"
