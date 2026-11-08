"""S1 Step 2: observe the unchanged final AppKit app, with bounded GUI attempts."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import traceback

from verify import Client, LOCK, group_members, require


def apple(script, timeout=15, language='AppleScript'):
    return subprocess.run(['/usr/bin/osascript', '-l', language, '-e', script],
                          capture_output=True, text=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dmg', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    require(sys.platform == 'darwin', 'Native macOS required')
    work = args.work.resolve()
    work.mkdir()
    evidence = work / 'evidence'
    evidence.mkdir()
    result = {'baseline': 'b4bce53d64d644e906d0176aa5d4328beafe9e3b',
              'status': 'failed', 'checks': [], 'unverified': [
                  'Finder drag-and-drop installation', 'Dock visual appearance and interaction',
                  'Gatekeeper/quarantine on a separate Mac']}
    def record(name, status, detail=None):
        item = {'check': name, 'status': status, 'detail': detail}
        result['checks'].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    mount = work / 'mounted'
    mount.mkdir()
    app = work / 'Remember Me.app'
    data = work / 'data'
    resources = app / 'Contents/Resources'
    executable = app / 'Contents/MacOS/remember-me'
    py = resources / 'runtime/python/bin/python3.12'
    process = None
    mounted = False
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    client = Client(port)
    url = client.base + '/admin/console'
    def ready():
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            require(process.poll() is None, 'Native AppKit launcher exited during startup')
            try:
                if client.request('/api/model').get('ready'):
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError('Native service readiness timed out')
    def launch():
        nonlocal process
        process = subprocess.Popen([str(executable), '--data-dir', str(data), '--port', str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        ready()
        record('native AppKit launcher and service start', 'passed', {'launcher_pid': process.pid})
    def cleanup_check(label):
        nonlocal process
        pid = process.pid
        process.wait(timeout=60)
        deadline = time.monotonic() + 15
        remaining = group_members(pid)
        while remaining and time.monotonic() < deadline:
            time.sleep(0.2)
            remaining = group_members(pid)
        (evidence / (label + '-processes.log')).write_text(repr(remaining))
        require(not remaining, f'{label}: native/backend process group remains: {remaining}')
        with socket.socket() as sock:
            require(sock.connect_ex(('127.0.0.1', port)) != 0, label + ': listener remains')
        for kind, name in [('primary', 'memories.db'), ('replica', 'memories.jsonl')]:
            acquired = subprocess.run([str(py), '-I', '-B', '-c', LOCK, str(resources), str(data / name), kind],
                                      capture_output=True, timeout=30)
            require(acquired.returncode == 0, label + ': writer lock remains: ' + repr(acquired.stderr))
        record(label + ': native/backend processes, port and both writer locks released', 'passed')
        process = None
    try:
        subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint',
                        str(mount), str(args.dmg.resolve())], check=True)
        mounted = True
        subprocess.run(['/usr/bin/ditto', str(mount / 'Remember Me.app'), str(app)], check=True)
        subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True)
        mounted = False
        # Observe the actual default handler. Never register a fake browser or
        # change the default browser to turn an unavailable observation into PASS.
        default = apple('ObjC.import("AppKit"); $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL('
                        '$.NSURL.URLWithString("' + url + '")).path.js', language='JavaScript')
        browser_path = default.stdout.strip()
        record('default HTTP handler discovery', 'passed' if default.returncode == 0 else 'unavailable',
               {'path': browser_path, 'error': default.stderr})
        launch()
        browser_name = Path(browser_path).stem
        if browser_name in ('Safari', 'Google Chrome'):
            try:
                # NSWorkspace observation does not launch the browser itself.
                bundle = 'com.apple.Safari' if browser_name == 'Safari' else 'com.google.Chrome'
                running = apple('ObjC.import("AppKit"); '
                                '$.NSRunningApplication.runningApplicationsWithBundleIdentifier("' + bundle + '").count',
                                language='JavaScript')
                (evidence / 'running-applications.log').write_text(running.stdout + running.stderr)
                require(running.returncode == 0 and int(running.stdout.strip()) > 0,
                        'Default browser was not observed running; do not launch it through the observer')
                observed = apple(f'tell application "{browser_name}" to get URL of every tab of every window')
                (evidence / 'browser.log').write_text(observed.stdout + observed.stderr)
                if observed.returncode == 0 and url in observed.stdout:
                    record('default browser opened service URL', 'passed', observed.stdout.strip())
                else:
                    record('default browser opened service URL', 'unavailable', observed.stderr or observed.stdout)
            except (subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
                record('default browser opened service URL', 'unavailable', str(exc))
        else:
            record('default browser opened service URL', 'unavailable', 'No supported real-browser URL observer: ' + browser_path)
        # One fixture only: check Web Restart lifecycle/persistence, not full CRUD.
        mid = client.tool('add', {'text': 'macOS GUI restart persistence fixture.', 'namespace': 'default'})['id']
        log_path = data / 'logs/macos-launcher.log'
        before = log_path.read_text(errors='replace').count('Application startup complete.')
        client.request('/api/maintenance/enter', {})
        client.request('/api/restart', {})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            require(process.poll() is None, 'Native owner exited during Web Restart')
            try:
                starts = log_path.read_text(errors='replace').count('Application startup complete.')
                if starts > before and client.request('/api/model').get('ready'):
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError('Web Restart did not complete a fresh service startup')
        require(client.tool('get_details', {'memory_ids': [mid]})['found'][0]['id'] == mid,
                'Web Restart lost fixture')
        record('Web Restart fresh service lifecycle, stable native owner, persistent fixture', 'passed', mid)
        # Do not grant Accessibility permission, reset TCC, or retry around a denial.
        try:
            command = apple('tell application "System Events"\n'
                            f'set frontmost of (first application process whose unix id is {process.pid}) to true\n'
                            'keystroke "q" using command down\nend tell')
            (evidence / 'cmd-q.log').write_text(command.stdout + command.stderr)
            if command.returncode == 0:
                try:
                    process.wait(timeout=15)
                    record('Cmd-Q native menu action', 'passed')
                except subprocess.TimeoutExpired:
                    record('Cmd-Q native menu action', 'failed', 'Keystroke returned success but app remained running')
            else:
                record('Cmd-Q native menu action', 'unavailable', command.stderr.strip())
        except subprocess.TimeoutExpired:
            record('Cmd-Q native menu action', 'unavailable', 'System Events timed out')
        if process.poll() is None:
            try:
                quit_event = apple('tell application id "org.remember-me.offline-beta" to quit')
                (evidence / 'appleevent-quit.log').write_text(quit_event.stdout + quit_event.stderr)
                if quit_event.returncode == 0:
                    process.wait(timeout=30)
                    record('AppleEvent Quit (distinct from Cmd-Q)', 'passed')
                else:
                    record('AppleEvent Quit (distinct from Cmd-Q)', 'unavailable', quit_event.stderr)
            except subprocess.TimeoutExpired:
                record('AppleEvent Quit (distinct from Cmd-Q)', 'unavailable', 'Quit observation timed out')
        if process.poll() is None:
            client.request('/api/maintenance/enter', {})
            client.request('/api/shutdown', {})
        cleanup_check('normal-exit')
        launch()
        os.kill(process.pid, signal.SIGKILL)
        cleanup_check('native-owner-SIGKILL')
        record('native owner crash triggers backend termination', 'passed')
        result['status'] = 'failed' if any(x['status'] == 'failed' for x in result['checks']) else 'completed'
    except BaseException:
        result['error'] = traceback.format_exc()
        raise
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if mounted:
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=False)
        if (data / 'logs').exists():
            subprocess.run(['/usr/bin/ditto', str(data / 'logs'), str(evidence / 'service-logs')], check=False)
        (evidence / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    require(result['status'] != 'failed', 'GUI verification has failed checks')


if __name__ == '__main__':
    main()
