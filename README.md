# CVM Workload Examples

Ready-to-run workloads for Confidential VMs. Each example
is already **built, published on-chain, and uploaded to this repo's GitHub
Releases** — so you don't need to build or publish anything. 

| Example | What it demonstrates |
|---------|----------------------|
| [fedora-oci](fedora-oci/) | Fedora shell-in box with SSH and a broad debugging/networking toolset |
| [multi-container-example](multi-container-example/) | 3 containers sharing a persistent disk + inter-container networking |
| [peer-attestation-demo](peer-attestation-demo/) | Two CVMs attest each other and talk over an encrypted channel |
| [baby-container-dynamic-update](baby-container-dynamic-update/) | Workload-owned baby-container image upload/update dashboard |

The steps below are common to every example. Each example's own README only
covers how to exercise it once it's running, plus its exact workload reference
and any per-example deploy flags.

## Prerequisites

- The `atakit` CLI installed (see [atakit-ng](https://github.com/automata-network/atakit/tree/atakit-ng)).
- A cloud target (GCP or Azure) configured under `[cloud.targets.*]` in `~/.config/atakit/config.toml`.
- A base image available locally, e.g.:

  ```bash
  atakit image pull <image_name>:<image_version> gcp
  ```

## 1. Point atakit at this repo

These workloads live in this repo's GitHub Releases. Declare it as a GitHub
workload repository in `~/.config/atakit/config.toml`:

```toml
[workload.repositories]
examples = { type = "github", repo = "melynx/cvm-workload-examples" }
```

## 2. Browse available workloads

Each release is one workload version.

```bash
atakit workload ls --remote
```

## 3. Pull a workload

```bash
atakit workload pull <name>:<version>
```

This downloads the `.atawl` archive into your local store and automatically
verifies its integrity — including the on-chain PCR23 measurement when
`[publish]` is configured. The exact `<name>:<version>` for each example is in
its README (linked above), or from `workload ls --remote`.

## 4. Exercise it

Each example README walks through its specific test cases, please check them out.

## Build & publish your own workload

The examples above were produced with the same flow. A workload is a directory
with an `atakit-workload.toml` plus whatever it builds from (a `Containerfile`,
source, measured-data files).

### 1. Scaffold

```bash
atakit workload create my-service
```

This creates `my-service/` with a fully-commented `atakit-workload.toml`. Add
your `Containerfile` and application code alongside it.

### 2. Define the workload

Edit `my-service/atakit-workload.toml`. A minimal build-from-source workload:

```toml
format = 2

[workload]
name = "my-service"
version = "v0.0.1"
base-image-mode = "blacklist"        # deploy on any base image
image = { build = ".", containerfile = "Containerfile" }
ports = ["3000:3000"]
```

Bump `version` on every change you publish — the on-chain workload ID is
derived from `name` + `version`, so a new version is a new identity.

### 3. Build

```bash
atakit workload build -d ./my-service
```

This produces the `.atawl` archive in your local store and prints its **PCR23**
measurement — the value attestation pins. Inspect a built workload anytime:

```bash
atakit workload info my-service:v0.0.1
```

### 4. Deploy to test

Same as deploying an example (see [Prerequisites](#prerequisites) for the base
image and cloud target):

```bash
atakit cloud deploy my-service:v0.0.1 --target gcp-tdx
atakit cloud status my-service-gcp-tdx
atakit cloud destroy my-service-gcp-tdx
```


> [!NOTE]
> You will want to configure `registration=optional/off` in your atakit config for testing, otherwise the CVM will fail to start the workload, as it checks whether the workload is registered on-chain.

### 5. Publish

Publishing is two independent actions. Together they let anyone `workload pull`
your archive and have its integrity verified against the chain — exactly how the
examples in this repo work.

**a. Register the spec on-chain** so attestation can verify deployments. This
signs a transaction, so it needs the `owner`/`gas` keys and a `[publish]` chain
configured.

```bash
atakit workload publish my-service:v0.0.1
```

**b. Upload the archive** to a workload repository so others can pull it.
Declare a repo you can write to in `~/.config/atakit/config.toml` — a GitHub
Releases repo (what this repo uses) or an HTTP registry:

```toml
[workload.repositories]
mine = { type = "github", repo = "you/your-workloads", credential = "private" }
```

Then push. For a GitHub repo this creates a release tagged `my-service/v0.0.1`
with the `.atawl` attached and the workload ID in the body:

```bash
atakit workload push my-service:v0.0.1
```

Consumers point atakit at `you/your-workloads` (step 1 above) and pull with
`atakit workload pull my-service:v0.0.1`, which re-verifies the PCR23
measurement against the on-chain spec from step 5a.
