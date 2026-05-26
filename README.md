# CVM Workload Examples

Ready-to-run workloads for Confidential VMs. Each example
is already **built, published on-chain, and uploaded to this repo's GitHub
Releases** — so you don't need to build or publish anything. 

| Example | What it demonstrates |
|---------|----------------------|
| [fedora-oci](fedora-oci/) | Fedora shell-in box with SSH and a broad debugging/networking toolset |
| [multi-container-example](multi-container-example/) | 3 containers sharing a persistent disk + inter-container networking |
| [peer-attestation-demo](peer-attestation-demo/) | Two CVMs attest each other and talk over an encrypted channel |

The steps below are common to every example. Each example's own README only
covers how to exercise it once it's running, plus its exact workload reference
and any per-example deploy flags.

## Prerequisites

- The `atakit` CLI installed (see [atakit-ng](../atakit-ng)).
- A cloud target (GCP or Azure) configured under `[cloud.targets.*]` in
  `~/.config/atakit/config.toml`.
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
