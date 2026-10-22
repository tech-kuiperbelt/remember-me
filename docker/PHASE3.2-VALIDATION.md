# Phase 3.2 — Manual GHCR release controls and readiness

Date: 2026-10-22. Status: **Release Ready — passed**. **Released: no.**

The implementation baseline was master `938dd22`. Both the release workflow and
the existing Docker CI now explicitly use `ubuntu-24.04`. The final master Cold
acceptance and independent Warm/Source-probe regression all passed on implementation
commit `d5b7df1717b8d1230e22f5c7604083b3c846dfc2` (initial implementation `3b9f8c1`).
Subsequent evidence-only documentation commits do not change tested build inputs
or executable controls. No formal version image, `latest`, Public setting or GitHub Release has
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

All three final runs used commit `d5b7df1` and independent `ubuntu-24.04` runners:

| Mode | Run | Result | Runtime/cache build |
|---|---|---|---|
| Cold | [39725036144](https://github.com/tech-kuiperbelt/remember-me/actions/runs/39725036144) | passed | 191.53 s |
| Warm | [39725104465](https://github.com/tech-kuiperbelt/remember-me/actions/runs/39725104465) | passed | 3.59 s |
| Source-probe | [39728739306](https://github.com/tech-kuiperbelt/remember-me/actions/runs/39728739306) | passed | 58.33 s |

Cold verified all 11 official model files and the actual CLS + Normalize pipeline,
completed offline CRUD/health/graceful-stop acceptance, and exported Private cache
`sha256:c7f1b8143223852b77b8bcc6a75eb697332ab8434f67cdae6847a07e99798421`.
API visibility was Private, anonymous cache access was denied, authenticated access
succeeded. Warm and Source-probe imported this exact cache manifest; both cached
`deps`, `model-tools`, `runtime-base`, `runtime-download`, with zero model download/
verification events. Source-probe rebuilt an application COPY step (24) and restored
its temporary source edit. Static checks passed 70 tests on each final runner;
the local 74-test run additionally includes Docker runtime-policy regression.

Initial hosted attempts on `3b9f8c1`: Cold `39722234643` passed (205.63 s
runtime build, correct CLS pipeline, 11 model files); Warm `39722963396` passed
(2.05 s, no model download). Source-probe `39723098288` stopped in static tests:
the Warm unit fixture inherited `MODE=source-probe`. The fixture now explicitly
sets its own Warm mode; production cache assertions are unchanged. The local
74-test suite also passes with inherited `MODE=source-probe`. The successful final-commit hosted
runs above supersede that failure; the initial attempts remain in JSON.

Real formal GHCR version push, package Public/anonymous pull and `latest`
promotion are intentionally not executed in this Ready task. They require the
user's separate authorization. GitHub Release is always manual; Phase 3.3 README
(`docker run` and optional Compose) and clean-host installation acceptance remain
separate work. Registry-side version immutability is protected by controlled
writers and workflow checks, not an atomic compare-and-set guarantee against
external administrative writes.
