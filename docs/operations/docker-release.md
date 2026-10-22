# Docker release operations (maintainers)

The release is manual: a Git tag selects source, an explicitly dispatched Actions
workflow builds/tests/pushes the image, and a maintainer later publishes GitHub
Release notes. Pushes, merges, tags and GitHub Releases do not automatically
publish an image.

## Readiness and authorization

- **Release Ready:** release controls/workflow and automated tests are complete,
  local compatibility checks pass, and the final code passed the existing Docker
  CI Cold acceptance on `ubuntu-24.04`. The image has not been publicly released.
- **Released:** separately authorized version publication, anonymous download,
  and the applicable `latest` promotion have completed. This does not create a
  GitHub Release or replace the Phase 3.3 installation acceptance.

See [Phase 3.2 evidence](../../docker/PHASE3.2-VALIDATION.md). Before the first
publication, also confirm the production-copy retrieval, host/image vector
comparison and `runtime-local` regression results. An implementation or mock test
pass is not evidence of successful GHCR publication.

Local candidate acceptance does not contact a registry or push:

```bash
.venv/bin/python .github/scripts/docker_release.py check \
  --image YOUR_LOCAL_IMAGE --artifacts /tmp/remember-me-candidate-check
```

The existing CI is separate. After the final code is on master, run its Cold,
Warm and Source-probe modes on independent runners; it writes only the private
build cache. Both workflows use `ubuntu-24.04`.

## Publish a version (separate authorization required)

1. Check the evidence, clean working tree, version choice and full source SHA.
   The selected commit must be in master history. Create and push only its tag:

   ```bash
   git switch master
   git pull --ff-only origin master
   git status --short
   git rev-parse HEAD
   git tag -a v1.0.0 FULL_REVIEWED_COMMIT_SHA -m "Remember Me v1.0.0"
   git rev-parse 'v1.0.0^{commit}'
   git push origin refs/tags/v1.0.0
   ```

2. In **Actions → docker-release → Run workflow**, choose **master**, enter
   `tag=v1.0.0`, the reviewed full `expected_commit`, `operation=publish`, leave
   `existing_digest` empty, and explicitly enable `confirm_publish` only after
   authorization and Go/No-Go checks. Use the actual chosen version throughout.
3. Check the run Summary and artifacts: target commit, candidate acceptance,
   version push status, local image ID and **registry manifest digest**. They are
   different identifiers. A successful build alone is not a successful release.
4. The fresh `verify-public` runner must pull both the version and digest without
   credentials. Only then may `promote-latest` update the stable alias.

The workflow uses its short-lived `GITHUB_TOKEN` with `contents: read` and
`packages: write`, not a long-lived PAT. The official package and private cache
must permit this repository's Actions. If automatic permission inheritance is
disabled, a package administrator must grant the repository write access under
**Manage Actions access**.

## First package: manually enable Public

The first push normally creates a **Private** package. Its anonymous verification
will fail, leaving the version in place and `latest` unchanged. This is an
incomplete release, not a reason to republish the version.

The **user** must navigate to the organization **Packages → remember-me → Package
settings → Change visibility → Public**, verify the package name, and confirm.
**Once Public, the package cannot be changed back to Private.** The distinct
**remember-me-buildcache** package must remain Private. The workflow never changes
either package's visibility.

After making the package Public, use `resume` as described below. Future versions
of the official package are public when pushed; review the candidate beforehand.

## Recover an incomplete release

Inspect the original Summary, `release.json`, push log and actual Registry state.
If the version exists, do not retry `publish` or move its tag.

Dispatch the same workflow on master with the same Tag/full commit,
`operation=resume`, and `existing_digest=sha256:...` copied from the original
evidence. Explicitly confirm the authorized recovery operation. It pulls that
immutable digest and repeats acceptance and anonymous verification; it never
builds or overwrites a version. The current version tag must still match the
supplied digest and commit. A mismatch stops recovery.

If a push failed and the version is conclusively absent, `publish` may be retried.
Authentication, network or server errors do not prove absence. If a committed
digest cannot be tied to the tested candidate, investigate before proceeding.

## Version policy and verification

- Git `v1.0.0` maps to image `:1.0.0`; `v1.1.0-rc.1` maps to `:1.1.0-rc.1`.
  SemVer build metadata (`+...`) is unsupported; no major/minor aliases are made.
- Published version tags are never overwritten by the workflow. Fixes use a new
  version. Protect Git `v*` against update/deletion through repository tag rules.
- Prereleases never update `latest`. Stable versions advance it only when newer
  than the current stable version and after anonymous acceptance. A maintenance
  release older than the current stable is published without downgrading `latest`.
- All releases and recoveries share one concurrency group. Running releases are
  not canceled; queued runs can be replaced, so check the actual run status.
- Versions are retained; there is no automatic package/version cleanup. Users
  roll back by selecting a retained version or digest, subject to data-schema and
  embedding-profile compatibility and their complete instance backup.

For an independent anonymous check, use an empty Docker configuration, substitute
the actual version/digest, and compare the pull output with the run evidence:

```bash
ANON_CONFIG=$(mktemp -d)
docker --config "$ANON_CONFIG" pull --platform linux/amd64 \
  ghcr.io/tech-kuiperbelt/remember-me:1.0.0
docker --config "$ANON_CONFIG" pull --platform linux/amd64 \
  ghcr.io/tech-kuiperbelt/remember-me@sha256:RECORDED_MANIFEST_DIGEST
```

## Manually publish GitHub Release

After the Phase 3.3 README (`docker run` and optional Compose) and clean-host
installation acceptance are complete, the user opens **Releases → Draft a new
release**, selects the **existing tested tag**, reviews the notes, and clicks
**Publish release** (or saves a draft first). Mark prereleases appropriately;
do not mark an older maintenance version as the newest release.

Include the Tag/full commit, image version/manifest digest, AMD64 support, model
revision, changes, installation link, backup/upgrade/rollback requirements, known
limitations and Actions evidence. GitHub's latest-release flag is separate from
the image's `latest` tag. The workflow never creates a draft or public GitHub
Release, and publishing Release notes does not trigger a build.
