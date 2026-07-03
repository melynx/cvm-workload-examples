# baby-container-dynamic-update

Demonstrates a workload-owned baby-container update flow. The measured workload
is a dashboard service that declares two baby-container slots, accepts helper
image uploads from a user, and calls the portal's workload-facing UDS from
inside the parent service.

Published version: `baby-container-dynamic-update:v0.1.3`.

## Architecture

```text
browser / curl
    |
    |  GET / dashboard
    |  POST /api/upload    Docker archive image tar
    |  POST /api/create
    v
dashboard parent service (:3000)
    |
    |  /run/atakit-portal.sock
    |  /baby-container/image/upload?slot=forex-worker
    |  /baby-container/create
    |  /baby-container/list
    |  /baby-container/logs
    v
atakit-portal
    |
    v
baby forex worker
    prints USD/SGD ticks to container logs
```

The workload, not the operator, owns the public upload/update policy. This
example keeps that policy open so it is easy to test, but a real workload would
authenticate users before accepting helper images.

## What It Demonstrates

- The measured workload commits to bounded runtime extension slots.
- Runtime helper code is uploaded after deployment as a raw image tar.
- The dashboard shows the currently staged baby image, instance status, and
  recent logs.
- Uploading `baby-forex-v2.tar` replaces `baby-forex-v1.tar` in the slot,
  demonstrating dynamic workload behavior without rebuilding the `.atawl`.
- The workload exposes both `forex-worker` and `forex-worker-alt`, so the same
  uploaded image can be exercised across multiple slots.
- Baby containers share the parent service network namespace and can reach the
  internet through the parent workload's network policy.
- The example uses `image-retention = "session"` and
  `instance-retention = "ephemeral"`: after a CVM restart, the dashboard comes
  back without staged baby images or instances.
- Uploads are spooled to a workload data disk at
  `/var/lib/baby-dashboard/uploads` before they are forwarded to the portal.
  This avoids `/tmp` tmpfs and keeps large helper image uploads out of the
  dashboard container's memory path.

## Workload Config

Important manifest settings in `atakit-workload.toml`:

- Parent service: `baby-container-dynamic-update`
- Dashboard port: `3000/tcp`
- Portal socket: `/run/atakit-portal.sock`
- Upload spool disk: `upload-spool`, mounted at
  `/var/lib/baby-dashboard/uploads`
- Upload spool size: `10GB`
- Baby slots: `forex-worker`, `forex-worker-alt`
- Max baby-container instances: `3`
- Slot image retention: `session`
- Slot instance retention: `ephemeral`

## Pull And Deploy

See the [repo README](../README.md) or
[Hoodi deployment guide](../docs/hoodi-deployment.md) for one-time setup.

```bash
atakit workload pull baby-container-dynamic-update:v0.1.3 --verify

atakit cloud deploy baby-container-dynamic-update:v0.1.3 \
  --target gcp-c3-standard-4 \
  --name baby-container-demo \
  --yes

atakit cloud status baby-container-demo --live
```

The dashboard listens on port `3000`.

## Build The Workload Locally

From this directory:

```bash
atakit workload build -d .
```

Then deploy as usual:

```bash
atakit cloud deploy baby-container-dynamic-update:v0.1.3 \
  --target gcp-c3-standard-4 \
  --name baby-container-demo \
  --yes
```

For pre-publish testing, deploy with registration optional/off or publish a new
version before using a target with `registration = "required"`.

## Build Runtime Baby Images

Baby images are not part of the measured workload archive. Build them on the
client side and upload them through the running dashboard:

```bash
./scripts/build-baby-images.sh
```

This creates Docker archive image tars:

```text
dist/baby-forex-v1.tar
dist/baby-forex-v2.tar
```

Both images run a small Python process that periodically fetches or falls back
to a USD/SGD exchange rate and prints JSON log lines. `v1` and `v2` use
different source/fallback behavior so the update is visible in logs.

## Exercise The Dashboard

Open:

```text
http://<cvm-ip>:3000/
```

Use the dashboard controls:

1. Upload `dist/baby-forex-v1.tar`.
2. Click **Create instance**.
3. Watch the loaded image, instance status, and forex logs.
4. Stop and remove the instance.
5. Upload `dist/baby-forex-v2.tar`.
6. Click **Create instance** again.
7. Confirm logs now show `"version": "v2"`.

The same flow can be driven with curl:

```bash
BASE_URL=http://<cvm-ip>:3000

curl -fsS -X POST \
  --data-binary @dist/baby-forex-v1.tar \
  "${BASE_URL}/api/upload"

curl -fsS -X POST -H 'content-type: application/json' -d '{}' \
  "${BASE_URL}/api/create"

curl -fsS "${BASE_URL}/api/state"
```

The parent service forwards those requests to:

```text
/run/atakit-portal.sock
```

The external client never talks to portal directly.

Gzip upload is also accepted for clients that need it:

```bash
gzip -c dist/baby-forex-v1.tar | curl -fsS -X POST \
  -H 'content-encoding: gzip' \
  --data-binary @- \
  "${BASE_URL}/api/upload"
```

To test a runtime update from the command line:

```bash
curl -fsS -X POST \
  --data-binary @dist/baby-forex-v2.tar \
  "${BASE_URL}/api/upload"

curl -fsS -X POST -H 'content-type: application/json' \
  -d '{"instance_id":"forex-worker-1"}' \
  "${BASE_URL}/api/remove"

curl -fsS -X POST -H 'content-type: application/json' -d '{}' \
  "${BASE_URL}/api/create"

curl -fsS "${BASE_URL}/api/state"
```

## API Summary

The dashboard exposes:

- `GET /` - browser dashboard
- `GET /api/state` - current staged image, baby-container instances, and logs
- `POST /api/upload` - upload a raw Docker archive tar, or gzip with
  `content-encoding: gzip`
- `POST /api/create` - create a baby-container instance from the staged image
- `POST /api/stop` - stop an instance
- `POST /api/remove` - remove an instance
- `POST /api/image/remove` - remove the staged image from a slot

## Cleanup

```bash
atakit cloud destroy baby-container-demo --yes
```

The upload spool is scratch space for dashboard uploads and is removed with the
deployment.
