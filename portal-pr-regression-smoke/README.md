# portal-pr-regression-smoke

Workload for the atakit-portal PR regression path around writable storage chmod
fallback and baby-container `SYS_CHROOT`.

It validates:

- writable service storage mounted from the data disk root (`base-path = "/"`);
- baby-container create with `cap_add = ["SYS_CHROOT"]`;
- baby-container writable storage; and
- baby-container logs showing `chroot_ok` and `storage_ok`.

Build and deploy:

```sh
atakit workload build -d .

atakit cloud deploy -d . \
  --target gcp-c3-standard-4 \
  --name portal-pr-regression-smoke \
  --yes
```

After deployment:

```sh
BASE_URL=http://<public-ip>:3200 ./scripts/e2e.sh
```
