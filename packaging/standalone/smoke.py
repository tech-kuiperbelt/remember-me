#!/usr/bin/env python3
"""S2 prototype smoke test; copies an artifact into a fresh, isolated work tree.

Run inside an OS network namespace with only loopback and --require-offline to
prove offline operation. This script does not disable networking itself and does
not claim clean-host or three-distribution acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.request


PROFILE_ID = "ep_6134c0b15d2bb471"
TEXT = "S2StandaloneSmoke: Portable Python preserves 中文离线记忆 and semantic retrieval."
UPDATED = TEXT + " Updated before graceful restart."


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def network_snapshot():
    interfaces = sorted(name.strip().split(":")[0] for name in
                        Path("/proc/net/dev").read_text().splitlines()[2:])
    return {"interfaces": interfaces,
            "namespace": os.readlink("/proc/self/ns/net"),
            "only_loopback": interfaces == ["lo"]}


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.protocol = "2025-03-26"
        self.next_id = 0

    def request(self, path, payload=None, *, accept="application/json"):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Accept": accept, "MCP-Protocol-Version": self.protocol}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=body, headers=headers)
        with self.opener.open(request, timeout=120) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def api(self, path):
        return json.loads(self.request(path)[2])

    def rpc(self, method, params=None, *, notification=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notification:
            self.next_id += 1
            payload["id"] = self.next_id
        status, _, body = self.request("/mcp", payload,
                                       accept="application/json, text/event-stream")
        if notification:
            require(status == 202, f"Notification rejected: {status}")
            return None
        envelope = json.loads(body)
        require(envelope.get("id") == self.next_id, "JSON-RPC response ID mismatch")
        require("error" not in envelope, f"JSON-RPC failure: {envelope}")
        return envelope["result"]

    def initialize(self):
        result = self.rpc("initialize", {
            "protocolVersion": self.protocol, "capabilities": {},
            "clientInfo": {"name": "remember-me-standalone-s2-smoke", "version": "1"},
        })
        self.protocol = result["protocolVersion"]
        self.rpc("notifications/initialized", notification=True)
        return result

    def tool(self, name, arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        require(not result.get("isError"), f"MCP {name} failed: {result}")
        return json.loads(result["content"][0]["text"])


def stop(process):
    if process.poll() is not None:
        require(process.returncode == 0, f"Service exited with {process.returncode}")
        return process.returncode
    process.send_signal(signal.SIGINT)
    try:
        code = process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise RuntimeError("Service did not stop gracefully within 60 seconds")
    # Uvicorn re-raises SIGINT after its shutdown handlers. Python may expose
    # that as -SIGINT or 130; completion and data checks below remain required.
    require(code in (0, -signal.SIGINT, 130), f"SIGINT stop exited with {code}")
    return code


def process_group_members(group):
    members = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == group:
                    members.append(int(entry.name))
            except (OSError, ValueError, IndexError):
                continue
    return sorted(members)


def database_checks(data, memory_id):
    with sqlite3.connect((data / "memories.db").as_uri() + "?mode=ro", uri=True) as db:
        require(db.execute("pragma integrity_check").fetchone()[0] == "ok", "SQLite integrity")
        require(not db.execute("pragma foreign_key_check").fetchall(), "SQLite foreign keys")
        profiles = db.execute("select distinct profile_id from memory_embeddings").fetchall()
        require(profiles == [(PROFILE_ID,)], f"Unexpected embedding profile: {profiles}")
    require((data / "config.jsonc").is_file(), "Persistent configuration missing")
    require(memory_id in (data / "memories.jsonl").read_text(), "Dual replica missing test memory")
    require(not (data / "models").exists(), "Service created an external model cache in data")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="New/empty directory outside the checkout")
    parser.add_argument("--require-offline", action="store_true",
                        help="Refuse unless the current OS network namespace has only loopback")
    parser.add_argument("--ready-timeout", type=float, default=300)
    args = parser.parse_args()
    artifact, work = args.artifact.resolve(), args.work.resolve()
    script = Path(__file__).resolve()
    require((artifact / "start.sh").is_file(), "Artifact start.sh is missing")
    # The verifier may also be mounted standalone at /checks/smoke.py.
    if script.parent.name == "standalone" and script.parent.parent.name == "packaging":
        checkout = script.parent.parent.parent
        require(not work.is_relative_to(checkout), "Smoke work must be outside the source checkout")
    require(not work.is_relative_to(artifact) and not artifact.is_relative_to(work),
            "Artifact and work directories must not contain each other")
    require(not work.exists() or (work.is_dir() and not any(work.iterdir())), "Work directory is not empty")
    network = network_snapshot()
    require(not args.require_offline or network["only_loopback"],
            f"Offline test requires a loopback-only OS network namespace: {network}")
    work.mkdir(parents=True, exist_ok=True)
    evidence = {"artifact_source": str(artifact), "work": str(work),
                "network": network, "offline_required": args.require_offline,
                "checks": [], "result": "failed"}
    process = None

    def passed(name, detail=True):
        evidence["checks"].append({"check": name, "detail": detail})
        print("PASS:", name, flush=True)

    try:
        runner = work / "runner"
        shutil.copytree(artifact, runner, symlinks=True)
        passed("copied artifact outside source checkout", str(runner))
        data, home = work / "data", work / "empty-home"
        home.mkdir()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        evidence["port"] = port
        # No host Python/provider/model/cache environment is inherited.
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                       "MEMORY_DEFAULT_NAMESPACE": "standalone-s2",
                       "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
                       "OPENBLAS_NUM_THREADS": "2", "TOKENIZERS_PARALLELISM": "false"}
        evidence["service_environment"] = environment
        memory_id = None
        for phase, flag in (("initial", "--data"), ("restarted", "--data-dir"),
                            ("relocated", "--data-dir")):
            if phase == "relocated":
                destination = work / "runner relocated with spaces"
                runner.rename(destination)
                runner = destination
                passed("moved complete runtime directory", str(runner))
            argv = [str(runner / "start.sh"), flag, str(data), "--port", str(port)]
            started = time.monotonic()
            with (work / f"{phase}.log").open("wb") as log:
                process = subprocess.Popen(argv, cwd=work, env=environment, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                client = Client(port)
                deadline = started + args.ready_timeout
                last_status = None
                while time.monotonic() < deadline:
                    require(process.poll() is None,
                            f"Service exited {process.returncode}; see {phase}.log")
                    try:
                        last_status = client.api("/api/model")
                        if last_status.get("ready"):
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError(f"Model never became ready: {last_status}; see {phase}.log")
                executable = Path(f"/proc/{process.pid}/exe").resolve()
                require(executable.is_relative_to(runner / "runtime/python"),
                        f"Service is not using bundled Python: {executable}")
                require(os.readlink(f"/proc/{process.pid}/ns/net") == network["namespace"],
                        "Service network namespace differs from smoke process")
                passed(f"{phase}: ready using bundled Python", {
                    "argv": argv, "cwd": os.readlink(f"/proc/{process.pid}/cwd"),
                    "executable": str(executable), "ready_seconds": time.monotonic() - started,
                    "model": last_status})
                for path in ("/admin/console", "/admin/console/static/app.css",
                             "/admin/console/static/js/main.js", "/read-me"):
                    status, content_type, body = client.request(path, accept="text/html, */*")
                    require(status == 200 and body, f"Web resource failed: {path}")
                    passed(f"{phase}: Web resource {path}", {"bytes": len(body), "type": content_type})
                config = client.api("/api/config")
                require(config["local_only"] is True, "Default local-only configuration changed")
                passed(f"{phase}: local-only config API")
                passed(f"{phase}: MCP initialize", client.initialize())
                tool_names = {tool["name"] for tool in client.rpc("tools/list")["tools"]}
                require({"stats", "add", "search_semantic", "get_details", "update", "delete"}
                        <= tool_names, f"Core MCP tools missing: {tool_names}")
                stats = client.tool("stats", {})
                if memory_id is None:
                    require(stats["total"] == 0, "Test data was not empty")
                    keywords = client.tool("list_keywords", {"mode": "semantic", "queries": ["standalone"]})
                    added = client.tool("add", {
                        "text": TEXT, "summary": "Standalone S2 offline persistence fixture.",
                        "namespace": "standalone-s2", "keyword_query_id": keywords["keyword_query_id"],
                        "metadata": {"keywords": ["standalone"], "categories": ["verification"],
                                     "anchors": ["S2StandaloneSmoke"]},
                    })
                    require(added.get("created") is True and added.get("id"), f"Memory add failed: {added}")
                    memory_id = added["id"]
                    evidence["memory_id"] = memory_id
                    updated = client.tool("update", {"memory_id": memory_id, "text": UPDATED,
                                                     "summary": "Updated standalone persistence fixture.",
                                                     "metadata": {"anchors": ["S2StandaloneSmoke"],
                                                                  "categories": ["verification"]}})
                    require(updated.get("success"), f"Memory update failed: {updated}")
                    passed("initial: MCP memory add/update with real embedding", memory_id)
                details = client.tool("get_details", {"memory_ids": [memory_id]})
                require(not details["missing"] and details["found"][0]["text"] == UPDATED,
                        f"Memory content did not persist: {details}")
                search = client.tool("search_semantic", {"query": TEXT, "namespace": "standalone-s2"})
                require(memory_id in {item["id"] for item in search["items"]}, f"Semantic search missed fixture: {search}")
                passed(f"{phase}: MCP read and semantic retrieval", memory_id)
                stopped = time.monotonic()
                service_pid = process.pid
                exit_code = stop(process)
                process = None
                require("Application shutdown complete." in (work / f"{phase}.log").read_text(),
                        "Uvicorn application shutdown completion was not logged")
                remaining = process_group_members(service_pid)
                require(not remaining, f"Related service processes remain after exit: {remaining}")
                passed(f"{phase}: SIGINT graceful stop", {
                    "exit_code": exit_code, "seconds": time.monotonic() - stopped,
                    "application_shutdown_complete": True, "remaining_processes": remaining})
            database_checks(data, memory_id)
            passed(f"{phase}: persisted SQLite, profile, config and Dual replica")
        evidence["result"] = "passed"
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if process is not None:
            try:
                stop(process)
            except Exception as exc:
                evidence["cleanup_error"] = str(exc)
        (work / "results.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(evidence, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
