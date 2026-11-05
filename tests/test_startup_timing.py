"""Diagnostic-only startup tracing, including early and failed startup paths."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fresh():
    spec = importlib.util.spec_from_file_location('isolated_startup_timing', ROOT/'app/startup_timing.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_does_not_open_trace_and_tracing_is_opt_in(monkeypatch,tmp_path):
    path=tmp_path/'trace.jsonl'
    monkeypatch.setenv('REMEMBER_ME_STARTUP_LOG',str(path))
    timing=fresh()
    assert not path.exists()
    monkeypatch.delenv('REMEMBER_ME_STARTUP_LOG')
    timing.mark('unused')
    assert not path.exists()


def test_elapsed_duration_and_cmd_gap_across_midnight(monkeypatch,tmp_path):
    timing=fresh();path=tmp_path/'trace.jsonl'
    monkeypatch.setenv('REMEMBER_ME_STARTUP_LOG',str(path))
    monkeypatch.setattr(timing.time,'perf_counter',lambda:100.0)
    monkeypatch.setattr(timing.time,'time',lambda:datetime(2026,11,5,0,0,0,125000).timestamp())
    timing.begin(100.0,'23:59:59.90')
    timing.mark('python_entry',at=100.0)
    timing.mark('dependencies_done',at=102.5,since=100.2)
    events=[json.loads(s) for s in path.read_text().splitlines()]
    assert events[0]['elapsed_ms']==0
    assert abs(events[0]['cmd_to_python_wall_ms']-225)<0.1
    assert events[1]['elapsed_ms']==2500 and events[1]['duration_ms']==2300
    assert events[0]['run_id']==events[1]['run_id']
    assert events[1]['since_cmd_wall_ms']==2725


def test_unfamiliar_shell_time_preserved_and_write_failure_nonfatal(monkeypatch,tmp_path,caplog):
    timing=fresh();path=tmp_path/'trace.jsonl'
    monkeypatch.setenv('REMEMBER_ME_STARTUP_LOG',str(path))
    origin=timing.time.perf_counter();timing.begin(origin,'unknown locale')
    timing.mark('python_entry')
    event=json.loads(path.read_text())
    assert event['cmd_time']=='unknown locale' and 'cmd_to_python_wall_ms' not in event
    monkeypatch.setenv('REMEMBER_ME_STARTUP_LOG',str(tmp_path/'missing'/'trace.jsonl'))
    timing.mark('must_not_break_startup')
    assert 'Could not write startup timing' in caplog.text


def test_failed_provider_retains_begin_without_false_ready(monkeypatch,tmp_path):
    from tests.test_runtime import runtime
    path=tmp_path/'trace.jsonl'
    monkeypatch.setenv('REMEMBER_ME_STARTUP_LOG',str(path))
    rt=runtime(tmp_path)
    def fail(**kwargs):raise RuntimeError('injected provider failure')
    rt.provider_factory=fail
    import pytest
    with pytest.raises(RuntimeError,match='injected provider failure'):rt.start()
    stages=[json.loads(s)['stage'] for s in path.read_text().splitlines()]
    assert 'provider_prepare_begin' in stages
    assert 'provider_prepare_done' not in stages and 'runtime_ready' not in stages
