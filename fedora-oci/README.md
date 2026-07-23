# fedora-oci

A Fedora-based OCI container with SSH and a broad set of debugging / networking
tools, packaged as an atakit workload. Useful as a shell-in environment for
poking around inside a CVM.

Published version: `fedora-oci:v0.0.15`.

This version runs only with `automata-linux:v0.2.7-debug`.

Use this workload when you need a debug container inside a deployed CVM. The
base image itself is minimal and does not provide SSH; this workload exposes SSH
from the Fedora container on host port `2200`.

## What's inside

Built from `docker.io/library/fedora:latest` plus:

- **SSH server** (`openssh-server`, running as `sshd -D` on container port 22)
- **Networking**: `iproute`, `iputils`, `bind-utils`, `net-tools`, `nmap-ncat`,
  `traceroute`, `whois`, `tcpdump`, `socat`, `mtr`, `wscat`
- **Diagnostics**: `htop`, `btop`, `lsof`, `strace`, `ltrace`, `procps-ng`,
  `file`, `tree`
- **General**: `vim-enhanced`, `tmux`, `less`, `jq`, `curl`, `wget`, `httpie`,
  `rsync`, `tar`/`gzip`/`xz`, `openssl`, `nodejs`/`npm`, `bash-completion`,
  `man-db`

## Login

A single non-root user is provisioned in the image:

| Field    | Value  |
|----------|--------|
| Username | `user` |
| Password | `user` |
| sudo     | `NOPASSWD: ALL` |

Host SSH keys were baked into the archive at build time via `ssh-keygen -A`, so
every instance launched from this published archive shares the same host keys.

## Ports

| Host | Container | Purpose |
|------|-----------|---------|
| `2200/tcp` | `22/tcp` | SSH |
| `8080/udp` | `8080/udp` | Reserved (no service bound by default) |

Extra firewall rules opened beyond the port-mapped set (see
`atakit-workload.toml`):

- `4000/tcp`
- `5000/udp`

## Workload Config

- `restart = "unless-stopped"` — sshd respawns if it exits.
- `RUST_LOG=info` — set in the container environment.
- `[baby-container] allow = true` — the workload may spawn ephemeral sidecars
  via the portal's `/baby-container/*` API.
- No measured-data, no unmeasured-data, no persistent disks, no portal socket.

## Pull And Deploy

See the [repo README](../README.md) or
[Hoodi deployment guide](../docs/hoodi-deployment.md) for one-time setup.

```bash
# Download the pre-built, on-chain-published archive into your local store.
atakit workload pull fedora-oci:v0.0.15 --verify

# Deploy to a configured Hoodi cloud target.
atakit cloud deploy fedora-oci:v0.0.15 \
  --target gcp-c3-standard-4 \
  --name fedora-oci-demo \
  --yes

# Get the external IP.
atakit cloud status fedora-oci-demo --live
```

## SSH in

Replace `${IP}` with the external IP from `atakit cloud status`. The default
SSH client config will warn about the unknown host key on first connect —
that's expected; the host keys were generated inside the build.

```bash
ssh -p 2200 user@${IP}
# password: user
```

A few useful things to try once inside:

```bash
# Confirm you're in a CVM workload
cat /etc/os-release
ip a
ss -tlnp

# Pop sudo (NOPASSWD)
sudo whoami

# Tools shipped with the image
htop
tcpdump -i any -nn icmp
jq --version
```

## Notes

- The archive is large (~200 MB) because the package list above is
  comprehensive.
- SSH uses **password auth** by default. For anything other than throwaway
  poking, provide an `authorized_keys` file at deploy time via
  `unmeasured-data` and use key-based login.
- If SSH does not complete, use `atakit cloud serial fedora-oci-demo` and
  workload logs/status first. The base image has no separate SSH path.

## Cleanup

```bash
atakit cloud destroy fedora-oci-demo --yes
```
