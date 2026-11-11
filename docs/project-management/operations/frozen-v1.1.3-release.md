# Frozen v1.1.3 Beta release

This procedure prepares and publishes one already-built bundle. It does not use
the older Docker `publish` operation, which rebuilds. Ordinary pushes never publish.

Before freezing: integrate the candidate through a normal PR, maintain all five
`release.jsonc` arrays last, merge master, record the exact merge SHA and build all
four platforms at that SHA. Do not commit build results afterward. Keep readiness
reports with the frozen bundle and Draft assets, outside the source history.

Archive original AppImage/Setup/DMG, sidecars, manifests, CI evidence and the split
Docker archive both in the local long-term release directory and in the main
repository's nonpublic Draft. Private GHCR staging is an additional copy; never
change its visibility or use the public formal version tag during preparation.
Upload without `--clobber`; interrupted uploads must be inspected, not overwritten.

`release-manifest.json` is generated after building and references the three
installers, sidecars and all frozen evidence/archives by file name, size and SHA256.
It also records source SHA, exact `release.jsonc` hash, normalized Notes hash,
Docker image ID and ordered archive part names. The manifest excludes itself and
the later `mac-acceptance.json` to avoid self-reference. Record its SHA256 outside
the Draft; every invocation requires that trust root.

The Draft targets the full source SHA and is a Pre-release. Its Notes use the
prepared concise bilingual body. No Git Tag is created by Draft preparation.
The tester's separate HTTPS file must match the exact frozen DMG hash. Use
`docs/installation/macos-beta-acceptance.md`; upload an actual completed report
as `mac-acceptance.json` only after the tester responds. Never fill in a success
record from CI or from assumptions.

## Preflight (safe before Mac acceptance)

```bash
gh workflow run frozen-beta-release.yml --ref master \
  -f operation=preflight -f source_commit=FULL_FROZEN_SHA \
  -f manifest_sha256=FROZEN_MANIFEST_SHA256 -f confirm_go=false
```

This verifies Draft metadata, every archived byte, checksum sidecars, exact
master/source/JSONC, release history, private-image identity and actual Docker
load/offline inference/business smoke. It does not create a Tag, push an official
image, change latest or publish a Release. Archive the resulting evidence too.

## Final GO (only after real-Mac acceptance and maintainer approval)

The final Ready-to-Publish bundle contains this command with concrete values:

```bash
gh workflow run frozen-beta-release.yml --ref master \
  -f operation=publish -f source_commit=FULL_FROZEN_SHA \
  -f manifest_sha256=FROZEN_MANIFEST_SHA256 -f confirm_go=true
```

No source edits, builds or asset replacements occur. The workflow rechecks the
same frozen bundle and actual Mac receipt, pushes the already-tested Docker image,
verifies it anonymously, creates the Tag only if absent, and publishes the existing
Draft as a Pre-release with GitHub latest disabled. It verifies public assets,
the image and unchanged Docker latest afterward. A retry inspects identity first;
an identical published image/Tag/Release is verified rather than recreated.
Any changed asset, source, higher version, conflicting Tag/image or Tag-only state
stops the run. Never move/delete a Tag or overwrite an existing version to recover.

Save the post-publication run and its evidence with the same archive. A failed
response may have committed a remote write; inspect state and rerun this entrypoint
with the same frozen SHA and manifest hash, never the older rebuilding flow.
