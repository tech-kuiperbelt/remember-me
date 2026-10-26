"""Opt-in ownership acceptance for the optional UID/GID 10001 initializer."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

IMAGE = 'remember-me:phase2.2-amd64'


def run(*args):
    return subprocess.run(['docker', 'run', '--rm', '--network', 'none', *args],
                          text=True, capture_output=True)


def main():
    global IMAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=IMAGE)
    IMAGE = parser.parse_args().image
    work = Path(tempfile.mkdtemp(prefix='rm-permissions-'))
    setup = r"""
import os
from pathlib import Path
r=Path('/cases')
for name in ['root-empty','user-empty','existing','wrong-owner','wrong-file','symlink','readonly','invalid']:
 p=r/name;p.mkdir();p.chmod(0o750)
 if name not in ['root-empty','wrong-owner']:os.chown(p,10001,10001)
 if name not in ['root-empty','user-empty','readonly']:
  f=p/'config.jsonc';f.write_text('// preserve\n{"debug":false}');os.chown(f,10001,10001)
  if name=='wrong-file':f.chmod(0o400);os.chown(f,0,0)
  if name=='invalid':f.write_text('{ invalid JSONC')
(r/'readonly').chmod(0o550)
s=r/'sentinel';s.write_text('outside unchanged');s.chmod(0o640)
(r/'symlink'/'outside').symlink_to('/sentinel')
"""
    result = run('--user', '0:0', '--entrypoint', 'python', '-v', str(work)+':/cases', IMAGE, '-c', setup)
    assert result.returncode == 0, result.stderr
    checks = []
    for name, expected in [('root-empty', 0), ('user-empty', 0), ('existing', 0),
                           ('wrong-owner', 1), ('wrong-file', 1), ('symlink', 1),
                           ('readonly', 1), ('invalid', 0)]:
        result = run('--read-only', '--user', '0:0', '--cap-drop', 'ALL',
                     *[v for cap in ['CHOWN','DAC_OVERRIDE','FOWNER','SETUID','SETGID'] for v in ['--cap-add',cap]],
                     '--entrypoint', 'python', '-v', str(work/name)+':/data'+(':ro' if name=='readonly' else ''), IMAGE,
                     '/opt/remember-me/docker/init_data.py')
        assert result.returncode == expected, (name, result.stdout, result.stderr)
        checks.append({'case': name, 'exit_code': result.returncode})
    verify = r"""
import os,json
from pathlib import Path
r=Path('/cases')
assert (r/'sentinel').read_text()=='outside unchanged'
assert (r/'sentinel').stat().st_uid==0
assert (r/'sentinel').stat().st_mode & 0o777 == 0o640
for n in ['root-empty','user-empty']:
 p=r/n;assert p.stat().st_uid==10001 and p.stat().st_gid==10001
 assert p.stat().st_mode & 0o777==0o750;assert not list(p.iterdir())
assert (r/'wrong-owner').stat().st_uid==0
for n in ['existing','wrong-owner','wrong-file','symlink']:
 assert (r/n/'config.jsonc').read_text()=='// preserve\n{"debug":false}'
assert (r/'invalid'/'config.jsonc').read_text()=='{ invalid JSONC'
print('ownership, contents and outside sentinel unchanged')
"""
    result = run('--user', '0:0', '--entrypoint', 'python', '-v', str(work)+':/cases:ro', IMAGE, '-c', verify)
    assert result.returncode == 0, result.stderr
    result = run('--read-only', '-v', str(work/'invalid')+':/data', IMAGE)
    assert result.returncode != 0 and 'config' in result.stderr.lower(), result
    report = {'work': str(work), 'checks': checks, 'invalid_config_startup': 'failed without overwrite'}
    (work/'results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
