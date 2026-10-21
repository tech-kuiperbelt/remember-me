# CPU production dependencies (Phase 1)

This lock targets **CPython 3.12, Linux AMD64, glibc >=2.34**. The reference
interpreter is Python 3.12.3. Alpine/musl, ARM64 and other Python minor versions
require separate resolution and validation.

`requirements-runtime.txt` is the single shared list of production inputs.
The existing `requirements.txt` includes it plus pytest, preserving the Python
installation entry point and `bin/install.sh`. Optional FastEmbed entry points
are unchanged. `docker-cpu.in` adds only the official, platform-specific CPU
Torch wheel; `docker-cpu-py312-linux-amd64.lock` pins the complete runtime closure
and approved SHA256 hashes. Do not edit the generated lock by hand or use a
developer environment's `pip freeze` as input.

## Clean installation / later Dockerfile consumption

From the repository root, create a fresh venv, then:

```bash
python3.12 -m venv /tmp/remember-me-cpu
/tmp/remember-me-cpu/bin/python -m pip install \
  --require-hashes --only-binary=:all: \
  -r requirements/docker-cpu-py312-linux-amd64.lock
/tmp/remember-me-cpu/bin/python -m pip check
/tmp/remember-me-cpu/bin/python bin/verify_cpu_runtime.py
```

The lock selects Torch by an exact official CPU wheel URL and hash, following
the CPU-before-general-resolution approach used by `bin/install.sh`. It does
not add PyTorch as a general extra index. Other runtime wheels resolve through
PyPI. `--require-hashes` rejects added/unpinned dependencies and altered artifacts;
`--only-binary` rejects source builds. pip is a bootstrap tool, excluded from the
application lock; the later image build must also pin its Python base/digest.

A future Dockerfile should copy the lock first, install it into a clean Python
3.12 environment with the command above and run `pip check`. After copying the
application and verification script, run the runtime/import verification. It must not install the development
`requirements.txt`, optional FastEmbed requirements, or a host `.venv`. These
files do not create or implement a Docker image.

## Regenerating or updating the lock

Use an independent Python 3.12.3 tooling environment with **pip-tools 7.6.2**
and **pip 24.0** (neither is an application dependency):

```bash
python3.12 -m venv /tmp/remember-me-lock-tools
/tmp/remember-me-lock-tools/bin/python -m pip install 'pip==24.0' 'pip-tools==7.6.2'
/tmp/remember-me-lock-tools/bin/python bin/lock_cpu_requirements.py
```

This preserves compatible existing pins. Add `--upgrade` for a deliberate full
refresh. To change Torch,
select the matching `cp312` / `manylinux_2_28_x86_64` `+cpu` wheel and SHA256 from
the [official CPU index](https://download.pytorch.org/whl/cpu/torch/), then update
`docker-cpu.in` before regenerating. Check all resulting pins and rerun clean
installation, CPU validation, real model inference and relevant regressions.
The script uses pip-tools for resolution, then pip's dry-run installation report
with `--ignore-installed` to select and hash only target-platform wheels. It
checks the manylinux baseline and rejects GPU/optional/test dependencies. No
package list is copied from the tooling venv. pip-tools' `--allow-unsafe` is
intentional: Torch requires setuptools at runtime, so it
must be included and hashed rather than omitted by pip-tools' default policy.

The reference Python patch version fixes the resolver's `Requires-Python`
selection. CPython 3.12 patch releases normally share `cp312` wheel tags; the
lock fixes versions and permitted hashes rather than allowing a new resolution.
Other patch versions still need installation validation before release. A single
selected wheel hash is saved per package; this is version/artifact integrity
reproducibility, not a claim of identical byte-for-byte virtual environments.

## Offline model verification

Use an existing HF cache with `models--BAAI--bge-m3/refs/main` pointing at
`5617a9f61b028005a4858fdac845db406aefb181` and its complete snapshot:

```bash
/tmp/remember-me-cpu/bin/python bin/verify_cpu_runtime.py \
  --model-cache /path/to/existing/sentence-transformers-cache \
  --expected-profile-id ep_6134c0b15d2bb471
```

The script checks exact installed versions, excludes non-production packages,
checks CPU Torch and its direct-wheel provenance, imports runtime modules, tests
the slow tokenizer, loads/warmups the model, and checks two normalized 1024-D
vectors. A process-local audit hook forbids network connections during model
verification. The physical snapshot is verified through the cache; no runtime
revision is added to the logical model name. The optional profile ID compares
identity only and never opens a business database. It does not replace testing
retrieval against a safe production-data copy or vector comparisons across
environments.

Actual measurements and remaining boundaries are recorded in
[`VALIDATION.md`](VALIDATION.md).

References: [PyTorch CPU install commands](https://pytorch.org/get-started/previous-versions/),
[pip-tools](https://pip-tools.readthedocs.io/en/stable/),
[pip hash checking](https://pip.pypa.io/en/stable/topics/secure-installs/).
