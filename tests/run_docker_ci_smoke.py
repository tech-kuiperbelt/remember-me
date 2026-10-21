"""CI smoke acceptance for the official-download image.

Runs the built image with no network and a read-only root, then checks model
readiness, health, add/semantic search, profile identity and graceful stop.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROFILE_ID = "ep_6134c0b15d2bb471"


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"docker {' '.join(args)}\n{result.stderr}\n{result.stdout}")
    return result


def exec_python(name, code):
    return docker("exec", name, "python", "-c", code).stdout.strip()


def api(name, path, payload=None, method=None):
    body = "None" if payload is None else repr(payload)
    code = (
        "import json, urllib.request\n"
        f"payload={body}\n"
        f"request=urllib.request.Request('http://127.0.0.1:1314{path}',"
        f"data=None if payload is None else json.dumps(payload).encode(),"
        f"headers={{'Content-Type':'application/json'}},method={method!r})\n"
        "print(urllib.request.urlopen(request, timeout=60).read().decode())\n"
    )
    return json.loads(exec_python(name, code))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--work", type=Path, default=Path("artifacts/smoke"))
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    name = f"rm-ci-smoke-{int(time.time())}"
    evidence = {"image": args.image, "container": name, "checks": []}

    def passed(check, detail=True):
        evidence["checks"].append({"check": check, "result": detail})
        print("PASS:", check, flush=True)

    docker("run", "-d", "--name", name, "--network", "none", "--read-only",
           "--tmpfs", "/data:uid=10001,gid=10001,mode=700", args.image)
    try:
        deadline = time.monotonic() + 240
        while True:
            try:
                model = api(name, "/api/model")
                if model.get("ready"):
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("model did not become ready within 240s")
            time.sleep(1)
        passed("model ready without network", model)

        deadline = time.monotonic() + 240
        while True:
            health = docker("inspect", "--format", "{{.State.Health.Status}}", name).stdout.strip()
            if health == "healthy":
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"health status is {health!r}, expected healthy")
            time.sleep(3)
        passed("container health is healthy", health)

        config = api(name, "/api/config")
        assert config.get("auto_download_model") is False, config
        assert config.get("local_only") is True, config
        passed("Docker configuration disables model download and keeps local_only", config)

        created = api(name, "/api/memories", {
            "text": "Phase 3.1 offline Chinese 中文推理 acceptance.",
            "metadata": {
                "keywords": ["docker-ci"],
                "categories": ["verification"],
                "anchors": ["Phase3.1Offline"],
            },
        })
        assert created.get("created") is True, created
        memory_id = created["id"]
        passed("memory write in read-only root", {"id": memory_id})

        search = api(name, "/api/memories/search", {
            "query": "Phase 3.1 offline Chinese 中文推理 acceptance",
        })
        assert memory_id in json.dumps(search, ensure_ascii=False), search
        passed("semantic search reaches the new memory", {"id": memory_id})

        vectors = json.loads(exec_python(name, (
            "import json, sqlite3\n"
            "c=sqlite3.connect('/data/memories.db')\n"
            "assert c.execute('pragma integrity_check').fetchone()[0]=='ok'\n"
            "assert not c.execute('pragma foreign_key_check').fetchall()\n"
            "print(json.dumps(c.execute("
            "'select distinct profile_id from memory_embeddings').fetchall()))\n"
        )))
        assert vectors == [[PROFILE_ID]], vectors
        passed("SQLite integrity and embedding profile", vectors)

        execute = exec_python(name, (
            "from pathlib import Path\n"
            "assert Path('/data/config.jsonc').is_file()\n"
            "assert Path('/data/memories.db').is_file()\n"
            "assert Path('/data/logs').is_dir()\n"
            "assert not Path('/data/models').exists()\n"
            "print('ok')\n"
        ))
        assert execute == "ok", execute
        passed("configuration, Dual JSONL and runtime files stay in /data")

        started = time.monotonic()
        docker("stop", "--time", "30", name)
        stopped = json.loads(docker("inspect", name).stdout)[0]
        assert stopped["State"]["ExitCode"] == 0, stopped["State"]
        evidence["stop_seconds"] = time.monotonic() - started
        passed("graceful stop exit 0", evidence["stop_seconds"])
    finally:
        docker("rm", "-f", name, check=False)
        (args.work / "results.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(evidence, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise
