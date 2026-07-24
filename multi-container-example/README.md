# multi-container-example

Demonstrates a multi-container workload with three containers sharing a
persistent disk and communicating over the workload container network.

Published version: `multi-container-example:v0.5.3`.

This version runs only with `automata-linux:v0.2.7-debug`.

## Architecture

```
                    +-----------+
    GET /          >|           |----> POST /process --> worker-a (:3001)
    POST /task ---->| coordinator|                        writes to /shared/
    GET /status --->| (:3000)   |----> POST /process --> worker-b (:3002)
    GET /results -->|           |                        writes to /shared/
    POST /clear --->+-----------+
                         |
                     /shared/ (persistent disk, shared by all three)
```

**coordinator** - HTTP server with a live dashboard at `/`. Accepts tasks via `POST /task`, writing task files to the shared disk and delegating to workers over the container network. `/status` queries workers' health endpoints. `/results` reads all files from the shared disk. `POST /clear` removes all files from the shared disk.

**worker-a** - Processes tasks and writes result files to the shared disk. Writes periodic heartbeats. Reads other workers' heartbeats from disk.

**worker-b** - Same as worker-a but depends on worker-a for startup ordering. Both workers use the same container image, parameterized via `WORKER_NAME` and `WORKER_PORT` environment variables.

## What this demonstrates

- **3 containers** in a single workload (coordinator + 2 workers)
- **Shared disk** mounted at `/shared` in all three containers
- **Disk reads/writes** from every container (tasks, results, heartbeats)
- **Network communication** between containers (coordinator calls worker HTTP endpoints)
- **depends_on** ordering (worker-b starts after worker-a)
- **Per-container environment** variables (`WORKER_NAME`, `WORKER_PORT`)
- **Single build context** with multiple Containerfiles
- Workload-level firewall and port exposure for a multi-service app

## Workload Config

Important manifest settings in `atakit-workload.toml`:

- Parent service: `multi-container-example`
- Dependencies: `worker-a`, `worker-b`
- Ports: `3000:3000`, `3001:3001`, `3002:3002`
- Extra firewall rule: `4000/tcp`
- Disk: `shared-data`, mounted at `/shared` in all containers
- Disk size: `10GB`
- Restart policy: `unless-stopped`

## Pull And Deploy

See the [repo README](../README.md) or
[Hoodi deployment guide](../docs/hoodi-deployment.md) for one-time setup.

```bash
# Download the pre-built, on-chain-published archive into your local store.
atakit workload pull multi-container-example:v0.5.3 --verify

# Deploy to a configured Hoodi cloud target.
atakit cloud deploy multi-container-example:v0.5.3 \
  --target gcp-c3-standard-4 \
  --name multi-container-demo \
  --yes

# Get the external IP.
atakit cloud status multi-container-demo --live
```

The workload exposes the coordinator dashboard on port `3000` and the workers
on `3001` / `3002`. Cross-container DNS (`worker-a`, `worker-b`) is provisioned
by the portal's per-workload network — no manual wiring needed.

## Exercise the running workload

Replace `${IP}` with the external IP from `atakit cloud status`.

```bash
# Open the dashboard
curl http://${IP}:3000/

# Submit a task (coordinator writes it to /shared and fans out to both workers)
curl -X POST http://${IP}:3000/task -d "hello world"

# Inspect status (coordinator polls each worker's /health)
curl http://${IP}:3000/status

# Read everything on the shared disk
curl http://${IP}:3000/results

# Clear the shared disk
curl -X POST http://${IP}:3000/clear
```

If a handler raises, the response is a JSON 500 with the Python traceback —
useful for diagnosing disk-mount, networking, or permission issues.

## Build Locally

Build from this directory if you are changing the example:

```bash
atakit workload build -d .
```

For pre-publish testing, deploy with registration optional/off or publish a new
version before using a target with `registration = "required"`.

## Cleanup

```bash
atakit cloud destroy multi-container-demo --yes
```

Destroying the deployment removes the shared persistent disk created for this
workload.
