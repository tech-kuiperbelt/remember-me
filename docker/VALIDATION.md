# Phase 2.1 AMD64 image validation

Date: 2026-10-21. **Phase 2.1 local AMD64 acceptance passed.** The default
official-download full build remains **unverified due to local bandwidth** and
is mandatory before official GHCR publication. No image has been published.

User approval recorded on 2026-10-21: **Phase 2.1 accepted; the project has
formally entered Phase 2.2.** Outstanding release gates above remain in effect.

## Fixed inputs and image targets

- Checkpoint: `edb2de0` plus this implementation.
- Base: `python:3.12.15-slim-bookworm@sha256:2ed6491b93cd49272ee6de2b5a38440c3448360322c089fc23e370722d74179d` (official AMD64 manifest).
- Installer: pip **26.2.1**, wheel SHA256 in `docker/pip-bootstrap.lock`.
- Production closure: the existing 70 versions/hashes in
  `requirements/docker-cpu-py312-linux-amd64.lock`, unchanged. The pip 24.0
  reference in its header describes the Phase 1 generator, not this installer.
- Model: `BAAI/bge-m3`, revision `5617a9f61b028005a4858fdac845db406aefb181`.
  `docker/bge-m3-manifest.json` records size, SHA256 and the official Git blob
  SHA1 or LFS SHA256 for all 11 dense runtime files. No ONNX/sparse/ColBERT weights.
- Model license: complete MIT text and copyright notice in
  `/opt/remember-me/models/licenses/BGE-M3-MIT.txt`; source and model attribution
  in adjacent `BGE-M3-NOTICE.txt`. The pinned model card remains in the snapshot;
  the project Apache-2.0 license remains `/opt/remember-me/LICENSE`.
- `runtime` (default) / `runtime-download`: official fixed-revision download.
  `runtime-local`: read-only named snapshot context through the same verifier.
- `validation` / `validation-local`: corresponding runtime plus the Phase 1
  verifier and production lock. The runtime targets do not inherit these files
  or test tools.

## Build and verification commands

Create a dedicated builder without `--use`; always name it explicitly:

```bash
docker buildx create --name rm-phase21 --driver docker-container --bootstrap

# First local-snapshot acceptance; source must be the complete pinned snapshot.
docker buildx build --builder rm-phase21 --platform linux/amd64 --load \
  --target runtime-local --progress plain \
  --build-context model_snapshot=/path/to/pinned/snapshot \
  -t remember-me:phase2.1-amd64 .

docker buildx build --builder rm-phase21 --platform linux/amd64 --load \
  --target validation-local --progress plain \
  --build-context model_snapshot=/path/to/pinned/snapshot \
  -t remember-me:phase2.1-validation-amd64 .

docker run --rm --network none --read-only \
  --tmpfs /data:uid=10001,gid=10001,mode=700 \
  --entrypoint sh remember-me:phase2.1-validation-amd64 -c \
  'mkdir -p /data/tmp && exec python bin/verify_cpu_runtime.py --model-cache /opt/remember-me/models/sentence-transformers --expected-profile-id ep_6134c0b15d2bb471'

docker run --rm --network none --entrypoint python \
  remember-me:phase2.1-amd64 -m pip check

# No host port is published. Inspect readiness from container loopback.
docker run -d --name rm-phase21-readonly --network none --read-only \
  --tmpfs /data:uid=10001,gid=10001,mode=700 \
  remember-me:phase2.1-amd64
docker exec rm-phase21-readonly python docker/healthcheck.py
docker inspect rm-phase21-readonly --format '{{json .State.Health}}'
docker stop --time 120 rm-phase21-readonly

# Required before official GHCR publication; does not use model_snapshot.
docker buildx build --builder rm-phase21 --platform linux/amd64 --load \
  --target runtime --progress plain \
  -t remember-me:phase2.1-download-amd64 .
```

The local test host uses an existing loopback HTTP proxy. Builder daemon
networking and build-only proxy arguments may be needed locally; they are not
image runtime settings. No global Docker daemon/default builder settings are
changed. Runtime acceptance uses `--network none`.

On this host the actual builder was recreated with these local-only options
after the first registry timeout:

```bash
docker buildx create --name rm-phase21 --driver docker-container \
  --driver-opt network=host \
  --driver-opt env.HTTP_PROXY=http://127.0.0.1:7890 \
  --driver-opt env.HTTPS_PROXY=http://127.0.0.1:7890 --bootstrap
```

The recorded build commands additionally used `--network host --allow
network.host --build-arg HTTP_PROXY=http://127.0.0.1:7890 --build-arg
HTTPS_PROXY=http://127.0.0.1:7890`. These predefined build arguments are absent
from the final image environment. Exact build commands and measurements are in
[`validation-results.json`](validation-results.json).

The first snapshot context was
`/home/yu/.remember-me/models/sentence-transformers/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181`.
The main context transferred **4.63 MB**; the separately named, read-only model
context transferred **2.29 GB**. Phase 1's existing HTTP wheel cache was seeded
into the builder's temporary `/root/.cache/pip` cache mount with a separate
temporary build (no host venv was copied). Its Dockerfile was:

```dockerfile
FROM python:3.12.15-slim-bookworm@sha256:2ed6491b93cd49272ee6de2b5a38440c3448360322c089fc23e370722d74179d
RUN --mount=type=cache,target=/root/.cache/pip --mount=type=bind,from=pip_seed,target=/seed,readonly cp -a /seed/. /root/.cache/pip/
```

Actual cache-seeding command:

```bash
docker buildx build --builder rm-phase21 --platform linux/amd64 --progress plain \
  --build-context pip_seed=/tmp/remember-me-phase1-pip-cache /tmp/rm-phase21-pipseed
```

The official download attempt used the `runtime` target without a model context
and a 300-second local validation deadline; cancellation and partial download
are recorded, not counted as a successful full build.

## Actual results

| Check | Result |
|---|---|
| Official base manifest resolution | AMD64 manifest digest confirmed; existing local Python slim image reports Python 3.12.15 |
| pip bootstrap | Hashed pip 26.2.1 installation passed in the base container and Dockerfile deps stage |
| Model provenance | All 11 local snapshot files match official fixed-revision Git/LFS metadata, including the 2,271,145,830-byte weight |
| Build-only packaging | Passed on host: standard cache layout, all symlinks internal, files/directories read-only, manifest and license files retained |
| Host regression suite | 1115 passed, 7 deprecation warnings, 47.67 s (Python 3.12.3); not a substitute for image tests |
| Actual Dockerfile build | Passed: Linux AMD64, `--load`, target `runtime-local`, pip check during build |
| Python 3.12.15 production lock installation / pip check | Passed: all 70 exact versions, official CPU Torch 2.14.1+cpu, CUDA/HIP null, no forbidden packages; lock SHA256 remains `041aca82c90f694da019898e31caa09a97c2312c8678bedf37f0cd86b5cb7655` |
| Image offline Chinese/English inference | Passed as UID/GID 10001:10001, `--network none --read-only`, only `/data` writable tmpfs; both outputs 1024-D, norms `0.9999999002` / `0.9999998322`; slow SentencePiece tokenizer passed |
| Profile identity | Logical `BAAI/bge-m3`, ID `ep_6134c0b15d2bb471`; no runtime revision and no production database opened |
| Default non-root HTTP startup | Passed without host port mapping, network disabled; `/api/model` ready after 13.86 s, read-me/Admin/main JS served, add plus semantic retrieval succeeded |
| Read-only HTTP startup | Passed with root filesystem read-only and only `/data` writable tmpfs; ready after 13.79 s; add/search and health passed |
| Model immutability | All 11 SHA256 values unchanged before/after HTTP inference; internal symlinks and root ownership verified; writes rejected with EACCES normally and EROFS under `--read-only` |
| Shutdown | Both test servers stopped by SIGTERM and exited 0, no OOM |
| Image content | No host env/database/log/cache files, project tests, verifier, production lock, pip-tools, pytest, FastEmbed, ONNX, GPU packages or compiler tools; no `/root/.cache/pip`; model MIT text/attribution retained |
| Layer caching | Repeat build passed in 9.09 s; application-only temporary source change passed in 12.50 s; dependency install and model packaging/copy were CACHED in both logs |
| Image regression suite | **1115 passed**, 6 deprecation warnings, **95.89 s**, Python 3.12.15 / glibc 2.36 / locked runtime; externally mounted pytest, no production test dependency |
| Default official model-download full build | **Unverified**: five small files downloaded and verified, weight partial reached 41,943,040 bytes / 2,271,145,830 bytes at the 300 s deadline; build canceled (exit 130). Must pass a full build before GHCR release |

The isolated regression checkout preserves tracked executable modes and has a
`.venv` symlink to `/data/regression`, a venv freshly created inside the test
container. Its `.pth` adds `/opt/venv/lib/python3.12/site-packages` and the
read-only `/tools` pytest mount. Tests use `--network none --read-only`, with
`/data:exec,uid=10001,gid=10001,mode=700` tmpfs and a read-only `/work` checkout;
`-p no:cacheprovider` avoids writing a source checkout cache. `exec` is needed
only for fixtures that copy a Python executable into temporary directories.
Initial test harness failures were missing executable modes, noexec tmpfs and
the operator-wrapper `.venv` prerequisite; correcting the harness yielded the
full pass without changing production code or test assertions for these failures.

## Image, layers and resource measurements

Image tag: `remember-me:phase2.1-amd64`.
Image ID: `sha256:ab2b4233317ef9033cb1b21e3ede2470dda065ae4e28246083295bcb2615336e`.
Independent builder: `rm-phase21`, docker-container driver, BuildKit **v0.33.1**.

| Measurement | Actual result |
|---|---:|
| First successful application image build, including load | 423.06 s |
| Validation target build | 9.08 s |
| Fully cached repeat build, including load | 9.09 s |
| Application-only source-change build, including load | 12.50 s |
| Image size reported by Docker (uncompressed) | 3,524,617,310 bytes / 3.283 GiB |
| Model layer | 2,293,337,721 bytes / 2.136 GiB |
| Runtime venv layer (no bytecode compilation) | 1,095,693,317 bytes / 1.020 GiB |
| Python/OS base layers together | 130,978,534 bytes |
| Web layer | 3,895,663 bytes |
| Application layer | 694,473 bytes |
| Project license / entrypoint+healthcheck layers | 11,358 / 1,861 bytes |
| BuildKit sampled maximum CPU | 322.46% (about 3.22 logical CPUs) |
| BuildKit sampled maximum memory | 356.9 MiB |
| BuildKit stats samples during first successful build | 140, roughly 3 s apart |
| Host used disk before/after first successful build | 195,576,119,296 / 210,903,433,216 bytes |
| Observed host disk increase | 15,327,313,920 bytes / 14.275 GiB |

This first successful build reused local model bytes and seeded wheel artifacts;
it is not a fresh-network dependency/model download benchmark. Builder bootstrap
previously took 562.3 s; an overlapping initial application build failed after
340.64 s on frontend registry resolution. These intervals overlap and must not
be summed as separate sequential time. A host-side attempt to fetch BuildKit
OCI blobs also encountered TLS errors; it produced no imported image and was
not needed after the daemon pull eventually completed.

Resource figures are sampled `docker stats` working-set values for the builder,
not guaranteed process peaks or whole-host usage. Disk change is a shared-host
observation including BuildKit snapshots, contexts and wheel-cache warmup, not
an isolated image footprint. No gzip image archive size or GHCR transfer size
was measured. The main layer sizes above come from `docker history`.

## Scope and remaining release gates

`HEALTHCHECK` only observes `GET /api/model` and requires `ready=true`.
It uses a **180-second start period**, a 30-second interval,
5-second Docker timeout (3-second HTTP timeout), and 3 retries. Its grace period
exceeds the measured approximately 14-second initialization with ample local
headroom. Both containers progressed from initial failed checks to `healthy`;
no repair or download is performed by the check. Slower hardware may need its
own initialization measurement.

New data roots initialize `auto_download_model=false` and `local_only=true`;
existing JSONC or legacy JSON is preserved for normal configuration loading.
The entrypoint execs the existing server as UID/GID `10001:10001`, with the
default `--host 0.0.0.0 --port 1314`. Application Runtime, embedding identity and
MCP security behavior are unchanged.

Phase 2.1 only covers normal offline startup and inference with the complete
built-in model. Disabling automatic download does **not** establish that Web
Retry can never download. Phase 2.2 must validate Retry local-only behavior
(including missing/corrupt/load-failure cases) and official Docker model
configuration entry points before release. No defense against deliberate
internal configuration overrides is required. Compose, bind-mount permissions,
data preservation/recreation, backup/upgrade and bridge/client integration
remain separate Phase 2.2 work. Actions, GHCR publication and ARM64 are excluded.
