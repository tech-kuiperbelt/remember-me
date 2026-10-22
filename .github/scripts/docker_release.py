#!/usr/bin/env python3
"""Manual AMD64 release control. `check` never accesses a registry or pushes.

Production commands require workflow_dispatch on master and explicit confirmation.
No command changes package visibility, deletes versions, or creates a Release.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_checks as checks
import docker_ci as ci

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "tech-kuiperbelt/remember-me"
IMAGE = "ghcr.io/" + REPOSITORY
CACHE = "tech-kuiperbelt/remember-me-buildcache"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z")
MANIFEST_TYPES = ", ".join(("application/vnd.oci.image.manifest.v1+json",
                            "application/vnd.docker.distribution.manifest.v2+json"))


class Failure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def version(value):
    match = SEMVER.fullmatch(value)
    require(match is not None and len(value) <= 128, "Invalid SemVer/image tag")
    major, minor, patch, pre = match.groups()
    require(not pre or all(not (part.isdigit() and len(part) > 1 and part[0] == "0")
                           for part in pre.split(".")), "Leading zero in prerelease")
    return (int(major), int(minor), int(patch)), pre


def tag_version(tag):
    require(tag.startswith("v"), "Tag must start with v")
    version(tag[1:])
    return tag[1:]


def digest(value):
    require(DIGEST.fullmatch(value) is not None, "Invalid manifest digest")
    return value


def command(argv, *, cwd=None, env=None, log=None, check=True):
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True)
    if log:
        Path(log).write_text(result.stdout + result.stderr)
    if check and result.returncode:
        raise Failure(f"Command failed ({result.returncode}): {argv[0]}\n"
                      f"{result.stdout}{result.stderr}")
    return result


def workflow_gate(environment, *, confirmed=True):
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong repository")
    require(environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "Manual dispatch required")
    require(environment.get("GITHUB_REF") == "refs/heads/master", "Dispatch from master required")
    require(SHA.fullmatch(environment.get("GITHUB_SHA", "")) is not None, "Invalid workflow SHA")
    if confirmed:
        require(environment.get("CONFIRM_PUBLISH") == "true", "Explicit release confirmation required")


def remote_tag(repo, tag):
    rows = command(["git", "ls-remote", "--exit-code", "origin",
                    f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], cwd=repo).stdout.splitlines()
    values = dict(row.split("\t", 1)[::-1] for row in rows)
    oid = values.get(f"refs/tags/{tag}", "")
    commit = values.get(f"refs/tags/{tag}^{{}}", oid)
    require(SHA.fullmatch(oid) and SHA.fullmatch(commit), "Tag not found or not a commit")
    return {"tag_oid": oid, "commit": commit}


def resolve_source(repo, tag, expected, workflow_sha, destination=None):
    tag_version(tag)
    require(SHA.fullmatch(expected) and SHA.fullmatch(workflow_sha), "Full Git SHA required")
    before = remote_tag(repo, tag)
    command(["git", "fetch", "--no-tags", "origin", f"refs/tags/{tag}"], cwd=repo)
    oid = command(["git", "rev-parse", "FETCH_HEAD"], cwd=repo).stdout.strip()
    commit = command(["git", "rev-parse", "FETCH_HEAD^{commit}"], cwd=repo).stdout.strip()
    require(before == {"tag_oid": oid, "commit": commit}, "Tag changed during fetch")
    require(commit == expected, "Tag does not match expected_commit")
    command(["git", "merge-base", "--is-ancestor", commit, workflow_sha], cwd=repo)
    if destination is not None:
        command(["git", "worktree", "add", "--detach", str(destination), commit], cwd=repo)
        require(command(["git", "status", "--porcelain"], cwd=destination).stdout == "",
                "Build source is dirty")
        require(command(["git", "rev-parse", "HEAD"], cwd=destination).stdout.strip() == expected,
                "Wrong build checkout")
    return before


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Registry:
    """Small OCI/GitHub reader; writes are limited to Docker publish/promote calls.

    Endpoint injection is only used by local HTTP fixtures, never CLI inputs.
    Authentication/transport failures are never interpreted as missing manifests.
    """
    def __init__(self, token="", actor="", *, registry="https://ghcr.io",
                 api="https://api.github.com"):
        self.token, self.actor = token, actor
        self.registry, self.api = registry, api
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, url, headers=None, *, blob=False):
        req = urllib.request.Request(url, headers={"User-Agent": "remember-me-release", **(headers or {})})
        try:
            with self.opener.open(req, timeout=60) as response:
                return response.status, response.headers, response.read(8 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            if blob and exc.code in (302, 303, 307, 308):
                target = urllib.parse.urljoin(url, exc.headers.get("Location", ""))
                parsed = urllib.parse.urlsplit(target)
                require(parsed.scheme == "https" and parsed.hostname is not None
                        and parsed.hostname.endswith(".githubusercontent.com"),
                        "Untrusted blob redirect")
                # Signed blob URLs carry their own authorization. Never forward
                # a registry bearer or GitHub token to the storage host.
                return self.request(target)
            return exc.code, exc.headers, exc.read(65536)
        except (OSError, ValueError) as exc:
            raise Failure(f"HTTP transport failed: {type(exc).__name__}") from None

    def github(self, path, *, missing=False):
        require(bool(self.token), "GITHUB_TOKEN required")
        status, _, body = self.request(self.api + path, {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if status == 404 and missing:
            return None
        require(status == 200, f"GitHub metadata query failed: HTTP {status}")
        return json.loads(body)

    def package(self, repository):
        owner, name = repository.split("/", 1)
        return self.github(f"/orgs/{owner}/packages/container/{urllib.parse.quote(name, safe='')}",
                           missing=True)

    def bearer(self, repository):
        headers = {}
        if self.token:
            basic = base64.b64encode(f"{self.actor}:{self.token}".encode()).decode()
            headers["Authorization"] = "Basic " + basic
        query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
        status, _, body = self.request(self.registry + "/token?" + query, headers)
        require(status == 200, f"Registry authorization failed: HTTP {status}")
        result = json.loads(body)
        value = result.get("token") or result.get("access_token")
        require(isinstance(value, str) and bool(value), "Registry token missing")
        return value

    def resource(self, repository, path, *, missing=False):
        status, headers, body = self.request(f"{self.registry}/v2/{repository}/{path}", {
            "Authorization": "Bearer " + self.bearer(repository), "Accept": MANIFEST_TYPES},
            blob=path.startswith("blobs/"))
        if status == 404 and missing:
            codes = {item.get("code") for item in json.loads(body).get("errors", [])}
            require(bool(codes) and codes <= {"MANIFEST_UNKNOWN", "NAME_UNKNOWN"},
                    "Unrecognized Registry 404")
            return None
        require(status == 200, f"Registry read failed: HTTP {status}")
        return headers, body

    def manifest(self, repository, ref, *, missing=False):
        result = self.resource(repository, "manifests/" + urllib.parse.quote(ref, safe=":"), missing=missing)
        if result is None:
            return None
        headers, body = result
        actual = "sha256:" + hashlib.sha256(body).hexdigest()
        require(headers.get("Docker-Content-Digest") == actual, "Manifest digest/header mismatch")
        if DIGEST.fullmatch(ref):
            require(ref == actual, "Manifest content does not match requested digest")
        value = json.loads(body)
        require(value.get("mediaType") in MANIFEST_TYPES.split(", "), "Single-platform manifest required")
        return {"digest": actual, "manifest": value}

    def inspect(self, ref, *, missing=False):
        value = self.manifest(REPOSITORY, ref, missing=missing)
        if value is None:
            return None
        config_digest = digest(value["manifest"]["config"]["digest"])
        _, body = self.resource(REPOSITORY, "blobs/" + config_digest)
        require("sha256:" + hashlib.sha256(body).hexdigest() == config_digest, "Config digest mismatch")
        config = json.loads(body)
        require(config.get("os") == "linux" and config.get("architecture") == "amd64", "Wrong image platform")
        return {**value, "image_id": config_digest, "labels": config.get("config", {}).get("Labels", {})}

    def existing_version(self, value):
        # GHCR can deny pull on an as-yet nonexistent package; establish absence
        # through the authenticated package API before querying its manifests.
        if self.package(REPOSITORY) is None:
            return None
        return self.inspect(value, missing=True)


def cache_gate(registry):
    package = registry.package(CACHE)
    require(package is not None and package.get("visibility") == "private", "Cache must remain Private")
    anonymous = Registry(registry=registry.registry, api=registry.api)
    try:
        anonymous.manifest(CACHE, "amd64")
    except Failure as exc:
        require(str(exc) in ("Registry authorization failed: HTTP 401",
                             "Registry authorization failed: HTTP 403",
                             "Registry read failed: HTTP 401", "Registry read failed: HTTP 403"),
                "Cache privacy not proven by an authorization denial")
    else:
        raise Failure("Cache accessible anonymously")
    manifest = registry.manifest(CACHE, "amd64", missing=True)
    return manifest["digest"] if manifest else None


def identity(info, value, commit, expected_digest=None, image_id=None):
    require(info is not None, "Version manifest missing")
    labels = info["labels"]
    require(labels.get("org.opencontainers.image.source") == "https://github.com/" + REPOSITORY,
            "Wrong image source")
    require(labels.get("org.opencontainers.image.version") == value, "Wrong image version")
    require(labels.get("org.opencontainers.image.revision") == commit, "Wrong image commit")
    if expected_digest:
        require(info["digest"] == expected_digest, "Version digest mismatch")
    if image_id:
        require(info["image_id"] == image_id, "Published config differs from tested image")


class Journal:
    def __init__(self, directory, initial):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.data = {"phase": "3.2", "result": "running", "release_state": "not_released", **initial}
        self.save()

    def save(self, **updates):
        self.data.update(updates)
        temporary = self.directory / "release.json.tmp"
        temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(self.directory / "release.json")
        summary = "# Docker release status\n\n" + "\n".join(
            f"- {key}: {self.data[key]}" for key in (
                "result", "release_state", "operation", "tag", "commit", "digest", "version_pushed",
                "anonymous_verified", "latest", "error", "next_action") if key in self.data) + "\n"
        (self.directory / "summary.md").write_text(summary)

    def outputs(self, **values):
        target = os.environ.get("GITHUB_OUTPUT")
        if target:
            with open(target, "a") as stream:
                for key, value in values.items():
                    require("\n" not in str(value) and "\r" not in str(value), "Invalid output")
                    stream.write(f"{key}={value}\n")


def prepare(args, registry, journal):
    env = os.environ
    workflow_gate(env)
    value = tag_version(args.tag)
    require(args.operation in ("publish", "resume"), "Unknown operation")
    if args.operation == "resume":
        digest(args.existing_digest)
    else:
        require(not args.existing_digest, "publish must not receive existing_digest")
    source = args.work / "source"
    source.parent.mkdir(parents=True, exist_ok=True)
    tag = resolve_source(args.control, args.tag, args.commit, env["GITHUB_SHA"], source)
    journal.save(**tag, workflow_sha=env["GITHUB_SHA"], version=value,
                 run_id=env.get("GITHUB_RUN_ID"), run_attempt=env.get("GITHUB_RUN_ATTEMPT"))
    existing = registry.existing_version(value)
    local_image = f"remember-me:release-{env.get('GITHUB_RUN_ID', 'local')}-{env.get('GITHUB_RUN_ATTEMPT', '1')}"
    if args.operation == "publish":
        require(existing is None, "Version already exists; publish never overwrites")
        cache_digest = cache_gate(registry)
        budget = {}
        ci.preflight("cold", budget)
        journal.save(disk=budget, cache_digest=cache_digest)
        require(not env.get("SOURCE_DATE_EPOCH"), "SOURCE_DATE_EPOCH invalidates the accepted cache path")
        build = ["docker", "buildx", "build", "--builder", args.builder,
                 "--platform", "linux/amd64", "--target", "runtime", "--load",
                 "--provenance=false", "--sbom=false", "--progress=plain",
                 "--metadata-file", str(journal.directory / "build-metadata.json"),
                 "--label", "org.opencontainers.image.version=" + value,
                 "--label", "org.opencontainers.image.revision=" + args.commit,
                 "-t", local_image]
        if cache_digest:
            build.extend(["--cache-from", f"type=registry,ref=ghcr.io/{CACHE}@{cache_digest}"])
        build.append(str(source))
        sampler = ci.DiskSampler()
        sampler.start()
        try:
            code, seconds = ci.stream_build(build, journal.directory / "build.log")
        finally:
            samples = sampler.stop()
            journal.save(disk_samples=samples,
                         minimum_available_bytes=min((row["available_bytes"] for row in samples), default=None))
        journal.save(build_seconds=seconds)
        require(code == 0, "Candidate build failed")
    else:
        identity(existing, value, args.commit, args.existing_digest)
        local_image = IMAGE + "@" + args.existing_digest
        command(["docker", "pull", "--platform", "linux/amd64", local_image],
                log=journal.directory / "resume-pull.log")
    # Verification tools are from the controlling workflow, production lock from
    # the exact tag. Neither is baked into the runtime image.
    candidate = checks.verify_candidate(local_image, source, journal.directory / "candidate")
    image_id = candidate["metadata"]["id"]
    labels = candidate["metadata"]["config"]["Labels"]
    identity({"labels": labels, "image_id": image_id, "digest": args.existing_digest}, value, args.commit)
    journal.save(candidate_verified=True, image_id=image_id)
    require(remote_tag(args.control, args.tag) == tag, "Remote Tag changed before publication")
    if args.operation == "publish":
        require(registry.existing_version(value) is None, "Version appeared before push; refusing overwrite")
        command(["docker", "tag", local_image, IMAGE + ":" + value])
        try:
            command(["docker", "push", IMAGE + ":" + value], log=journal.directory / "push.log")
        except Exception:
            journal.save(version_pushed="unknown; inspect registry before retry")
            # A failed push can still have committed a manifest. Record only a
            # digest whose config matches the tested image, never retry a push.
            committed = registry.existing_version(value)
            if committed:
                identity(committed, value, args.commit, image_id=image_id)
                journal.save(digest=committed["digest"], version_pushed=True)
            raise
    published = registry.existing_version(value)
    identity(published, value, args.commit,
             args.existing_digest if args.operation == "resume" else None, image_id)
    journal.save(digest=published["digest"], version_pushed=True, result="version_available")
    journal.outputs(version=value, commit=args.commit, tag=args.tag,
                    digest=published["digest"], image_id=image_id)


def verify_public(args, registry, journal):
    value = tag_version(args.tag)
    digest(args.existing_digest)
    require(SHA.fullmatch(args.commit), "Full commit required")
    require(not registry.token, "Anonymous verification must not use Registry credentials")
    # Also isolate Docker from the runner user's credential helper/config.
    with tempfile.TemporaryDirectory(prefix="rm-anonymous-") as directory:
        environment = {key: item for key, item in os.environ.items()
                       if key not in ("GITHUB_TOKEN", "GH_TOKEN", "DOCKER_AUTH_CONFIG")}
        environment["DOCKER_CONFIG"] = directory
        for ref in (":" + value, "@" + args.existing_digest):
            command(["docker", "--config", directory, "pull", "--platform", "linux/amd64", IMAGE + ref],
                    env=environment, log=journal.directory / ("tag-pull.log" if ref[0] == ":" else "digest-pull.log"))
    identity(registry.inspect(value), value, args.commit, args.existing_digest, args.image_id)
    identity(registry.inspect(args.existing_digest), value, args.commit, args.existing_digest, args.image_id)
    journal.save(anonymous_verified=True, digest=args.existing_digest, result="anonymous_verified")
    journal.outputs(verified_digest=args.existing_digest)


def promote(args, registry, journal):
    workflow_gate(os.environ)
    value = tag_version(args.tag)
    digest(args.existing_digest)
    require(args.verified_digest == args.existing_digest, "Anonymous verification receipt mismatch")
    resolve_source(args.control, args.tag, args.commit, os.environ["GITHUB_SHA"])
    identity(registry.inspect(value), value, args.commit, args.existing_digest, args.image_id)
    candidate_version, prerelease = version(value)
    if prerelease:
        journal.save(latest="skipped: prerelease", result="verified", release_state="released")
        return
    current = registry.inspect("latest", missing=True)
    if current:
        current_value = current["labels"].get("org.opencontainers.image.version", "")
        current_version, current_pre = version(current_value)
        require(not current_pre, "Existing latest must be a stable version")
        require(current["labels"].get("org.opencontainers.image.source") == "https://github.com/" + REPOSITORY,
                "Existing latest has wrong source")
        require(registry.inspect(current_value)["digest"] == current["digest"], "Existing latest/version conflict")
        if current_version > candidate_version:
            journal.save(latest="retained: newer stable already published", result="verified", release_state="released")
            return
        if current_version == candidate_version:
            require(current["digest"] == args.existing_digest, "Same version has different digest")
            journal.save(latest="already points to verified digest", result="verified", release_state="released")
            return
    # Recheck the immutable version just before the only mutable-tag write.
    identity(registry.inspect(value), value, args.commit, args.existing_digest, args.image_id)
    journal.save(latest="update_started")
    command(["docker", "buildx", "imagetools", "create", "--prefer-index=false",
             "-t", IMAGE + ":latest", IMAGE + "@" + args.existing_digest],
            log=journal.directory / "latest.log")
    identity(registry.inspect("latest"), value, args.commit, args.existing_digest, args.image_id)
    journal.save(latest="updated and verified", result="verified", release_state="released")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "prepare", "verify-public", "promote"))
    parser.add_argument("--image", help="Local image for non-publishing check only")
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--control", type=Path, default=ROOT)
    parser.add_argument("--work", type=Path, default=Path("artifacts/release-work"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/release"))
    parser.add_argument("--builder", default=os.environ.get("BUILDER", "release"))
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG", ""))
    parser.add_argument("--commit", default=os.environ.get("EXPECTED_COMMIT", ""))
    parser.add_argument("--operation", default=os.environ.get("RELEASE_OPERATION", "publish"))
    parser.add_argument("--existing-digest", default=os.environ.get("EXISTING_DIGEST", ""))
    parser.add_argument("--verified-digest", default=os.environ.get("VERIFIED_DIGEST", ""))
    parser.add_argument("--image-id", default=os.environ.get("IMAGE_ID", ""))
    args = parser.parse_args()
    args.artifacts = args.artifacts.resolve()
    args.work = args.work.resolve()
    initial = {"operation": args.operation, "tag": args.tag, "commit": args.commit}
    if args.command in ("check", "prepare"):
        initial["version_pushed"] = False
    if args.existing_digest:
        initial["digest"] = args.existing_digest
    journal = Journal(args.artifacts, initial)
    try:
        if args.command == "check":
            require(bool(args.image), "check requires --image")
            checks.verify_candidate(args.image, args.source, args.artifacts / "candidate")
            journal.save(result="local_candidate_verified", release_state="not_released")
        else:
            workflow_gate(os.environ, confirmed=args.command != "verify-public")
            require(SHA.fullmatch(args.commit), "Full expected_commit required")
            tag_version(args.tag)
            if args.command != "prepare":
                digest(args.existing_digest)
                digest(args.image_id)
            registry = Registry() if args.command == "verify-public" else Registry(
                os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_ACTOR", ""))
            {"prepare": prepare, "verify-public": verify_public, "promote": promote}[args.command](args, registry, journal)
    except Exception as exc:
        journal.save(result="failed", error=str(exc))
        if args.command == "verify-public":
            journal.save(next_action=(
                "Anonymous delivery is incomplete; latest was not updated. If the first package is still Private, "
                "the user must set remember-me (NOT remember-me-buildcache) to Public in Package settings. "
                "Public cannot be changed back to Private. After correcting the failure, dispatch resume with "
                "the same tag, full commit and recorded digest; do not publish/overwrite the version again."))
        print(f"Release stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as stream:
                stream.write((args.artifacts / "summary.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
