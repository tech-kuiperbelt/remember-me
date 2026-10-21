"""Opt-in REAL CLI sessions against an isolated Docker deployment.

Reuses existing client authentication; never prints or edits credentials/configs.
No SDK/HTTP probe is counted as a real client result. Logs stay in /tmp.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client', choices=['codex', 'opencode'])
    parser.add_argument('--port', type=int, default=18142)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='rm-real-' + args.client + '-'))
    namespace = 'docker-' + args.client + '-' + str(int(time.time()))
    prompt = (
        'This is an authorized integration acceptance test of Remember Me in an isolated Docker instance. '
        'Use ONLY the rm_phase22 MCP server, without shell or other tools. Perform actual calls in order: '
        '1 stats; 2 add a memory with text "Docker client persistence acceptance ORIGINAL", '
        f'namespace "{namespace}", summary "CLI acceptance", metadata categories ["verification"], '
        'keywords ["persistence"], anchors ["DockerClientAcceptance"], confirm_codes ["namespace_not_found"]; '
        '3 search_semantic query "Docker persistence acceptance" in that namespace; '
        '4 update the returned memory ID with text "Docker client persistence acceptance UPDATED", '
        'metadata categories ["verification"], keywords ["persistence"], anchors ["DockerClientAcceptance"]; '
        '5 get_details for that ID and verify UPDATED. Do not simulate any tool call. '
        'Do not delete the memory. Finish with the ID, namespace, and success/failure of each call.'
    )
    env = dict(os.environ)
    if args.client == 'codex':
        command = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral', '--json',
                   '--skip-git-repo-check', '-C', str(work), '--sandbox', 'read-only', '-m', 'gpt-6.1-sol',
                   '-c', 'approval_policy="never"',
                   '-c', f'mcp_servers.rm_phase22.url="http://127.0.0.1:{args.port}/mcp"',
                   '-c', 'mcp_servers.rm_phase22.default_tools_approval_mode="approve"',
                   '-c', 'mcp_servers.rm_phase22.enabled_tools=["stats","add","search_semantic","update","get_details"]', prompt]
    else:
        # OpenCode merges configs. Disable every global MCP except the test server.
        import sys
        sys.path.insert(0, str(ROOT))
        from app.config.jsonc import loads
        names = set()
        for name in ['opencode.json', 'opencode.jsonc']:
            path = Path.home() / '.config/opencode' / name
            if path.exists():
                names.update(loads(path.read_text()).get('mcp', {}))
        config = {'mcp': {name: {'enabled': False} for name in names},
                  'permission': {'*': 'deny', 'rm_phase22_*': 'allow'}}
        config['mcp']['rm_phase22'] = {'type': 'remote', 'url': f'http://127.0.0.1:{args.port}/sse', 'enabled': True}
        env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
        command = ['opencode', 'run', '--pure', '--dir', str(work), '--format', 'json',
                   '-m', 'deepseek/deepseek-flash', prompt]
    print('Client:', args.client, 'namespace:', namespace, 'evidence:', work, flush=True)
    started = time.monotonic()
    with (work/'events.jsonl').open('w') as out, (work/'stderr.log').open('w') as err:
        result = subprocess.run(command, env=env, stdout=out, stderr=err, timeout=300)
    events = []
    for line in (work/'events.jsonl').read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    calls = []

    def walk(value):
        if isinstance(value, dict):
            if value.get('type') == 'mcp_tool_call':
                calls.append({'server': value.get('server'), 'tool': value.get('tool'),
                              'status': value.get('status'), 'error': value.get('error')})
            if value.get('type') == 'tool' and str(value.get('tool', '')).startswith('rm_phase22_'):
                calls.append({'tool': value['tool'], 'status': value.get('state', {}).get('status')})
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(events)
    tools = {call['tool'].removeprefix('rm_phase22_') for call in calls if call['status'] == 'completed'}
    report = {'client': args.client, 'exit_code': result.returncode, 'namespace': namespace,
              'seconds': time.monotonic()-started, 'calls': calls,
              'passed': result.returncode == 0 and {'stats','add','search_semantic','update','get_details'} <= tools}
    if report['passed']:
        # CLI exit status or a completed protocol call alone is insufficient:
        # verify that the real tool session changed the actual persisted record.
        with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/memories?namespace={namespace}', timeout=30) as response:
            records = json.load(response)
        report['memory_ids'] = [record['id'] for record in records]
        report['persisted_updated'] = len(records) == 1 and 'UPDATED' in records[0]['text']
        report['passed'] = report['persisted_updated']
    (work/'results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
