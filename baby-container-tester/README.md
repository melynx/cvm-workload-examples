# Baby-Container Tester

`baby-container-tester:v0.2.0` is a general dashboard for testing runtime
baby-container images inside a Confidential VM. It replaces the older
`baby-container-dynamic-update` forex demo.

The same workload source runs on Intel TDX and AMD SEV-SNP. The dashboard reads
the live cloud, machine, TEE, attestation, chain, verification backend, prover,
workload identity, and session identity from the Atakit portal. It does not
hard-code those values.

The dashboard can:

- upload an OCI or Docker image archive;
- create one isolated baby-container;
- show image, instance, container, and log state;
- stop and remove the instance;
- remove the uploaded image;
- report the measured workload payload size from the live file; and
- report a baby-container payload size from a structured log record.

The public dashboard has no authentication. Use it only on a test deployment or
restrict port `3000` at the cloud firewall.

## Environment detection

The dashboard reads `GET /portal-external-api/status` through
`/run/atakit-portal.sock`. This makes these fields dynamic:

- cloud and machine type;
- Intel TDX or AMD SEV-SNP;
- hardware or mock attestation;
- chain registration and session state;
- `auto`, `solidity`, or `zk` verification;
- prover backend and execution mode; and
- workload reference, workload ID, and session ID.

The portal still enforces valid combinations. AMD SEV-SNP requires the ZK path.
Intel TDX can use Solidity or ZK. With `tee_backend = "auto"`, Atakit resolves
the backend for the current TEE and the dashboard shows the result.

## Generate the measured workload payload

`payload.bin` is generated and ignored by Git. The default is
`1,153,433,600` bytes, so the measured workload test payload is larger than
1 GiB. The generator uses deterministic bytes so two builds with the same
source and size produce the same payload.

```sh
cd baby-container-tester
./scripts/generate-workload-payload.py
atakit workload build -d .
```

Choose another workload payload size with `--bytes`. Pass `--force` when
replacing a payload whose size differs:

```sh
./scripts/generate-workload-payload.py --bytes 2147483648 --force
```

The dashboard reports the live size of `/app/payload.bin`. This value is not
the compressed `.atawl` file size.

## Build the sample baby-container

The included sample creates a sparse payload file and reports its logical byte
size as JSON. Its default is exactly 1 GiB:

```sh
./scripts/build-sample-baby.sh
```

Set `BABY_PAYLOAD_BYTES` to test another size:

```sh
BABY_PAYLOAD_BYTES=536870912 ./scripts/build-sample-baby.sh
```

The archive is written under `dist/`. A compatible custom image can use the
same protocol by writing one JSON object to standard output:

```json
{"kind":"my-baby.started","payload_bytes":1073741824}
```

An image that does not write this record still works. The dashboard shows its
archive size, image ID, runtime state, container ID, and raw logs, but leaves
the reported logical payload size unknown.

## Deploy

Build the workload first, then deploy its generated `.atawl` or deploy from the
source directory. For example:

```sh
atakit cloud deploy -d . \
  --target gcp-c3-standard-4 \
  --name baby-container-tester-tdx \
  --chain hoodi \
  --yes
```

For GCP AMD SEV-SNP, use an `n2d` target and a chain profile with
`tee_backend = "zk"` plus a configured prover:

```sh
atakit cloud deploy -d . \
  --target gcp-n2d-standard-4 \
  --name baby-container-tester-sevsnp \
  --chain hoodi \
  --yes
```

Open `http://<cvm-ip>:3000/` after the workload reaches `Running`.

## Run the sample test

Use the dashboard, or run:

```sh
BASE_URL=http://<cvm-ip>:3000 ./scripts/e2e-dashboard.sh
```

The script builds the sample image, uploads its raw archive, creates the
baby-container, reads `/api/state`, and fails unless the dashboard reports the
requested logical payload size.

The same flow can be run by hand:

```sh
BASE_URL=http://<cvm-ip>:3000
ARCHIVE=dist/baby-container-tester-sample-1073741824.tar

curl -fsS -X POST \
  -H 'content-type: application/octet-stream' \
  --data-binary "@${ARCHIVE}" \
  "${BASE_URL}/api/upload"

curl -fsS -X POST \
  -H 'content-type: application/json' \
  -d '{}' \
  "${BASE_URL}/api/create"

curl -fsS "${BASE_URL}/api/state"
```

Do not use `curl -F`. Multipart form boundaries would become part of the image
archive. The dashboard and the examples above send raw archive bytes.

## API

- `GET /health` returns service health and measured workload payload bytes.
- `GET /api/state` returns live portal, image, instance, payload, and log state.
- `POST /api/upload` accepts a raw OCI or Docker archive up to 1 GiB.
- `POST /api/create` creates one instance from the uploaded image.
- `POST /api/stop` stops the current instance.
- `POST /api/remove` removes the current instance.
- `POST /api/image/remove` removes the uploaded image.

## Local tests

```sh
python3 -m unittest -v test_server.py
```

## Cleanup

Use the Atakit deployment lifecycle command that owns the VM:

```sh
atakit cloud destroy baby-container-tester-tdx --yes
atakit cloud destroy baby-container-tester-sevsnp --yes
```
