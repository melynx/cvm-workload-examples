# storage-ip-env-smoke

Small end-to-end workload for manifest v5 storage and IP environment support.

Published version: `storage-ip-env-smoke:v0.1.2`.

Its empty blacklist permits `automata-linux:v0.2.7-debug` and other base
images that are not explicitly denied.

It checks:

- workload storage subpaths, including a read-only mount
- dependency storage subpaths
- measured `env-file`
- runtime `unmeasured-env-file`
- nested `unmeasured-data` tree mounting
- non-empty, valid `ATAKIT_PUBLIC_IP` and `ATAKIT_INTERNAL_IP` values
- identical IP values in the main workload and dependency containers
- `ip-env = true` on the workload and dependency containers
- baby-container slot storage policy and `ip-env`

After deployment, query:

```sh
curl -fsS http://<public-ip>:3100/status
```

To exercise the baby-container path, build and upload `Containerfile.baby`,
then create the configured `smoke-worker` instance:

```sh
mkdir -p "$HOME/tmp/storage-ip-env-smoke"
podman build -f Containerfile.baby -t storage-ip-env-smoke-baby:latest .
podman save storage-ip-env-smoke-baby:latest \
  -o "$HOME/tmp/storage-ip-env-smoke/storage-ip-env-smoke-baby.tar"
curl -fsS -X POST \
  --data-binary @"$HOME/tmp/storage-ip-env-smoke/storage-ip-env-smoke-baby.tar" \
  http://<public-ip>:3100/baby/upload
curl -fsS -X POST http://<public-ip>:3100/baby/create
curl -fsS http://<public-ip>:3100/baby/status
```
