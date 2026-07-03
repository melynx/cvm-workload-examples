# storage-ip-env-smoke

Small e2e workload for manifest v3 storage and IP environment support.

It checks:

- workload storage subpaths, including a read-only mount
- dependency storage subpaths
- measured `env-file`
- runtime `unmeasured-env-file`
- nested `unmeasured-data` tree mounting
- `ip-env = true` on the workload and dependency containers
- baby-container slot storage policy and `ip-env`

After deployment, query:

```sh
curl -fsS http://<public-ip>:3100/status
```

To exercise the baby-container path, build and upload `Containerfile.baby`,
then create the configured `smoke-worker` instance:

```sh
podman build -f Containerfile.baby -t storage-ip-env-smoke-baby:latest .
podman save storage-ip-env-smoke-baby:latest -o /tmp/storage-ip-env-smoke-baby.tar
curl -fsS -X POST --data-binary @/tmp/storage-ip-env-smoke-baby.tar \
  http://<public-ip>:3100/baby/upload
curl -fsS -X POST http://<public-ip>:3100/baby/create
curl -fsS http://<public-ip>:3100/baby/status
```
