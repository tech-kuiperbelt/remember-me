"""Convert the prepared POC tree to one Beta runtime without copying model weights."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--storage', required=True, choices=['sqlite','dual'])
    p.add_argument('--seal', action='store_true', help='Seal Setup input only after a successful business smoke')
    args = p.parse_args()
    work = args.work.resolve()
    bundle = work / 'bundle'
    if args.storage != 'dual':
        raise RuntimeError('Only the approved dual Beta policy is enabled')
    if args.seal:
        smoke = json.loads((work / 'evidence/business-smoke.json').read_text(encoding='utf-8'))
        if smoke['status'] != 'passed':
            raise RuntimeError('Business smoke must pass before sealing Setup input')
        shutil.copy2(work / 'evidence/business-smoke.json', bundle / 'build-evidence/business-smoke.json')
        print(f'Setup input ready: {bundle}; no public Portable ZIP is produced')
        return
    if sys.platform != 'win32':
        raise RuntimeError('Native preparation requires Windows')
    manifest = json.loads((bundle / 'models/manifest.json').read_text(encoding='utf-8'))
    hub = bundle / 'models/sentence-transformers/models--BAAI--bge-m3'
    destination = hub / 'snapshots' / manifest['revision']
    destination.parent.mkdir(parents=True)
    shutil.move(bundle / 'models' / manifest['revision'], destination)
    (hub / 'refs').mkdir()
    (hub / 'refs/main').write_text(manifest['revision'], encoding='utf-8')
    for name in ('start.cmd','beta_start.py','tray_launcher.py','windows_tray.py','windows_autostart.py','WINDOWS-TESTING.md'):
        shutil.copy2(ROOT / 'packaging/windows-beta' / name, bundle / name)
    policy = {'status':'Beta candidate for manual Windows 11 testing', 'storage':args.storage,
              'data_directory':'%LOCALAPPDATA%/remember-me-beta', 'delivery':'Setup with tray launcher',
              'known_limitations':['Strict S1-A Defender audit remains unresolved; original evidence retained',
                                   'Approved Windows Beta policy: file fsync + atomic replace; no directory fsync/power-loss parity; local NTFS only',
                                   'Hosted Windows Server success is not clean Windows 11 acceptance']}
    (bundle / 'beta-policy.json').write_text(json.dumps(policy,indent=2), encoding='utf-8')
    evidence = bundle / 'build-evidence'
    evidence.mkdir()
    for name in ('build-manifest.json','runtime-sha256.json','wheel-sha256.json','resolve.json',
                 'windows-candidate.lock','windows-offline-candidate.lock','pip-check.txt'):
        shutil.copy2(work / 'evidence' / name, evidence / name)
    inputs = [*Path(__file__).parent.glob('*.*')]
    beta_manifest = {'status':'Beta candidate; strict S1-A audit not claimed passed',
                     'source_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
                     'model_revision':manifest['revision'], 'storage':args.storage,
                     'input_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                     'model_layout':'HF snapshots with real files; no duplicated blobs or external caches'}
    (bundle / 'beta-build.json').write_text(json.dumps(beta_manifest,indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
