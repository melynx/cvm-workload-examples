# baby-container-dynamic-update

Demonstrates a workload-owned baby-container update flow. The measured workload
is a dashboard service that declares two baby-container slots, accepts helper
image uploads from a user, and calls the portal's workload-facing UDS from
inside the parent service.

## Architecture

```text
browser / curl
    |
    |  GET / dashboard
    |  POST /api/upload    raw podman-save tar
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

## Build The Workload

From this directory:

```bash
atakit workload build -d .
```

Then deploy as usual:

```bash
atakit cloud deploy baby-container-dynamic-update:v0.1.1 \
  --image <base-image>:<version> \
  --target <target>
```

The dashboard listens on port `3000`.

## Build Runtime Baby Images

Baby images are not part of the measured workload archive. Build them on the
client side and upload them through the running dashboard:

```bash
./scripts/build-baby-images.sh
```

This creates:

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

curl -fsS -X POST --data-binary @dist/baby-forex-v1.tar \
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
