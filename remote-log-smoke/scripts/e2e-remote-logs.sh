#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

LOG_RECEIVER_HOST="${LOG_RECEIVER_HOST:-127.0.0.1}"
LOG_RECEIVER_PORT="${LOG_RECEIVER_PORT:-18080}"
LOG_RECEIVER_URL="${LOG_RECEIVER_URL:-http://${LOG_RECEIVER_HOST}:${LOG_RECEIVER_PORT}}"
LOG_RUN_ID="${LOG_RUN_ID:-remote-log-smoke-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
POLL_TIMEOUT_SECONDS="${POLL_TIMEOUT_SECONDS:-300}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-5}"
BUILD_ONLY="${BUILD_ONLY:-0}"

RUNTIME_ROOT="${ROOT}/.runtime/${LOG_RUN_ID}"
mkdir -p "${RUNTIME_ROOT}/secrets"
cat > "${RUNTIME_ROOT}/secrets/runtime.env" <<EOF
LOG_RECEIVER_HOST=${LOG_RECEIVER_HOST}
LOG_RECEIVER_PORT=${LOG_RECEIVER_PORT}
LOG_RUN_ID=${LOG_RUN_ID}
EOF

run_atakit() {
  if [ -n "${ATAKIT:-}" ]; then
    "$ATAKIT" "$@"
  elif command -v atakit >/dev/null 2>&1; then
    atakit "$@"
  else
    echo "atakit binary not found; set ATAKIT=/path/to/atakit or install atakit" >&2
    return 127
  fi
}

if [ -n "${CONTAINER_ENGINE:-}" ]; then
  run_atakit workload build -d "${ROOT}" --no-store --engine "${CONTAINER_ENGINE}"
else
  run_atakit workload build -d "${ROOT}" --no-store
fi

ARCHIVE="${ROOT}/remote-log-smoke-v0.1.0.atawl"
MANIFEST_JSON="${ROOT}/.runtime/${LOG_RUN_ID}/manifest.json"
zstd -dc "${ARCHIVE}" | tar -xOf - remote-log-smoke/manifest.json > "${MANIFEST_JSON}"

jq -e '
  .meta.name == "remote-log-smoke" and
  .config.logging["log-readers"] == ["log-shipper"] and
  .config.dependencies.worker.logging["log-readers"] == ["log-shipper"] and
  .config.dependencies.scheduler.logging["log-readers"] == ["log-shipper"] and
  .config.dependencies.metrics.logging["log-readers"] == ["log-shipper"] and
  .config.dependencies["log-shipper"]["workload-logs"] == true and
  .config.dependencies["log-shipper"]["measured-data"] == true and
  .config.dependencies["log-shipper"]["unmeasured-data"] == true and
  .config.dependencies["log-shipper"]["unmeasured-env-files"] == ["unmeasured-data/secrets/runtime.env"]
' "${MANIFEST_JSON}" >/dev/null

echo "Built ${ARCHIVE}"
echo "Runtime config: ${RUNTIME_ROOT}/secrets/runtime.env"
echo "Run receiver on the log host:"
echo "  python3 ${ROOT}/tools/log-receiver.py --host 0.0.0.0 --port ${LOG_RECEIVER_PORT}"
echo "Deploy/init with:"
echo "  atakit cloud deploy -d ${ROOT} --unmeasured-data-dir ${RUNTIME_ROOT} --target <target> --name remote-log-smoke --yes"
echo "Run id: ${LOG_RUN_ID}"

if [ "${BUILD_ONLY}" = "1" ]; then
  exit 0
fi

deadline=$(( $(date +%s) + POLL_TIMEOUT_SECONDS ))
required_services="app worker scheduler metrics"

while [ "$(date +%s)" -lt "$deadline" ]; do
  response="$(curl -fsS "${LOG_RECEIVER_URL}/events?run_id=${LOG_RUN_ID}" || true)"
  if [ -n "$response" ]; then
    ok=1
    for service in $required_services; do
      if ! printf '%s' "$response" | jq -e --arg service "$service" '.services | index($service)' >/dev/null; then
        ok=0
      fi
    done
    if [ "$ok" = "1" ]; then
      echo "Received logs from all services for ${LOG_RUN_ID}"
      printf '%s\n' "$response" | jq '{count, services}'
      exit 0
    fi
    printf 'Waiting for services. Current receiver state: '
    printf '%s\n' "$response" | jq -c '{count, services}'
  else
    echo "Waiting for receiver at ${LOG_RECEIVER_URL}"
  fi
  sleep "$POLL_INTERVAL_SECONDS"
done

echo "Timed out waiting for logs from: ${required_services}" >&2
exit 1
