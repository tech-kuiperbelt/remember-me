"""Windows native icon/controller/startup/write measurements in a new data root.

No GUI automation. Results are runner measurements, never VM visual acceptance.
"""
import time
_BEGIN = time.perf_counter()
import argparse
import ctypes
from ctypes import wintypes as W
import http.client
import json
import os
from pathlib import Path
import socket
import shutil
import sys
import subprocess
import uuid


def check_autostart_registry(bundle, data):
    import windows_autostart as startup
    import winreg
    saved_run, saved_owner = startup.RUN_KEY, startup.OWNER_KEY
    prefix = 'Software\\RememberMeBeta\\Diagnostics\\' + uuid.uuid4().hex
    startup.RUN_KEY, startup.OWNER_KEY = prefix+'\\Run', prefix+'\\Owner'
    try:
        entry = startup.Autostart(bundle, data)
        assert not entry.enabled()
        entry.set_enabled(True)
        assert entry.enabled()
        entry.set_enabled(False)
        assert not entry.enabled()
        return True
    finally:
        startup.RUN_KEY, startup.OWNER_KEY = saved_run, saved_owner
        for key in (prefix+'\\Run', prefix+'\\Owner', prefix):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except FileNotFoundError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', required=True, type=Path)
    work = parser.parse_args().work.resolve()
    if sys.platform != 'win32':
        raise RuntimeError('Native measurements require Windows')
    bundle, evidence = work/'bundle', work/'evidence'
    data = work/'performance-smoke-data'
    data.mkdir()  # Never reuse a user or previous benchmark directory.
    logs = data/'logs'
    logs.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    (data/'config.jsonc').write_text(json.dumps({'port':port,'local_only':True,'auto_download_model':False}), encoding='utf-8')
    os.environ.update(REMEMBER_ME_BETA_DATA_DIR=str(data),
                      REMEMBER_ME_STARTUP_LOG=str(logs/'startup-timing.jsonl'))
    sys.path.insert(0, str(bundle))
    from app.startup_timing import begin, mark
    from tray_launcher import Controller, Windows
    from windows_tray import load_brand_icon
    begin(_BEGIN)
    mark('headless_controller_probe_entry', at=_BEGIN)
    result = {'status':'failed', 'scope':'Windows runner; headless controller and native icon load, not GUI/VM acceptance', 'requests':[]}
    controller = Controller(bundle, Windows())
    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.DestroyIcon.argtypes, user.DestroyIcon.restype = [W.HICON], W.BOOL
        icon = load_brand_icon(bundle)
        try:
            result['native_brand_icon_loaded'] = bool(icon)
        finally:
            user.DestroyIcon(icon)
        # Exercise the native registry API only under a fresh test subtree.
        # Never register an OS login task during an experiment.
        result['autostart_isolated_registry_toggle'] = check_autostart_registry(bundle, data)
        # Exercise explicit data cleanup only in a fresh diagnostic root.
        cleanup_data = work/'uninstall-smoke-data'
        cleanup_data.mkdir()
        (cleanup_data/'config.jsonc').write_text('{}', encoding='utf-8')
        (cleanup_data/'memories.db').write_bytes(b'isolated cleanup fixture')
        helper = Path(__file__).resolve().parents[1]/'windows-setup/uninstall-data.py'
        command = [sys.executable, '-I', '-B', str(helper), '--program', str(bundle), '--data', str(cleanup_data)]
        subprocess.run(command, check=True)
        assert (cleanup_data/'memories.db').exists()
        subprocess.run(command+['--delete'], check=True)
        assert not cleanup_data.exists()
        result['uninstall_isolated_data_cleanup'] = True
        started = time.perf_counter()
        controller.start()
        deadline = time.monotonic()+240
        while time.monotonic()<deadline:
            state = controller.poll()
            if state == 'Running': break
            if state == 'Error' or controller.error:
                raise RuntimeError(controller.error or state)
            time.sleep(0.2)
        else:
            raise RuntimeError('Controller startup timed out')
        result['controller_to_ready_seconds'] = time.perf_counter()-started
        # Two real requests; no metadata embeddings, no personal text or data.
        for label, text in [('short','Windows offline timing smoke.'),
                            ('long',('这是用于测量长文本写入耗时的离线测试记忆，不包含用户数据。'*80)[:2000])]:
            connection = http.client.HTTPConnection('127.0.0.1', controller.port, timeout=600)
            started = time.perf_counter()
            try:
                connection.connect()
                if not controller._owned_listener():
                    raise RuntimeError('Refusing request to an unowned listener')
                payload = json.dumps({'text':text,'namespace':'default'}).encode('utf-8')
                connection.request('POST','/api/memories',payload,{'Content-Type':'application/json'})
                response = connection.getresponse()
                body = json.loads(response.read())
                if response.status >= 400 or not body.get('id'):
                    raise RuntimeError(f'Add failed: HTTP {response.status}')
                result['requests'].append({'case':label,'text_chars':len(text),'seconds':time.perf_counter()-started})
            finally:
                connection.close()
        result['status'] = 'passed'
    finally:
        controller.stop()
        deadline = time.monotonic()+120
        while controller.job and controller.api.pids(controller.job) and time.monotonic()<deadline:
            try:
                controller.poll()
            except (OSError, RuntimeError):
                pass  # An in-flight maintenance request may time out; retry state.
            time.sleep(0.2)
        if controller.job and controller.api.pids(controller.job):
            result['status'] = 'failed'
            result['shutdown_error'] = 'Owned service did not stop within 120 seconds; not force-killed'
        else:
            if controller.process and controller.poll() == 'Error':
                result['status'] = 'failed'
                result['shutdown_error'] = controller.error
            controller.close()
        service_log = logs/'launcher-service.log'
        if service_log.exists():
            shutil.copy2(service_log, evidence/'diagnostics-server.log')
        for filename in ('startup-timing.jsonl','performance-timing.jsonl'):
            source = logs/filename
            if source.exists():
                (evidence/filename).write_bytes(source.read_bytes())
        (evidence/'performance-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        if result['status'] != 'passed':
            raise RuntimeError('Native diagnostics failed; inspect retained evidence')


if __name__ == '__main__':
    main()
