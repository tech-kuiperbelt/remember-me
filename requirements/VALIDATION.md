# Phase 1 CPU production dependency validation

Validated on **2026-10-21**, Linux AMD64 / CPython **3.12.3**, host glibc **2.39**.
The dependency-locking task passes its acceptance criteria. The wider Docker
roadmap's Phase 1 still has separate pending gates; Phase 2 has not started.

## Delivered files

| File | Purpose |
|---|---|
| `requirements-runtime.txt` | Shared production input, extracted without changing dependency ranges |
| `requirements.txt` | Compatible Python entry point: runtime input plus existing pytest requirement |
| `requirements/docker-cpu.in` | Official CPU wheel URL and SHA256, reusing shared production inputs |
| `requirements/docker-cpu-py312-linux-amd64.lock` | 70 exact runtime versions, one selected wheel hash per package |
| `bin/lock_cpu_requirements.py` | Regenerate from declared inputs using pinned resolver tools and pip's dry-run report |
| `bin/verify_cpu_runtime.py` | Verify installed versions, CPU provenance, imports and optional offline model inference |
| `requirements/README.md` | Clean installation, regeneration and later image-stage consumption instructions |
| `requirements/validation-results.json` | Machine-readable measured results and validation limits |
| `tests/test_cpu_install.py` | Existing installer fixture now copies the shared runtime input too |

## Actual installation and checks

A new `/tmp/remember-me-phase1-prod` venv was created with `python3 -m venv`.
No installed packages or venv files were copied from the developer environment.
Previously downloaded wheel artifacts were reused from a separate temporary
cache; the selected Torch wheel's SHA256 was independently compared with the
official CPU index. The final lock was installed with:

```bash
/tmp/remember-me-phase1-prod/bin/python -m pip install \
  --require-hashes --only-binary=:all: \
  --cache-dir /tmp/remember-me-phase1-pip-cache \
  --report /tmp/remember-me-phase1-install-report.json \
  -r requirements/docker-cpu-py312-linux-amd64.lock
```

All 70 runtime packages installed successfully. `pip check` passed. The
verification script confirmed exact lock/version agreement, no unexpected
packages beyond bootstrap pip, successful Sentence Transformers, SentencePiece,
Tokenizer, Transformers, Protobuf and `app.server` imports. `python -m app.server
--help` succeeded without starting business resources.

Key pinned versions:

| Package | Version |
|---|---|
| torch | 2.14.1+cpu |
| sentence-transformers | 5.7.0 |
| transformers | 5.20.2 |
| tokenizers | 0.23.3 |
| sentencepiece | 0.2.2 |
| protobuf | 7.36.2 |
| mcp | 1.30.0 |

Torch is a CPU build: both `torch.version.cuda` and `torch.version.hip` are null.
Its installed direct-wheel provenance is the official CPU URL, with SHA256
`5a6363570c753812540a05eb82380e329469cbe668643e88111414c12627711f`.
No CUDA/NVIDIA/Triton, FastEmbed, ONNX or pytest distributions are in the
production environment. Setuptools is retained because Torch requires it;
Pygments/Rich are runtime transitives, not developer residue. All declared and
resolved runtime requirements have an exact version or fixed CPU URL plus a hash.

## Actual BGE-M3 validation

Existing model weights were reused from a cache whose `refs/main` points to
`5617a9f61b028005a4858fdac845db406aefb181`. No 2.27 GB weight download was needed.

```bash
/tmp/remember-me-phase1-prod/bin/python bin/verify_cpu_runtime.py \
  --model-cache /home/yu/.remember-me/models/sentence-transformers \
  --expected-profile-id ep_6134c0b15d2bb471
```

The slow `XLMRobertaTokenizer` worked, demonstrating that SentencePiece loading
dependencies are available even when a fast tokenizer file is also cached.
The Remember Me provider loaded and warmed up BGE-M3 and embedded English and
Chinese inputs. Both vectors have 1024 dimensions; their L2 norms are
`1.0000000151` and `1.0000000924`. Model validation used local-only loading and a
process audit hook rejecting socket connections and DNS lookups.

The logical model remains `BAAI/bge-m3`; no runtime revision was passed. The
resulting profile ID is `ep_6134c0b15d2bb471`, matching the known active profile
identity. No business database was opened, modified, migrated or reindexed.
This verifies profile identity; retrieval against a production-data copy and
numeric comparison with previously stored vectors remain separate work.

## Regression and reproducibility

- Ordinary Python `.venv`: **1110 passed**, 7 existing deprecation warnings,
  48.22 seconds. Includes CPU installer, provider, model and startup tests.
- Locked runtime dependencies: **1110 passed**, 6 deprecation warnings,
  47.06 seconds. A separate regression venv references the production
  site-packages and an external pytest tools directory. Tests and subprocesses
  see the same locked runtime versions; pytest was not installed into production.
- An initial test launch using only `PYTHONPATH` had 14 subprocess failures
  because some tests overwrite that variable and could not import external
  pytest. Loading tools through the separate regression venv fixed the test
  harness; no production code or test assertions were changed to resolve it.
- Regenerating the existing lock produced byte-identical output, SHA256
  `041aca82c90f694da019898e31caa09a97c2312c8678bedf37f0cd86b5cb7655`.
- Clean installation used pip 24.0, hash checking, binary wheels only and the
  complete pinned closure. No source builds or unresolved transitive ranges.

## Measured dependency size

| Measurement | Exact bytes | Approximate size |
|---|---:|---:|
| Selected 70 compressed wheel archives | 306,752,335 | 292.5 MiB |
| Installed runtime files listed in distribution RECORDs | 1,344,791,529 | 1.25 GiB |
| Entire site-packages after inference, including bootstrap pip | 1,358,650,473 | 1.27 GiB |

Archive sizes were matched to the actual install report by SHA256; unrelated
cache artifacts and tooling wheels were excluded. Installed sizes include
generated bytecode where recorded, but exclude model weights, base Python/OS,
download caches and any future image-layer overhead. These are dependency
measurements, not Docker compressed/uncompressed image measurements.

## Compatibility and remaining boundaries

The ordinary Python requirements entry point and `bin/install.sh` workflow retain
their dependency ranges and CPU-first behavior. Optional provider entry points
are unchanged. The Docker input adds a platform-specific CPU selection and a
fully pinned closure; it does not change application/Profile/network behavior.

Only Python 3.12.3 has been installed and run. Other CPython 3.12 patch releases
share the wheel ABI, but `Requires-Python`, installer behavior or wheel selection
can differ; validate the concrete patch/base digest chosen for the image. The
generator rejects wheels above the glibc 2.34 AMD64 baseline. Compatibility with
that baseline is inferred from wheel tags; actual runtime testing here used
glibc 2.39, not a slim Docker OS. ARM64 and musl are unsupported by this lock.

No Dockerfile, Compose, GitHub Actions or image build was performed. OS-level
network isolation, read-only model mounts, official image Retry/download policy,
non-root container permissions, safe-copy existing-data retrieval and Docker
Bridge integration remain roadmap gates. No production database, schema,
Embedding Profile or vector index was modified.

The later image stage should consume the lock and verification commands in
[`README.md`](README.md), select a concrete compatible Python base/digest, then
run its own image-specific gates. The full roadmap remains in
`docs/project-management/DOCKER_RELEASE_ROADMAP.md` (locally gitignored).
