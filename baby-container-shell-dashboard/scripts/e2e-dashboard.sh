#!/usr/bin/env sh
set -eu

BASE_URL="${BASE_URL:-}"
CVM_HOST="${CVM_HOST:-}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ARCHIVE="${BABY_IMAGE_ARCHIVE:-${ROOT}/dist/baby-shell-v0.1.0.tar}"

if [ -z "$BASE_URL" ]; then
  echo "Set BASE_URL to http://<cvm-ip>:3000" >&2
  exit 1
fi

if [ ! -f "$ARCHIVE" ]; then
  "${ROOT}/scripts/build-baby-image.sh"
fi

echo "Checking the dashboard"
curl -fsS "${BASE_URL}/api/health"
echo

echo "Uploading ${ARCHIVE}"
gzip -c "$ARCHIVE" | curl -fsS -X POST \
  -H 'content-encoding: gzip' \
  --data-binary @- \
  "${BASE_URL}/api/upload"
echo

echo "Starting ssh-shell-1"
curl -fsS -X POST -H 'content-type: application/json' -d '{}' \
  "${BASE_URL}/api/create"
echo

attempt=0
while [ "$attempt" -lt 30 ]; do
  if curl -fsS -X POST -H 'content-type: application/json' \
    -d '{"suite":"normal"}' "${BASE_URL}/api/command"; then
    echo
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done
if [ "$attempt" -eq 30 ]; then
  echo "The normal-user checks did not pass" >&2
  exit 1
fi

echo "Running the root-user checks"
curl -fsS -X POST -H 'content-type: application/json' \
  -d '{"suite":"root"}' "${BASE_URL}/api/command"
echo

if [ -n "$CVM_HOST" ]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "sshpass is required when CVM_HOST is set" >&2
    exit 1
  fi
  export SSHPASS=atakit-test
  ssh_common="-p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
  echo "Running a direct SSH command as tester"
  # shellcheck disable=SC2086
  sshpass -e ssh $ssh_common "tester@${CVM_HOST}" 'id; whoami; touch /tmp/direct-tester-check'
  echo "Running a direct SSH command as root"
  # shellcheck disable=SC2086
  sshpass -e ssh $ssh_common "root@${CVM_HOST}" 'id; whoami; test -r /etc/shadow; touch /tmp/direct-root-check'
fi

echo "Stopping ssh-shell-1"
curl -fsS -X POST -H 'content-type: application/json' -d '{}' \
  "${BASE_URL}/api/stop"
echo
echo "Removing ssh-shell-1"
curl -fsS -X POST -H 'content-type: application/json' -d '{}' \
  "${BASE_URL}/api/remove"
echo
echo "Final dashboard state"
curl -fsS "${BASE_URL}/api/state"
echo
