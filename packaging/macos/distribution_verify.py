"""Inspect accepted DMG without signing, unquarantining or changing system policy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

EXPECTED = 'dced0eec97d12287f8767c37bdd757ae9d5ec1f91c83bef117f33c7d15ea222c'
MAGIC = {bytes.fromhex(x) for x in ('cffaedfe','feedfacf','cafebabe','bebafeca','cafebabf','bfbafeca')}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dmg', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir()
    evidence = work / 'evidence'
    evidence.mkdir()
    results = {'checks': [], 'unverified': ['Real browser download quarantine provenance', 'Finder drag and drop', 'Real-user first-open dialog and Open Anyway approval', 'Gatekeeper on another physical Mac']}
    def run(name, argv, timeout=60):
        try:
            value = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=timeout)
            record = {'name':name,'returncode':value.returncode,'stdout':value.stdout,'stderr':value.stderr}
        except subprocess.TimeoutExpired:
            record = {'name':name,'timeout':timeout}
        (evidence / (name + '.json')).write_text(json.dumps(record,indent=2))
        return record
    def record(name, passed, detail=None):
        results['checks'].append({'name':name,'passed':passed,'detail':detail})
    dmg = args.dmg.resolve()
    h = hashlib.sha256()
    with dmg.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):
            h.update(chunk)
    record('DMG SHA256',h.hexdigest()==EXPECTED,h.hexdigest())
    record('Release asset below 2 GiB',dmg.stat().st_size < 2**31,dmg.stat().st_size)
    run('system', ['/usr/bin/sw_vers'])
    run('gatekeeper-status', ['/usr/sbin/spctl','--status'])
    run('dmg-quarantine', ['/usr/bin/xattr','-p','com.apple.quarantine',dmg])
    record('DMG container integrity',run('hdiutil-verify',['/usr/bin/hdiutil','verify',dmg])['returncode']==0)
    mount = work / 'mount'
    mount.mkdir()
    attached = False
    try:
        attach=run('mount',['/usr/bin/hdiutil','attach','-readonly','-nobrowse','-mountpoint',mount,dmg])
        attached=attach.get('returncode')==0
        if not attached:
            raise RuntimeError('DMG mount failed')
        # User-writable Applications is supported without privilege elevation.
        applications=work/'Applications'
        applications.mkdir()
        app=applications/'Remember Me.app'
        record('Copy app to user-writable Applications',run('copy',['/usr/bin/ditto',mount/'Remember Me.app',app])['returncode']==0)
        record('Bundle strict deep signature',run('bundle-signature',['/usr/bin/codesign','--verify','--deep','--strict','--verbose=4',app])['returncode']==0)
        run('bundle-identity',['/usr/bin/codesign','-d','--verbose=4',app])
        nested=[]
        for path in sorted(app.rglob('*')):
            if path.is_symlink() or not path.is_file():continue
            with path.open('rb') as stream:magic=stream.read(4)
            if magic not in MAGIC:continue
            value=subprocess.run(['/usr/bin/codesign','--verify','--strict','--verbose=2',str(path)],capture_output=True,text=True)
            nested.append({'path':str(path.relative_to(app)),'returncode':value.returncode,'detail':value.stderr})
        (evidence/'nested-signatures.json').write_text(json.dumps(nested,indent=2))
        record('Every nested Mach-O signature',all(v['returncode']==0 for v in nested),{'count':len(nested),'failed':[v['path'] for v in nested if v['returncode']]})
        run('app-quarantine',['/usr/bin/xattr','-p','com.apple.quarantine',app])
        run('app-policy',['/usr/sbin/spctl','--assess','--type','execute','--verbose=4',app])
        run('dmg-policy',['/usr/sbin/spctl','--assess','--type','open','--context','context:primary-signature','--verbose=4',dmg])
        # No fabricated quarantine attribute: gh downloads are not browser downloads.
        # Only request a normal LaunchServices first open; no approval or bypass.
        run('launchservices-first-open',['/usr/bin/open','-n',app,'--args','--data-dir',work/'data','--port','19987'],timeout=30)
        time.sleep(15)
        run('first-open-processes',['/bin/ps','-axo','pid=,ppid=,command='])
        run('first-open-listener',['/usr/sbin/lsof','-nP','-iTCP:19987','-sTCP:LISTEN'])
        # Normal application Quit only; existing Step 2 proves cleanup details.
        run('quit',['/usr/bin/osascript','-e','tell application id "org.remember-me.offline-beta" to quit'],timeout=15)
        run('gatekeeper-status-after',['/usr/sbin/spctl','--status'])
    finally:
        if attached:run('detach',['/usr/bin/hdiutil','detach',mount])
        results['status']='completed; policy results are observations, not approval'
        (evidence/'results.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results,indent=2))

if __name__=='__main__':main()
