# Windows S1-A-P — native execution handoff

Status (2026-11-03): **local preparation only; S1-A remains incomplete**.
No Windows lock, wheel availability, DLL compatibility, inference success or resource
measurement is claimed. No business code, database/storage behavior or Launcher changes.

## Files and execution

- `build.ps1`: Windows x64 PowerShell 7 entrypoint; requires a nonexistent isolated
  work directory and 12 GiB free space. Downloads the approved PBS 3.12.15 /
  20261020 Windows asset with SHA256 verification. Uses only that Python.
- `prepare.py`: installs existing hash-pinned pip 26.2.1; extracts **all** existing
  production versions (including Torch 2.14.1+cpu) as constraints. Real Windows
  pip dry-run resolves binary wheels and records `resolve.json`. Only PyPI and
  official PyTorch wheel hosts are accepted. Creates `windows-candidate.lock`
  from actual URL/SHA256 metadata, downloads with `--require-hashes`, installs
  offline from wheelhouse and runs pip check. Any unavailable version/conflict
  stops the run; never relax pins. Windows-only additional transitive dependencies
  are resolver outputs requiring review, not approved production additions.
- `verify.py`: relocated prefix, stdlib/native features, subprocess/spawn,
  package versions and pip check, CPU-only Torch, live DLL path inventory,
  complete model offline inference, Chinese/English 1024-dimensional vectors,
  CLS pooling, Normalize and numerical comparison against normalized raw CLS.
- `../../.github/workflows/windows-s1a-poc.yml`: isolated native evidence job.

After separate authorization, on Windows PowerShell 7:

```powershell
./packaging/windows-poc/build.ps1 -Work "$env:RUNNER_TEMP/remember-me-windows-s1a"
```

Do not rerun over existing output. Retain failures and use a new work directory
for another experiment. No user data directory is opened. No application startup
is attempted: the existing `fcntl` storage import remains S1-B work.

## Integrity and layout

PBS archive URL/digest are literal in `build.ps1`; license URLs/digests reuse
`../standalone/runtime.env`. Source revision and all POC/input/resource hashes
are recorded in evidence manifests. Model uses the existing 11-file
`docker/bge-m3-manifest.json`, revision
`5617a9f61b028005a4858fdac845db406aefb181`, validating size, SHA256 and Git/LFS
identity through existing verification code. Download directly to one final
snapshot: no symlinks, hub blob duplicates, external model cache or second Python.
Runtime is moved after the build Python exits, then verified at its new prefix.
Output is an unpacked POC directory `bundle/` with app/web/release/license layout;
no shipping archive or Launcher is produced. Shipped version stays in release.jsonc.
This is not a release, so release.jsonc content is not changed.

During inference, cache/data/home variables point inside the work directory,
PATH permits only PBS and Windows System32, offline flags are enabled, and Python
socket connection attempts fail. Model is loaded by the explicit local revision
path. This tests the Python HTTP stack; it is not an OS firewall or proof that
arbitrary native libraries cannot network. Loaded modules must be inside PBS or
Windows; `loaded-modules.json` captures actual Torch/OpenMP/VC/SSL/SQLite DLL
origins for review. Missing native dependencies or external runner DLLs fail;
no automatic VC runtime download or repair is performed. A clean Windows VM
and firewall-level validation remain later acceptance work.

## Evidence and acceptance pending

Upload evidence only (14 days), not the multi-GB runtime/model/wheels. Inspect:

1. `build.log`, runner image/OS/disk records, exact source commit and input hashes.
2. Real native resolution report, candidate lock, wheel hashes and pip check;
   compare every shared version against production constraints, review any extras.
3. Portable prefix relocation, SSL context, ctypes, SQLite FTS5, subprocess, spawn.
4. CPU Torch and actual DLL origins (including Windows-provided VC runtime).
5. All 11 model files, true offline inference, two 1024D finite unit vectors,
   CLS and Normalize checks. `native-results.json` exists only on full success.
6. Bundle bytes/free disk; peak working set, peak pagefile commitment, current
   working set; model import/load and two-text encode timings. These are process
   counters, not whole-machine or process-tree peaks. Review runner metadata too.

On failure, partial evidence is retained and uploaded via `always()`; no success
file is synthesized. Initial resolution is online and trusts registry metadata;
subsequent downloads/install verify those recorded hashes. The generated lock is
an experimental candidate and requires human review before reuse or production.

## Remote procedure — NOT authorized by this preparation

Planned branch: **`poc/windows-s1a-native`** (not created or pushed here).
Only an exact `push` to that branch triggers this workflow, with an additional
repository/ref job guard. No workflow_dispatch, PR, tag, schedule, workflow_run,
Release or Docker calls. Existing Docker workflows accept workflow_dispatch only.
No Secrets, package write or contents write; checkout does not persist credentials.
Checkout/upload actions use the existing repository's full commit pins.
The windows-2022 image itself is GitHub-managed and mutable; record ImageVersion,
never claim that the OS/toolchain is bit-for-bit pinned.

Maintainer steps after approval:

1. Review/commit these materials and record the full reviewed commit SHA.
2. Explicitly authorize both pushing that commit to the dedicated test branch
   and the resulting Windows Actions execution/resource cost. Check remote branch
   state first; do not force-update it. Push only that branch, no tags or master.
3. Observe `windows-s1a-poc`, download evidence, compare artifact/source SHA.
4. Review failures or candidate lock/native results. Any changed versions,
   additional dependencies/redistributables or rerun fixes require review as
   appropriate. No automatic S1-B, Release, Docker publication or lock promotion.

GitHub documents branch-filtered push triggers and the default-branch requirement
for manual dispatch: [workflow triggers](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow),
[manual runs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

## Resource estimate and limitations

Planning estimates, **not measurements**: model files ~2.14 GiB; installed runtime,
wheels, sources and temporary downloads may total ~6–10 GiB. Require 12 GiB free
before network downloads; no preinstalled runner software is deleted. Model/load
peak RAM may be ~4–8+ GiB; CPU inference is potentially minutes. Budget one job
up to 90 minutes, with actual timing recorded. GitHub lists public Windows x64
standard runners at 4 CPU / 16 GB RAM / 14 GB SSD; private runners may have less
RAM, so check repository/runner capacity first:
[runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Insufficient disk/RAM, unavailable pinned Windows wheels, native DLL failures and
slow/failed model download are expected blocking risks. Hosted-runner success
cannot establish clean-machine portability. No larger paid runner is selected
without separate maintainer approval.

## Actual Linux local checks (2026-11-03)

Passed: both Python scripts parsed with Python 3 AST; workflow parsed with PyYAML;
exact push branch, read-only contents permission and full 40-character action pins
asserted; all reused input/resource paths exist; model manifest contains 11 files,
valid SHA256 fields and 2,293,331,623 total bytes; existing Docker workflow events
remain dispatch-only; `git diff --check` passed. Manual code review separated the
direct-URL download lock from the name/version/hash offline installation lock
(`windows-offline-candidate.lock`), since pip direct URLs can bypass `--no-index`.
No Windows candidate lock was generated locally.

Unavailable locally: `pwsh` and `actionlint`; PowerShell parser/native commands
and Actions-specific lint are pending. All native checklist items above remain
pending. Python AST/YAML checks do not execute Windows code or validate Windows
wheel availability. Changes are uncommitted; no push or remote run was performed.

## Minimal corrections after review (2026-11-03)

Disk rejection now records actual available/required bytes and the reason before
throwing, retaining the 12 GiB threshold. The workflow records disks and parses
build.ps1 with the PowerShell Parser before downloading; its independent preflight
evidence directory is uploaded on failure. Direct model inference now also checks
the existing EmbeddingSpec metadata identity `ep_6134c0b15d2bb471`, without service
or storage startup. This does not validate provider/storage integration (S1-B).
All three workflow event definitions were checked; the other two accept only
workflow_dispatch. See `docs/project-management/dist/WINDOWS_S1AP_LOCAL_CORRECTIONS_REPORT.md`
for actual local checks, remaining native checks and limitations.
