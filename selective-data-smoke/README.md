# selective-data-smoke

Small workload for manifest v4 selective measured and unmeasured data mounts.

The workload service receives only:

- `measured-data/data/public.txt`
- `unmeasured-data/runtime/public.env`

The `sidecar` dependency receives only:

- `measured-data/data/private.txt`
- `unmeasured-data/runtime/private.env`

Build with an atakit-ng checkout that emits manifest format 4:

```sh
atakit workload build -d cvm-workload-examples/selective-data-smoke
```
