# Hoodi Deployment Guide

This guide deploys the published workload examples with the published
`automata-linux:v0.2.2-debug` base image on Hoodi.

Validated on 2026-06-19 with:

- Base image: `automata-linux:v0.2.2-debug`
- Hoodi base image ID:
  `0xf2291716b993b24a8a44b616a65c7088e0da54930c57f0fd08fa2920b944f609`
- GCP TDX target: `c3-standard-4`
- Published workload repository: `melynx/cvm-workload-examples`

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
session_registry = "0xB247950fBBFCE245641e433AFd7d8884328CE5A1"
workload_registry = "0xda6430E06385F7516963f8A3B4e87beBb89860F8"
base_image_registry = "0xCbe56f9B73c822679Cf36DcF8D99434E0f1588Ca"
expire_offset = 3600

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
image = "automata-linux:v0.2.2-debug"

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
atakit image pull automata-linux:v0.2.2-debug gcp

atakit workload pull fedora-oci:v0.0.13 --verify
atakit workload pull multi-container-example:v0.5.1 --verify
atakit workload pull baby-container-dynamic-update:v0.1.3 --verify
atakit workload pull peer-attestation-demo:v0.0.3 --verify
```

## Deploy standalone examples

```sh
atakit cloud deploy fedora-oci:v0.0.13 \
  --target gcp-c3-standard-4 \
  --name fedora-oci-demo \
  --yes

atakit cloud deploy multi-container-example:v0.5.1 \
  --target gcp-c3-standard-4 \
  --name multi-container-demo \
  --yes

atakit cloud deploy baby-container-dynamic-update:v0.1.3 \
  --target gcp-c3-standard-4 \
  --name baby-container-demo \
  --yes
```

Collect public IPs:

```sh
atakit cloud status fedora-oci-demo --live
atakit cloud status multi-container-demo --live
atakit cloud status baby-container-demo --live
```

## Deploy peer attestation

`peer-attestation-demo` needs one unmeasured `peer-config.json` per instance.

```sh
mkdir -p peer-alpha peer-beta
cat > peer-alpha/peer-config.json <<EOF
{"node_name":"alpha"}
EOF

atakit cloud deploy peer-attestation-demo:v0.0.3 \
  --target gcp-c3-standard-4 \
  --name peer-demo-alpha \
  --unmeasured-data-dir peer-alpha \
  --yes

atakit cloud status peer-demo-alpha --live
```

Use the alpha public IP in beta's config:

```sh
cat > peer-beta/peer-config.json <<EOF
{"node_name":"beta","peer_addr":"<alpha-ip>:4000"}
EOF

atakit cloud deploy peer-attestation-demo:v0.0.3 \
  --target gcp-c3-standard-4 \
  --name peer-demo-beta \
  --unmeasured-data-dir peer-beta \
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
atakit cloud destroy baby-container-demo --yes
atakit cloud destroy peer-demo-alpha peer-demo-beta --yes
atakit cloud ls
```

The destroy commands remove the deployments, firewalls, and workload disks.
They do not remove the reusable imported cloud image
`automata-linux-v0-2-2-debug`.
