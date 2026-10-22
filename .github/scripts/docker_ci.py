#!/usr/bin/env python3
"""Phase 3.1 GitHub Actions build and cache acceptance.

Modes:
  cold          official-download runtime build, image acceptance, cache export
  warm          fresh runner, remote-cache-only build, expect model/deps cache hits
  source-probe  fresh runner, remote-cache-only build after a temporary source edit
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docker_checks

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
CACHE_REF = os.environ.get(
    "CACHE_REF", "ghcr.io/tech-kuiperbelt/remember-me-buildcache:amd64")
MODE = os.environ.get("MODE", "cold")
SHA = os.environ.get("GITHUB_SHA", "local")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")
RUN_ATTEMPT = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
RUNNER_NAME = os.environ.get("RUNNER_NAME", "local")
IMAGE = os.environ.get("IMAGE", f"remember-me:phase3.1-ci-{SHA}")
VALIDATION_IMAGE = os.environ.get(
    "VALIDATION_IMAGE", f"remember-me:phase3.1-validation-{SHA}")
BUILDER = os.environ.get("BUILDER", f"ci-{RUN_ID}")
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
WEIGHT_SHA256 = "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38"
WEIGHT_BYTES = 2271145830
PROFILE_ID = "ep_6134c0b15d2bb471"

# Conservative, measured-size based estimates. GB here means 10^9 bytes.
IMAGE_BYTES = 3_524_622_940
MODEL_CACHE_BYTES = 2_293_337_721
PIP_CACHE_BYTES = 500_000_000
OVERHEAD_BYTES = 500_000_000


class Failure(RuntimeError):
    pass


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def command(argv, *, check=True, env=None, log=None):
    result = subprocess.run(argv, text=True, capture_output=True,
                            env={**os.environ, **(env or {})})
    if log:
        Path(log).write_text(result.stdout + result.stderr)
    if check and result.returncode:
        raise Failure(f"{' '.join(argv)}\n{result.stderr}\n{result.stdout}")
    return result


def docker(*args, check=True):
    return command(["docker", *args], check=check)


def stream_build(argv, log_path, env=None):
    """Run a long build while teeing plain progress to stdout and a log file."""
    print("+", " ".join(argv), flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log_path.open("w") as log:
        process = subprocess.Popen(
            argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, **(env or {})})
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            print(line, end="", flush=True)
        code = process.wait()
    return code, time.monotonic() - started


def parse_stage_cache(log_text):
    """Map BuildKit step ids to stage names and cached state."""
    stages, _, cached = parse_steps(log_text)
    return {stage for step, stage in stages.items() if step in cached}


def parse_steps(log_text):
    """Return step id -> (stage, command) and the set of cached step ids."""
    stages = {}
    commands = {}
    for line in log_text.splitlines():
        match = re.match(r"#(\d+) \[([^\]]+)\] ?(.*)", line)
        if match:
            step, stage, command = match.groups()
            stages.setdefault(step, stage.split()[0])
            commands.setdefault(step, command.strip())
    cached = {step for step in stages if re.search(rf"^#{step} CACHED$", log_text, re.M)}
    return stages, commands, cached


def verified_files(log_text):
    return sorted(set(re.findall(r"Verified (\S+): ([0-9a-f]{64})", log_text)))


class DiskSampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.samples = []

    def run(self):
        while not self.stop_event.is_set():
            free = shutil.disk_usage("/").free
            self.samples.append({"time": time.time(), "available_bytes": free})
            self.stop_event.wait(5)

    def stop(self):
        self.stop_event.set()
        self.join(timeout=10)
        return self.samples


def disk_report():
    usage = shutil.disk_usage("/")
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "available_bytes": usage.free,
        "available_GB": round(usage.free / 1_000_000_000, 3),
        "available_GiB": round(usage.free / (1024 ** 3), 3),
    }


def estimate_peak(mode):
    if mode == "cold":
        return (IMAGE_BYTES * 2 + MODEL_CACHE_BYTES
                + PIP_CACHE_BYTES + OVERHEAD_BYTES)
    return (IMAGE_BYTES + MODEL_CACHE_BYTES + PIP_CACHE_BYTES
            + OVERHEAD_BYTES + 1_000_000_000)


def preflight(mode, results):
    available = shutil.disk_usage("/").free
    estimate = estimate_peak(mode)
    required = int(estimate * 1.15)
    results["disk"] = {
        "before": disk_report(),
        "estimated_peak_bytes": estimate,
        "required_bytes": required,
        "estimate_GB": round(estimate / 1_000_000_000, 3),
        "required_GB": round(required / 1_000_000_000, 3),
    }
    print(json.dumps(results["disk"], indent=2), flush=True)
    if available < required:
        raise Failure(
            f"insufficient disk: available {available} bytes "
            f"({available / 1_000_000_000:.2f} GB), required {required} bytes "
            f"({required / 1_000_000_000:.2f} GB). No fallback is authorized.")


def builder_cache_state():
    result = docker("buildx", "du", "--builder", BUILDER, check=False)
    return {"exit_code": result.returncode, "output": result.stdout + result.stderr}


def image_metadata(image):
    return docker_checks.image_metadata(image, run=command)


def image_content_checks(image, results):
    return docker_checks.image_content_checks(image, results, run=command)


def cache_manifest():
    result = docker("buildx", "imagetools", "inspect", CACHE_REF,
                    "--format", "{{json .Manifest}}", check=False)
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def gh_visibility():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return {"status": "no_token"}
    result = subprocess.run(
        ["gh", "api",
         "/orgs/tech-kuiperbelt/packages/container/remember-me-buildcache",
         "--jq", ".visibility"],
        text=True, capture_output=True,
        env={**os.environ, "GH_TOKEN": token})
    return {
        "exit_code": result.returncode,
        "visibility": result.stdout.strip(),
        "error": result.stderr.strip(),
    }


def manifest_probe(anonymous):
    env = dict(os.environ)
    if anonymous:
        env["DOCKER_CONFIG"] = tempfile.mkdtemp(prefix="rm-anon-docker-")
    result = subprocess.run(
        ["docker", "manifest", "inspect", CACHE_REF],
        text=True, capture_output=True, env=env)
    return {
        "anonymous": anonymous,
        "exit_code": result.returncode,
        "stdout": result.stdout.strip()[:500],
        "stderr": result.stderr.strip()[:500],
    }


def privacy_gate(results):
    visibility = gh_visibility()
    anonymous = manifest_probe(True)
    authenticated = manifest_probe(False)
    results["cache"]["privacy"] = {
        "package": "tech-kuiperbelt/remember-me-buildcache",
        "api": visibility,
        "anonymous_probe": anonymous,
        "authenticated_probe": authenticated,
    }
    if visibility.get("exit_code") != 0 or visibility.get("visibility") != "private":
        raise Failure(f"cache package privacy is not confirmed: {visibility}")
    if anonymous["exit_code"] == 0:
        raise Failure("anonymous cache manifest probe succeeded; package is public")
    denied = ("unauthorized", "denied", "authentication required", "no basic auth")
    if not any(token in anonymous["stderr"].lower() for token in denied):
        raise Failure(
            "anonymous probe failed without an authorization denial; "
            f"privacy is not proven: {anonymous}")
    if authenticated["exit_code"] != 0:
        raise Failure(f"authenticated cache manifest probe failed: {authenticated}")


def cache_check_before(results):
    manifest = cache_manifest()
    results["cache"]["before"] = manifest
    if manifest["exit_code"] != 0:
        raise Failure(f"remote cache is not readable: {manifest}")


def cache_check_after_cold(results, log_path):
    manifest = cache_manifest()
    results["cache"]["after"] = manifest
    text = Path(log_path).read_text()
    marker = "exporting cache to registry"
    segment = text.split(marker, 1)[1] if marker in text else ""
    if not segment:
        results["cache"]["export_log_status"] = "not_observed"
    elif re.search(r"(?i)(error|failed)", segment):
        results["cache"]["export_log_status"] = "failed"
    elif "writing cache image manifest" in segment:
        results["cache"]["export_log_status"] = "exported"
    else:
        results["cache"]["export_log_status"] = "incomplete"
    results["cache"]["export_status"] = (
        "exported"
        if manifest["exit_code"] == 0
        and results["cache"]["export_log_status"] == "exported"
        else "failed"
    )
    if manifest["exit_code"] != 0:
        raise Failure(f"cache export did not produce a readable manifest: {manifest}")
    if results["cache"]["export_log_status"] == "failed":
        raise Failure("cache export reported an error despite ignore-error=true")
    if results["cache"]["export_log_status"] != "exported":
        raise Failure(
            "cache export completion was not observed in the build log: "
            f"{results['cache']['export_log_status']}"
        )


def check_cached(log_path, results, *, expect_model_download):
    text = Path(log_path).read_text()
    stages, commands, cached_steps = parse_steps(text)
    cached_stages = {stage for step, stage in stages.items() if step in cached_steps}
    verified = verified_files(text)
    results["cache"]["cached_stages"] = sorted(cached_stages)
    results["cache"]["cached_steps"] = [
        {"step": step, "stage": stages[step], "command": commands.get(step, "")}
        for step in sorted(cached_steps, key=int)
    ]
    results["cache"]["verified_files"] = [path for path, _ in verified]
    results["cache"]["verified_count"] = len(verified)
    if expect_model_download:
        if len(verified) != 11:
            raise Failure(f"cold build verified {len(verified)} files, expected 11")
        if "runtime-download" in cached_stages:
            raise Failure("cold build used a cached runtime-download stage")
    else:
        if verified:
            raise Failure(f"warm build still downloaded/verified model files: {verified}")
        for stage in ("deps", "runtime-download"):
            if stage not in cached_stages:
                raise Failure(
                    f"warm build did not report {stage} as CACHED: {sorted(cached_stages)}")
        saw_model_download = "[model-download" in text
        if saw_model_download and "model-download" not in cached_stages:
            raise Failure("warm build re-ran model-download without a cache hit")
        if not saw_model_download:
            results["cache"]["model_download"] = "skipped_by_cached_downstream"
        if MODE == "source-probe":
            app_steps = [
                step for step, stage in stages.items()
                if stage == "runtime-download"
                and commands.get(step, "").startswith(("COPY app/", "COPY web/"))
            ]
            rebuilt = [step for step in app_steps if step not in cached_steps]
            results["cache"]["source_probe_rebuilt_steps"] = rebuilt
            if not rebuilt:
                raise Failure("source probe did not rebuild an application layer")


def run_smoke(image, results):
    result = command([sys.executable, str(ROOT / "tests/run_docker_ci_smoke.py"),
                      "--image", image, "--work", str(ARTIFACTS / "smoke")])
    results["smoke"] = result.stdout


def run_cold(results, sampler):
    results["builder_before"] = builder_cache_state()
    build_command = [
        "docker", "buildx", "build",
        "--builder", BUILDER,
        "--platform", "linux/amd64",
        "--target", "runtime",
        "--load",
        "--no-cache",
        "--provenance=false", "--sbom=false",
        "--progress=plain",
        "--metadata-file", str(ARTIFACTS / "cold-metadata.json"),
        "--cache-to", f"type=registry,ref={CACHE_REF},mode=min,ignore-error=true",
        "-t", IMAGE,
        ".",
    ]
    code, seconds = stream_build(build_command, ARTIFACTS / "cold-build.log")
    results["cold_build"] = {"exit_code": code, "seconds": seconds}
    if code:
        raise Failure(f"cold build failed with exit code {code}")
    check_cached(ARTIFACTS / "cold-build.log", results, expect_model_download=True)
    results["metadata"] = image_metadata(IMAGE)
    image_content_checks(IMAGE, results)
    run_smoke(IMAGE, results)
    docker("rmi", "-f", IMAGE, check=False)

    validation_command = [
        "docker", "buildx", "build",
        "--builder", BUILDER,
        "--platform", "linux/amd64",
        "--target", "validation",
        "--load",
        "--provenance=false", "--sbom=false",
        "--progress=plain",
        "--metadata-file", str(ARTIFACTS / "validation-metadata.json"),
        "-t", VALIDATION_IMAGE,
        ".",
    ]
    code, seconds = stream_build(validation_command, ARTIFACTS / "validation-build.log")
    results["validation_build"] = {"exit_code": code, "seconds": seconds}
    if code:
        raise Failure(f"validation build failed with exit code {code}")
    verify = docker(
        "run", "--rm", "--network", "none", "--read-only",
        "--tmpfs", "/data:uid=10001,gid=10001,mode=700",
        "-e", "TMPDIR=/data",
        "--entrypoint", "/opt/venv/bin/python", VALIDATION_IMAGE,
        "/opt/remember-me/bin/verify_cpu_runtime.py",
        "--model-cache", "/opt/remember-me/models/sentence-transformers",
        "--expected-profile-id", PROFILE_ID,
    )
    results["verifier"] = verify.stdout
    parsed = json.loads(verify.stdout)
    if parsed.get("model_validation") != "offline_inference_passed":
        raise Failure(f"offline verifier did not pass: {parsed}")
    if parsed.get("profile", {}).get("profile_id") != PROFILE_ID:
        raise Failure(f"unexpected profile: {parsed.get('profile')}")
    docker("rmi", "-f", VALIDATION_IMAGE, check=False)
    cache_check_after_cold(results, ARTIFACTS / "cold-build.log")
    privacy_gate(results)


def run_cache_only(results):
    cache_check_before(results)
    probe_changed = False
    try:
        if MODE == "source-probe":
            target = ROOT / "web/static/js/main.js"
            target.write_text(target.read_text() + "\n/* phase3.1 cache probe */\n")
            probe_changed = True
        build_command = [
            "docker", "buildx", "build",
            "--builder", BUILDER,
            "--platform", "linux/amd64",
            "--target", "runtime",
            "--output", "type=cacheonly",
            "--provenance=false", "--sbom=false",
            "--progress=plain",
            "--metadata-file", str(ARTIFACTS / f"{MODE}-metadata.json"),
            "--cache-from", f"type=registry,ref={CACHE_REF}",
            ".",
        ]
        code, seconds = stream_build(build_command, ARTIFACTS / f"{MODE}-build.log")
        results["cache_build"] = {"exit_code": code, "seconds": seconds}
        if code:
            raise Failure(f"{MODE} build failed with exit code {code}")
        check_cached(ARTIFACTS / f"{MODE}-build.log", results, expect_model_download=False)
        if MODE == "source-probe":
            diff = command(["git", "diff", "--", "web/static/js/main.js"]).stdout
            results["source_probe"] = {"changed": bool(diff), "diff": diff}
            if not diff:
                raise Failure("source probe did not change the build context")
    finally:
        if probe_changed:
            command(["git", "checkout", "--", "web/static/js/main.js"], check=False)


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    results = {
        "phase": "3.1",
        "mode": MODE,
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": {
            "id": RUN_ID,
            "attempt": RUN_ATTEMPT,
            "runner": RUNNER_NAME,
            "sha": SHA,
            "repository": os.environ.get("GITHUB_REPOSITORY"),
            "server_url": os.environ.get("GITHUB_SERVER_URL"),
        },
        "cache": {"ref": CACHE_REF, "builder": BUILDER},
        "result": "failed",
    }
    sampler = None
    try:
        preflight(MODE, results)
        sampler = DiskSampler()
        sampler.start()
        if MODE == "cold":
            run_cold(results, sampler)
        elif MODE in ("warm", "source-probe"):
            run_cache_only(results)
        else:
            raise Failure(f"unknown mode: {MODE}")
        results["result"] = "passed"
    except Exception as exc:
        results["error"] = str(exc)
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        raise
    finally:
        if sampler:
            samples = sampler.stop()
            results["disk"]["samples"] = samples
            if samples:
                results["disk"]["minimum_available_bytes"] = min(
                    item["available_bytes"] for item in samples)
                results["disk"]["minimum_available_GB"] = round(
                    results["disk"]["minimum_available_bytes"] / 1_000_000_000, 3)
        results["disk_after"] = disk_report()
        write_json(ARTIFACTS / "results.json", results)
        summary = (
            f"# Phase 3.1 {MODE}\n\n"
            f"- Run: {RUN_ID} attempt {RUN_ATTEMPT} on {RUNNER_NAME}\n"
            f"- SHA: {SHA}\n"
            f"- Result: {results['result']}\n"
            f"- Cache: {results['cache'].get('export_status', 'not_exported')}\n"
            f"- Error: {results.get('error', '')}\n"
        )
        (ARTIFACTS / "summary.md").write_text(summary)
        print(summary, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
