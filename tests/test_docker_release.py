"""Release safety tests: synthetic Git repos and a loopback-only mock registry.

No Docker daemon, production credentials or external registry writes are used.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("docker_release", ROOT / ".github/scripts/docker_release.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(body):
    return "sha256:" + hashlib.sha256(body).hexdigest()


class MockRegistry:
    def __init__(self):
        self.refs, self.blobs, self.manifests = {}, {}, {}
        self.visibility = "private"
        self.fail = None
        self.add("amd64", "0.0.0", "a" * 40, repository=R.CACHE)

    def add(self, ref, value, commit, *, repository=R.REPOSITORY):
        config = encoded({"os": "linux", "architecture": "amd64", "config": {"Labels": {
            "org.opencontainers.image.source": "https://github.com/" + R.REPOSITORY,
            "org.opencontainers.image.version": value,
            "org.opencontainers.image.revision": commit,
        }}})
        body = encoded({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "config": {"digest": sha(config)}, "layers": []})
        self.blobs[sha(config)] = config
        self.manifests[(repository, sha(body))] = body
        self.refs[(repository, ref)] = body
        return {"digest": sha(body), "image_id": sha(config),
                "labels": json.loads(config)["config"]["Labels"]}


@pytest.fixture
def registry():
    state = MockRegistry()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, *, raw=False, headers=None):
            body = body if raw else encoded(body)
            self.send_response(code)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if state.fail:
                self.reply(state.fail, {"errors": [{"code": "DENIED"}]})
                return
            if path.startswith("/orgs/"):
                name = path.rsplit("/", 1)[-1]
                if name == "remember-me-buildcache":
                    self.reply(200, {"visibility": state.visibility})
                elif any(repo == R.REPOSITORY for repo, _ in state.refs):
                    self.reply(200, {"visibility": "public"})
                else:
                    self.reply(404, {})
            elif path == "/token":
                scope = parse_qs(urlsplit(self.path).query)["scope"][0]
                if R.CACHE in scope and not self.headers.get("Authorization") and state.visibility == "private":
                    self.reply(403, {})
                else:
                    self.reply(200, {"token": "mock-bearer"})
            elif "/manifests/" in path:
                repo, ref = path.removeprefix("/v2/").split("/manifests/")
                body = state.refs.get((repo, ref)) or state.manifests.get((repo, ref))
                if body is None:
                    self.reply(404, {"errors": [{"code": "MANIFEST_UNKNOWN"}]})
                else:
                    self.reply(200, body, raw=True, headers={"Docker-Content-Digest": sha(body)})
            elif "/blobs/" in path:
                key = path.split("/blobs/", 1)[1]
                self.reply(200, state.blobs[key], raw=True)
            else:
                self.reply(404, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    address = f"http://127.0.0.1:{server.server_port}"
    state.client = R.Registry("mock-token", "mock-actor", registry=address, api=address)
    state.anonymous = R.Registry(registry=address, api=address)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.fixture
def workflow(monkeypatch):
    values = {"GITHUB_REPOSITORY": R.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch",
              "GITHUB_REF": "refs/heads/master", "GITHUB_SHA": "a" * 40,
              "CONFIRM_PUBLISH": "true", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    return values


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "git"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=Release test", "-c", "user.email=test@example.invalid",
                               "-c", "commit.gpgsign=false", *args], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-b", "master")
    (repo / "content").write_text("synthetic release input")
    git("add", "content")
    git("commit", "-m", "synthetic fixture")
    commit = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    return repo, commit, git


def arguments(tmp_path, **updates):
    values = dict(tag="v1.0.0", commit="a" * 40, operation="publish", existing_digest="",
                  verified_digest="", image_id="", control=tmp_path, work=tmp_path / "work", builder="test")
    return argparse.Namespace(**{**values, **updates})


@pytest.mark.parametrize("tag", ["v0.0.0", "v1.2.3", "v1.2.3-rc.1", "v1.2.3-alpha-1.beta", "v10.20.30-0"])
def test_accept_semver(tag):
    assert R.tag_version(tag) == tag[1:]


@pytest.mark.parametrize("tag", ["1.0.0", "V1.0.0", "v01.0.0", "v1.0", "v1.0.0-01", "v1.0.0+build",
                                "v1.0.0-", "v1.0.0-a..b", "v1.0.0\n", "v1.0.0;echo secret",
                                "v1.0.0-" + "a" * 128])
def test_reject_semver(tag):
    with pytest.raises(R.Failure):
        R.tag_version(tag)


@pytest.mark.parametrize("key,value", [("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/tags/v1.0.0"),
                                     ("GITHUB_REPOSITORY", "fork/remember-me"), ("CONFIRM_PUBLISH", "false"),
                                     ("GITHUB_SHA", "short")])
def test_dispatch_and_confirmation_required(workflow, key, value):
    with pytest.raises(R.Failure):
        R.workflow_gate({**workflow, key: value})


@pytest.mark.parametrize("annotated", [False, True])
def test_git_resolves_tag_and_exact_source(git_repo, tmp_path, annotated):
    repo, commit, git = git_repo
    git("tag", *( ["-a", "-m", "release"] if annotated else []), "v1.0.0")
    result = R.resolve_source(repo, "v1.0.0", commit, commit, tmp_path / "checkout")
    assert result["commit"] == commit
    assert (tmp_path / "checkout/content").read_text() == "synthetic release input"


def test_git_rejects_wrong_sha_and_nonmaster_commit(git_repo, tmp_path):
    repo, original, git = git_repo
    git("checkout", "-b", "unmerged")
    (repo / "content").write_text("unmerged")
    git("commit", "-am", "unmerged fixture")
    commit = git("rev-parse", "HEAD")
    git("tag", "v1.0.0")
    with pytest.raises(R.Failure, match="expected_commit"):
        R.resolve_source(repo, "v1.0.0", original, original)
    with pytest.raises(R.Failure, match="Command failed"):
        R.resolve_source(repo, "v1.0.0", commit, original)


def test_registry_missing_is_distinct_from_auth_failure(registry):
    assert registry.client.existing_version("1.0.0") is None
    registry.add("1.0.0", "1.0.0", "a" * 40)
    assert registry.client.inspect("2.0.0", missing=True) is None
    registry.fail = 403
    with pytest.raises(R.Failure, match="403"):
        registry.client.existing_version("2.0.0")


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_registry_errors_never_mean_missing(registry, code):
    registry.fail = code
    with pytest.raises(R.Failure):
        registry.client.manifest(R.REPOSITORY, "1.0.0", missing=True)


def test_cache_must_be_private_with_real_authorization_denial(registry):
    assert R.cache_gate(registry.client).startswith("sha256:")
    registry.visibility = "public"
    with pytest.raises(R.Failure, match="Private"):
        R.cache_gate(registry.client)


def test_registry_checks_content_digests_and_platform(registry):
    info = registry.add("1.0.0", "1.0.0", "a" * 40)
    assert registry.client.inspect("1.0.0")["digest"] == info["digest"]
    registry.blobs[info["image_id"]] = b"modified"
    with pytest.raises(R.Failure, match="Config digest"):
        registry.client.inspect("1.0.0")


def test_registry_refuses_wrong_platform_even_with_valid_digest(registry):
    config = encoded({"os": "linux", "architecture": "arm64", "config": {"Labels": {}}})
    registry.blobs[sha(config)] = config
    body = encoded({"mediaType": "application/vnd.oci.image.manifest.v1+json", "config": {"digest": sha(config)}})
    registry.refs[(R.REPOSITORY, "1.0.0")] = body
    with pytest.raises(R.Failure, match="platform"):
        registry.client.inspect("1.0.0")


def test_registry_404_must_be_an_explicit_manifest_unknown(monkeypatch):
    client = R.Registry()
    monkeypatch.setattr(client, "bearer", lambda repo: "test")
    monkeypatch.setattr(client, "request", lambda *a, **kw: (404, {}, b'{}'))
    with pytest.raises(R.Failure, match="Unrecognized"):
        client.manifest(R.REPOSITORY, "missing", missing=True)


def test_cache_network_failure_does_not_prove_privacy(registry, monkeypatch):
    original = R.Registry.manifest

    def read(client, *args, **kwargs):
        if not client.token:
            raise R.Failure("HTTP transport failed: TimeoutError")
        return original(client, *args, **kwargs)

    monkeypatch.setattr(R.Registry, "manifest", read)
    with pytest.raises(R.Failure, match="privacy not proven"):
        R.cache_gate(registry.client)


@pytest.fixture
def prepare_fixture(tmp_path, monkeypatch, workflow, git_repo, registry):
    repo, commit, git = git_repo
    git("tag", "v1.0.0")
    monkeypatch.setenv("GITHUB_SHA", commit)
    args = arguments(tmp_path, control=repo, commit=commit)
    preview = MockRegistry().add("1.0.0", "1.0.0", commit)
    calls = []
    real_command = R.command

    def command(argv, **kwargs):
        if argv[0] == "git":
            return real_command(argv, **kwargs)
        calls.append(argv)
        if argv[:2] == ["docker", "push"]:
            registry.add("1.0.0", "1.0.0", commit)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def candidate(*args, **kwargs):
        calls.append(["verified"])
        return {"metadata": {"id": preview["image_id"], "config": {"Labels": preview["labels"]}}}

    monkeypatch.setattr(R, "command", command)
    monkeypatch.setattr(R.ci, "stream_build", lambda argv, log: (calls.append(argv) or (0, .1)))
    monkeypatch.setattr(R.ci, "preflight", lambda mode, results: None)
    monkeypatch.setattr(R.checks, "verify_candidate", candidate)
    journal = R.Journal(tmp_path / "evidence", {"operation": "publish"})
    return args, journal, calls, registry, git


def test_publish_only_after_candidate_verified_and_no_cache_export(prepare_fixture):
    args, journal, calls, registry, _ = prepare_fixture
    R.prepare(args, registry.client, journal)
    push = next(i for i, argv in enumerate(calls) if argv[:2] == ["docker", "push"])
    assert calls.index(["verified"]) < push
    build = next(argv for argv in calls if argv[:3] == ["docker", "buildx", "build"])
    assert "--load" in build and "--cache-from" in build and "--cache-to" not in build
    assert "--no-cache" not in build
    assert journal.data["digest"] == registry.client.inspect("1.0.0")["digest"]


def test_existing_version_refused_before_build(prepare_fixture):
    args, journal, calls, registry, _ = prepare_fixture
    registry.add("1.0.0", "1.0.0", args.commit)
    with pytest.raises(R.Failure, match="already exists"):
        R.prepare(args, registry.client, journal)
    assert not calls


def test_validation_failure_does_not_push(prepare_fixture, monkeypatch):
    args, journal, calls, registry, _ = prepare_fixture
    monkeypatch.setattr(R.checks, "verify_candidate", lambda *a: R.require(False, "failed check"))
    with pytest.raises(R.Failure, match="failed check"):
        R.prepare(args, registry.client, journal)
    assert not any(argv[:2] == ["docker", "push"] for argv in calls)


def test_version_race_and_tag_change_stop_push(prepare_fixture, monkeypatch):
    args, journal, calls, registry, _ = prepare_fixture
    original = R.checks.verify_candidate

    def verify(*values):
        result = original(*values)
        registry.add("1.0.0", "1.0.0", args.commit)
        return result

    monkeypatch.setattr(R.checks, "verify_candidate", verify)
    with pytest.raises(R.Failure, match="appeared before push"):
        R.prepare(args, registry.client, journal)
    assert not any(argv[:2] == ["docker", "push"] for argv in calls)


def test_remote_tag_change_after_build_stops_push(prepare_fixture, monkeypatch):
    args, journal, calls, registry, _ = prepare_fixture
    original = R.remote_tag
    count = 0

    def remote(*values):
        nonlocal count
        count += 1
        return original(*values) if count == 1 else {"tag_oid": "f" * 40, "commit": "f" * 40}

    monkeypatch.setattr(R, "remote_tag", remote)
    with pytest.raises(R.Failure, match="Remote Tag changed"):
        R.prepare(args, registry.client, journal)
    assert not any(argv[:2] == ["docker", "push"] for argv in calls)


def test_push_error_records_committed_digest_without_retry(prepare_fixture, monkeypatch):
    args, journal, calls, registry, _ = prepare_fixture
    original = R.command

    def command(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[:2] == ["docker", "push"]:
            raise R.Failure("connection lost after manifest commit")
        return result

    monkeypatch.setattr(R, "command", command)
    with pytest.raises(R.Failure, match="connection lost"):
        R.prepare(args, registry.client, journal)
    assert journal.data["version_pushed"] is True
    assert journal.data["digest"] == registry.client.inspect("1.0.0")["digest"]
    assert sum(argv[:2] == ["docker", "push"] for argv in calls) == 1


def test_resume_revalidates_without_build_or_version_push(prepare_fixture):
    args, journal, calls, registry, _ = prepare_fixture
    info = registry.add("1.0.0", "1.0.0", args.commit)
    args.operation, args.existing_digest = "resume", info["digest"]
    R.prepare(args, registry.client, journal)
    assert ["verified"] in calls
    assert not any(argv[:2] == ["docker", "push"] or argv[:3] == ["docker", "buildx", "build"] for argv in calls)


def test_resume_refuses_wrong_digest_before_docker(prepare_fixture):
    args, journal, calls, registry, _ = prepare_fixture
    registry.add("1.0.0", "1.0.0", args.commit)
    args.operation, args.existing_digest = "resume", "sha256:" + "f" * 64
    with pytest.raises(R.Failure, match="digest mismatch"):
        R.prepare(args, registry.client, journal)
    assert not calls


def test_anonymous_pull_has_no_credentials_and_confirms_both_refs(tmp_path, registry, monkeypatch):
    info = registry.add("1.0.0", "1.0.0", "a" * 40)
    args = arguments(tmp_path, existing_digest=info["digest"], image_id=info["image_id"])
    calls = []
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-be-forwarded")
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "must-not-be-forwarded")

    def command(argv, **kwargs):
        calls.append(argv)
        assert "GITHUB_TOKEN" not in kwargs["env"]
        assert "DOCKER_AUTH_CONFIG" not in kwargs["env"]
        assert not list(Path(kwargs["env"]["DOCKER_CONFIG"]).iterdir())
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(R, "command", command)
    journal = R.Journal(tmp_path / "evidence", {})
    R.verify_public(args, registry.anonymous, journal)
    assert len(calls) == 2 and all("pull" in call for call in calls)
    assert journal.data["anonymous_verified"] is True


def test_anonymous_failure_produces_no_verification_receipt(tmp_path, registry, monkeypatch):
    info = registry.add("1.0.0", "1.0.0", "a" * 40)
    args = arguments(tmp_path, existing_digest=info["digest"], image_id=info["image_id"])
    monkeypatch.setattr(R, "command", lambda *a, **kw: R.require(False, "private package"))
    journal = R.Journal(tmp_path / "evidence", {})
    with pytest.raises(R.Failure):
        R.verify_public(args, registry.anonymous, journal)
    assert "anonymous_verified" not in journal.data


@pytest.mark.parametrize("candidate,current,promoted", [
    ("1.0.0", None, True), ("1.1.0", "1.0.0", True), ("1.10.0", "1.9.0", True),
    ("1.1.1", "1.2.0", False), ("1.0.0", "1.0.0", False), ("2.0.0-rc.1", "1.0.0", False),
])
def test_latest_only_advances_verified_stable(tmp_path, registry, workflow, monkeypatch, candidate, current, promoted):
    info = registry.add(candidate, candidate, "a" * 40)
    if current:
        registry.add(current, current, "a" * 40)
        registry.refs[(R.REPOSITORY, "latest")] = registry.refs[(R.REPOSITORY, current)]
    args = arguments(tmp_path, tag="v" + candidate, existing_digest=info["digest"],
                     verified_digest=info["digest"], image_id=info["image_id"])
    monkeypatch.setattr(R, "resolve_source", lambda *a: {})
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        registry.refs[(R.REPOSITORY, "latest")] = registry.refs[(R.REPOSITORY, candidate)]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(R, "command", command)
    R.promote(args, registry.client, R.Journal(tmp_path / "evidence", {}))
    assert bool(calls) is promoted
    if calls:
        assert "--prefer-index=false" in calls[0] and info["digest"] in calls[0][-1]


def test_latest_requires_anonymous_receipt(tmp_path, registry, workflow):
    args = arguments(tmp_path, existing_digest="sha256:" + "a" * 64)
    with pytest.raises(R.Failure, match="receipt"):
        R.promote(args, registry.client, R.Journal(tmp_path / "evidence", {}))


def test_latest_failure_can_resume_without_replacing_version(tmp_path, registry, workflow, monkeypatch):
    info = registry.add("1.0.0", "1.0.0", "a" * 40)
    args = arguments(tmp_path, existing_digest=info["digest"], verified_digest=info["digest"], image_id=info["image_id"])
    monkeypatch.setattr(R, "resolve_source", lambda *a: {})
    monkeypatch.setattr(R, "command", lambda *a, **kw: R.require(False, "latest push failed"))
    with pytest.raises(R.Failure, match="latest push failed"):
        R.promote(args, registry.client, R.Journal(tmp_path / "evidence", {}))
    assert registry.client.inspect("1.0.0")["digest"] == info["digest"]
    registry.refs[(R.REPOSITORY, "latest")] = registry.refs[(R.REPOSITORY, "1.0.0")]
    journal = R.Journal(tmp_path / "recovered", {})
    R.promote(args, registry.client, journal)
    assert journal.data["latest"] == "already points to verified digest"


def test_latest_equal_version_with_different_digest_is_rejected(tmp_path, registry, workflow, monkeypatch):
    old = registry.add("latest", "1.0.0", "b" * 40)
    info = registry.add("1.0.0", "1.0.0", "a" * 40)
    # Both labels claim the same version but the mutable alias was tampered with.
    args = arguments(tmp_path, existing_digest=info["digest"], verified_digest=info["digest"], image_id=info["image_id"])
    monkeypatch.setattr(R, "resolve_source", lambda *a: {})
    calls = []
    monkeypatch.setattr(R, "command", lambda *a, **kw: calls.append(a))
    with pytest.raises(R.Failure, match="conflict"):
        R.promote(args, registry.client, R.Journal(tmp_path / "evidence", {}))
    assert not calls and registry.client.inspect("latest")["digest"] == old["digest"]


def test_check_subcommand_is_registry_free(tmp_path, monkeypatch):
    monkeypatch.setattr(R.sys, "argv", ["docker_release.py", "check", "--image", "local:test",
                                      "--artifacts", str(tmp_path)])
    monkeypatch.setattr(R, "Registry", lambda *a, **kw: pytest.fail("check contacted registry"))
    monkeypatch.setattr(R.checks, "verify_candidate", lambda *a, **kw: {})
    assert R.main() == 0
    evidence = json.loads((tmp_path / "release.json").read_text())
    assert evidence["release_state"] == "not_released" and evidence["version_pushed"] is False


def test_compatibility_comparison_rejects_numerical_drift():
    spec = importlib.util.spec_from_file_location("compatibility", ROOT / "tests/run_docker_release_compatibility.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = [1.0] + [0.0] * 1023
    data = {"profile": module.PROFILE, "versions": {}, "vectors": [baseline] * 16}
    assert module.compare(data, data)["pairs"] == 16
    changed = {**data, "vectors": [[0.0, 1.0] + [0.0] * 1022] * 16}
    with pytest.raises(AssertionError, match="Component error"):
        module.compare(data, changed)


def test_database_evidence_separates_operational_time_from_identity_and_vectors(tmp_path):
    import sqlite3
    spec = importlib.util.spec_from_file_location("compatibility_state", ROOT / "tests/run_docker_release_compatibility.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "copy.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            create table memories(id text,deleted integer);
            create table memory_embeddings(memory_id text,profile_id text,embedding blob);
            create table index_value_embeddings(index_value_id integer,profile_id text,embedding blob);
            create table embedding_profiles(profile_id text,status text,model text,updated_at text);
            insert into memories values('m',0);
            insert into memory_embeddings values('m','profile',X'0102');
            insert into index_value_embeddings values(1,'profile',X'0304');
            insert into embedding_profiles values('profile','active','BAAI/bge-m3','before');
        """)
    before = module.database_state(path)
    with sqlite3.connect(path) as db:
        db.execute("update embedding_profiles set updated_at='after'")
    after = module.database_state(path)
    assert before["profiles_sha256"] == after["profiles_sha256"]
    assert before["profile_updated_at"] != after["profile_updated_at"]
    with sqlite3.connect(path) as db:
        db.execute("update embedding_profiles set model='different'")
        db.execute("update memory_embeddings set embedding=X'0506'")
    changed = module.database_state(path)
    assert changed["profiles_sha256"] != after["profiles_sha256"]
    assert changed["memory_embeddings"]["sha256"] != after["memory_embeddings"]["sha256"]


def test_workflows_are_manual_pinned_and_least_privilege():
    release = yaml.safe_load((ROOT / ".github/workflows/docker-release.yml").read_text())
    ci = yaml.safe_load((ROOT / ".github/workflows/docker-amd64-ci.yml").read_text())
    for workflow in (release, ci):
        events = workflow.get("on", workflow.get(True))
        assert set(events) == {"workflow_dispatch"}
        for job in workflow["jobs"].values():
            assert job["runs-on"] == "ubuntu-24.04"
            for step in job["steps"]:
                if "uses" in step:
                    assert re.search(r"@[0-9a-f]{40}$", step["uses"])
    assert release["permissions"] == {}
    assert release["concurrency"] == {"group": "remember-me-ghcr-release", "cancel-in-progress": False}
    assert release[True]["workflow_dispatch"]["inputs"]["confirm_publish"]["default"] is False
    public = release["jobs"]["verify-public"]
    assert public["permissions"] == {}
    assert not any("login-action" in step.get("uses", "") or "checkout@" in step.get("uses", "") for step in public["steps"])
    assert release["jobs"]["promote-latest"]["needs"] == ["prepare-publish", "verify-public"]
    for job in release["jobs"].values():
        assert job["permissions"].get("contents") != "write"


def test_shared_candidate_checks_do_not_bake_tools_into_runtime(tmp_path):
    calls = []
    labels = {"org.opencontainers.image.source": "https://github.com/" + R.REPOSITORY,
              "org.opencontainers.image.licenses": "Apache-2.0", "io.remember-me.model.revision": R.checks.REVISION,
              "io.remember-me.model.license": "MIT"}

    def run(argv, **kwargs):
        calls.append(argv)
        stdout = ""
        if argv[:3] == ["docker", "image", "inspect"]:
            stdout = json.dumps([{"Id": "sha256:" + "a" * 64, "Config": {"Labels": labels}}])
        elif "--mount" in argv:
            stdout = json.dumps({"model_validation": "offline_inference_passed", "profile": {"profile_id": R.checks.PROFILE_ID}})
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    R.checks.verify_candidate("local:test", ROOT, tmp_path, run=run)
    verifier = next(argv for argv in calls if "--mount" in argv)
    assert "--network" in verifier and "none" in verifier and "--read-only" in verifier
    assert "TMPDIR=/data" in verifier
    assert all("readonly" in verifier[i + 1] for i, arg in enumerate(verifier) if arg == "--mount")
    assert not any(argv[:3] == ["docker", "buildx", "build"] for argv in calls)
