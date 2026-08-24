# Portal socket and dual-verifier dashboard

This GCP Intel TDX workload runs the portal regression checks for items 1–5.
It also sends one challenge-bound session evidence bundle to two real
`atakit-verifierd` containers:

- `verifierd-chain` reads measurement, workload, and GCP AK-root policy from
  the Solidity registry graph rooted at the measured `SessionRegistry`.
- `verifierd-trust-pack` reads the same policy from signed
  `collateral-trust` and `workload-trust` `.atatp` files.

Both daemons use the default Automata Hoodi on-chain PCCS source for Intel TDX
DCAP collateral. Automata transports Intel-signed collateral. It does not
become the measurement-policy authority.

The dashboard is available on TCP port 3300. The machine-readable result is
available at `/api/snapshot`.

```sh
curl -fsS http://VM_IP:3300/api/snapshot | jq
```

The dashboard shows:

- the chain verifier's authority coordinates and observed trust-input sources;
- each loaded trust pack's kind, issuer, revision, validity window, and digest;
- the measured trust-pack publisher public keys;
- the session binding reported by each verifier;
- the shared Intel TDX collateral source;
- the assumptions that remain outside the proof, including the lack of pack
  digest pins;
- every portal route, readiness, signing, session-rotation, socket-isolation,
  platform, and verifier check.

## Why this run uses local session binding

Set the deployment target's `registration` field to `"off"`. The portal then
commits a local-bound hardware session.

Trust authority and session binding are separate choices. The chain verifier
can verify a local-bound session while still reading policy from Solidity.
Trust-pack mode has no trusted chain coordinates, so it cannot authenticate a
chain-bound session. Local binding lets both policy authorities verify the
same evidence. The dashboard requires this setup and fails if the evidence is
chain-bound.

This test does not prove that the session was registered on chain. A caller
that relies on registration must separately require chain-bound evidence.

## Build inputs

The verifier image and generated trust packs are not committed. First package
the verifier image from the matching `atakit-ng` checkout:

```sh
mkdir -p portal-socket-dashboard/images
cd <atakit-ng-checkout>
./crates/atakit-verifierd/package-image.sh \
  <cvm-workload-examples-checkout>/portal-socket-dashboard/images/atakit-verifierd.tar
```

You also need a GCP vTPM AK root certificate that you already trust. Treat it
as a publisher input. Do not trust a new root only because the session under
test supplied it.

Run the build helper from this directory:

```sh
./scripts/build-e2e.sh \
  --gcp-ak-root-cert /path/to/gcp-ak-root.pem \
  --chain-rpc-url http://hoodi-fork.example:8545 \
  --chain-id 31337 \
  --session-registry 0x0123456789abcdef0123456789abcdef01234567
```

Use the live chain coordinates after the clean fork reset and contract graph
deployment. The helper requires them for every build. It replaces the explicit
placeholders in a staged copy of `atakit-workload.toml`; it does not change the
checked-in manifest. This prevents a new archive from silently measuring a
registry address from an older fork.

The helper performs these steps in order:

1. It stages the workload source and injects the supplied chain RPC URL, chain
   ID, and `SessionRegistry` address into the measured environment.
2. It runs `atakit-imgbuild measurement-pack` for
   `automata-linux:v0.3.0-debug` unless `--measurement-pack` supplies an
   existing pack.
3. It builds `portal-socket-dashboard-v0.3.0.atawl`.
4. It builds `collateral.atatp` from the trusted GCP AK root.
5. It builds `workload.atatp` from the workload archive and measurement pack.

Outputs are written below `.runtime/`. The helper prints the exact workload
archive and unmeasured-data root to use for deployment.

The manifest measures the publisher public key used by the local `owner` key.
The helper refuses a different key. You may set `WORKLOAD_SIGNING_KEY` or
`COLLATERAL_SIGNING_KEY` to aliases for the same provisioned key.

## Publish and deploy

The chain verifier needs the v0.3.0 workload policy in the configured
`hoodi-fork` registry. Publish the archive printed by the helper:

```sh
atakit workload publish \
  .runtime/build/portal-socket-dashboard-v0.3.0.atawl \
  --chain hoodi-fork --yes
```

Then deploy with a GCP TDX target whose effective registration mode is off:

```sh
atakit cloud deploy \
  .runtime/build/portal-socket-dashboard-v0.3.0.atawl \
  --unmeasured-data-root .runtime/unmeasured \
  --target GCP_TDX_REGISTRATION_OFF_TARGET
```

The `VERIFIERD_*` variables in `atakit-workload.toml` are measured manifest
environment. The two `.atatp` files are unmeasured bytes, but their signatures,
validity windows, kinds, and publisher keys are checked before the trust-pack
daemon starts.

The workload does not set `VERIFIERD_COLLATERAL_TRUST_PACK_SHA256` or
`VERIFIERD_WORKLOAD_TRUST_PACK_SHA256`. Pinning the workload pack inside the
same workload archive would create a circular input: the archive is needed to
build the pack. The dashboard reports the exact loaded digests and states the
resulting assumption: either measured publisher may replace its pack with a
different correctly signed and unexpired pack.
