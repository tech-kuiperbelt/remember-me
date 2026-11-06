"""Real shell entrypoint chain and actual Launcher environment cleaning.

Uses an explicit fake bundled interpreter; no model, GUI or user data accessed.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from app.release_info import identify_install_method

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def payload(tmp_path):
    appdir = tmp_path/'中文 AppImage with spaces'
    root = appdir/'payload'
    python = root/'runtime/python/bin/python3'
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nprintf "%s\\n" "${REMEMBER_ME_INSTALL_METHOD-UNKNOWN}"\n')
    python.chmod(0o755)
    shutil.copy2(ROOT/'packaging/standalone/start.sh',root/'start.sh')
    shutil.copy2(ROOT/'packaging/appimage/AppRun',appdir/'AppRun')
    (root/'start.sh').chmod(0o755)
    (appdir/'AppRun').chmod(0o755)
    return appdir,root


def test_real_apprun_to_clean_launcher_to_service_declares_appimage(payload):
    appdir,root = payload
    # Exercise the exact existing environment cleaner between the real scripts.
    code = ('import os,subprocess,sys;sys.path.insert(0,sys.argv[1]);'
            'from launcher.controller import clean_environment;'
            'raise SystemExit(subprocess.call([sys.argv[2]],env=clean_environment()))')
    import shlex
    command = [sys.executable,'-I','-c',code,str(ROOT),str(root/'start.sh')]
    (root/'launch.sh').write_text('#!/bin/sh\nexec '+shlex.join(command)+'\n')
    (root/'launch.sh').chmod(0o755)
    env = dict(os.environ,APPDIR=str(appdir),APPIMAGE=str(appdir.parent/'Remember Me.AppImage'),
               REMEMBER_ME_INSTALL_METHOD='DOCKER',MEMORY_DEPLOYMENT_MODE='docker')
    proc = subprocess.run([str(appdir/'AppRun')],env=env,capture_output=True,text=True,check=True)
    assert proc.stdout.strip()=='APPIMAGE',proc.stderr
    assert identify_install_method({'REMEMBER_ME_INSTALL_METHOD':proc.stdout.strip()})=='APPIMAGE'


@pytest.mark.parametrize('marker,appdir_kind', [('0','correct'),('1','wrong'),('1','missing')])
def test_standalone_rejects_stale_or_unrelated_appimage_markers(payload,marker,appdir_kind):
    appdir,root = payload
    selected = appdir if appdir_kind=='correct' else root.parent.parent
    if appdir_kind=='missing': selected=appdir/'missing'
    env = dict(os.environ,REMEMBER_ME_INSTALL_METHOD='APPIMAGE',REMEMBER_ME_APPIMAGE=marker,
               APPDIR=str(selected),APPIMAGE='/tmp/unrelated.AppImage')
    proc = subprocess.run([str(root/'start.sh')],env=env,capture_output=True,text=True,check=True)
    assert proc.stdout.strip()=='UNKNOWN'


def test_plain_standalone_does_not_reuse_inherited_method(payload):
    _,root=payload
    env=dict(os.environ,REMEMBER_ME_INSTALL_METHOD='DOCKER')
    env.pop('REMEMBER_ME_APPIMAGE',None)
    proc=subprocess.run([str(root/'start.sh')],env=env,capture_output=True,text=True,check=True)
    assert proc.stdout.strip()=='UNKNOWN'
