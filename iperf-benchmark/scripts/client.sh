#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <cvm-ip> [iperf3 options]" >&2
  exit 2
fi
host="$1"
shift
if command -v iperf3 >/dev/null 2>&1; then
  exec iperf3 -c "$host" -p 5201 "$@"
fi
engine="${CONTAINER_ENGINE:-podman}"
command -v "$engine" >/dev/null 2>&1 || {
  echo "Install iperf3 or $engine to run the benchmark." >&2
  exit 1
}
image="localhost/atakit-iperf-client:local"
"$engine" build -q -t "$image" -f "$ROOT/Containerfile" "$ROOT" >&2
exec "$engine" run --rm --network=host "$image" iperf3 -c "$host" -p 5201 "$@"
