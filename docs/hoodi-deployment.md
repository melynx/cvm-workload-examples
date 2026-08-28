# Hoodi Deployment Guide

This guide deploys the published workload examples with the published
`automata-linux:v0.3.0-debug` base image on Hoodi.

The current published base image is:

- Base image: `automata-linux:v0.3.0-debug`
- Hoodi base image ID:
  `0x2fdcb9f3ffdcaaf693739c5bdec264f8f5d2404d7a268bf407e62ef9a2439445`
- Published workload repository: `melynx/cvm-workload-examples`

The deployment flow below follows the GCP TDX `c3-standard-4` path previously
validated on Hoodi.

The live `automata-linux:v0.3.0-debug` variants are:

| Platform | Variant |
| --- | --- |
| `gcp-tdx` | `c3-standard-4` |
| `gcp-sev-snp` | `n2d-standard-4` |
| `azure-tdx` | `Standard_DC2es_v6` |
| `azure-sev-snp` | `Standard_DC2as_v5` |

The eight releases other than `storage-ip-env-smoke:v0.1.3` whitelist only
`automata-linux:v0.3.0-debug`. The existing
`storage-ip-env-smoke:v0.1.3` release keeps an empty blacklist and permits
`automata-linux:v0.3.0-debug`.

## Configure atakit

Add the base image repository, workload repository, Hoodi contracts, keys, and
GCP target to `~/.config/atakit/config.toml`:

```toml
[image.repositories]
automata = { repo = "automata-network/automata-linux" }

[workload.repositories]
examples = { type = "github", repo = "melynx/cvm-workload-examples" }

[chains.hoodi]
rpc_url = "https://ethereum-hoodi-rpc.publicnode.com"
chain_id = 560048
session_registry = "0x7575BceC155b272077C87aD3Ae5Ef54Cf9DC6601"
workload_registry = "0x3a2E3D05cAAb7261e97F27ceDdffFbC9eEEe6b0D"
base_image_registry = "0x6b837e4Cc2A7BDaA1d938CEE7fa0b82458dA0b14"
tee_backend = "auto"

[owner_operations]
op_expiry_seconds = 3600

[keys.owner]
type = "es256k"
mode = "provisioned"
file = "~/.config/atakit/owner_key"

[keys.gas]
type = "es256k"
mode = "provisioned"
file = "~/.config/atakit/gas_key"

[publish]
chain = "hoodi"
owner_key = "owner"
relay_key = "gas"

[cloud.defaults]
chain = "hoodi"
registration = "required"
owner_key = "owner"
gas_wallet = "gas"
image = "automata-linux:v0.3.0-debug"

[cloud.providers.gcp-tdx]
platform = "gcp"
project = "<gcp-project-id>"
region = "asia-southeast1-b"

[cloud.targets.gcp-c3-standard-4]
provider = "gcp-tdx"
vmtype = "c3-standard-4"

[cloud.targets.gcp-c3-standard-4.metadata]
serial-port-enable = "true"
```

## Pull published artifacts

```sh
atakit image pull automata-linux:v0.3.0-debug gcp

atakit workload pull baby-container-tester:v0.2.0 --verify
atakit workload pull fedora-oci:v0.0.17 --verify
atakit workload pull iperf-benchmark:v0.1.3 --verify
atakit workload pull multi-container-example:v0.5.5 --verify
atakit workload pull peer-attestation-demo:v0.0.5 --verify
atakit workload pull portal-pr-regression-smoke:v0.1.3 --verify
atakit workload pull remote-log-smoke:v0.1.3 --verify
atakit workload pull selective-data-smoke:v0.1.3 --verify
atakit workload pull storage-ip-env-smoke:v0.1.3 --verify
```

## Deploy standalone examples

```sh
atakit cloud deploy fedora-oci:v0.0.17 \
  --target gcp-c3-standard-4 \
  --name fedora-oci-demo \
  --yes

atakit cloud deploy multi-container-example:v0.5.5 \
  --target gcp-c3-standard-4 \
  --name multi-container-demo \
  --yes

atakit cloud deploy baby-container-tester:v0.2.0 \
  --target gcp-c3-standard-4 \
  --name baby-container-tester-demo \
  --yes
```

Collect public IPs:

```sh
atakit cloud status fedora-oci-demo --live
atakit cloud status multi-container-demo --live
atakit cloud status baby-container-tester-demo --live
```

## Deploy peer attestation

`peer-attestation-demo` needs one unmeasured `peer-config.json` per instance.

```sh
mkdir -p peer-alpha peer-beta
cat > peer-alpha/peer-config.json <<EOF
{"node_name":"alpha"}
EOF

atakit cloud deploy peer-attestation-demo:v0.0.5 \
  --target gcp-c3-standard-4 \
  --name peer-demo-alpha \
  --unmeasured-data-root peer-alpha \
  --yes

atakit cloud status peer-demo-alpha --live
```

Use the alpha public IP in beta's config:

```sh
cat > peer-beta/peer-config.json <<EOF
{"node_name":"beta","peer_addr":"<alpha-ip>:4000"}
EOF

atakit cloud deploy peer-attestation-demo:v0.0.5 \
  --target gcp-c3-standard-4 \
  --name peer-demo-beta \
  --unmeasured-data-root peer-beta \
  --yes
```

Open both dashboards:

```text
http://<peer-alpha-ip>:3000/
http://<peer-beta-ip>:3000/
```

## Cleanup

```sh
atakit cloud destroy fedora-oci-demo --yes
atakit cloud destroy multi-container-demo --yes
atakit cloud destroy baby-container-tester-demo --yes
atakit cloud destroy peer-demo-alpha peer-demo-beta --yes
atakit cloud ls
```

The destroy commands remove the deployments, firewalls, and workload disks.
They do not remove the reusable imported cloud image
`automata-linux-v0-3-0-debug`.
