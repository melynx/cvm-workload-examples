# multi-container-example

Demonstrates a multi-container workload with three containers sharing a persistent disk and communicating over the container network.

## Architecture

```
                    +-----------+
    POST /task ---->|           |----> GET /process --> worker-a (:3001)
    GET /status --->| coordinator|                       writes to /shared/
    GET /results -->| (:3000)   |----> GET /process --> worker-b (:3002)
                    +-----------+                       writes to /shared/
                         |
                     /shared/ (persistent disk, shared by all three)
```

**coordinator** - HTTP server accepting tasks. Writes task files to the shared disk, then calls each worker over the network to process them. `/status` queries workers' health endpoints. `/results` reads all files from the shared disk.

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

## Build

```
atakit workload build -d .
```

## Test locally (after CVM agent supports dependencies)

```
# Submit a task
curl -X POST http://localhost:3000/task -d "hello world"

# Check status (queries workers over network)
curl http://localhost:3000/status

# Read all files from the shared disk
curl http://localhost:3000/results
```
