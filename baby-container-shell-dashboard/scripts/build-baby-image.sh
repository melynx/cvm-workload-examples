#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DIST="${ROOT}/dist"
ENGINE="${ENGINE:-}"

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
image="atakit-baby-shell:v0.1.0"
archive="${DIST}/baby-shell-v0.1.0.tar"
temporary_archive="${archive}.tmp"

"$ENGINE" build -t "$image" -f "${ROOT}/baby-shell/Containerfile" "${ROOT}/baby-shell"
rm -f "$temporary_archive"
case "${ENGINE##*/}" in
  podman)
    "$ENGINE" save --format docker-archive -o "$temporary_archive" "$image"
    ;;
  *)
    "$ENGINE" save -o "$temporary_archive" "$image"
    ;;
esac
mv "$temporary_archive" "$archive"
sha256sum "$archive"
