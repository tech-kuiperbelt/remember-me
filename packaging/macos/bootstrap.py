"""Packaged macOS entry point; use the existing HTTP runtime and restart owner."""
import argparse
import asyncio
import fcntl
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import threading
import traceback
from urllib.request import build_opener, ProxyHandler


def configure(root, selected=None):
    root = root.resolve()
    data = Path(selected).expanduser() if selected else Path.home() / 'Library/Application Support/remember-me'
    if not data.is_absolute():
        raise ValueError('Data directory must be absolute')
    data = data.resolve()
    if data.is_relative_to(root.parent.parent):
        raise ValueError('Persistent data must be outside Remember Me.app')
    for key in list(os.environ):
        if key.startswith(('PYTHON', 'MEMORY_', 'EMBED_', 'SENTENCE_TRANSFORMERS_',
                           'FASTEMBED_', 'HF_', 'HUGGINGFACE_', 'TRANSFORMERS_', 'TORCH_')):
            os.environ.pop(key, None)
    os.environ.update(MEMORY_DATA_DIR=str(data), MEMORY_MODEL_DIR=str(root / 'models'),
                      MEMORY_STORAGE='dual', MEMORY_OFFLINE_BUNDLE='1', EMBED_PROVIDER='sentence_transformers',
                      SENTENCE_TRANSFORMERS_MODEL='BAAI/bge-m3', SENTENCE_TRANSFORMERS_DEVICE='cpu',
                      DISABLE_SAFETENSORS_CONVERSION='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    sys.dont_write_bytecode = True
    data.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(root))
    from app.config.configuration import atomic_text
    config = data / 'config.jsonc'
    if not config.exists() and not (data / 'config.json').exists():
        try:
            atomic_text(config, json.dumps({'auto_download_model': False}) + '\n', exclusive=True)
        except FileExistsError:
            pass
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir')
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--owner-pid', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    data = configure(root, args.data_dir)
    os.chdir(data)
    logs = data / 'logs'
    logs.mkdir(exist_ok=True)
    # If Finder force-quits/crashes the native owner, do not orphan its server.
    if args.owner_pid:
        def watch_owner():
            while os.getppid() == args.owner_pid:
                threading.Event().wait(0.5)
            os.kill(os.getpid(), signal.SIGTERM)
        threading.Thread(target=watch_owner, daemon=True).start()
    # Stable sidecar, never unlinked: avoids replacing a locked inode on relaunch.
    with (data / 'macos-launcher.lock').open('a') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            with (logs / 'macos-launcher.log').open('a') as log:
                log.write('Remember Me is already active for this data directory\n')
            raise RuntimeError('Remember Me is already active for this data directory')
        with (logs / 'macos-launcher.log').open('a', buffering=1) as log:
            if not args.no_browser:
                os.dup2(log.fileno(), 1)
                os.dup2(log.fileno(), 2)
            stop_browser = threading.Event()
            from app.config.configuration import Configuration
            port = args.port if args.port is not None else Configuration.load(data).port
            def browser_when_ready():
                opener = build_opener(ProxyHandler({}))
                while not stop_browser.wait(0.5):
                    try:
                        with opener.open(f'http://127.0.0.1:{port}/api/model', timeout=2):
                            pass
                        subprocess.run(['/usr/bin/open', f'http://127.0.0.1:{port}/admin/console'], check=False)
                        return
                    except OSError:
                        continue
            if not args.no_browser:
                threading.Thread(target=browser_when_ready, daemon=True).start()
            try:
                from app.server import main as serve
                argv = ['--data-dir', str(data)]
                if args.port is not None:
                    argv += ['--port', str(args.port)]
                while asyncio.run(serve(argv, exec_restart=False)):
                    pass
            except BaseException:
                traceback.print_exc(file=log)
                raise
            finally:
                stop_browser.set()


if __name__ == '__main__':
    main()
