# selective-data-smoke

Small workload for manifest v4 selective measured and unmeasured data mounts.

The workload service receives only:

- `measured-data/data/public.txt`
- `unmeasured-data/runtime/public.env`
- `unmeasured-data/myconfig.env`
- `unmeasured-data/second_level/something.txt`

The `sidecar` dependency receives only:

- `measured-data/data/private.txt`
- `unmeasured-data/runtime/private.env`

The source package uses logical data paths rooted at:

- `measured-data/`
- `unmeasured-data/`

Build with an atakit-ng checkout that emits manifest format 4:

```sh
atakit workload build -d cvm-workload-examples/selective-data-smoke
```
