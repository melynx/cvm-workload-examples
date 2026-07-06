# remote-log-smoke

Small workload for validating remote log collection through a Fluent Bit sidecar.

The workload runs four generic log producers: `app`, `worker`, `scheduler`, and
`metrics`. Each producer writes structured heartbeat logs through the portal's
`k8s-file` logging path and grants read access to `log-shipper`. The
`log-shipper` dependency uses `docker.io/fluent/fluent-bit:4.2.4` directly,
mounts the measured Fluent Bit config, reads the portal-provided workload log
mount, and posts events to an external HTTP receiver.

Runtime receiver settings are provided through unmeasured data:

```env
LOG_RECEIVER_HOST=127.0.0.1
LOG_RECEIVER_PORT=18080
LOG_RUN_ID=remote-log-smoke-default
```

Start the receiver on the log host:

```sh
python3 tools/log-receiver.py --host 0.0.0.0 --port 18080
```

Build and verify the manifest preflight:

```sh
BUILD_ONLY=1 ./scripts/e2e-remote-logs.sh
```

Build the workload archive directly:

```sh
atakit workload build -d .
```

For a full run, set `LOG_RECEIVER_HOST` to the receiver address and run the
script once to build the workload and print the runtime directory. Deploy the
workload with that directory as `--unmeasured-data-root`, then rerun the script
with the same `LOG_RUN_ID` so it can poll the receiver:

```sh
LOG_RECEIVER_HOST=<receiver-ip-or-dns> ./scripts/e2e-remote-logs.sh
atakit cloud deploy -d . \
  --target gcp-c3-standard-4 \
  --name remote-log-smoke \
  --unmeasured-data-root <printed-runtime-dir> \
  --yes
LOG_RECEIVER_HOST=<receiver-ip-or-dns> LOG_RUN_ID=<same-run-id> ./scripts/e2e-remote-logs.sh
```

The run passes only after the receiver reports events from `app`, `worker`,
`scheduler`, and `metrics` for the generated `LOG_RUN_ID`.
