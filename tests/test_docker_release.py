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
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("docker_release", ROOT / ".github/scripts/docker_release.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def release_document(value="1.0.0", **updates):
    return {"version": value, **{field: [] for field in R.CONTENT_FIELDS}, **updates}


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(body):
    return "sha256:" + hashlib.sha256(body).hexdigest()


class MockRegistry:
    def __init__(self):
        self.refs, self.blobs, self.manifests = {}, {}, {}
        self.visibility = "private"
        self.fail = None
        self.releases, self.posts = [], []
        self.source_repo = None
        self.tag_failure = None
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
            if path == f"/repos/{R.REPOSITORY}/releases":
                self.reply(200, state.releases)
            elif path.endswith("/versions"):
                items = {}
                for (repo, tag), body in state.refs.items():
                    if repo == R.REPOSITORY:
                        items.setdefault(sha(body), []).append(tag)
                self.reply(200, [{"name": key, "metadata": {"container": {"tags": tags}}}
                                 for key, tags in items.items()])
            elif path.startswith("/orgs/"):
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

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.posts.append((self.path, data))
            assert self.path == f"/repos/{R.REPOSITORY}/git/refs"
            if state.tag_failure:
                self.reply(state.tag_failure, {})
                return
            assert state.source_repo is not None
            result = subprocess.run(["git", "update-ref", data["ref"], data["sha"], "0" * 40],
                                    cwd=state.source_repo, capture_output=True)
            if result.returncode:
                self.reply(422, {})
            else:
                self.reply(201, {"ref": data["ref"], "object": {"type": "commit", "sha": data["sha"]}})

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
    (repo / "release.jsonc").write_text("// synthetic release\n" + json.dumps(release_document()))
    git("add", "content", "release.jsonc")
    git("commit", "-m", "synthetic fixture")
    commit = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    return repo, commit, git


def arguments(tmp_path, **updates):
    values = dict(tag="v1.0.0", commit="a" * 40, operation="publish", existing_digest="",
                  verified_digest="", image_id="", release_sha256="b" * 64,
                  control=tmp_path, work=tmp_path / "work", builder="test")
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
    registry.source_repo = repo
    monkeypatch.setenv("GITHUB_SHA", commit)
    args = arguments(tmp_path, tag="", control=repo, commit=commit)
    preview = MockRegistry().add("1.0.0", "1.0.0", commit)
    calls = []
    real_command = R.command

    def command(argv, **kwargs):
        if argv[0] == "git":
            return real_command(argv, **kwargs)
        calls.append(argv)
        if argv[:2] == ["docker", "push"]:
            registry.add("1.0.0", "1.0.0", commit)
        if argv[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "sha256": hashlib.sha256((repo / "release.jsonc").read_bytes()).hexdigest(),
                "current": {"version": "1.0.0", "release": release_document(),
                            "install_method": "DOCKER", "error_code": None}}), "")
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
    with pytest.raises(R.Failure, match="already exists"):
        R.prepare(args, registry.client, journal)
    assert not any(argv[:2] == ["docker", "push"] for argv in calls)


def test_remote_tag_change_after_build_stops_push(prepare_fixture, monkeypatch):
    args, journal, calls, registry, _ = prepare_fixture
    monkeypatch.setattr(R, "remote_tag", lambda *a: {"tag_oid": "f" * 40, "commit": "f" * 40})
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
    args, journal, calls, registry, git = prepare_fixture
    git("tag", "v1.0.0")
    info = registry.add("1.0.0", "1.0.0", args.commit)
    args.operation, args.existing_digest = "resume", info["digest"]
    R.prepare(args, registry.client, journal)
    assert ["verified"] in calls
    assert not any(argv[:2] == ["docker", "push"] or argv[:3] == ["docker", "buildx", "build"] for argv in calls)


def test_resume_refuses_wrong_digest_before_docker(prepare_fixture):
    args, journal, calls, registry, git = prepare_fixture
    git("tag", "v1.0.0")
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
        if argv[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sha256": args.release_sha256,
                "current": {"version": "1.0.0", "release": release_document(),
                            "install_method": "DOCKER", "error_code": None}}), "")
        assert "GITHUB_TOKEN" not in kwargs["env"]
        assert "DOCKER_AUTH_CONFIG" not in kwargs["env"]
        assert not list(Path(kwargs["env"]["DOCKER_CONFIG"]).iterdir())
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(R, "command", command)
    journal = R.Journal(tmp_path / "evidence", {})
    R.verify_public(args, registry.anonymous, journal)
    assert len(calls) == 3 and all("pull" in call for call in calls[:2])
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
    ("1.0.0", None, True), ("1.2.0", "1.0.0", True), ("1.10.0", "1.9.0", True),
    ("1.0.0", "1.0.0", False),
])
def test_latest_only_advances_verified_stable(tmp_path, registry, workflow, monkeypatch, candidate, current, promoted):
    info = registry.add(candidate, candidate, "a" * 40)
    if current:
        registry.add(current, current, "a" * 40)
        registry.refs[(R.REPOSITORY, "latest")] = registry.refs[(R.REPOSITORY, current)]
    args = arguments(tmp_path, tag="v" + candidate, existing_digest=info["digest"],
                     verified_digest=info["digest"], image_id=info["image_id"])
    monkeypatch.setattr(R, "resolve_source", lambda *a: {})
    monkeypatch.setattr(R, "release_at_commit", lambda *a: (SimpleNamespace(version=candidate), args.release_sha256))
    monkeypatch.setattr(R, "release_gate", lambda *a, **kw: None)
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
    monkeypatch.setattr(R, "release_at_commit", lambda *a: (SimpleNamespace(version="1.0.0"), args.release_sha256))
    monkeypatch.setattr(R, "release_gate", lambda *a, **kw: None)
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
    monkeypatch.setattr(R, "release_at_commit", lambda *a: (SimpleNamespace(version="1.0.0"), args.release_sha256))
    monkeypatch.setattr(R, "release_gate", lambda *a, **kw: None)
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
    assert release["jobs"]["prepare-publish"]["permissions"] == {"contents": "write", "packages": "write"}
    for name, job in release["jobs"].items():
        if name != "prepare-publish":
            assert job["permissions"].get("contents") != "write"
    inputs = release[True]["workflow_dispatch"]["inputs"]
    assert "tag" not in inputs and "version" not in inputs
    assert inputs["operation"]["default"] == "validate"
    assert release["jobs"]["validate"]["permissions"] == {"contents": "read"}
    for name in ("verify-public", "promote-latest"):
        assert release["jobs"][name]["env"]["RELEASE_TAG"] == "${{ needs.prepare-publish.outputs.tag }}"


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


@pytest.mark.parametrize("lower,higher", [
    ("1.9.0", "1.10.0"), ("2.0.0", "10.0.0"), ("1.0.9", "1.0.10"),
    ("1.0.0-alpha", "1.0.0-alpha.1"), ("1.0.0-alpha.2", "1.0.0-alpha.10"),
    ("1.0.0-2", "1.0.0-alpha"), ("1.0.0-beta.2", "1.0.0-beta.11"),
    ("1.0.0-rc.99", "1.0.0"), ("1.0.0", "1.0.1-rc.1"),
])
def test_history_uses_semver_precedence(lower, higher):
    assert R.version_key(lower) < R.version_key(higher)


def test_metadata_reads_target_blob_not_branch_or_dirty_worktree(git_repo, tmp_path):
    from app.release_info import read_local_release
    repo, commit, git = git_repo
    expected = hashlib.sha256((repo / 'release.jsonc').read_bytes()).hexdigest()
    (repo / 'release.jsonc').write_text(json.dumps(release_document('9.0.0')))
    git('commit', '-am', 'later version')
    workflow_commit = git('rev-parse', 'HEAD')
    (repo / 'release.jsonc').write_text('{invalid dirty input')
    source = tmp_path / 'exact-source'
    release, release_hash = R.resolve_commit(repo, commit, workflow_commit, source)
    assert release.version == '1.0.0' and release_hash == expected
    assert read_local_release(source, environment={'REMEMBER_ME_INSTALL_METHOD':'SOURCE'}).version == release.version
    assert read_local_release(source, environment={'REMEMBER_ME_INSTALL_METHOD':'DOCKER'}).version == release.version


@pytest.mark.parametrize('content', [
    '{bad', json.dumps(release_document(None)), json.dumps(release_document('v1.0.0')),
    json.dumps(release_document('01.0.0')), json.dumps(release_document('1.0')),
    json.dumps(release_document('1.0.0-rc.1')), json.dumps(release_document('1.0.0+build')),
    json.dumps(release_document(new='wrong type')), json.dumps({'version':'1.0.0'}),
    '{"version":"1.0.0","version":"2.0.0"}', ' ' * (R.MAX_RELEASE_BYTES + 1),
], ids=['malformed','unknown','v-prefix','leading-zero','short','prerelease','build-metadata',
        'wrong-array-type','missing-fields','duplicate-key','oversized'])
def test_invalid_committed_metadata_is_not_publishable(git_repo, content):
    repo, _, git = git_repo
    (repo / 'release.jsonc').write_text(content)
    git('commit', '-am', 'invalid fixture')
    with pytest.raises(R.Failure):
        R.release_at_commit(repo, git('rev-parse', 'HEAD'))


@pytest.mark.parametrize('kind', ['missing','symlink'])
def test_committed_release_must_be_regular_file(git_repo, kind):
    repo, _, git = git_repo
    (repo / 'release.jsonc').unlink()
    if kind == 'symlink':
        (repo / 'release.jsonc').symlink_to('content')
    git('add', 'release.jsonc'); git('commit', '-m', 'missing or symlink fixture')
    with pytest.raises(R.Failure, match='regular file'):
        R.release_at_commit(repo, git('rev-parse', 'HEAD'))


def test_metadata_preview_generates_notes_without_any_remote_access(git_repo, tmp_path, monkeypatch):
    repo, commit, _ = git_repo
    monkeypatch.setattr(R.sys, 'argv', ['docker_release.py','metadata','--commit',commit,
        '--control',str(repo),'--artifacts',str(tmp_path/'notes')])
    monkeypatch.setattr(R, 'Registry', lambda *a, **kw: pytest.fail('metadata contacted registry'))
    assert R.main() == 0
    notes = (tmp_path/'notes/release-notes.md').read_text()
    assert 'v1.0.0' in notes and commit in notes and R.IMAGE+':1.0.0' in notes
    assert all(heading in notes for heading in ['New','Improvements','Fixes','Upgrade Notes','Breaking Changes'])


def test_notes_escape_plain_text_and_keep_all_structured_fields(tmp_path):
    content = {'new':['<script>alert(1)</script>'], 'improvements':['[link](https://example.invalid)'],
               'fixes':['a\n# not a heading'], 'upgrade':['Back up 中文'], 'breaking':['Schema changed']}
    release = R.validate_release(release_document(**content))
    journal = R.Journal(tmp_path, {})
    R.write_notes(journal, release, 'a'*40, 'b'*64, 'sha256:'+'c'*64)
    notes = (tmp_path/'release-notes.md').read_text()
    assert '<script>' not in notes and '&lt;script&gt;' in notes
    assert '\\[link\\]' in notes and '\n  \\# not a heading' in notes
    assert 'Back up 中文' in notes and 'Schema changed' in notes and 'sha256:'+'c'*64 in notes
    assert notes.index('## Upgrade Notes') < notes.index('## New')


@pytest.mark.parametrize('history_source', ['tag','release','ghcr'])
@pytest.mark.parametrize('previous,allowed', [('0.9.0',True),('1.0.0',False),('1.1.0',False),
                                            ('1.0.0-rc.10',True),('1.1.0-rc.1',False)])
def test_all_histories_enforce_strict_increase(git_repo, registry, history_source, previous, allowed):
    repo, commit, git = git_repo
    if history_source == 'tag':
        git('tag','v'+previous)
    elif history_source == 'release':
        registry.releases = [{'tag_name':'v'+previous,'draft':False}]
    else:
        registry.add(previous,previous,commit)
    if allowed:
        assert R.release_gate(repo,registry.client,'1.0.0',commit) == previous
    else:
        with pytest.raises(R.Failure):
            R.release_gate(repo,registry.client,'1.0.0',commit)


def test_first_release_has_no_ceiling_and_drafts_do_not_publish(git_repo, registry):
    repo, commit, _ = git_repo
    registry.releases = [{'tag_name':'v99.0.0','draft':True}]
    assert R.release_gate(repo,registry.client,'1.0.0',commit) is None


def test_history_pagination_cannot_silently_ignore_high_versions(monkeypatch):
    client = R.Registry('synthetic')
    pages = {1:[{'n':i} for i in range(100)], 2:[{'n':100}]}
    monkeypatch.setattr(client,'github',lambda path:pages[int(parse_qs(urlsplit(path).query)['page'][0])])
    assert list(client.pages('/fixture'))[-1] == {'n':100}
    monkeypatch.setattr(client,'github',lambda path:[{}]*100)
    with pytest.raises(R.Failure,match='pagination limit'):
        list(client.pages('/fixture'))
    monkeypatch.setattr(client,'github',lambda path:{'unexpected':'object'})
    with pytest.raises(R.Failure,match='history page'):
        list(client.pages('/fixture'))


def test_deleted_alias_does_not_hide_retained_manifest(registry, monkeypatch):
    info = registry.add('2.0.0','2.0.0','a'*40)
    original = registry.client.pages
    def pages(path):
        if path.endswith('/versions'):
            yield {'name':info['digest'],'metadata':{'container':{'tags':[]}}}
        else:
            yield from original(path)
    monkeypatch.setattr(registry.client,'pages',pages)
    assert registry.client.published_versions() == {'2.0.0'}


def test_tag_creation_is_after_validation_and_bound_to_exact_commit(prepare_fixture, monkeypatch):
    args, journal, calls, registry, git = prepare_fixture
    original = registry.client.create_tag
    def create(tag, commit):
        assert ['verified'] in calls
        assert any(call[:2] == ['docker','run'] for call in calls)
        assert not any(call[:2] == ['docker','push'] for call in calls)
        original(tag,commit)
    monkeypatch.setattr(registry.client,'create_tag',create)
    R.prepare(args,registry.client,journal)
    assert registry.posts == [(f'/repos/{R.REPOSITORY}/git/refs',{'ref':'refs/tags/v1.0.0','sha':args.commit})]
    assert git('rev-parse','v1.0.0') == args.commit
    assert journal.data['runtime_identity']['current']['version'] == journal.data['version']


@pytest.mark.parametrize('status',[403,409,422,500])
def test_tag_creation_failure_never_pushes(prepare_fixture,status):
    args,journal,calls,registry,_ = prepare_fixture
    registry.tag_failure=status
    with pytest.raises(R.Failure,match='Create-only Tag failed'):
        R.prepare(args,registry.client,journal)
    assert not any(call[:2] == ['docker','push'] for call in calls)


def test_existing_tag_rejected_even_at_same_commit(prepare_fixture):
    args,journal,calls,registry,git = prepare_fixture
    git('tag','v1.0.0')
    with pytest.raises(R.Failure,match='Tag already exists'):
        R.prepare(args,registry.client,journal)
    assert not calls and not registry.posts


def test_validation_failure_creates_no_tag(prepare_fixture,monkeypatch):
    args,journal,_,registry,git=prepare_fixture
    monkeypatch.setattr(R.checks,'verify_candidate',lambda *a: R.require(False,'bad candidate'))
    with pytest.raises(R.Failure,match='bad candidate'):
        R.prepare(args,registry.client,journal)
    assert not registry.posts and not git('tag','--list')


@pytest.mark.parametrize('mutation',['bytes','version','method','unknown'])
def test_image_runtime_must_match_committed_release(monkeypatch,mutation):
    observed={'sha256':'b'*64,'current':{'version':'1.0.0','install_method':'DOCKER',
              'error_code':None,'release':release_document()}}
    if mutation=='bytes':observed['sha256']='c'*64
    if mutation=='version':observed['current']['version']='2.0.0'
    if mutation=='method':observed['current']['install_method']='SOURCE'
    if mutation=='unknown':observed['current']['error_code']='local_release_invalid'
    monkeypatch.setattr(R,'command',lambda *a,**kw:subprocess.CompletedProcess(a,0,json.dumps(observed),''))
    with pytest.raises(R.Failure):R.verify_release_file('synthetic:local','1.0.0','b'*64)


@pytest.mark.parametrize('history_source',['tag','release','ghcr'])
def test_resume_cannot_bypass_newer_history(prepare_fixture,history_source):
    args,journal,calls,registry,git=prepare_fixture
    git('tag','v1.0.0')
    info=registry.add('1.0.0','1.0.0',args.commit)
    args.operation,args.existing_digest='resume',info['digest']
    if history_source=='tag':git('tag','v2.0.0')
    elif history_source=='release':registry.releases=[{'tag_name':'v2.0.0','draft':False}]
    else:registry.add('2.0.0','2.0.0',args.commit)
    with pytest.raises(R.Failure,match='strictly higher'):
        R.prepare(args,registry.client,journal)
    assert not calls and not registry.posts


def test_complete_publish_anonymous_promote_and_digest_resume(prepare_fixture,monkeypatch,tmp_path):
    args,journal,calls,registry,_=prepare_fixture
    R.prepare(args,registry.client,journal)
    args.existing_digest=journal.data['digest'];args.image_id=journal.data['image_id']
    args.release_sha256=journal.data['release_sha256']
    R.verify_public(args,registry.anonymous,R.Journal(tmp_path/'public',{}))
    args.verified_digest=args.existing_digest
    original=R.command
    def command(argv,**kw):
        result=original(argv,**kw)
        if argv[:3]==['docker','buildx','imagetools']:
            registry.refs[(R.REPOSITORY,'latest')]=registry.refs[(R.REPOSITORY,'1.0.0')]
        return result
    monkeypatch.setattr(R,'command',command)
    R.promote(args,registry.client,R.Journal(tmp_path/'latest',{}))
    assert registry.client.inspect('latest')['digest']==args.existing_digest
    resume=arguments(tmp_path,tag='',commit=args.commit,control=args.control,
                     work=tmp_path/'resumed-source',operation='resume',existing_digest=args.existing_digest)
    calls.clear()
    R.prepare(resume,registry.client,R.Journal(tmp_path/'resume',{}))
    assert len(registry.posts)==1
    assert not any(call[:2]==['docker','push'] or call[:3]==['docker','buildx','build'] for call in calls)


def test_derived_version_is_the_version_check_runtime_and_remote_target(git_repo,tmp_path):
    import asyncio
    from app.release_info import read_local_release
    from tests.version_update_helpers import Github,check
    repo,commit,_=git_repo
    release,_=R.resolve_commit(repo,commit,commit,tmp_path/'source')
    current=read_local_release(tmp_path/'source',environment={'REMEMBER_ME_INSTALL_METHOD':'DOCKER'})
    gh=Github(release.version);gh.raw[release.version]=release.as_dict()
    async def scenario():
        manager=gh.manager(current)
        try:
            state=await check(manager)
            assert state['check']['status']=='UP_TO_DATE'
            assert state['current']['version']==state['latest']['version']==release.version
            assert any('/v'+release.version+'/release.jsonc' in url for url in gh.seen)
        finally:
            await manager.aclose()
    asyncio.run(scenario())


def test_same_tag_creation_race_cannot_overwrite(prepare_fixture,monkeypatch):
    args,journal,calls,registry,git=prepare_fixture
    original=registry.client.create_tag
    def race(tag,commit):
        git('tag',tag)
        original(tag,commit)
    monkeypatch.setattr(registry.client,'create_tag',race)
    with pytest.raises(R.Failure,match='HTTP 422'):
        R.prepare(args,registry.client,journal)
    assert not any(call[:2]==['docker','push'] for call in calls)
    assert git('rev-parse','v1.0.0')==args.commit


@pytest.mark.parametrize('when',['during-build','after-tag'])
def test_newer_history_race_stops_version_push(prepare_fixture,monkeypatch,when):
    args,journal,calls,registry,git=prepare_fixture
    if when=='during-build':
        original=R.checks.verify_candidate
        def candidate(*values):
            result=original(*values)
            registry.releases=[{'tag_name':'v2.0.0','draft':False}]
            return result
        monkeypatch.setattr(R.checks,'verify_candidate',candidate)
    else:
        original=registry.client.create_tag
        def create(tag,commit):
            original(tag,commit)
            git('tag','v2.0.0')
        monkeypatch.setattr(registry.client,'create_tag',create)
    with pytest.raises(R.Failure,match='strictly higher|Higher or equal'):
        R.prepare(args,registry.client,journal)
    assert not any(call[:2]==['docker','push'] for call in calls)


def test_tag_only_failed_publication_cannot_reuse_version(prepare_fixture,monkeypatch,tmp_path):
    args,journal,calls,registry,git=prepare_fixture
    original=R.command
    def failed_push(argv,**kw):
        if argv[:2]==['docker','push']:
            raise R.Failure('failed before manifest commit')
        return original(argv,**kw)
    monkeypatch.setattr(R,'command',failed_push)
    with pytest.raises(R.Failure,match='failed before manifest'):
        R.prepare(args,registry.client,journal)
    assert git('rev-parse','v1.0.0')==args.commit and registry.client.existing_version('1.0.0') is None
    calls.clear()
    retry=arguments(tmp_path,tag='',commit=args.commit,control=args.control,work=tmp_path/'retry')
    with pytest.raises(R.Failure,match='Tag already exists'):
        R.prepare(retry,registry.client,R.Journal(tmp_path/'retry-evidence',{}))
    assert not calls


def test_newer_release_prevents_latest_even_after_anonymous_verification(prepare_fixture,monkeypatch,tmp_path):
    args,journal,calls,registry,_=prepare_fixture
    R.prepare(args,registry.client,journal)
    args.existing_digest=args.verified_digest=journal.data['digest']
    args.image_id=journal.data['image_id'];args.release_sha256=journal.data['release_sha256']
    registry.add('2.0.0','2.0.0',args.commit)
    calls.clear()
    with pytest.raises(R.Failure,match='strictly higher'):
        R.promote(args,registry.client,R.Journal(tmp_path/'latest-refused',{}))
    assert not calls


def test_prepare_cannot_receive_independently_entered_tag(prepare_fixture):
    args,journal,calls,registry,_=prepare_fixture
    args.tag='v1.0.0'
    with pytest.raises(R.Failure,match='derive it'):
        R.prepare(args,registry.client,journal)
    assert not calls and not registry.posts


@pytest.mark.parametrize('value',['0.0.0','1.0.1','1.2.1','1.2.2','2.4.7','10.10.3'])
def test_even_minor_stable_policy_does_not_restrict_major_or_patch(value):
    assert R.stable_version(value) == tuple(map(int,value.split('.')))


@pytest.mark.parametrize('value',['1.1.0','1.3.0','2.1.2','1.2.0-rc.1','1.3.0-rc.1'])
def test_odd_minor_and_semver_prereleases_cannot_be_published(git_repo,value):
    repo,_,git=git_repo
    (repo/'release.jsonc').write_text(json.dumps(release_document(value)))
    git('commit','-am','development series fixture')
    with pytest.raises(R.Failure,match='even minor|formal X.Y.Z'):
        R.release_at_commit(repo,git('rev-parse','HEAD'))


def test_readmes_document_minor_policy_without_claiming_releases_exist():
    english=(ROOT/'README.md').read_text()
    chinese=(ROOT/'README.zh-CN.md').read_text()
    assert '**minor**' in english and '**次版本号**' in chinese
    for text in (english,chinese):
        assert all(value in text for value in ('1.2.x','1.4.x','1.3.x','1.2.1'))
    assert 'not announcements' in english and '不表示已有对应版本' in chinese
