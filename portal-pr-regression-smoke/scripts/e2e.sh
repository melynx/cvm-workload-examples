#!/usr/bin/env sh
set -eu

BASE_URL="${BASE_URL:-}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENGINE="${CONTAINER_ENGINE:-podman}"

if [ -z "$BASE_URL" ]; then
  echo "Set BASE_URL, for example:" >&2
  echo "  BASE_URL=http://<cvm-ip>:3200 $0" >&2
  exit 1
fi

tmp="${ROOT}/.runtime"
mkdir -p "$tmp"
tar_path="${tmp}/portal-pr-regression-baby.tar"

"$ENGINE" build -f "${ROOT}/Containerfile.baby" -t portal-pr-regression-baby:latest "${ROOT}"
rm -f "$tar_path"
"$ENGINE" save portal-pr-regression-baby:latest -o "$tar_path"

curl -fsS "$BASE_URL/status" | jq -e '.ok == true and .diskroot.write == true' >/dev/null
curl -fsS -X POST --data-binary "@${tar_path}" "$BASE_URL/baby/upload" | jq -e '.image_id | startswith("sha256:")' >/dev/null
curl -fsS -X POST "$BASE_URL/baby/create" | jq -e '.instance.instance_id == "regression-1"' >/dev/null

deadline=$(( $(date +%s) + ${POLL_TIMEOUT_SECONDS:-180} ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS "$BASE_URL/baby/status" | jq -e '.ok == true' >/dev/null; then
    echo "portal-pr-regression-smoke passed"
    exit 0
  fi
  sleep 5
done

echo "Timed out waiting for baby chroot/storage logs" >&2
curl -fsS "$BASE_URL/baby/status" || true
exit 1
