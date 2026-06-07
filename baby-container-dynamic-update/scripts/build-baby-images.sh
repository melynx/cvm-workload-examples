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

for version in v1 v2; do
  image="atakit-baby-forex:${version}"
  context="${ROOT}/baby-${version}"
  tar="${DIST}/baby-forex-${version}.tar"
  "$ENGINE" build -t "$image" -f "${context}/Containerfile" "$context"
  "$ENGINE" save -o "$tar" "$image"
  echo "$tar"
done
