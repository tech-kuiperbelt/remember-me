"""Installation guides and Compose must describe one deployment policy."""
from pathlib import Path
import re
import shlex
import subprocess

import pytest
import yaml

from tests.run_docker_installation import installation_blocks, docker_run_argv

ROOT = Path(__file__).resolve().parents[1]
READMES = ('README.md', 'README.zh-CN.md')
DOCKER_GUIDES = ('docs/installation/docker.md', 'docs/installation/docker.zh-CN.md')
SOURCE_GUIDES = ('docs/installation/source.md', 'docs/installation/source.zh-CN.md')
ALL_DOCS = READMES + DOCKER_GUIDES + SOURCE_GUIDES
IMAGE = 'ghcr.io/tech-kuiperbelt/remember-me:latest'


def test_bilingual_installation_commands_match():
    assert installation_blocks(ROOT / DOCKER_GUIDES[0]) == installation_blocks(ROOT / DOCKER_GUIDES[1])


@pytest.mark.parametrize('filename', ALL_DOCS)
def test_document_shell_blocks_are_valid_bash(filename):
    blocks = re.findall(r'```bash\n(.*?)\n```', (ROOT / filename).read_text(), flags=re.S)
    if filename in DOCKER_GUIDES + SOURCE_GUIDES:
        assert blocks
    for block in blocks:
        result = subprocess.run(['bash', '-n'], input=block, capture_output=True, text=True)
        assert result.returncode == 0, (filename, block, result.stderr)


@pytest.mark.parametrize('filename', DOCKER_GUIDES)
def test_guide_release_image_and_compose_source_match(filename):
    text = (ROOT / filename).read_text()
    commands = installation_blocks(ROOT / filename)
    assert commands['minimal'] == 'docker run --name remember-me -v "$PWD:/data" -p 127.0.0.1:1314:1314 ' + IMAGE
    assert 'https://raw.githubusercontent.com/tech-kuiperbelt/remember-me/master/compose.yaml' in text
    assert 'remember-me:phase2.2-amd64' not in text
    assert 'remember-me:phase3.2-ready-fixed' not in text
    assert 'init_data.py' not in text
    assert 'ghcr.io/tech-kuiperbelt/remember-me@sha256:...' in text
    assert 'http://127.0.0.1:5263/admin/console' in text
    assert 'http://127.0.0.1:1314/admin/console' in text


def test_compose_and_recommended_run_persistence_and_defaults():
    doc = yaml.safe_load((ROOT / 'compose.yaml').read_text())
    assert set(doc['services']) == {'remember-me'}
    app = doc['services']['remember-me']
    run = shlex.split(installation_blocks(ROOT / DOCKER_GUIDES[0])['run'])
    assert app['image'] == '${REMEMBER_ME_IMAGE:-' + IMAGE + '}'
    assert app['platform'] == 'linux/amd64'
    assert 'user' not in app and not app.get('read_only')
    assert not app.get('cap_drop') and not app.get('security_opt') and not app.get('depends_on')
    assert not {'--user', '--read-only', '--cap-drop', '--security-opt', '--platform'} & set(run)
    assert app['restart'] == run[run.index('--restart') + 1] == 'unless-stopped'
    assert app['stop_grace_period'] == run[run.index('--stop-timeout') + 1] + 's' == '120s'
    assert app['ports'] == ['127.0.0.1:${REMEMBER_ME_PORT:-1314}:1314']
    assert run[run.index('-p') + 1] == '127.0.0.1:1314:1314'
    assert [run[i + 1] for i, value in enumerate(run) if value == '-v'] == [
        '$PWD:/data', '/etc/localtime:/etc/localtime:ro']
    volumes = {item['target']: item for item in app['volumes']}
    assert set(volumes) == {'/data', '/etc/localtime'}
    assert volumes['/data'] == {'type': 'bind', 'source': '${REMEMBER_ME_DATA_DIR:-.}',
                                'target': '/data', 'bind': {'create_host_path': True}}
    assert volumes['/etc/localtime'] == {'type': 'bind', 'source': '/etc/localtime',
                                        'target': '/etc/localtime', 'read_only': True}
    assert app['command'] == ['--host', '0.0.0.0', '--port', '1314']


def test_optional_security_is_separate_from_default_commands():
    commands = installation_blocks(ROOT / DOCKER_GUIDES[0])
    minimal, run, secure = (shlex.split(commands[key]) for key in ('minimal', 'run', 'secure'))
    assert '-d' not in minimal and '--user' not in minimal
    assert minimal[minimal.index('-p') + 1] == '127.0.0.1:1314:1314'
    assert secure[secure.index('--user') + 1] == '$(id -u):$(id -g)'
    assert '--read-only' in secure
    assert secure[secure.index('--cap-drop') + 1] == 'ALL'
    assert secure[secure.index('--security-opt') + 1] == 'no-new-privileges:true'
    assert '--read-only' not in run


@pytest.mark.parametrize('mode', ['minimal', 'run', 'secure'])
def test_acceptance_adapts_hardcoded_examples_to_isolated_local_image(mode, tmp_path):
    block = installation_blocks(ROOT / DOCKER_GUIDES[0])[mode]
    data = tmp_path / 'data with spaces'
    extra = ['--mount', 'type=bind,src=/tmp/fixture,dst=/fixture,readonly']
    argv = docker_run_argv(block, image='remember-me:local-fixture', name='isolated-name',
                           data=data, port=19143, extra=extra)
    assert argv[-1] == 'remember-me:local-fixture' and IMAGE not in argv
    assert argv[argv.index('--name') + 1] == 'isolated-name'
    assert argv[argv.index('-v') + 1] == str(data) + ':/data'
    assert argv[argv.index('--pull') + 1] == 'never'
    assert argv[-3:-1] == extra
    assert argv[argv.index('-p') + 1] == '127.0.0.1:19143:1314'
    assert '127.0.0.1:1314:1314' not in argv and '127.0.0.1:5263:1314' not in argv
    if mode == 'minimal':
        assert '-d' not in argv


@pytest.mark.parametrize('port', [1314, 5263])
def test_acceptance_refuses_documented_or_production_ports(port, tmp_path):
    block = installation_blocks(ROOT / DOCKER_GUIDES[0])['run']
    with pytest.raises(AssertionError):
        docker_run_argv(block, image='remember-me:local-fixture', name='isolated-name',
                        data=tmp_path, port=port)


@pytest.mark.parametrize('filename', ALL_DOCS)
def test_public_links_exist_and_exclude_internal_operations(filename):
    for target in re.findall(r'\]\(([^\s)]+)\)', (ROOT / filename).read_text()):
        if '://' in target or target.startswith('#'):
            continue
        assert not target.startswith(('docs/operations/', 'docs/project-management/')), target
        assert (ROOT / filename).parent.joinpath(target.split('#')[0]).resolve().exists(), target


@pytest.mark.parametrize('filename', DOCKER_GUIDES)
def test_backup_restore_and_update_commands_are_present(filename):
    text = (ROOT / filename).read_text()
    assert 'sudo tar --numeric-owner -C "$REMEMBER_ME_DATA_DIR"' in text
    assert 'sudo tar --numeric-owner --same-owner -C "$REMEMBER_ME_RESTORE_DIR"' in text
    assert 'REMEMBER_ME_DATA_DIR="$PWD"' in text
    assert 'REMEMBER_ME_BACKUP_DIR="$(dirname "$REMEMBER_ME_DATA_DIR")/remember-me-backups"' in text
    assert 'REMEMBER_ME_RESTORE_DIR="$(dirname "$REMEMBER_ME_DATA_DIR")/remember-me-restored"' in text
    assert 'docker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"' in text
    assert 'docker stop --time 120 remember-me' in text
    assert "{{.State.ExitCode}}" in text
    assert '/mcp' in text and '/sse' in text


@pytest.mark.parametrize('filename', SOURCE_GUIDES)
def test_source_update_uses_safe_current_branch_commands(filename):
    text = (ROOT / filename).read_text()
    commands = re.findall(r'```bash\n(.*?)\n```', text, flags=re.S)
    command = next(block for block in commands if 'git pull --ff-only' in block)
    assert command.splitlines() == ['git pull --ff-only', 'bash bin/install.sh', 'bash bin/start.sh']
    assert 'release.jsonc' in text and ('detached' in text.lower() or 'Detached HEAD' in text)
    for line in command.splitlines():
        tokens = shlex.split(line)
        assert not set(tokens) & {'reset', 'clean', 'stash', '--force', '--hard', 'checkout'}


@pytest.mark.parametrize('filename', DOCKER_GUIDES)
def test_compose_upgrade_preserves_instance_file_and_forces_target_pull(filename):
    text = (ROOT / filename).read_text().split('<a id="upgrade-and-rollback"></a>')[1]
    commands = re.findall(r'```bash\n(.*?)\n```', text, flags=re.S)
    compose = next(shlex.split(block) for block in commands if 'docker compose' in block)
    assert compose[:6] == ['docker', 'compose', '-f', 'compose.yaml', 'up', '-d']
    assert compose[compose.index('--pull') + 1] == 'always'
    assert '--wait' in compose and compose[compose.index('--wait-timeout') + 1] == '300'
    assert '.env' in text and 'REMEMBER_ME_IMAGE' in text
    assert 'Upgrade Notes / Breaking Changes' in text
    assert not any(option in compose for option in ('--volumes', '-v', '--renew-anon-volumes'))


@pytest.mark.parametrize('readme,source,docker', [
    ('README.md', 'docs/installation/source.md', 'docs/installation/docker.md'),
    ('README.zh-CN.md', 'docs/installation/source.zh-CN.md', 'docs/installation/docker.zh-CN.md'),
])
def test_readme_navigation_links_to_guides_and_omits_install_commands(readme, source, docker):
    text = (ROOT / readme).read_text()
    assert f']({source})' in text and f']({docker})' in text
    assert 'BGE-M3' in text
    for command in ('git clone https://github.com/tech-kuiperbelt/remember-me.git',
                    'bash bin/install.sh', 'docker run -d --name remember-me',
                    'docker compose -f compose.yaml up -d',
                    'sudo tar --numeric-owner'):
        assert command not in text


@pytest.mark.parametrize('filename,headings', [
    ('docs/installation/source.md', ('## Install', '## Start', '## Stop', '## Data directory',
                                     '## Backup and restore', '## Upgrade and rollback')),
    ('docs/installation/source.zh-CN.md', ('## 安装', '## 启动', '## 停止', '## 数据目录',
                                           '## 备份与恢复', '## 升级与回滚')),
    ('docs/installation/docker.md', ('## Prepare one instance', '## Option 1: docker run',
                                     '## Option 2: Docker Compose', '## Connect, inspect and stop',
                                     '## Permissions, backups and restore', '## Manual upgrades and rollback')),
    ('docs/installation/docker.zh-CN.md', ('## 准备一个实例', '## 方式一：docker run',
                                           '## 方式二：Docker Compose', '## 连接、检查和停止',
                                           '## 权限、备份和恢复', '## 手动升级和回滚')),
])
def test_installation_guides_cover_full_lifecycle(filename, headings):
    text = (ROOT / filename).read_text()
    for heading in headings:
        assert heading in text, (filename, heading)


@pytest.mark.parametrize('filename,help_label', [
    ('docs/installation/source.md', 'Help → Getting Started'),
    ('docs/installation/source.zh-CN.md', 'Help → 开始使用'),
    ('docs/installation/docker.md', 'Help → Getting Started'),
    ('docs/installation/docker.zh-CN.md', 'Help → 开始使用'),
])
def test_installation_guides_point_to_web_help_without_duplicating_configuration(filename, help_label):
    text = (ROOT / filename).read_text()
    assert 'http://127.0.0.1:1314/admin/console' in text
    assert 'http://127.0.0.1:1314/read-me' in text
    assert help_label in text
    assert '../../README.md#connect--enable-agent-memory' not in text
    assert '../../README.zh-CN.md#连接并启用-agent-记忆' not in text
    for detail in ('codex mcp add', '"memory-server"', 'AGENTS.md'):
        assert detail not in text


@pytest.mark.parametrize('readme,heading,help_label', [
    ('README.md', '## After Installation', 'Help → Getting Started'),
    ('README.zh-CN.md', '## 安装完成后', 'Help → 开始使用'),
])
def test_readme_keeps_short_after_installation_guidance(readme, heading, help_label):
    text = (ROOT / readme).read_text()
    assert heading in text
    assert 'http://127.0.0.1:1314/read-me' in text
    assert 'http://127.0.0.1:1314/admin/console' in text
    assert help_label in text
    for detail in ('Connect & Enable Agent Memory', '连接并启用 Agent 记忆',
                   'codex mcp add', '"memory-server"', 'AGENTS.md', 'CLAUDE.md',
                   'http://127.0.0.1:1314/mcp', 'http://127.0.0.1:1314/sse'):
        assert detail not in text


def test_help_uses_canonical_bilingual_readme_sections():
    html = (ROOT / 'web/index.html').read_text()
    assert 'id="help-updates"' in html
    for anchor in ('source-updates', 'docker-updates'):
        assert f'data-update-readme="{anchor}"' in html
        for filename in READMES:
            assert f'<a id="{anchor}"></a>' in (ROOT / filename).read_text()


def _anchors(path):
    text = path.read_text()
    found = set(re.findall(r'<a id="([^"]+)"', text))
    body = re.sub(r'```.*?```', '', text, flags=re.S)
    for line in body.splitlines():
        heading = re.match(r'#{1,6}\s+(.*)', line)
        if heading:
            slug = re.sub(r'<[^>]+>', '', heading.group(1)).strip().lower()
            slug = re.sub(r'[^\w\s-]', '', slug, flags=re.UNICODE)
            found.add(slug.replace(' ', '-'))
    return found


@pytest.mark.parametrize('filename', ALL_DOCS)
def test_cross_document_links_and_anchors_resolve(filename):
    path = ROOT / filename
    for target in re.findall(r'\]\(([^)\s]+)\)', path.read_text()):
        if '://' in target:
            continue
        relative, _, fragment = target.partition('#')
        resolved = (path.parent / relative).resolve() if relative else path.resolve()
        assert resolved.exists(), (filename, target)
        if fragment:
            assert fragment in _anchors(resolved), (filename, target, sorted(_anchors(resolved)))


def test_bilingual_source_canonical_commands_match():
    def blocks(path):
        return re.findall(r'```bash\n(.*?)\n```', (ROOT / path).read_text(), flags=re.S)
    english, chinese = blocks(SOURCE_GUIDES[0]), blocks(SOURCE_GUIDES[1])
    for marker in ('git clone https://github.com/tech-kuiperbelt/remember-me.git',
                   'bash bin/start.sh', 'git pull --ff-only',
                   'remember-me-source-backup.tar',
                   'restored-data'):
        en = next(block for block in english if marker in block)
        zh = next(block for block in chinese if marker in block)
        assert en == zh, marker
