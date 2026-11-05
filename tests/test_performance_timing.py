"""Opt-in diagnostics must not alter writes, failures or recorded user content."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from app import performance_timing as timing


def test_disabled_creates_no_resources(tmp_path, monkeypatch):
    monkeypatch.delenv('REMEMBER_ME_PERFORMANCE_LOG', raising=False)
    assert timing.operation_id() is None
    with timing.phase('unused'):
        pass
    assert not list(tmp_path.iterdir())


def test_failure_is_preserved_and_phase_duration_written(tmp_path, monkeypatch):
    path = tmp_path/'performance.jsonl'
    monkeypatch.setenv('REMEMBER_ME_PERFORMANCE_LOG', str(path))
    ticks=iter([1.0,1.25])
    monkeypatch.setattr(timing.time,'perf_counter',lambda: next(ticks))
    with pytest.raises(ValueError,match='original failure'):
        with timing.phase('embedding',operation_id='test'):
            raise ValueError('original failure')
    value=json.loads(path.read_text())
    assert value['status']=='failed' and value['duration_ms']==250
    assert 'original failure' not in path.read_text()


def test_add_timing_preserves_real_storage_and_logs_only_lengths(tmp_path, monkeypatch):
    from tests.test_runtime import runtime
    path=tmp_path/'performance.jsonl'
    monkeypatch.setenv('REMEMBER_ME_PERFORMANCE_LOG',str(path))
    rt=runtime(tmp_path)
    rt.start()
    try:
        result=rt.service.memory_add('PRIVATE MEMORY TEXT')
        assert rt.service.memory_get(result['id'])['text']=='PRIVATE MEMORY TEXT'
        events=[json.loads(line) for line in path.read_text().splitlines()]
        assert [event['stage'] for event in events]==['embedding','index_embeddings','database','memory_add']
        assert len({event['operation_id'] for event in events})==1
        assert all(event['status']=='passed' for event in events)
        assert 'PRIVATE MEMORY TEXT' not in path.read_text()
        assert result['id'] not in path.read_text()
        assert events[-1]['text_chars']==19
    finally:
        rt.close()


def test_log_failure_does_not_prevent_write(tmp_path, monkeypatch):
    from tests.test_runtime import runtime
    monkeypatch.setenv('REMEMBER_ME_PERFORMANCE_LOG',str(tmp_path/'missing/performance.jsonl'))
    rt=runtime(tmp_path)
    rt.start()
    try:
        assert rt.service.memory_add('still saved')['created']
    finally:
        rt.close()
