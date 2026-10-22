"""Opt-in local compatibility evidence; production is only opened read-only.

Creates a SQLite Backup API snapshot, then mounts a separate working copy.
Private database contents and individual vectors remain in the local work dir.
Only aggregate hashes, counts and numeric comparisons belong in public evidence.
"""
import argparse
from contextlib import closing
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "ep_6134c0b15d2bb471"
TEXTS = ["Remember Me semantic memory.", "中文长期记忆检索验证。", "Docker 与 Python mixed language 1024 dimensions.",
         "A", "数据库备份与恢复", "First line.\nSecond line with punctuation: version 1.2.3!",
         "Reliable local inference preserves the meaning of saved knowledge. " * 24,
         "固定模型快照应保持查询和文档向量一致，容器重建后仍能检索原有记忆。" * 32]


def vectors(cache):
    sys.path.insert(0, str(ROOT))
    import torch
    from app.embedding.embed import SentenceTransformersEmbedder
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_XET="1")

    def offline(event, args):
        if event in ("socket.connect", "socket.getaddrinfo"):
            raise RuntimeError("Offline vector probe attempted network access")

    sys.addaudithook(offline)
    torch.set_num_threads(2)
    provider = SentenceTransformersEmbedder("BAAI/bge-m3", cache_dir=str(cache), device="cpu",
                                           offline=True, auto_download=False)
    try:
        assert provider.spec.profile_id == PROFILE
        return {"profile": provider.spec.profile_id, "device": "cpu",
                "versions": {name: importlib.metadata.version(name)
                             for name in ("torch", "sentence-transformers", "transformers", "numpy")},
                "vectors": [fn(text) for fn in (provider.embed_query, provider.embed_document) for text in TEXTS]}
    finally:
        provider.close()


def database_state(path):
    with closing(sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)) as connection:
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("pragma foreign_key_check").fetchall()
        result = {}
        for table, keys in (("memory_embeddings", "memory_id,profile_id"),
                            ("index_value_embeddings", "index_value_id,profile_id")):
            aggregate = hashlib.sha256()
            count = 0
            for row in connection.execute(f"select {keys},embedding from {table} order by {keys}"):
                aggregate.update(json.dumps(row[:-1], separators=(",", ":")).encode())
                aggregate.update(hashlib.sha256(row[-1]).digest())
                count += 1
            result[table] = {"count": count, "sha256": aggregate.hexdigest()}
        connection.row_factory = sqlite3.Row
        profiles = [dict(row) for row in connection.execute("select * from embedding_profiles order by profile_id")]
        # configure_embedding_profile refreshes updated_at on every startup.
        # Compare all identity/definition fields; retain the operational times
        # separately so this expected change remains visible in the evidence.
        result["profile_updated_at"] = {row["profile_id"]: row.pop("updated_at") for row in profiles}
        result["profiles_sha256"] = hashlib.sha256(json.dumps(profiles).encode()).hexdigest()
        result["active_profile"] = connection.execute(
            "select profile_id from embedding_profiles where status='active'").fetchone()[0]
        result["live_memories"] = connection.execute("select count(*) from memories where deleted=0").fetchone()[0]
        return result


def probe_database():
    sys.path.insert(0, str(ROOT))
    from app.config.settings import Settings
    from app.runtime import create_runtime
    import torch
    torch.set_num_threads(2)
    before = database_state("/data/memories.db")
    assert before["active_profile"] == PROFILE
    with closing(sqlite3.connect("/data/memories.db")) as connection:
        samples = connection.execute(
            "select id,text,namespace from memories where deleted=0 and length(text) between 20 and 512 "
            "order by length(text),id limit 3").fetchall()
    assert len(samples) == 3, "Need three existing records for the copy retrieval probe"
    runtime = create_runtime(Settings.from_config("/data", environment=os.environ), configure_logging=False)
    try:
        runtime.start()
        for mid, text, namespace in samples:
            matches = runtime.service.memory_search(text, k=10, namespace=namespace)
            assert any(row["id"] == mid for row in matches), "Existing record not retrieved"
    finally:
        runtime.close()
    after = database_state("/data/memories.db")
    stable = lambda value: {key: item for key, item in value.items() if key != "profile_updated_at"}
    assert stable(before) == stable(after), "Profile definition or existing vectors changed during copy retrieval"
    return {"queries_passed": len(samples), "before": before, "after": after, "unchanged": True,
            "profile_updated_at_changed": before["profile_updated_at"] != after["profile_updated_at"]}


def compare(host, image):
    assert host["profile"] == image["profile"] == PROFILE
    assert len(host["vectors"]) == len(image["vectors"]) == 16
    comparisons = []
    for left, right in zip(host["vectors"], image["vectors"]):
        assert len(left) == len(right) == 1024
        assert all(math.isfinite(value) for value in left + right)
        norms = [math.sqrt(sum(value * value for value in vec)) for vec in (left, right)]
        maximum = max(abs(a - b) for a, b in zip(left, right))
        cosine = sum(a * b for a, b in zip(left, right)) / math.prod(norms)
        assert max(abs(norm - 1) for norm in norms) <= 1e-5, "Vector not normalized"
        assert maximum <= 1e-4, "Component error exceeds 1e-4"
        assert cosine >= .99999, "Cosine below 0.99999"
        comparisons.append({"maximum_component_error": maximum, "cosine": cosine, "norms": norms})
    return {"pairs": len(comparisons), "dimension": 1024, "comparisons": comparisons,
            "maximum_component_error": max(row["maximum_component_error"] for row in comparisons),
            "minimum_cosine": min(row["cosine"] for row in comparisons),
            "host_versions": host["versions"], "image_versions": image["versions"]}


def execute(argv, log):
    result = subprocess.run(argv, text=True, capture_output=True)
    Path(log).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Probe failed; private local log: {log}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--model-cache", type=Path)
    parser.add_argument("--worker", choices=("vectors", "database"))
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(vectors(args.model_cache) if args.worker == "vectors" else probe_database()))
        return
    if not args.image or not args.source_db or not args.model_cache:
        parser.error("--image, --source-db and --model-cache are required")
    work = Path(tempfile.mkdtemp(prefix="rm-phase32-compat-"))
    print(f"Private work directory: {work}", flush=True)
    (work / "input").mkdir()
    (work / "data").mkdir()
    results = {"image": args.image, "result": "failed", "production_open_mode": "read-only SQLite Backup API",
               "production_mounted": False, "reindex": False}
    try:
        source_uri = args.source_db.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source:
            with closing(sqlite3.connect(work / "input/memories.db")) as target:
                source.backup(target)
        results["snapshot"] = database_state(work / "input/memories.db")
        script = Path(__file__).resolve()
        host = json.loads(execute([sys.executable, str(script), "--worker", "vectors",
                                   "--model-cache", str(args.model_cache.resolve())], work / "host-vectors-private.log"))
        image_base = ["docker", "run", "--rm", "--network", "none", "--read-only",
                      "--tmpfs", "/data:uid=10001,gid=10001,mode=700",
                      "-e", "TMPDIR=/data",
                      "--mount", f"type=bind,src={script},dst=/verification/probe.py,readonly",
                      "-e", "PYTHONPATH=/opt/remember-me", "--entrypoint", "python", args.image]
        image = json.loads(execute(image_base + ["/verification/probe.py", "--worker", "vectors",
                                                "--model-cache", "/opt/remember-me/models/sentence-transformers"],
                                   work / "image-vectors-private.log"))
        results["vectors"] = compare(host, image)
        # Only the disposable snapshot is visible to this root initialization
        # helper. The application/probe below still runs as the image's UID 10001.
        execute(["docker", "run", "--rm", "--network", "none", "--read-only", "--user", "0:0",
                 "--mount", f"type=bind,src={work / 'input'},dst=/snapshot,readonly",
                 "--mount", f"type=bind,src={work / 'data'},dst=/data",
                 "--entrypoint", "python", args.image, "-c",
                 "import shutil,os; shutil.copy2('/snapshot/memories.db','/data/memories.db'); "
                 "os.chown('/data',10001,10001); os.chown('/data/memories.db',10001,10001)"],
                work / "copy.log")
        probe = ["docker", "run", "--rm", "--network", "none", "--read-only",
                 "--mount", f"type=bind,src={work / 'data'},dst=/data",
                 "--mount", f"type=bind,src={script},dst=/verification/probe.py,readonly",
                 "-e", "PYTHONPATH=/opt/remember-me", "-e", "TMPDIR=/data", "--entrypoint", "python", args.image,
                 "/verification/probe.py", "--worker", "database"]
        results["database"] = json.loads(execute(probe, work / "database-private.log"))
        results["result"] = "passed"
    except Exception as exc:
        results["error"] = str(exc)
        raise
    finally:
        (work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results, indent=2), flush=True)
    if results["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
