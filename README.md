# CVM Workload Examples

Ready-to-run workloads for Confidential VMs. Published examples are built,
registered on Hoodi, and attached to this repo's GitHub Releases, so you can
pull and deploy them without rebuilding. Local smoke and regression examples can
be built from source with `atakit workload build`.

| Example | Checkout version | What it demonstrates |
| --- | --- | --- |
| [fedora-oci](fedora-oci/) | `v0.0.17` | Fedora shell-in box with SSH and debugging/networking tools |
| [multi-container-example](multi-container-example/) | `v0.5.5` | Three containers sharing a persistent disk and container network |
| [baby-container-tester](baby-container-tester/) | `v0.2.0` | Portable TDX/SEV-SNP baby-container image, lifecycle, and payload-size dashboard |
| [peer-attestation-demo](peer-attestation-demo/) | `v0.0.6` source | Two CVMs verify current sessions through `atakit-verifierd`; its encrypted-message framing is not a secure channel |
| [iperf-benchmark](iperf-benchmark/) | `v0.1.3` | Minimal iperf3 server for TCP/UDP throughput testing |
| [remote-log-smoke](remote-log-smoke/) | `v0.1.3` | Remote log collection through a Fluent Bit sidecar |
| [storage-ip-env-smoke](storage-ip-env-smoke/) | `v0.1.3` | Data-disk, non-empty IP, environment, and baby-container storage smoke test |
| [selective-data-smoke](selective-data-smoke/) | `v0.1.3` | Manifest v5 selective measured and unmeasured data mounts |
| [portal-pr-regression-smoke](portal-pr-regression-smoke/) | `v0.1.3` | Regression coverage for portal baby-container capability and storage behavior |
| [portal-socket-dashboard](portal-socket-dashboard/) | `v0.3.0` source | GCP TDX dashboard that compares Solidity-registry and signed trust-pack verifier authorities over the same portal socket evidence |

The current published base image is `automata-linux:v0.3.0-debug`. The quick
start below follows the GCP TDX `c3-standard-4` path previously validated on
Hoodi.

Published releases other than `storage-ip-env-smoke:v0.1.3` whitelist only
`automata-linux:v0.3.0-debug`. The existing
`storage-ip-env-smoke:v0.1.3` release keeps an empty blacklist and permits
`automata-linux:v0.3.0-debug`.

For a fuller deployment walkthrough, see
[docs/hoodi-deployment.md](docs/hoodi-deployment.md).

## Published Hoodi State

Current published base image:

- Image: `automata-linux:v0.3.0-debug`
- Hoodi base image ID:
  `0x2fdcb9f3ffdcaaf693739c5bdec264f8f5d2404d7a268bf407e62ef9a2439445`
- GitHub release:
  `https://github.com/automata-network/automata-linux/releases/tag/v0.3.0-debug`

Published platform profiles:

| Platform | Variants |
|----------|----------|
| `gcp-tdx` | `c3-standard-4` |
| `gcp-sev-snp` | `n2d-standard-4` |
| `azure-tdx` | `Standard_DC2es_v6` |
| `azure-sev-snp` | `Standard_DC2as_v5` |

## Prerequisites

- Rust and Cargo.
- `git`, `curl`, and either Docker or Podman.
- Google Cloud CLI authenticated for a project that can create confidential VM
  instances in the selected zone.
- A Hoodi-funded gas key and an owner key for CVM registration.

Install the atakit CLI:

```sh
git clone https://github.com/automata-network/atakit.git
cd atakit
cargo install --path crates/atakit-cli
```

Store keys as hex strings:

```sh
mkdir -p ~/.config/atakit
printf "0x<owner-private-key-hex>\n" > ~/.config/atakit/owner_key
printf "0x<gas-private-key-hex>\n" > ~/.config/atakit/gas_key
chmod 600 ~/.config/atakit/owner_key ~/.config/atakit/gas_key
```

Authenticate to GCP and select the project:

```sh
gcloud auth login
gcloud auth application-default login
gcloud config set project <gcp-project-id>
```

## Configure atakit

Add the Hoodi chain, base image repository, this workload repository, keys, and
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

## Pull artifacts

Pull the published base image:

```sh
atakit image pull automata-linux:v0.3.0-debug gcp
```

Pull and verify the published workload archives:

```sh
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

## Deploy examples

Deploy the four standalone examples:

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

atakit cloud deploy iperf-benchmark:v0.1.3 \
  --target gcp-c3-standard-4 \
  --name iperf-benchmark-demo \
  --yes
```

Deploy the peer attestation demo as two CVMs. The beta node auto-connects to the
alpha node through the address in `peer-config.json`.

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

Use the alpha public IP from `cloud status`:

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

## Exercise examples

Collect public IPs:

```sh
atakit cloud status fedora-oci-demo --live
atakit cloud status multi-container-demo --live
atakit cloud status baby-container-tester-demo --live
atakit cloud status iperf-benchmark-demo --live
atakit cloud status peer-demo-alpha --live
atakit cloud status peer-demo-beta --live
```

Fedora OCI:

```sh
ssh -p 2200 user@<fedora-ip>
# password: user
```

Multi-container dashboard and shared-disk flow:

```sh
curl http://<multi-container-ip>:3000/
curl -X POST http://<multi-container-ip>:3000/task -d "hello world"
curl http://<multi-container-ip>:3000/status
curl http://<multi-container-ip>:3000/results
curl -X POST http://<multi-container-ip>:3000/clear
```

Iperf benchmark:

```sh
iperf3 -c <iperf-ip> -p 5201
iperf3 -c <iperf-ip> -p 5201 -R
iperf3 -c <iperf-ip> -p 5201 -u -b 100M
```

Remote log smoke:

```sh
cd remote-log-smoke
python3 tools/log-receiver.py --host 0.0.0.0 --port 18080
LOG_RECEIVER_HOST=<receiver-ip-or-dns> ./scripts/e2e-remote-logs.sh
```

Deploy the workload with the runtime directory printed by the script as
`--unmeasured-data-root`, then rerun the script with the same `LOG_RUN_ID` to
poll the receiver.

Portal PR regression smoke:

```sh
atakit workload build -d cvm-workload-examples/portal-pr-regression-smoke
atakit cloud deploy -d cvm-workload-examples/portal-pr-regression-smoke \
  --target gcp-c3-standard-4 \
  --name portal-pr-regression-smoke \
  --yes

BASE_URL=http://<portal-pr-regression-ip>:3200 \
  cvm-workload-examples/portal-pr-regression-smoke/scripts/e2e.sh
```

Baby-container tester:

```sh
cd baby-container-tester
BASE_URL=http://<baby-container-ip>:3000 ./scripts/e2e-dashboard.sh
```

The default sample reports exactly `1,073,741,824` bytes. Set
`BABY_PAYLOAD_BYTES` before the command to test another logical size. The
dashboard reads the live cloud, TEE, verification backend, prover, workload,
session, image, instance, and payload fields from the running CVM.

Peer attestation demo:

```text
http://<peer-alpha-ip>:3000/
http://<peer-beta-ip>:3000/
```

If beta was deployed with `peer_addr`, it auto-connects to alpha. Otherwise,
enter `<peer-alpha-ip>:4000` or `<peer-beta-ip>:4000` in the other node's
dashboard and connect manually.

## Cleanup

Destroy deployments when done:

```sh
atakit cloud destroy fedora-oci-demo --yes
atakit cloud destroy multi-container-demo --yes
atakit cloud destroy baby-container-tester-demo --yes
atakit cloud destroy iperf-benchmark-demo --yes
atakit cloud destroy peer-demo-alpha peer-demo-beta --yes
```

Confirm the local deployment inventory is empty:

```sh
atakit cloud ls
```

The deploy flow imports the base image into the selected GCP project as
`automata-linux-v0-3-0-debug`. The cleanup commands above remove the example
deployments, firewalls, and the multi-container persistent disk; they do not
delete that reusable project image.

## Build and publish your own workload

A workload directory contains an `atakit-workload.toml` plus the files needed to
build or package the workload. Create a new workload:

```sh
atakit workload create my-service
```

Edit `my-service/atakit-workload.toml`:

```toml
format = 5

[workload]
name = "my-service"
version = "v0.0.1"
base-image-mode = "blacklist"
image = { build = ".", containerfile = "Containerfile" }
ports = ["3000:3000"]
```

Build and test:

```sh
atakit workload build -d ./my-service
atakit cloud deploy my-service:v0.0.1 --target gcp-c3-standard-4 --yes
```

For pre-publish testing, use a target or config with registration set to
optional or off. With `registration = "required"`, the CVM expects the workload
to already be registered on-chain.

Publish the workload registration and upload the archive:

```sh
atakit workload publish my-service:v0.0.1
atakit workload push my-service:v0.0.1
```

Consumers can then point atakit at your workload repository and pull the same
`name:version`.
