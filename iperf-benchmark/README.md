# iperf-benchmark

A minimal `iperf3` server workload for measuring CVM network throughput and
debugging packet loss, jitter, and source-path issues.

Published version: `iperf-benchmark:v0.1.2`.

This version runs only with `automata-linux:v0.2.7-debug`.

## What It Runs

- `iperf3` server mode on port `5201`
- TCP and UDP exposed on the same host/container port
- No SSH server, dashboard, persistent disk, or extra debug tooling

## Build

From the repository root:

```sh
atakit workload build -d cvm-workload-examples/iperf-benchmark
```

This creates:

```text
cvm-workload-examples/iperf-benchmark/iperf-benchmark-v0.1.2.atawl
```

## Deploy

```sh
atakit cloud deploy iperf-benchmark:v0.1.2 \
  --target gcp-c3-standard-4 \
  --name iperf-benchmark-demo \
  --yes
```

Get the public IP:

```sh
atakit cloud status iperf-benchmark-demo --live
```

## Run Benchmarks

TCP upload from your client to the CVM:

```sh
iperf3 -c <cvm-ip> -p 5201
```

TCP reverse test from the CVM back to your client:

```sh
iperf3 -c <cvm-ip> -p 5201 -R
```

UDP test at 100 Mbit/s:

```sh
iperf3 -c <cvm-ip> -p 5201 -u -b 100M
```

Use longer runs when diagnosing intermittent drops or throughput changes:

```sh
iperf3 -c <cvm-ip> -p 5201 -t 60
iperf3 -c <cvm-ip> -p 5201 -u -b 100M -t 60
```

## Cleanup

```sh
atakit cloud destroy iperf-benchmark-demo --yes
```
