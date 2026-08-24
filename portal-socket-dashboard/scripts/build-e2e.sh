#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --gcp-ak-root-cert PATH --chain-rpc-url URL --chain-id ID --session-registry ADDRESS [--measurement-pack PATH] [--valid-for DURATION]"
  echo
  echo "Build the portal socket dashboard archive and both trust packs."
}

gcp_ak_root_cert=""
chain_rpc_url=""
chain_id=""
session_registry=""
measurement_pack=""
valid_for="30d"
workload_signing_key="${WORKLOAD_SIGNING_KEY:-owner}"
collateral_signing_key="${COLLATERAL_SIGNING_KEY:-owner}"
atakit_bin="${ATAKIT_BIN:-atakit}"
atakit_imgbuild_bin="${ATAKIT_IMGBUILD_BIN:-atakit-imgbuild}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gcp-ak-root-cert)
      gcp_ak_root_cert="$2"
      shift 2
      ;;
    --chain-rpc-url)
      chain_rpc_url="$2"
      shift 2
      ;;
    --chain-id)
      chain_id="$2"
      shift 2
      ;;
    --session-registry)
      session_registry="$2"
      shift 2
      ;;
    --measurement-pack)
      measurement_pack="$2"
      shift 2
      ;;
    --valid-for)
      valid_for="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$gcp_ak_root_cert" || ! -f "$gcp_ak_root_cert" ]]; then
  echo "--gcp-ak-root-cert must name an existing GCP vTPM AK root certificate" >&2
  exit 2
fi
if [[ ! "$chain_rpc_url" =~ ^https?://[^[:space:]]+$ ]]; then
  echo "--chain-rpc-url must be an HTTP or HTTPS URL" >&2
  exit 2
fi
if [[ ! "$chain_id" =~ ^[1-9][0-9]*$ ]]; then
  echo "--chain-id must be a positive integer" >&2
  exit 2
fi
if [[ ! "$session_registry" =~ ^0x[[:xdigit:]]{40}$ ]]; then
  echo "--session-registry must be a canonical 20-byte 0x hexadecimal address" >&2
  exit 2
fi

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
example_dir="$(CDPATH= cd -- "$script_dir/.." && pwd)"
runtime_dir="$example_dir/.runtime"
build_dir="$runtime_dir/build"
pack_input_dir="$runtime_dir/pack-inputs"
unmeasured_dir="$runtime_dir/unmeasured"
workload_source_dir="$runtime_dir/workload-source"
workload_archive="$build_dir/portal-socket-dashboard-v0.3.0.atawl"
expected_public_key="0x04685f12ea338abcb3e42a407dcdc9f498300e5b93a7f7f6b9b7a13699ec07193438eccd5f978267445942f5398b81886b533adc082615de29807d73dc728a7af8"

mkdir -p "$build_dir" "$pack_input_dir" "$unmeasured_dir"

# The fork registry address can change after a clean reset. Build from a staged
# copy so every archive measures the current chain coordinates without changing
# the checked-in manifest.
mkdir -p "$workload_source_dir"
rsync -a --delete --exclude '/.runtime/' "$example_dir/" "$workload_source_dir/"
manifest="$workload_source_dir/atakit-workload.toml"
rendered_manifest="$manifest.rendered"
awk \
  -v rpc_url="$chain_rpc_url" \
  -v chain_id="$chain_id" \
  -v session_registry="$session_registry" \
  '
    $1 == "CHAIN_AUTHORITY_RPC_URL" { print $1 " = \"" rpc_url "\""; next }
    $1 == "CHAIN_AUTHORITY_CHAIN_ID" { print $1 " = \"" chain_id "\""; next }
    $1 == "CHAIN_AUTHORITY_SESSION_REGISTRY" { print $1 " = \"" session_registry "\""; next }
    $1 == "VERIFIERD_RPC_URL" { print $1 " = \"" rpc_url "\""; next }
    $1 == "VERIFIERD_CHAIN_ID" { print $1 " = \"" chain_id "\""; next }
    $1 == "VERIFIERD_SESSION_REGISTRY" { print $1 " = \"" session_registry "\""; next }
    { print }
  ' "$manifest" >"$rendered_manifest"
mv "$rendered_manifest" "$manifest"

require_rendered_line() {
  local expected="$1"
  local expected_count="$2"
  local actual_count
  actual_count="$(grep -Fxc "$expected" "$manifest" || true)"
  if [[ "$actual_count" != "$expected_count" ]]; then
    echo "Expected $expected_count rendered manifest line(s), found $actual_count: $expected" >&2
    exit 1
  fi
}

require_rendered_line "CHAIN_AUTHORITY_RPC_URL = \"$chain_rpc_url\"" 1
require_rendered_line "CHAIN_AUTHORITY_CHAIN_ID = \"$chain_id\"" 1
require_rendered_line "CHAIN_AUTHORITY_SESSION_REGISTRY = \"$session_registry\"" 1
require_rendered_line "VERIFIERD_RPC_URL = \"$chain_rpc_url\"" 1
require_rendered_line "VERIFIERD_CHAIN_ID = \"$chain_id\"" 1
require_rendered_line "VERIFIERD_SESSION_REGISTRY = \"$session_registry\"" 1

key_public_value() {
  local key_name="$1"
  NO_COLOR=1 "$atakit_bin" keys show "$key_name" \
    | sed -nE 's/.*(0x04[[:xdigit:]]{128}).*/\1/p'
}

for key_name in "$workload_signing_key" "$collateral_signing_key"; do
  actual_public_key="$(key_public_value "$key_name")"
  if [[ "$actual_public_key" != "$expected_public_key" ]]; then
    echo "Key $key_name does not match the publisher public key measured in atakit-workload.toml" >&2
    echo "Expected: $expected_public_key" >&2
    echo "Actual:   ${actual_public_key:-<missing>}" >&2
    exit 1
  fi
done

if [[ -z "$measurement_pack" ]]; then
  measurement_output="$(
    NO_COLOR=1 "$atakit_imgbuild_bin" measurement-pack \
      automata-linux:v0.3.0-debug \
      --signing-key "$workload_signing_key"
  )"
  echo "$measurement_output"
  measurement_pack="$(
    echo "$measurement_output" | sed -n 's/^Measurement pack:[[:space:]]*//p' | tail -n 1
  )"
fi

if [[ -z "$measurement_pack" || ! -f "$measurement_pack" ]]; then
  echo "The measurement-pack command did not produce a readable JSON path" >&2
  exit 1
fi
if [[ ! -f "${measurement_pack%.json}.sig" || ! -f "${measurement_pack%.json}.pubkey" ]]; then
  echo "The measurement pack must have matching .sig and .pubkey files" >&2
  exit 1
fi

"$atakit_bin" workload build \
  --dir "$workload_source_dir" \
  --output "$build_dir" \
  --unmeasured-data-root "$unmeasured_dir" \
  --signing-key "$workload_signing_key" \
  --no-store

if [[ ! -f "$workload_archive" ]]; then
  echo "Workload build did not create $workload_archive" >&2
  exit 1
fi

cp "$gcp_ak_root_cert" "$pack_input_dir/gcp-ak-root.pem"
cat >"$pack_input_dir/collateral.toml" <<'EOF'
platforms = ["gcp-tdx"]
gcp_ak_root_cert = "./gcp-ak-root.pem"
EOF

"$atakit_bin" trust-pack build collateral \
  --config "$pack_input_dir/collateral.toml" \
  --issuer "portal-socket-dashboard collateral authority" \
  --valid-for "$valid_for" \
  --signing-key "$collateral_signing_key" \
  --out "$unmeasured_dir/collateral.atatp"

"$atakit_bin" trust-pack build workload \
  "$workload_archive" \
  --measurement-pack "$measurement_pack" \
  --issuer "portal-socket-dashboard workload authority" \
  --valid-for "$valid_for" \
  --signing-key "$workload_signing_key" \
  --out "$unmeasured_dir/workload.atatp"

echo
echo "E2E inputs are ready:"
echo "  Workload archive: $workload_archive"
echo "  Unmeasured data:  $unmeasured_dir"
echo "  Measurement pack: $measurement_pack"
echo
echo "Deploy with a GCP TDX target whose registration field is off:"
echo "  $atakit_bin cloud deploy '$workload_archive' --unmeasured-data-root '$unmeasured_dir' --target TARGET"
echo
echo "Pack digest pins are intentionally absent because the workload archive is an input to the workload pack."
echo "The live dashboard reports the exact loaded digests and this remaining rollback assumption."
