#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DIST="${ROOT}/dist"
ENGINE="${ENGINE:-}"
BABY_PAYLOAD_BYTES="${BABY_PAYLOAD_BYTES:-1073741824}"
IMAGE="baby-container-tester-sample:${BABY_PAYLOAD_BYTES}"
ARCHIVE="${DIST}/baby-container-tester-sample-${BABY_PAYLOAD_BYTES}.tar"
TEMPORARY="${ARCHIVE}.tmp"

if [ -z "$ENGINE" ]; then
  if command -v podman >/dev/null 2>&1; then
    ENGINE=podman
  elif command -v docker >/dev/null 2>&1; then
    ENGINE=docker
  else
    echo "podman or docker is required" >&2
    exit 1
  fi
fi

mkdir -p "$DIST"
"$ENGINE" build \
  --build-arg "BABY_PAYLOAD_BYTES=${BABY_PAYLOAD_BYTES}" \
  -t "$IMAGE" \
  -f "${ROOT}/baby-src/Containerfile" \
  "${ROOT}/baby-src"
rm -f "$TEMPORARY"
case "${ENGINE##*/}" in
  podman)
    "$ENGINE" save --format oci-archive -o "$TEMPORARY" "$IMAGE"
    ;;
  *)
    "$ENGINE" image save -o "$TEMPORARY" "$IMAGE"
    ;;
esac
mv "$TEMPORARY" "$ARCHIVE"
echo "$ARCHIVE"
