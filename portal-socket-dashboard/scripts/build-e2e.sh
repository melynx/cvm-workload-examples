#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --gcp-ak-root-cert PATH [--measurement-pack PATH] [--valid-for DURATION]"
  echo
  echo "Build the portal socket dashboard archive and both trust packs."
}

gcp_ak_root_cert=""
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

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
example_dir="$(CDPATH= cd -- "$script_dir/.." && pwd)"
runtime_dir="$example_dir/.runtime"
build_dir="$runtime_dir/build"
pack_input_dir="$runtime_dir/pack-inputs"
unmeasured_dir="$runtime_dir/unmeasured"
workload_archive="$build_dir/portal-socket-dashboard-v0.3.0.atawl"
expected_public_key="0x04685f12ea338abcb3e42a407dcdc9f498300e5b93a7f7f6b9b7a13699ec07193438eccd5f978267445942f5398b81886b533adc082615de29807d73dc728a7af8"

mkdir -p "$build_dir" "$pack_input_dir" "$unmeasured_dir"

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
  --dir "$example_dir" \
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
