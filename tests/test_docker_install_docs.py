"""README installation commands and Compose must describe one deployment policy."""
from pathlib import Path
import re
import shlex
import subprocess

import pytest
import yaml

from tests.run_docker_installation import installation_blocks

ROOT = Path(__file__).resolve().parents[1]
READMES = ('README.md', 'README.zh-CN.md')
IMAGE = 'ghcr.io/tech-kuiperbelt/remember-me:1.0.0'


def test_bilingual_installation_commands_match():
    assert installation_blocks(ROOT / READMES[0]) == installation_blocks(ROOT / READMES[1])


@pytest.mark.parametrize('filename', READMES)
def test_readme_shell_blocks_are_valid_bash(filename):
    blocks = re.findall(r'```bash\n(.*?)\n```', (ROOT / filename).read_text(), flags=re.S)
    assert blocks
    for block in blocks:
        result = subprocess.run(['bash', '-n'], input=block, capture_output=True, text=True)
        assert result.returncode == 0, (filename, block, result.stderr)


@pytest.mark.parametrize('filename', READMES)
def test_readme_release_image_and_compose_source_match(filename):
    text = (ROOT / filename).read_text()
    assert 'export REMEMBER_ME_IMAGE=' + IMAGE in text
    assert 'https://raw.githubusercontent.com/tech-kuiperbelt/remember-me/v1.0.0/compose.yaml' in text
    assert 'remember-me:phase2.2-amd64' not in text
    assert 'REMEMBER_ME_IMAGE=remember-me:phase3.2-ready-fixed' in text
    assert 'REMEMBER_ME_DATA_DIR="$PWD/data"' in text
    assert 'REMEMBER_ME_PORT=1314' in text
    assert '1.0.0' in text and 'v1.0.0' in text
    assert '10001:10001' in text and '0750' in text


def test_compose_and_run_security_and_persistence_match():
    doc = yaml.safe_load((ROOT / 'compose.yaml').read_text())
    app, init = (doc['services'][name] for name in ('remember-me', 'data-init'))
    run = shlex.split(installation_blocks(ROOT / 'README.md')['run'].replace('\\\n', ''))
    assert app['image'] == init['image'] == '${REMEMBER_ME_IMAGE:-' + IMAGE + '}'
    assert app['platform'] == init['platform'] == run[run.index('--platform') + 1] == 'linux/amd64'
    assert app['read_only'] and init['read_only'] and '--read-only' in run
    assert app['user'] == run[run.index('--user') + 1] == '10001:10001'
    assert app['cap_drop'] == [run[run.index('--cap-drop') + 1]] == ['ALL']
    assert app['security_opt'] == [run[run.index('--security-opt') + 1]] == ['no-new-privileges:true']
    assert app['restart'] == run[run.index('--restart') + 1] == 'unless-stopped'
    assert app['stop_grace_period'] == run[run.index('--stop-timeout') + 1] + 's' == '120s'
    assert app['ports'] == ['127.0.0.1:${REMEMBER_ME_PORT:-1314}:1314']
    assert run[run.index('-p') + 1] == '127.0.0.1:${REMEMBER_ME_PORT}:1314'
    assert run.count('--mount') == 1
    assert run[run.index('--mount') + 1] == 'type=bind,src=$REMEMBER_ME_DATA_DIR,dst=/data'
    assert app['volumes'] == init['volumes'] and len(app['volumes']) == 1
    assert app['volumes'][0] == {'type': 'bind', 'source': '${REMEMBER_ME_DATA_DIR:-./data}',
                                'target': '/data', 'bind': {'create_host_path': True}}
    assert app['depends_on'] == {'data-init': {'condition': 'service_completed_successfully'}}
    assert app['command'] == ['--host', '0.0.0.0', '--port', '1314']


def test_run_initializer_reuses_compose_helper_and_capabilities():
    doc = yaml.safe_load((ROOT / 'compose.yaml').read_text())['services']['data-init']
    run = shlex.split(installation_blocks(ROOT / 'README.md')['initialize'].replace('\\\n', ''))
    assert run[run.index('--entrypoint') + 1] == doc['entrypoint'][0] == 'python'
    assert run[-1] == doc['entrypoint'][1] == '/opt/remember-me/docker/init_data.py'
    assert run[run.index('--user') + 1] == doc['user'] == '0:0'
    assert run[run.index('--network') + 1] == doc['network_mode'] == 'none'
    assert '--read-only' in run and '--rm' in run
    assert run[run.index('--cap-drop') + 1] == 'ALL'
    assert [run[i+1] for i,v in enumerate(run) if v == '--cap-add'] == doc['cap_add']
    assert '--privileged' not in run


@pytest.mark.parametrize('filename', READMES)
def test_public_readme_links_exist_and_exclude_internal_operations(filename):
    for target in re.findall(r'\]\(([^\s)]+)\)', (ROOT / filename).read_text()):
        if '://' in target or target.startswith('#'):
            continue
        assert not target.startswith(('docs/operations/', 'docs/project-management/')), target
        assert (ROOT / target.split('#')[0]).exists(), target


@pytest.mark.parametrize('filename', READMES)
def test_backup_restore_and_update_commands_are_present(filename):
    text = (ROOT / filename).read_text()
    assert 'sudo tar --numeric-owner -C "$REMEMBER_ME_DATA_DIR"' in text
    assert 'sudo tar --numeric-owner --same-owner -C "$PWD/restored-data"' in text
    assert 'docker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"' in text
    assert 'docker stop --time 120 remember-me' in text
    assert "{{.State.ExitCode}}" in text
    assert '/mcp' in text and '/sse' in text


@pytest.mark.parametrize('filename', READMES)
def test_source_update_uses_safe_current_branch_commands(filename):
    text = (ROOT / filename).read_text().split('<a id="source-updates"></a>')[1].split('<a id="docker-updates"></a>')[0]
    command = re.findall(r'```bash\n(.*?)\n```', text, flags=re.S)[0]
    assert command.splitlines() == ['git pull --ff-only', 'bash bin/install.sh', 'bash bin/start.sh']
    assert 'release.jsonc' in text and ('detached' in text.lower() or 'Detached HEAD' in text)
    for line in command.splitlines():
        tokens = shlex.split(line)
        assert not set(tokens) & {'reset', 'clean', 'stash', '--force', '--hard', 'checkout'}


@pytest.mark.parametrize('filename', READMES)
def test_compose_upgrade_preserves_instance_file_and_forces_target_pull(filename):
    text = (ROOT / filename).read_text().split('<a id="docker-updates"></a>')[1]
    commands = re.findall(r'```bash\n(.*?)\n```', text, flags=re.S)
    compose = next(shlex.split(block) for block in commands if 'docker compose' in block)
    assert compose[:6] == ['docker', 'compose', '-f', 'compose.yaml', 'up', '-d']
    assert compose[compose.index('--pull') + 1] == 'always'
    assert '--wait' in compose and compose[compose.index('--wait-timeout') + 1] == '300'
    assert '.env' in text and 'REMEMBER_ME_IMAGE' in text
    assert 'Upgrade Notes / Breaking Changes' in text
    assert not any(option in compose for option in ('--volumes', '-v', '--renew-anon-volumes'))


def test_help_uses_canonical_bilingual_readme_sections():
    html = (ROOT / 'web/index.html').read_text()
    assert 'id="help-updates"' in html
    for anchor in ('source-updates', 'docker-updates'):
        assert f'data-update-readme="{anchor}"' in html
        for filename in READMES:
            assert f'<a id="{anchor}"></a>' in (ROOT / filename).read_text()
