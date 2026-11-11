"""Frozen joint-release gates; no production credentials or Registry writes."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("unified_release", ROOT / ".github/scripts/unified_release.py")
U = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(U)


@pytest.fixture
def manifest():
    names = list(U.INSTALLERS) + [n + ".sha256" for n in U.INSTALLERS] + ["docker.tar.gz.part-000"]
    return {"version": "1.1.3", "source_commit": "a" * 40, "release_sha256": "b" * 64,
            "notes_sha256": U.notes_sha256("Reviewed notes\n"),
            "assets": [{"name": n, "size": 10, "sha256": "c" * 64} for n in names],
            "docker": {"image_id": "sha256:" + "d" * 64, "archive_parts": [names[-1]]}}


def image_info(manifest):
    return {"digest": "sha256:" + "e" * 64, "image_id": manifest["docker"]["image_id"],
            "labels": {"org.opencontainers.image.source": "https://github.com/" + U.REPOSITORY,
                       "org.opencontainers.image.version": "1.1.3",
                       "org.opencontainers.image.revision": manifest["source_commit"]}}


def acceptance(manifest):
    return {"source_commit": manifest["source_commit"], "dmg_sha256": "c" * 64,
            "tester": "Independent Apple Silicon Mac tester", "accepted_at": "2026-11-11T00:00:00Z",
            "checks": {k: True for k in U.MAC_CHECKS}}


def metadata(manifest):
    records = manifest["assets"] + [{"name": U.MANIFEST, "size": 100, "sha256": "f" * 64}]
    return {"id": 123, "tag_name": U.TAG, "target_commitish": manifest["source_commit"],
            "html_url": "https://github.com/" + U.REPOSITORY + "/releases/tag/" + U.TAG,
            "body": "Reviewed notes\n", "draft": True, "prerelease": True,
            "assets": [{"name": r["name"], "size": r["size"], "digest": "sha256:" + r["sha256"],
                        "state": "uploaded", "browser_download_url": "https://example.invalid/" + r["name"]}
                       for r in records]}


def test_valid_manifest(manifest):
    assert U.validate_manifest(manifest) is manifest


def test_draft_lookup_uses_authenticated_listing_then_release_id(monkeypatch):
    seen = []
    expected = {"id": 321, "tag_name": U.TAG, "draft": True}

    def query(path):
        seen.append(path)
        if "?per_page=100&page=1" in path:
            return [{"id": 1, "tag_name": "v1.1.2"}] * 100
        if "?per_page=100&page=2" in path:
            return [expected]
        assert path.endswith("/releases/321")
        return expected

    monkeypatch.setattr(U, "gh_json", query)
    assert U.release_metadata() is expected
    assert len(seen) == 3
    assert all("/releases/tags/" not in path for path in seen)


def test_duplicate_pending_drafts_are_rejected(monkeypatch):
    monkeypatch.setattr(U, "gh_json", lambda path: [{"id": 1, "tag_name": U.TAG}] * 2)
    with pytest.raises(U.release.Failure, match="exactly one"):
        U.release_metadata()


def test_publication_manifest_can_pin_private_stage_digest(manifest):
    manifest["docker"].update(staging_ref=f"ghcr.io/{U.release.CACHE}:candidate-{manifest['source_commit']}",
                              staging_digest="sha256:" + "e" * 64)
    U.validate_manifest(manifest)
    assert U.frozen_digest(manifest) == manifest["docker"]["staging_digest"]
    manifest["docker"]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(U.release.Failure, match="digest differ"):
        U.validate_manifest(manifest)


def test_private_stage_checks_actual_config_bytes_and_identity(manifest, monkeypatch):
    body = json.dumps({"os": "linux", "architecture": "amd64", "config": {
        "Labels": image_info(manifest)["labels"]}}).encode()
    image_id = "sha256:" + hashlib.sha256(body).hexdigest()
    manifest["docker"].update(image_id=image_id,
                              staging_ref=f"ghcr.io/{U.release.CACHE}:candidate-{manifest['source_commit']}",
                              staging_digest="sha256:" + "e" * 64)
    events = []

    class Registry:
        def manifest(self, repo, digest):
            assert repo == U.release.CACHE and digest == manifest["docker"]["staging_digest"]
            return {"digest": digest, "manifest": {"config": {"digest": image_id}}}

        def resource(self, repo, path):
            assert repo == U.release.CACHE and path == "blobs/" + image_id
            return {}, body

    monkeypatch.setattr(U.release, "cache_gate", lambda _: events.append("private-gate"))
    monkeypatch.setattr(U, "run", lambda argv: events.append(argv) or json.dumps({"config": {"digest": image_id}}))
    U.verify_staging(Registry(), manifest)
    assert events[0] == "private-gate"
    assert events[1] == ["docker", "buildx", "imagetools", "create", "--dry-run", "--prefer-index=false",
                         "--tag", U.release.IMAGE + ":1.1.3",
                         f"ghcr.io/{U.release.CACHE}@{manifest['docker']['staging_digest']}"]
    manifest["docker"]["image_id"] = "sha256:" + "0" * 64
    with pytest.raises(U.release.Failure, match="config differs"):
        U.verify_staging(Registry(), manifest)


@pytest.mark.parametrize("change", [
    lambda m: m.update(version="1.1.2"),
    lambda m: m.update(source_commit="short"),
    lambda m: m.update(release_sha256="bad"),
    lambda m: m.update(notes_sha256="bad"),
    lambda m: m["assets"][0].update(name="../escape"),
    lambda m: m["assets"][0].update(name="/escape"),
    lambda m: m["assets"][0].update(size=True),
    lambda m: m["assets"][0].update(sha256="bad"),
    lambda m: m["assets"].append(m["assets"][0].copy()),
    lambda m: m["assets"].pop(0),
    lambda m: m["assets"].append({"name": U.MANIFEST, "size": 1, "sha256": "a" * 64}),
    lambda m: m["assets"].append({"name": U.MAC_ACCEPTANCE, "size": 1, "sha256": "a" * 64}),
    lambda m: m["docker"].update(image_id="bad"),
    lambda m: m["docker"].update(digest="bad"),
    lambda m: m["docker"].update(archive_parts=["unlisted"]),
    lambda m: m["docker"].update(archive_parts=[m["docker"]["archive_parts"][0]] * 2),
    lambda m: m["docker"].update(archive_parts=[U.INSTALLERS[0]]),
])
def test_invalid_manifest_fails_closed(manifest, change):
    change(manifest)
    with pytest.raises(U.release.Failure):
        U.validate_manifest(manifest)


@pytest.mark.parametrize("check", U.MAC_CHECKS)
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_every_real_mac_check_must_be_boolean_true(manifest, check, value):
    record = acceptance(manifest)
    record["checks"][check] = value
    with pytest.raises(U.release.Failure, match="incomplete"):
        U.validate_acceptance(record, manifest)


@pytest.mark.parametrize("field,value", [("source_commit", "f" * 40), ("dmg_sha256", "f" * 64),
                                         ("tester", " "), ("accepted_at", "")])
def test_acceptance_bound_to_final_source_and_dmg(manifest, field, value):
    record = acceptance(manifest)
    record[field] = value
    with pytest.raises(U.release.Failure):
        U.validate_acceptance(record, manifest)


def test_fully_matching_acceptance(manifest):
    record = acceptance(manifest)
    assert U.validate_acceptance(record, manifest) is record


def test_installers_verify_bytes_independently(tmp_path):
    path = tmp_path / "installer"
    path.write_bytes(b"original")
    record = {"name": path.name, "size": 8, "sha256": hashlib.sha256(b"original").hexdigest()}
    U.verify_file(path, record)
    path.write_bytes(b"modified")
    with pytest.raises(U.release.Failure, match="hash mismatch"):
        U.verify_file(path, record)


def test_draft_asset_set_and_body_are_frozen(manifest):
    U.verify_draft(metadata(manifest), manifest, "f" * 64)
    for key, changed in [("body", "Changed notes"), ("target_commitish", "master"),
                         ("tag_name", "v1.1.2"), ("prerelease", False)]:
        data = metadata(manifest)
        data[key] = changed
        with pytest.raises(U.release.Failure):
            U.verify_draft(data, manifest, "f" * 64)
    for mutate in (lambda d: d["assets"].pop(),
                   lambda d: d["assets"][0].update(digest="sha256:" + "0" * 64),
                   lambda d: d["assets"][0].update(size=9),
                   lambda d: d["assets"].append({"name": "extra"})):
        data = metadata(manifest)
        mutate(data)
        with pytest.raises(U.release.Failure):
            U.verify_draft(data, manifest, "f" * 64)


def test_initial_publication_plan(manifest):
    assert U.publication_plan(manifest, {"1.0.1", "1.1.2"}, None, None, True) == {
        "create_tag": True, "push_image": True, "publish_release": True}


@pytest.mark.parametrize("tagged,draft", [(False, True), (True, True), (True, False)])
def test_matching_immutable_identity_can_resume(manifest, tagged, draft):
    plan = U.publication_plan(manifest, {"1.1.2", "1.1.3"},
                              manifest["source_commit"] if tagged else None, image_info(manifest), draft)
    assert not plan["push_image"]
    assert plan["create_tag"] is not tagged
    assert plan["publish_release"] is draft


@pytest.mark.parametrize("higher", ["1.1.4", "1.2.0", "2.0.0"])
def test_higher_history_blocks_fresh_and_resume(manifest, higher):
    for info in (None, image_info(manifest)):
        with pytest.raises(U.release.Failure, match="Newer"):
            U.publication_plan(manifest, {higher}, None, info, True)


def test_tag_only_state_and_conflicts_are_rejected(manifest):
    with pytest.raises(U.release.Failure, match="Tag-only"):
        U.publication_plan(manifest, {"1.1.3"}, manifest["source_commit"], None, True)
    with pytest.raises(U.release.Failure, match="another source"):
        U.publication_plan(manifest, {"1.1.3"}, "f" * 40, image_info(manifest), True)
    for key, value in [("image_id", "sha256:" + "0" * 64)]:
        info = image_info(manifest)
        info[key] = value
        with pytest.raises(U.release.Failure):
            U.publication_plan(manifest, {"1.1.3"}, None, info, True)
    manifest["docker"]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(U.release.Failure, match="digest mismatch"):
        U.publication_plan(manifest, {"1.1.3"}, None, image_info(manifest), True)


def test_missing_go_blocks_before_download_or_mutation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(U, "preflight", lambda _: calls.append("preflight"))
    with pytest.raises(U.release.Failure, match="confirm-go"):
        U.publish(argparse.Namespace(confirm_go=False, work=tmp_path))
    assert not calls


def test_anonymous_image_verification_precedes_and_does_not_require_git_tag(tmp_path, manifest, monkeypatch):
    from types import SimpleNamespace
    info = image_info(manifest)
    commands = []

    class Anonymous:
        token = ""

        def inspect(self, ref):
            assert ref in (U.VERSION, info["digest"])
            return info

    monkeypatch.setattr(U.release, "remote_tag", lambda *a: pytest.fail("Git Tag must not be required yet"))
    monkeypatch.setattr(U.release, "command", lambda argv, **kwargs: commands.append((argv, kwargs)) or SimpleNamespace(stdout=""))
    monkeypatch.setattr(U.release, "verify_release_file", lambda *a: {"verified": True})
    args = argparse.Namespace(tag=U.TAG, commit=manifest["source_commit"], existing_digest=info["digest"],
                              image_id=manifest["docker"]["image_id"], release_sha256=manifest["release_sha256"])
    journal = U.release.Journal(tmp_path / "anonymous", {})
    U.release.verify_public(args, Anonymous(), journal)
    assert len(commands) == 2
    assert all(argv[0] == "docker" and "pull" in argv for argv, _ in commands)
    assert all("GH_TOKEN" not in options["env"] and "GITHUB_TOKEN" not in options["env"] for _, options in commands)
    assert journal.data["anonymous_verified"] is True


def test_preflight_checks_bundle_without_mac_receipt_or_publication(tmp_path, manifest, monkeypatch):
    records = {item["name"]: item for item in manifest["assets"]}
    for name in U.INSTALLERS:
        body = ("frozen " + name).encode()
        (tmp_path / name).write_bytes(body)
        records[name].update(size=len(body), sha256=hashlib.sha256(body).hexdigest())
        sidecar = (records[name]["sha256"] + "  " + name + "\n").encode()
        (tmp_path / (name + ".sha256")).write_bytes(sidecar)
        records[name + ".sha256"].update(size=len(sidecar), sha256=hashlib.sha256(sidecar).hexdigest())
    part = manifest["docker"]["archive_parts"][0]
    (tmp_path / part).write_bytes(b"frozen archive")
    records[part].update(size=14, sha256=hashlib.sha256(b"frozen archive").hexdigest())
    (tmp_path / U.MANIFEST).write_text(json.dumps(manifest))
    manifest_hash = U.sha256(tmp_path / U.MANIFEST)
    data = metadata(manifest)
    next(a for a in data["assets"] if a["name"] == U.MANIFEST)["digest"] = "sha256:" + manifest_hash
    events = []
    monkeypatch.setattr(U, "release_metadata", lambda: data)
    monkeypatch.setattr(U, "check_source", lambda *a: events.append("source"))
    monkeypatch.setattr(U, "registry_client", lambda: object())
    monkeypatch.setattr(U, "remote_state", lambda *a: ({"push_image": True, "create_tag": True,
                                                     "publish_release": True}, None))
    monkeypatch.setattr(U, "load_and_verify", lambda *a: events.append("runtime"))
    monkeypatch.setattr(U, "run", lambda argv, **kw: pytest.fail(f"Unexpected command: {argv}"))
    args = argparse.Namespace(work=tmp_path, source=ROOT, manifest_sha256=manifest_hash)
    U.preflight(args)
    assert events == ["source", "runtime"]
    assert not (tmp_path / U.MAC_ACCEPTANCE).exists()
    assert "WAITING_FOR_MAC_ACCEPTANCE" in json.loads((tmp_path / "preflight.json").read_text())["state"]


@pytest.mark.parametrize("private_staging", [False, True])
def test_go_orders_copy_verify_tag_publish_and_second_go_is_read_only(tmp_path, manifest, monkeypatch, private_staging):
    if private_staging:
        manifest["docker"].update(staging_ref=f"ghcr.io/{U.release.CACHE}:candidate-{manifest['source_commit']}",
                                  staging_digest="sha256:" + "e" * 64)
    data = metadata(manifest)
    receipt = tmp_path / U.MAC_ACCEPTANCE
    receipt.write_text(json.dumps(acceptance(manifest)))
    data["assets"].append({"name": U.MAC_ACCEPTANCE, "size": receipt.stat().st_size,
                           "digest": "sha256:" + U.sha256(receipt), "state": "uploaded",
                           "browser_download_url": "https://example.invalid/receipt"})
    state = {"tag": None, "image": None}
    events = []

    class Registry:
        def inspect(self, tag, **kwargs):
            assert tag == "latest"
            return {"digest": "sha256:" + "1" * 64}

        def existing_version(self, version):
            return state["image"]

        def request(self, url):
            return 200, {}, json.dumps(data).encode()

    registry = Registry()
    monkeypatch.setattr(U, "preflight", lambda _: (manifest, data, registry))
    monkeypatch.setattr(U, "download_asset", lambda name, work, **kwargs: receipt)
    monkeypatch.setattr(U, "release_metadata", lambda: copy.deepcopy(data))
    monkeypatch.setattr(U.release, "Registry", Registry)
    monkeypatch.setattr(U.release, "remote_tag", lambda *a: {"commit": state["tag"]})
    monkeypatch.setattr(U.release, "verify_public", lambda *a: events.append("anonymous-verify"))
    monkeypatch.setattr(U, "verify_staging", lambda *a: events.append("private-verify"))

    def remote(*_):
        history = {"1.1.2"} | ({"1.1.3"} if state["image"] or state["tag"] else set())
        return U.publication_plan(manifest, history, state["tag"], state["image"], data["draft"]), state["image"]

    def run(argv, **kwargs):
        assert "build" not in argv and "latest" not in argv
        if argv[:2] == ["docker", "push"]:
            events.append("push")
            state["image"] = image_info(manifest)
        elif argv[:4] == ["docker", "buildx", "imagetools", "create"]:
            assert "--prefer-index=false" in argv
            assert argv[-1] == f"ghcr.io/{U.release.CACHE}@{manifest['docker']['staging_digest']}"
            events.append("registry-copy")
            state["image"] = image_info(manifest)
        elif argv[:4] == ["gh", "api", "--method", "POST"]:
            events.append("create-tag")
            state["tag"] = manifest["source_commit"]
        elif argv[:3] == ["gh", "release", "edit"]:
            events.append("publish")
            data["draft"] = False
        return ""

    monkeypatch.setattr(U, "remote_state", remote)
    monkeypatch.setattr(U, "run", run)
    args = argparse.Namespace(confirm_go=True, work=tmp_path, source=ROOT, manifest_sha256="f" * 64)
    U.publish(args)
    expected = ["private-verify", "registry-copy"] if private_staging else ["push"]
    assert events == expected + ["anonymous-verify", "create-tag", "publish"]
    events.clear()
    U.publish(args)
    assert events == ["anonymous-verify"]
    assert json.loads((tmp_path / "publication.json").read_text())["latest"] == "untouched"
