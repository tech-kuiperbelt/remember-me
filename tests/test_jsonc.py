"""Comment-aware configuration parsing, editing and legacy adoption."""
import json

import pytest
from starlette.testclient import TestClient

from app import jsonc
from app.configuration import Configuration
from app.config_migrate import migrate
from app.server import create_http_app
from tests.test_configuration import legacy_layout
from tests.test_model_bootstrap import make_runtime, preserve_process_environment
from tests.conftest import FakeProvider


def test_comments_trailing_commas_and_strings_are_distinguished():
    text = r'''
// header
{
  "url": "http://localhost:7890", // comment
  "literal": "/* not a comment */ // quoted \" text",
  /* multiline
     comment */
  "nested": {"enabled": true,},
  "array": [1, 2,],
}
'''
    result = jsonc.loads(text)
    assert result['url'] == 'http://localhost:7890'
    assert result['literal'] == '/* not a comment */ // quoted " text'
    assert result['nested'] == {'enabled': True}
    assert result['array'] == [1, 2]


@pytest.mark.parametrize('text', [
    '{/* unfinished', '{"a": 1, "a": 2}', '{,}', '[,]', '{"a":,}',
    '{"a": 1,,}', '{a: 1}', "{'a': 1}", '{"a": "unfinished}',
])
def test_invalid_jsonc_is_not_accepted(text):
    with pytest.raises(ValueError):
        jsonc.loads(text)


@pytest.mark.parametrize('text', [
    '{}', '{ /* empty */ }', '{"port":1314}', '{"port":1314,}',
    '{"port":1314 // note\n}', '{"port":1314, // note\n}',
    '{\n  // port note\n  "port":1314\n}',
    '{"proxy":{}}', '{"proxy":{"enabled":false // note\n}}',
])
def test_missing_fields_can_be_added_without_losing_comments(text):
    defaults = Configuration.load(environment={}).as_dict()
    updated = jsonc.update(text, defaults)
    assert jsonc.loads(updated) == defaults
    for comment in ('/* empty */', '// note', '// port note'):
        if comment in text:
            assert comment in updated
    assert jsonc.update(updated, defaults) == updated


def test_scalar_updates_retain_inline_comments_spacing_and_line_endings():
    original = '// config\r\n{\r\n  "proxy": {\r\n    "enabled": false, // flag\r\n' \
               '    "url": "http://localhost:7890", /* address */\r\n  },\r\n}\r\n'
    updated = jsonc.update(original, {'proxy': {'enabled': True, 'url': 'https://localhost:8443'}})
    expected = original.replace('false', 'true').replace('http://localhost:7890', 'https://localhost:8443')
    assert updated == expected


def test_legacy_json_read_is_pure_startup_adopts_and_jsonc_takes_precedence(tmp_path):
    legacy = tmp_path / 'config.json'
    original = '{"port": 2345, "language": "en"}\n'
    legacy.write_text(original)
    config = Configuration.load(tmp_path, environment={})
    assert config.port == 2345 and config.path.name == 'config.jsonc'
    assert not config.path.exists()
    Configuration.load(tmp_path, create=True, environment={})
    assert config.path.read_text() == original
    assert legacy.read_text() == original
    config.path.write_text('// preferred\n{"port": 3456}')
    legacy.write_text('invalid legacy is ignored')
    assert Configuration.load(tmp_path, environment={}).port == 3456


def test_malformed_legacy_is_not_converted_or_overwritten(tmp_path):
    legacy = tmp_path / 'config.json'
    legacy.write_text('{"port":')
    with pytest.raises(ValueError):
        Configuration.load(tmp_path, create=True, environment={})
    assert not (tmp_path / 'config.jsonc').exists()
    assert legacy.read_text() == '{"port":'


def test_defaults_create_documented_jsonc_and_save_preserves_it(tmp_path):
    config = Configuration.load(tmp_path, create=True, environment={})
    original = config.path.read_text()
    assert '// HTTP 端口' in original and '// 代理关闭' in original
    config.save()
    assert config.path.read_text() == original


def test_admin_proxy_save_preserves_user_comments(tmp_path, preserve_process_environment):
    rt = make_runtime(tmp_path, lambda **kwargs: FakeProvider())
    path = tmp_path / 'config.jsonc'
    text = '''// operator note
{
  "port": 1314, // keep port
  "proxy": {
    "enabled": false, /* keep flag */
    "url": "http://127.0.0.1:7890", // keep URL note
  },
}
'''
    path.write_text(text)
    with TestClient(create_http_app(rt)) as client:
        assert client.get('/api/maintenance').json()['state'] == 'normal'
        result = client.post('/api/config', json={'port': 2314, 'language': 'en',
                            'auto_download_model': False, 'proxy': {'enabled': True}})
        assert result.status_code == 200
        saved = path.read_text()
        for comment in ('// operator note', '// keep port', '/* keep flag */', '// keep URL note'):
            assert comment in saved
        values = jsonc.loads(saved)
        assert values['proxy'] == {'enabled': True, 'url': 'http://127.0.0.1:7890'}
        assert values['auto_download_model'] is False
        assert values['port'] == 2314 and values['language'] == 'en'
        assert rt.settings.PORT == 1314 and rt.settings.LANGUAGE == 'zh-CN'
        assert client.get('/api/maintenance').json()['state'] == 'normal'
        assert client.get('/api/stats').status_code == 200
        assert client.post('/api/memories', json={'text': 'configuration save did not pause writes'}).status_code == 201
        before = path.read_text()
        assert client.post('/api/config', json={'port': True}).status_code == 400
        assert client.post('/api/config', json={'language': 'fr'}).status_code == 400
        assert path.read_text() == before
        assert client.post('/api/maintenance/enter').status_code == 200
        rt.maintenance.start_action('sync')
        try:
            assert client.post('/api/config', json={'language': 'zh-CN'}).status_code == 200
            assert rt.maintenance.snapshot()['action'] == 'sync'
        finally:
            rt.maintenance.finish_action()


def test_data_migration_preserves_jsonc_comments(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    original = '// migration note\n' + json.dumps(Configuration.load(source, environment={}).as_dict())
    (source / 'config.jsonc').write_text(original)
    target = tmp_path / 'new-data'
    migrate(target, source, environment=env, project_root=project)
    assert (target / 'config.jsonc').read_text() == original
    assert (source / 'config.jsonc').read_text() == original


def test_recovery_of_old_migration_journal_still_restores_json_config(tmp_path):
    project, source, env = legacy_layout(tmp_path)
    target = tmp_path / 'target'
    target.mkdir()
    staging = target / '.migration-old'
    staging.mkdir()
    original = '{"port":2314}'
    (target / 'config.json').write_text('{"port":3456}')
    # An older version's journal has no config_file field.
    state = {'project_root': str(project), 'old_env': (project / '.env').read_text(),
             'old_config': original, 'databases': [], 'staging': str(staging),
             'phase': 'copying', 'published': []}
    (target / '.config-migration.json').write_text(json.dumps(state))
    assert migrate(target, source, project_root=project, environment=env)['recovered']
    assert (target / 'config.json').read_text() == original
    assert not (target / 'config.jsonc').exists()
    assert Configuration.load(target, environment={}).port == 2314
