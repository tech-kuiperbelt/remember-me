# Linux standalone prototype

Build one Linux x86_64 directory with Portable CPython, the existing CPU production
lock, complete fixed-revision BGE-M3 dense runtime, app and Web UI. No tray,
AppImage, publishing or cross-distribution support claim is part of this task.

From the source root, with Bash, Git, curl, tar, coreutils, network access and
several GB of free disk space:

```bash
bash packaging/standalone/build.sh
```

No developer venv is copied or required. The build downloads the fixed CPython
archive in `runtime.env`, checks SHA256, installs `docker/pip-bootstrap.lock` and
`requirements/docker-cpu-py312-linux-amd64.lock` with hashes and binary wheels only,
then uses `docker/package_model.py` to download and verify the existing pinned
model manifest. The bundled Python verifies stdlib/FTS5/spawn, exact dependencies,
CPU Torch and real offline BGE-M3 inference before reporting success.

To avoid downloading model bytes already available in a trusted fixed snapshot:

```bash
bash packaging/standalone/build.sh --model-source /path/to/5617a9f61b028005a4858fdac845db406aefb181
```

That source is read only, each file is verified, and output receives its own copy.
The model packager rejects input links escaping the supplied snapshot directory.
Never point this option at user data or copy an entire unverified model cache.

Default output is `dist/remember-me-standalone-linux-x86_64/`; cache is
`cache/standalone/`. Both are Git-ignored. Existing output is always refused,
including partial builds (marked `.build-incomplete`); inspect it and choose a new
`--output DIR` for a fresh build. `--cache-dir DIR` selects another download cache.
Do not commit generated Python, wheels, models or output directories.

Copy the complete output directory elsewhere, preserving symlinks, and run:

```bash
./start.sh --data-dir /absolute/path/to/separate-test-data --port 1315
```

Choose an unused port and isolated data directory. `--data` is accepted by the
existing server parser as the `--data-dir` abbreviation. Other existing flags are
forwarded directly. Foreground Ctrl+C stops gracefully; rerun the same command to
restart with the same data. The script does not use system Python/pip/Docker,
source a host `.env`, or activate a venv. It uses CPU Sentence Transformers and the
bundled model root; `--model-dir` remains the existing explicit CLI override.
It disables Transformers' optional online safetensors conversion: the pinned
complete `pytorch_model.bin` is used as-is, and no conversion thread should keep
the foreground process alive after Ctrl+C. Weights and dependency pins are unchanged.

## Data Location

On Linux, `start.sh` defaults to `~/.remember-me/`; `--data-dir` takes precedence
over `MEMORY_DATA_DIR`, and relative paths resolve against the standalone directory.
The bundled model is under `models/`, separate from the default data directory.
Switching installation methods does not migrate data; replacing the program does
not delete the persistent data directory. See the [installation guide](../../docs/installation/standalone.md)
or [中文指南](../../docs/installation/standalone.zh-CN.md).

`app/`, `web/`, the shipped `release.jsonc`, licenses and Python/wheel metadata are
retained. `build-manifest.json` records source commit/dirty state, controlled input
hashes, installed versions, model revision and shipped application-resource hashes.
This is controlled-input repeatability, not byte-for-byte identical builds.

`licenses/runtime/` supplements the install-only archive with the same upstream
release's complete license catalog and its explicit OpenSSL 3 license. Both inputs
have fixed SHA256 and official Git-blob checks. The upstream catalog also describes
unused components and legacy OpenSSL; it is not a linked-component inventory, and
the separate Apache-2.0 text applies to this runtime's OpenSSL 3. Source URLs and
hashes are in the build manifest. CPython and wheel license texts remain in place.

The current dependency lock targets glibc >=2.34. OS shared libraries and CPU
compatibility still require validation of the actual output; a relocatable Python
alone does not prove a supported distro matrix. Preserve bundled licenses when
copying or redistributing. Existing installation-method UI may display UNKNOWN;
the prototype does not introduce a new application deployment mode.
