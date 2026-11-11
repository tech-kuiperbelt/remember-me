#!/usr/bin/env python3
"""Verify or publish the frozen 1.1.3 Beta bundle. Never builds or writes latest.

The manifest SHA256 is the out-of-band trust root. A Draft's mutable name, notes,
or assets alone never authorize publication. GitHub/Registry writes occur only
in ``publish --confirm-go`` after the matching real-Mac acceptance is supplied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_release as release
import docker_checks as checks

REPOSITORY = release.REPOSITORY
VERSION = "1.1.3"
TAG = "v" + VERSION
MANIFEST = "release-manifest.json"
MAC_ACCEPTANCE = "mac-acceptance.json"
INSTALLERS = (
    "remember-me-1.1.3-linux-x86_64.AppImage",
    "remember-me-windows-x64-beta-setup.exe",
    "remember-me-macos-arm64-beta.dmg",
)
MAC_CHECKS = (
    "browser_download", "sha256", "quarantine", "finder_install",
    "gatekeeper_per_app", "icon", "launch", "memory_write", "restart_persistence",
)
HEX = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")


def require(condition, message):
    release.require(condition, message)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(data)
    return value.hexdigest()


def notes_sha256(body):
    return hashlib.sha256((body.rstrip() + "\n").encode("utf-8")).hexdigest()


def run(argv, *, cwd=None):
    # Capture output: gh auth token must never be echoed by this command helper.
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
    require(result.returncode == 0, f"{argv[0]} command failed (exit {result.returncode})")
    return result.stdout


def gh_json(path):
    return json.loads(run(["gh", "api", path]))


def validate_manifest(value):
    require(isinstance(value, dict) and value.get("version") == VERSION, "Only frozen 1.1.3 Beta is supported")
    require(release.SHA.fullmatch(value.get("source_commit", "")) is not None, "Full source_commit required")
    for key in ("release_sha256", "notes_sha256"):
        require(HEX.fullmatch(value.get(key, "")) is not None, f"Invalid {key}")
    assets = value.get("assets")
    require(isinstance(assets, list) and 1 <= len(assets) <= 100, "Invalid asset list")
    names = set()
    for item in assets:
        require(isinstance(item, dict), "Invalid asset record")
        name = item.get("name", "")
        require(NAME.fullmatch(name) is not None and name not in names, "Invalid or duplicate asset name")
        require(name not in (MANIFEST, MAC_ACCEPTANCE), "Manifest/acceptance cannot be self-referenced")
        require(type(item.get("size")) is int and item["size"] > 0, f"Invalid size: {name}")
        require(HEX.fullmatch(item.get("sha256", "")) is not None, f"Invalid hash: {name}")
        names.add(name)
    require(set(INSTALLERS) | {name + ".sha256" for name in INSTALLERS} <= names,
            "All three installers and checksum files are required")
    docker = value.get("docker", {})
    require(isinstance(docker, dict), "Invalid Docker record")
    require(release.DIGEST.fullmatch(docker.get("image_id", "")) is not None, "Invalid Docker image_id")
    if docker.get("digest") is not None:
        release.digest(docker["digest"])
    if "staging_ref" in docker or "staging_digest" in docker:
        require(docker.get("staging_ref") == f"ghcr.io/{release.CACHE}:candidate-{value['source_commit']}",
                "Staging must use the exact private commit-specific candidate reference")
        release.digest(docker.get("staging_digest", ""))
        require(docker.get("digest", docker["staging_digest"]) == docker["staging_digest"],
                "Staging and promised public manifest digest differ")
    parts = docker.get("archive_parts")
    require(isinstance(parts, list) and parts and all(isinstance(n, str) and n in names for n in parts)
            and len(set(parts)) == len(parts), "Invalid Docker archive parts")
    require(not set(parts) & (set(INSTALLERS) | {n + ".sha256" for n in INSTALLERS}),
            "Docker archive overlaps installers")
    return value


def validate_acceptance(value, manifest):
    require(isinstance(value, dict), "Invalid Mac acceptance record")
    require(value.get("source_commit") == manifest["source_commit"], "Mac acceptance source differs")
    dmg = next(a for a in manifest["assets"] if a["name"] == INSTALLERS[2])
    require(value.get("dmg_sha256") == dmg["sha256"], "Mac acceptance DMG differs")
    require(isinstance(value.get("tester"), str) and value["tester"].strip(), "Mac tester identity required")
    require(isinstance(value.get("accepted_at"), str) and value["accepted_at"].strip(), "Mac acceptance date required")
    observed = value.get("checks", {})
    require(isinstance(observed, dict) and all(observed.get(k) is True for k in MAC_CHECKS),
            "Real-Mac acceptance is incomplete")
    return value


def verify_file(path, record):
    require(path.is_file() and not path.is_symlink(), f"Missing regular asset: {record['name']}")
    require(path.stat().st_size == record["size"], f"Asset size mismatch: {record['name']}")
    require(sha256(path) == record["sha256"], f"Asset hash mismatch: {record['name']}")


def download_asset(name, work, *, refresh=False):
    require(NAME.fullmatch(name) is not None, "Invalid download name")
    path = work / name
    if refresh or not path.exists():
        run(["gh", "release", "download", TAG, "--repo", REPOSITORY,
             "--pattern", name, "--dir", str(work), *(["--clobber"] if refresh else [])])
    return path


def verify_draft(metadata, manifest, manifest_hash):
    require(metadata.get("tag_name") == TAG, "Wrong Release tag")
    require(metadata.get("target_commitish") == manifest["source_commit"], "Release target is not frozen SHA")
    require(metadata.get("prerelease") is True, "Release must remain a Pre-release")
    require(type(metadata.get("draft")) is bool, "Missing Draft state")
    require(notes_sha256(metadata.get("body", "")) == manifest["notes_sha256"], "Release notes changed")
    assets = metadata.get("assets", [])
    require(isinstance(assets, list), "Invalid remote assets")
    by_name = {a["name"]: a for a in assets}
    require(len(by_name) == len(assets), "Duplicate remote asset name")
    expected = {a["name"] for a in manifest["assets"]} | {MANIFEST}
    require(expected <= by_name.keys() and by_name.keys() <= expected | {MAC_ACCEPTANCE},
            "Release asset set differs from frozen manifest")
    for record in manifest["assets"]:
        observed = by_name[record["name"]]
        require(observed.get("state") == "uploaded" and observed.get("size") == record["size"],
                f"Remote asset is incomplete: {record['name']}")
        require(observed.get("digest") == "sha256:" + record["sha256"], f"Remote asset hash differs: {record['name']}")
    require(by_name[MANIFEST].get("digest") == "sha256:" + manifest_hash, "Remote manifest changed")


def publication_plan(manifest, history, tag_commit, image_info, is_draft):
    """Pure fail-closed retry plan; only the frozen identity may be resumed."""
    current = release.version_key(VERSION)
    require(all(release.version_key(v) <= current for v in history), "Newer release history blocks publication")
    if tag_commit is not None:
        require(tag_commit == manifest["source_commit"], "Existing Tag points to another source")
        require(image_info is not None, "Tag-only release cannot be recovered by publishing a new image")
    if image_info is not None:
        release.identity(image_info, VERSION, manifest["source_commit"],
                         frozen_digest(manifest), manifest["docker"]["image_id"])
    if VERSION in history:
        require(tag_commit is not None or image_info is not None, "Existing version has no provable frozen identity")
    if not is_draft:
        require(tag_commit is not None and image_info is not None, "Published Release identity is incomplete")
    return {"create_tag": tag_commit is None, "push_image": image_info is None, "publish_release": is_draft}


def registry_client():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or run(["gh", "auth", "token"]).strip()
    actor = os.environ.get("GITHUB_ACTOR") or gh_json("user")["login"]
    return release.Registry(token, actor)


def frozen_digest(manifest):
    return manifest["docker"].get("digest") or manifest["docker"].get("staging_digest")


def verify_staging(registry, manifest):
    """A private, digest-pinned Registry copy preserves compressed layer bytes."""
    docker = manifest["docker"]
    if "staging_ref" not in docker:
        return
    release.cache_gate(registry)
    record = registry.manifest(release.CACHE, docker["staging_digest"])
    config_digest = release.digest(record["manifest"]["config"]["digest"])
    _, body = registry.resource(release.CACHE, "blobs/" + config_digest)
    require("sha256:" + hashlib.sha256(body).hexdigest() == config_digest, "Staging config hash differs")
    config = json.loads(body)
    require(config.get("os") == "linux" and config.get("architecture") == "amd64", "Staging platform differs")
    release.identity({"image_id": config_digest, "digest": record["digest"],
                      "labels": config.get("config", {}).get("Labels", {})},
                     VERSION, manifest["source_commit"], frozen_digest(manifest), docker["image_id"])
    # Exercise the exact copy parser/auth/manifest path without pushing. Docker
    # save/load can recompress layers; this path retains the registry manifest.
    source = f"ghcr.io/{release.CACHE}@{docker['staging_digest']}"
    preview = run(["docker", "buildx", "imagetools", "create", "--dry-run", "--prefer-index=false",
                   "--tag", release.IMAGE + ":" + VERSION, source])
    require(json.loads(preview) == record["manifest"], "Registry-copy preview changes the frozen manifest")


def remote_state(source, registry, manifest, metadata):
    tags = release.remote_versions(source)
    tag_commit = release.remote_tag(source, TAG)["commit"] if TAG in tags else None
    history = {release.tag_version(t) for t in tags} | registry.published_versions()
    image = registry.existing_version(VERSION)
    return publication_plan(manifest, history, tag_commit, image, metadata["draft"]), image


def check_source(source, manifest):
    source = Path(source).resolve()
    require(run(["git", "rev-parse", "HEAD"], cwd=source).strip() == manifest["source_commit"], "Checkout differs from frozen source")
    require(not run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source), "Source checkout has tracked modifications")
    master = run(["git", "ls-remote", "origin", "refs/heads/master"], cwd=source).split()
    require(master and master[0] == manifest["source_commit"], "Remote master differs from frozen source")
    document, digest = release.release_at_commit(source, manifest["source_commit"])
    require(document.version == VERSION and digest == manifest["release_sha256"], "Committed release.jsonc differs")


def load_and_verify(manifest, work, source):
    archive = work / "frozen-docker.tar.gz"
    with archive.open("wb") as output:
        for name in manifest["docker"]["archive_parts"]:
            with (work / name).open("rb") as part:
                shutil.copyfileobj(part, output, 1024 * 1024)
    run(["docker", "load", "--input", str(archive)])
    image = manifest["docker"]["image_id"]
    observed = json.loads(run(["docker", "image", "inspect", image]))[0]
    release.identity({"image_id": observed["Id"], "labels": observed["Config"].get("Labels", {}), "digest": None},
                     VERSION, manifest["source_commit"], image_id=image)
    release.verify_release_file(image, VERSION, manifest["release_sha256"])
    checks.verify_candidate(image, source, work / "runtime-verification")
    return image


def release_metadata():
    # REST /releases/tags/<tag> finds published releases only. Before the Tag
    # exists, authenticated listings include the Draft's pending tag instead.
    found = []
    for page in range(1, 101):
        rows = gh_json(f"repos/{REPOSITORY}/releases?per_page=100&page={page}")
        require(isinstance(rows, list) and len(rows) <= 100, "Invalid Release listing")
        found.extend(row for row in rows if row.get("tag_name") == TAG)
        if len(rows) < 100:
            break
    else:
        raise release.Failure("Release listing exceeded pagination limit")
    require(len(found) == 1, "Expected exactly one frozen Release/Draft")
    identifier = found[0].get("id")
    require(type(identifier) is int and identifier > 0, "Invalid Release id")
    return gh_json(f"repos/{REPOSITORY}/releases/{identifier}")


def preflight(args):
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    require(HEX.fullmatch(args.manifest_sha256) is not None, "Expected manifest SHA256 required")
    manifest_path = download_asset(MANIFEST, work)
    require(sha256(manifest_path) == args.manifest_sha256, "Frozen manifest hash differs")
    manifest = validate_manifest(json.loads(manifest_path.read_text()))
    metadata = release_metadata()
    verify_draft(metadata, manifest, args.manifest_sha256)
    check_source(args.source, manifest)
    for record in manifest["assets"]:
        verify_file(download_asset(record["name"], work), record)
    assets = {a["name"]: a for a in manifest["assets"]}
    for name in INSTALLERS:
        contents = (work / (name + ".sha256")).read_text().split()
        require(len(contents) == 2 and contents[0] == assets[name]["sha256"]
                and contents[1].lstrip("*") == name, f"Checksum sidecar differs: {name}")
    registry = registry_client()
    plan, existing = remote_state(args.source, registry, manifest, metadata)
    verify_staging(registry, manifest)
    load_and_verify(manifest, work, args.source)
    result = {"state": "PREFLIGHT_PASSED_WAITING_FOR_MAC_ACCEPTANCE_AND_MAINTAINER_GO", "source_commit": manifest["source_commit"],
              "manifest_sha256": args.manifest_sha256, "draft_release_id": metadata["id"], "plan": plan}
    (work / "preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return manifest, metadata, registry


def publish(args):
    require(args.confirm_go, "Explicit maintainer --confirm-go is required")
    manifest, metadata, registry = preflight(args)
    acceptance_path = download_asset(MAC_ACCEPTANCE, args.work.resolve(), refresh=True)
    acceptance = validate_acceptance(json.loads(acceptance_path.read_text()), manifest)
    # Match the current uploaded acceptance too; a cached local record cannot
    # replace a changed or withdrawn tester receipt.
    metadata = release_metadata()
    receipt = next((a for a in metadata["assets"] if a["name"] == MAC_ACCEPTANCE), None)
    require(receipt and receipt.get("state") == "uploaded" and receipt.get("size") == acceptance_path.stat().st_size,
            "Mac acceptance asset is absent or incomplete")
    require(receipt.get("digest") == "sha256:" + sha256(acceptance_path), "Mac acceptance asset hash differs")
    verify_draft(metadata, manifest, args.manifest_sha256)
    plan, _ = remote_state(args.source, registry, manifest, metadata)
    latest_before = registry.inspect("latest", missing=True)
    latest_digest = latest_before["digest"] if latest_before else None
    # Publish only the already-verified frozen image. A failed response is not
    # blindly retried: the next invocation first inspects the remote identity.
    if plan["push_image"]:
        image = release.IMAGE + ":" + VERSION
        if "staging_ref" in manifest["docker"]:
            verify_staging(registry, manifest)
            source = f"ghcr.io/{release.CACHE}@{manifest['docker']['staging_digest']}"
            run(["docker", "buildx", "imagetools", "create", "--prefer-index=false", "--tag", image, source])
        else:
            run(["docker", "tag", manifest["docker"]["image_id"], image])
            run(["docker", "push", image])
    published = registry.existing_version(VERSION)
    release.identity(published, VERSION, manifest["source_commit"],
                     frozen_digest(manifest), manifest["docker"]["image_id"])
    journal = release.Journal(args.work / "public-verification", {})
    verify_args = argparse.Namespace(tag=TAG, commit=manifest["source_commit"], existing_digest=published["digest"],
                                     image_id=manifest["docker"]["image_id"], release_sha256=manifest["release_sha256"])
    release.verify_public(verify_args, release.Registry(), journal)
    metadata = release_metadata()
    verify_draft(metadata, manifest, args.manifest_sha256)
    plan, _ = remote_state(args.source, registry, manifest, metadata)
    if plan["create_tag"]:
        run(["gh", "api", "--method", "POST", f"repos/{REPOSITORY}/git/refs",
             "-f", "ref=refs/tags/" + TAG, "-f", "sha=" + manifest["source_commit"]])
        require(release.remote_tag(args.source, TAG)["commit"] == manifest["source_commit"], "Created Tag differs")
    metadata = release_metadata()
    verify_draft(metadata, manifest, args.manifest_sha256)
    plan, _ = remote_state(args.source, registry, manifest, metadata)
    if plan["publish_release"]:
        run(["gh", "release", "edit", TAG, "--repo", REPOSITORY, "--draft=false", "--prerelease", "--latest=false"])
    final = release_metadata()
    verify_draft(final, manifest, args.manifest_sha256)
    require(final["draft"] is False and final["prerelease"] is True, "Release publication not verified")
    latest_after = registry.inspect("latest", missing=True)
    require((latest_after["digest"] if latest_after else None) == latest_digest, "Docker latest changed during publication")
    status, _, body = release.Registry().request(f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{TAG}")
    require(status == 200, "Published Release is not anonymously readable")
    verify_draft(json.loads(body), manifest, args.manifest_sha256)
    for asset in final["assets"]:
        run(["curl", "--fail", "--silent", "--show-error", "--location", "--head",
             "--connect-timeout", "30", "--max-time", "120", asset["browser_download_url"]])
    result = {"state": "PUBLISHED", "source_commit": manifest["source_commit"],
              "manifest_sha256": args.manifest_sha256, "docker_digest": published["digest"],
              "mac_acceptance_sha256": sha256(acceptance_path), "release_url": final["html_url"],
              "latest": "untouched"}
    (args.work / "publication.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "publish"))
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=release.ROOT)
    parser.add_argument("--confirm-go", action="store_true")
    args = parser.parse_args()
    try:
        publish(args) if args.command == "publish" else preflight(args)
    except (release.Failure, checks.Failure, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Release stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
