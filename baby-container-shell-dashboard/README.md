# baby-container-shell-dashboard

`baby-container-shell-dashboard:v0.1.0` is a test-only workload for exercising
interactive baby containers. The measured parent service exposes a web
dashboard. The dashboard uploads and controls an SSH-enabled baby-container
image through `/run/atakit-portal.sock`.

This workload only accepts `automata-linux:v0.2.8-debug`.

## Test credentials

The baby container uses fixed credentials so a tester can log in without
creating a key:

- normal user: `tester`
- root user: `root`
- password for both users: `atakit-test`
- SSH port: `2222`

This is unsafe for production. Anyone who can reach port `2222` can log in as
`tester` or `root`.

The Fedora base image normally sets `/etc/shadow` to mode `0000`. This test
image sets `/etc/shadow` and `/etc/gshadow` to mode `0400` so OpenSSH can read
the fixed password hashes after the portal drops every capability except
`CHOWN`, `SETUID`, `SETGID`, and `SYS_CHROOT`. Only user-namespace root can read
those files.

The `root` account is root inside the baby container's user namespace. It is
not root in the confidential virtual machine or on the cloud host.

## Architecture

```text
browser (:3000)
    |
    | upload, create, list, logs, stop, remove
    v
dashboard parent service
    |
    | /run/atakit-portal.sock
    v
atakit-portal
    |
    | creates with CHOWN, SETUID, SETGID, and SYS_CHROOT
    v
SSH baby container (:2222)
    |
    +-- tester shell
    +-- root shell
```

The dashboard runs its command checks over SSH to `127.0.0.1:2222`. This tests
the same OpenSSH service that a remote tester reaches through the confidential
virtual machine's port `2222`.

The workload opts in to the portal-provided network environment. The dashboard
uses `ATAKIT_PUBLIC_IP` to print complete normal-user and root-user SSH
commands. If the platform cannot supply a public IP, the dashboard prints
`public IP unavailable` instead.

The baby container shares the parent service network namespace. The portal
refuses `NET_ADMIN` and `NET_RAW` for baby containers. The baby root filesystem
is read-only. A declared baby-container storage mount supplies writable `/tmp`.

## Build

From this directory:

```bash
atakit workload build -d .
./scripts/build-baby-image.sh
```

The second command creates:

```text
dist/baby-shell-v0.1.0.tar
```

The baby image includes OpenSSH, Bash, sudo, coreutils, procps, iproute, curl,
jq, Python, findutils, and util-linux.

The dashboard image pins the Linux `amd64` Python 3.12 Alpine manifest digest.
The baby image pins the Linux `amd64` Fedora 42 manifest digest. The test
targets the `amd64` Google Cloud confidential virtual machine used by this
campaign.

## Use the dashboard

Open:

```text
http://<cvm-ip>:3000/
```

Then:

1. Upload `dist/baby-shell-v0.1.0.tar`.
2. Click **Start baby container**.
3. Run the fixed normal-user checks.
4. Run the fixed root-user checks.
5. Run custom commands as either `tester` or `root`.
6. Use **Stop** and **Remove** to end the baby container.

The fixed normal-user checks create a file in `/tmp`, run Python and jq, call
the dashboard health endpoint, confirm that `/etc/shadow` is unreadable, and
use `sudo -n id -u` to switch to root.

The fixed root-user checks read `/etc/shadow`, create a file in `/tmp`, change
its owner to `tester`, switch to `tester`, inspect the network and processes,
and confirm the remaining capability limits: the missing `NET_ADMIN` and
`NET_RAW` capabilities reject a new network interface and a raw socket, and
the read-only root filesystem rejects a write under `/etc`.

## Use SSH directly

```bash
ssh -t -p 2222 tester@<ATAKIT_PUBLIC_IP> bash -i
ssh -t -p 2222 root@<ATAKIT_PUBLIC_IP> bash -i
```

Enter `atakit-test` when OpenSSH asks for the password.

The dashboard requests `CHOWN` when it starts the baby container. OpenSSH can
therefore assign a pseudo-terminal to `tester` or `root`. The debugging commands
use `ssh -t`, so Bash has normal terminal job control.

## Dashboard API

- `GET /` returns the dashboard.
- `GET /api/health` returns the parent service health.
- `GET /api/state` returns staged images, instances, and logs.
- `POST /api/upload` uploads a Docker archive tar. Gzip is accepted with
  `content-encoding: gzip`.
- `POST /api/create` starts fixed instance `ssh-shell-1` with `CHOWN`, `SETUID`,
  `SETGID`, and `SYS_CHROOT`.
- `POST /api/command` runs a fixed check suite or a custom SSH command.
- `POST /api/stop` stops `ssh-shell-1`.
- `POST /api/remove` removes `ssh-shell-1`.

The included end-to-end script drives the same API:

```bash
BASE_URL=http://<cvm-ip>:3000 \
CVM_HOST=<cvm-ip> \
./scripts/e2e-dashboard.sh
```

`CVM_HOST` is optional. When it is present, the script also makes direct SSH
connections as `tester` and `root`.

## Cleanup

Remove the baby container from the dashboard before destroying the cloud
deployment with the normal `atakit cloud destroy` command.
