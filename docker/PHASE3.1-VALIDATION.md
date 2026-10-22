# Phase 3.1 — GitHub Actions AMD64 official-download validation

Date: 2026-10-21. Status: **passed**. Final validated code is on `master` at `3a258e3` (the validation runs themselves were executed from a temporary push-triggered branch). No public GHCR image, Release, stable tag
or `latest` tag was published.

Follow-up (2026-10-22): Phase 3.2 compatibility acceptance found that the packaged
`refs/main` trailing newline caused offline module discovery to fall back to
Mean Pooling. These historical checks proved their listed build/cache/health
conditions, but not host/image vector-space equivalence. The packager and the
actual CLS-module verification were corrected in Phase 3.2; see
[Phase 3.2 evidence](PHASE3.2-VALIDATION.md). Do not use a historical local image
as the corrected release candidate.

## Environment and inputs

- Repository: `tech-kuiperbelt/remember-me` (public).
- Runner: GitHub-hosted `ubuntu-latest` (public-repository standard 4 vCPU / 16 GB
  per GitHub documentation); the measured disk was 154.9 GB total with 114.1 GB
  available before the cold build. This is
  well above the 14 GB figure in the hosted-runner documentation; no larger
  runner or disk fallback was needed.
- Build: existing `Dockerfile`, `runtime` target, official Hugging Face download,
  revision `5617a9f61b028005a4858fdac845db406aefb181`, 70-package CPU lock,
  BuildKit `v0.33.1`, `linux/amd64`.
- Cache: private `ghcr.io/tech-kuiperbelt/remember-me-buildcache:amd64`,
  Registry Cache `mode=min`.
- Trigger: `workflow_dispatch`. The validation runs below were executed from a
  temporary push-triggered branch because this environment has no GitHub API
  token; the final committed workflow is manual-only.

## Runs

| Run | ID | Result | Duration | Evidence |
|---|---|---|---|---|
| Cold official build | [39596639262](https://github.com/tech-kuiperbelt/remember-me/actions/runs/39596639262) | passed | 215.45 s build | 11 model files verified, image accepted, cache exported |
| Warm cross-run | [39597821561](https://github.com/tech-kuiperbelt/remember-me/actions/runs/39597821561) | passed | 1.92 s | `deps`, `model-tools`, `runtime-base`, `runtime-download` CACHED; zero model downloads |
| Source-only cross-run | [39598394942](https://github.com/tech-kuiperbelt/remember-me/actions/runs/39598394942) | passed | 46.93 s | model/deps layers CACHED; app layers rebuilt; zero model downloads |

These are three independent workflow runs on fresh hosted runners. The warm and
source-only runs imported the cache manifest exported by the cold run; no local
BuildKit cache was reused across jobs.

## Cold build

- Cache export: `sha256:2d04612394b0e64e89184ed5a8ae4b3abc0cc42b7f66031c5b8b1d7bfb353e79`
  (`application/vnd.oci.image.manifest.v1+json`, 4532 bytes).
- `docker/buildx` log: 11 unique `Verified <path>: <sha256>` entries; the
  `pytorch_model.bin` download completed and matched
  `b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38`.
- Image: local image ID `sha256:fa15dcedacd9aaafad7860f39a771603fe1f2281ec1e3056234965e14d92df0a`;
  size `3,524,610,215` bytes. This is a local image ID, not a registry manifest
  digest.
- Disk: 114.133 GB available before, 101.936 GB minimum during the build,
  107.256 GB after.
- Image checks passed: UID/GID 10001, read-only root startup, model `refs/main`,
  weight size/SHA256, labels, `pip check`, no `.env`/database/tests/verifier or
  production lock in the runtime image.
- Offline smoke passed: model ready, health `healthy`, Docker config with
  `auto_download_model=false` and `local_only=true`, memory add, semantic search,
  SQLite integrity, profile `ep_6134c0b15d2bb471`, files under `/data`, graceful
  stop exit 0 (1.74 s).
- Offline verifier passed in `validation`: Python 3.12.15, 70 locked packages,
  Torch 2.14.1+cpu, 1024-dimensional normalized vectors, expected profile.

## Cross-run cache result

Warm run:

```text
cached stages: deps, model-tools, runtime-base, runtime-download
verified model files: 0
build time: 1.92 s
```

Source-only probe (temporary edit of `web/static/js/main.js`, reverted):

```text
cached stages: deps, model-tools, runtime-base, runtime-download
verified model files: 0
build time: 46.93 s
app COPY layers rebuilt
```

This satisfies the Phase 3.1 requirement that remote cache reuse be proven across
independent workflow runs and fresh runners.

## Private cache gate

- GitHub package API: `visibility = private`.
- Anonymous `docker manifest inspect`: exit 1, `unauthorized`.
- Authenticated `docker manifest inspect`: exit 0.
- The cache package was created by the workflow and was not exposed as a public
  image or user-facing pull target.

## Build defects found and fixed

1. **Model download cache mount prevented remote reuse.** The original
   `model-download` stage used `--mount=type=cache`; BuildKit does not export cache
   mount contents, so the 2.27 GB model was downloaded again on every fresh
   runner. The download now uses a temporary directory removed in the same layer.
2. **A separate build stage consumed through `COPY --from` lost its cache record.**
   The pinned model is now produced in the final `runtime-download` stage, so the
   verified model layer is part of the exported final-stage cache.
3. **`SOURCE_DATE_EPOCH` invalidated the model cache.** It was removed from the
   Phase 3.1 build path. Strict reproducible-digest builds are not required for
   Phase 3.1; if added later, they must use a separate cache scope because the
   source epoch changes the cache key.
4. **Registry cache `mode=max` was not needed.** `mode=min` includes the model and
   dependency layers because they are in the final image, and produced the
   smallest verified cache manifest.

The runtime image contents, model revision, dependency lock and application
behavior are unchanged. The changes are build-path only.

## Not verified

- ARM64, Docker Desktop, rootless/user-namespace hosts and special filesystems.
- `docker pull` from GHCR for a public image (not published).
- Real client sessions (Codex/OpenCode) were already accepted in Phase 2.2 and
  were not repeated in this CI job.
- Bit-for-bit image digest reproducibility (explicitly not required).

## Reproduction

```bash
# cold: official download, image acceptance, cache export
gh workflow run docker-amd64-ci.yml -f mode=cold

# fresh runner: remote-cache reuse
gh workflow run docker-amd64-ci.yml -f mode=warm

# fresh runner: model/deps cache hit, app-layer rebuild
gh workflow run docker-amd64-ci.yml -f mode=source-probe
```

Machine-readable summary: [phase3.1-validation-results.json](phase3.1-validation-results.json).
