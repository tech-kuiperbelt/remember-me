"""Unit checks for the Phase 3.1 CI log parsing/cache assertions."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "docker_ci", ROOT / ".github" / "scripts" / "docker_ci.py")
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def verified(count=11):
    return "\n".join(f"Verified file{i}.json: {'a' * 64}" for i in range(count))


def test_cold_log_requires_all_model_files_and_no_cached_runtime(tmp_path):
    results = {"cache": {}}
    log = write(tmp_path, "cold.log", verified() + "\n#14 [runtime-download 2/6] RUN package\n#14 DONE\n")
    CI.check_cached(log, results, expect_model_download=True)
    assert results["cache"]["verified_count"] == 11
    assert results["cache"]["cached_stages"] == []


def test_cold_log_rejects_cached_runtime_download(tmp_path):
    results = {"cache": {}}
    log = write(tmp_path, "cold.log", verified() + "\n#14 [runtime-download 2/6] RUN package\n#14 CACHED\n")
    with pytest.raises(CI.Failure, match="cached runtime-download"):
        CI.check_cached(log, results, expect_model_download=True)


def test_warm_log_accepts_cached_dependencies_without_model_download(tmp_path, monkeypatch):
    # Workflow MODE must not change the scenario this unit test exercises.
    monkeypatch.setattr(CI, "MODE", "warm")
    results = {"cache": {}}
    log = write(tmp_path, "warm.log", """
#20 [deps 5/5] RUN pip install
#20 CACHED
#16 [runtime-download 2/6] RUN package
#16 CACHED
#23 [runtime-download 3/6] COPY app/ ./app/
#23 CACHED
""")
    CI.check_cached(log, results, expect_model_download=False)
    assert "deps" in results["cache"]["cached_stages"]
    assert "runtime-download" in results["cache"]["cached_stages"]
    assert results["cache"]["model_download"] == "skipped_by_cached_downstream"


def test_source_probe_requires_an_application_layer_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(CI, "MODE", "source-probe")
    results = {"cache": {}}
    log = write(tmp_path, "source.log", """
#20 [deps 5/5] RUN pip install
#20 CACHED
#16 [runtime-download 2/6] RUN package
#16 CACHED
#23 [runtime-download 3/6] COPY app/ ./app/
#23 DONE 0.8s
#24 [runtime-download 4/6] COPY web/ ./web/
#24 DONE 0.2s
""")
    CI.check_cached(log, results, expect_model_download=False)
    assert results["cache"]["source_probe_rebuilt_steps"] == ["23", "24"]


def test_source_probe_rejects_fully_cached_app_layers(tmp_path, monkeypatch):
    monkeypatch.setattr(CI, "MODE", "source-probe")
    results = {"cache": {}}
    log = write(tmp_path, "source.log", """
#20 [deps 5/5] RUN pip install
#20 CACHED
#16 [runtime-download 2/6] RUN package
#16 CACHED
#23 [runtime-download 3/6] COPY app/ ./app/
#23 CACHED
#24 [runtime-download 4/6] COPY web/ ./web/
#24 CACHED
""")
    with pytest.raises(CI.Failure, match="did not rebuild an application layer"):
        CI.check_cached(log, results, expect_model_download=False)
