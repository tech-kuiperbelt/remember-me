# Phase 3.2 — Manual GHCR release controls and readiness

Date: 2026-10-22. Status: **local acceptance passed; awaiting hosted acceptance**.
**Release Ready is not yet declared. Released: no.**

The implementation baseline was master `938dd22`. Both the release workflow and
the existing Docker CI now explicitly use `ubuntu-24.04`. The final master Cold
run is a required Ready gate; independent Warm/Source-probe regression follows.
The final implementation SHA and hosted Run IDs will be recorded after those
runs. No formal version image, `latest`, Public setting or GitHub Release has
been created or modified by this work.

## Implemented controls

- Independent `workflow_dispatch` release workflow, exact Tag/full-commit/master
  ancestry checks, immutable-version checks before build and push, fixed global
  release concurrency, and per-job minimal `GITHUB_TOKEN` permissions.
- Existing Dockerfile, pinned inputs, Buildx and private Registry Cache reused;
  release only imports the cache. Runtime content checks are minimally shared,
  retaining the existing CI cache modes and assertions.
- Candidate acceptance precedes version push. A fresh anonymous runner checks
  the version and manifest digest before a higher stable version can advance
  `latest`. Prereleases and older maintenance versions do not advance it.
- Explicit digest-based `resume` validates and completes an existing release
  without rebuilding or overwriting its version. First Package Public remains
  manual and irreversible; the private cache is never made public.
- A local `check` command exercises actual image acceptance without contacting
  or writing a Registry. [Maintainer operations](../docs/operations/docker-release.md)
  describe authorization, release, Public setup, resume and manual Release notes.

## Automated tests

`74 passed`, one existing Starlette TestClient deprecation warning:

```bash
.venv/bin/python -m pytest -q \
  tests/test_docker_release.py tests/test_docker_ci_script.py \
  tests/test_docker_assets.py tests/test_docker_policy.py
```

Tests use synthetic Git repositories, a loopback Mock Registry and intercepted
Docker writes. They cover source/tag races, SemVer/input validation, permissions,
duplicate versions, ambiguous Registry failures, partial pushes, anonymous
acceptance, monotonic stable promotion, and recovery. No formal Registry write is
used to prove these paths. Workflow tests also require both runners to be pinned
and the release to remain manual-only.

## Real local acceptance

Built current `runtime-local` with the fixed official model snapshot using the
existing isolated `rm-phase21` builder; no image was pushed. Image
`remember-me:phase3.2-ready-fixed`:

- Local image ID `sha256:72110e672f1e94fb8879a9d21eac02548931c4e2c4e49c1517b79c6d252f8c82`;
  size `3,524,622,939` bytes. This ID is not a Registry manifest digest.
- Full candidate checks passed: UID/GID 10001, read-only/no-network inference,
  readiness/health, complete CRUD and semantic retrieval, explicit Dual JSONL
  update content, SQLite integrity, and graceful exit 0.
- The existing CPU verifier, mounted read-only into the actual runtime candidate,
  passed 70 locked packages, CPU-only Torch, normalized 1024-dimensional vectors
  and Profile `ep_6134c0b15d2bb471`. Loaded modules are explicitly
  `Transformer → Pooling(cls) → Normalize`.
- The production DB was opened read-only using SQLite Backup API; only a second,
  disposable working copy was mounted in Docker. Three existing-record searches
  succeeded. All 3213 memory vectors and 2165 index-value vectors retained their
  exact aggregate BLOB hashes and counts; no reindex was performed.
- Profile identity/definition and active ID remained unchanged. Existing startup
  code refreshes the active profile's operational `updated_at`; this change is
  separately recorded and is excluded only from the definition comparison.
- Eight nonprivate text fixtures, query/document paths, yielded 16 pairs of
  1024-dimensional host/image vectors. Maximum component difference `0.0`;
  minimum cosine `0.9999999999999998`. Host Torch/Transformers were
  `2.14.0+cpu`/`5.17.0`; image versions `2.14.1+cpu`/`5.20.2`.

Private snapshots, individual vectors and database-query logs stay local. The
[machine summary](phase3.2-validation-results.json) contains aggregate results.

## Defects found and fixed during acceptance

1. **Trailing newline in model `refs/main` silently changed pooling.** The
   packaging script wrote the SHA followed by `\n`. Offline Hub lookup reads the
   ref verbatim, so `modules.json` was not found; Sentence Transformers loaded
   the base weights with default Mean Pooling instead of CLS + Normalize. Before
   the fix, host/image cosine ranged approximately 0.629–0.838 despite matching
   model file hashes, dimensions and logical Profile. The packager now writes
   exactly the SHA; tests assert raw ref bytes and snapshot resolution, and the
   verifier checks the actual model modules/pooling. Dependency locks, weights,
   Profile identity and application runtime architecture were not changed.
2. **Standalone verification had no writable temporary directory.** Bypassing
   the normal container entrypoint left `/data/tmp` absent; PyTorch import could
   not create its cache on a read-only root. Test/verifier containers now set
   `TMPDIR=/data` on their disposable writable data mount. Application container
   defaults and entrypoint remain unchanged.

Historical Phase 2/3.1 checks did not prove the final image's vector-space
compatibility; dimensions, normalization and Profile ID alone did not catch the
pooling fallback. Existing local acceptance images retain that historical defect
and must not be treated as the corrected candidate. No public version had been
published from them.

## Hosted and public delivery boundaries

Pending: final-code `ubuntu-24.04` Cold and independent Warm/Source-probe Runs.
Until the actual Cold evidence is present the state remains awaiting acceptance,
not Release Ready.

Real formal GHCR version push, package Public/anonymous pull and `latest`
promotion are intentionally not executed in this Ready task. They require the
user's separate authorization. GitHub Release is always manual; Phase 3.3 README
(`docker run` and optional Compose) and clean-host installation acceptance remain
separate work. Registry-side version immutability is protected by controlled
writers and workflow checks, not an atomic compare-and-set guarantee against
external administrative writes.
