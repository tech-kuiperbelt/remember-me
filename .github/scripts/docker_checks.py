"""Small shared image acceptance checks for Docker CI and manual releases."""
import json
import subprocess
import sys
from pathlib import Path

REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
WEIGHT_SHA256 = "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38"
WEIGHT_BYTES = 2271145830
PROFILE_ID = "ep_6134c0b15d2bb471"


class Failure(RuntimeError):
    pass


def command(argv, *, check=True):
    result = subprocess.run(argv, text=True, capture_output=True)
    if check and result.returncode:
        raise Failure(f"Command failed: {argv[0]}\n{result.stdout}{result.stderr}")
    return result


def image_metadata(image, *, run=command):
    inspect = run(["docker", "image", "inspect", image])
    history = run(["docker", "history", "--no-trunc", image])
    item = json.loads(inspect.stdout)[0]
    return {
        "id": item.get("Id"),
        "repo_digests": item.get("RepoDigests"),
        "size_bytes": item.get("Size"),
        "config": {
            "User": item.get("Config", {}).get("User"),
            "Entrypoint": item.get("Config", {}).get("Entrypoint"),
            "Env": item.get("Config", {}).get("Env"),
            "Labels": item.get("Config", {}).get("Labels"),
            "Healthcheck": item.get("Config", {}).get("Healthcheck"),
        },
        "history": history.stdout,
    }


def image_content_checks(image, results, *, run=command):
    checks = []
    shell = (
        "set -eu; "
        f"test \"$(cat /opt/remember-me/models/sentence-transformers/"
        f"models--BAAI--bge-m3/refs/main)\" = {REVISION}; "
        f"test \"$(stat -Lc %s /opt/remember-me/models/sentence-transformers/"
        f"models--BAAI--bge-m3/snapshots/{REVISION}/pytorch_model.bin)\" = {WEIGHT_BYTES}; "
        f"test \"$(sha256sum /opt/remember-me/models/sentence-transformers/"
        f"models--BAAI--bge-m3/snapshots/{REVISION}/pytorch_model.bin | cut -d' ' -f1)\" = {WEIGHT_SHA256}; "
        "test ! -e /opt/remember-me/.env; "
        "test ! -e /opt/remember-me/tests; "
        "test ! -e /opt/remember-me/bin/verify_cpu_runtime.py; "
        "test ! -e /opt/remember-me/requirements/docker-cpu-py312-linux-amd64.lock; "
        "test \"$(id -u):$(id -g)\" = 10001:10001; "
        "/opt/venv/bin/python -m pip check"
    )
    result = run(["docker", "run", "--rm", "--network", "none", "--read-only",
                  "--entrypoint", "sh", image, "-c", shell], check=False)
    checks.append({"check": "model/refs/labels/user/content", "exit_code": result.returncode,
                   "output": result.stdout + result.stderr})
    if result.returncode:
        raise Failure(f"image content checks failed:\n{result.stdout}{result.stderr}")
    labels = json.loads(run(["docker", "image", "inspect", image]).stdout)[0]["Config"]["Labels"]
    expected = {
        "org.opencontainers.image.source": "https://github.com/tech-kuiperbelt/remember-me",
        "org.opencontainers.image.licenses": "Apache-2.0",
        "io.remember-me.model.revision": REVISION,
        "io.remember-me.model.license": "MIT",
    }
    for key, value in expected.items():
        if labels.get(key) != value:
            raise Failure(f"label {key} is {labels.get(key)!r}, expected {value!r}")
    results["content_checks"] = checks


def verify_candidate(image, source, artifacts, *, run=command, tools=None):
    """Check the actual runtime image, mounting existing verification tools read-only."""
    source, artifacts = Path(source).resolve(), Path(artifacts).resolve()
    tools = Path(tools or Path(__file__).resolve().parents[2]).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    results = {"image": image}
    try:
        results["metadata"] = image_metadata(image, run=run)
        image_content_checks(image, results, run=run)
        smoke = run([sys.executable, str(tools / "tests/run_docker_ci_smoke.py"),
                     "--image", image, "--work", str(artifacts / "smoke")])
        (artifacts / "smoke.log").write_text(smoke.stdout + smoke.stderr)
        verify = run([
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--tmpfs", "/data:uid=10001,gid=10001,mode=700",
            "-e", "TMPDIR=/data",
            "--mount", f"type=bind,src={tools / 'bin'},dst=/opt/remember-me/bin,readonly",
            "--mount", f"type=bind,src={source / 'requirements'},dst=/opt/remember-me/requirements,readonly",
            "--entrypoint", "/opt/venv/bin/python", image,
            "/opt/remember-me/bin/verify_cpu_runtime.py",
            "--model-cache", "/opt/remember-me/models/sentence-transformers",
            "--expected-profile-id", PROFILE_ID,
        ])
        (artifacts / "verifier.log").write_text(verify.stdout + verify.stderr)
        results["verifier"] = json.loads(verify.stdout)
        if (results["verifier"].get("model_validation") != "offline_inference_passed"
                or results["verifier"].get("profile", {}).get("profile_id") != PROFILE_ID):
            raise Failure("Offline verifier/profile did not pass")
        results["result"] = "passed"
        return results
    except Exception as exc:
        results.update(result="failed", error=str(exc))
        raise
    finally:
        (artifacts / "candidate.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False) + "\n")
