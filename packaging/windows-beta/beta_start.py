"""Minimal foreground Beta entrypoint; data survives replacement of this directory."""
import time

_PYTHON_ENTRY = time.perf_counter()

import json
import ctypes
import os
from pathlib import Path
import asyncio
import logging
import sys
import traceback

_BOOTSTRAP_IMPORTS_DONE = time.perf_counter()


def configure(root):
    selected = os.environ.get('REMEMBER_ME_BETA_DATA_DIR')
    base = os.environ.get('LOCALAPPDATA')
    if not selected and not base:
        raise RuntimeError('LOCALAPPDATA is missing; set REMEMBER_ME_BETA_DATA_DIR to an absolute data directory')
    data = Path(selected) if selected else Path(base) / 'remember-me-beta'
    if not data.is_absolute():
        raise RuntimeError('Beta data directory must be absolute')
    data = data.resolve()
    if data.is_relative_to(root):
        raise RuntimeError('Persistent data must be outside the extracted program directory')
    for key in list(os.environ):
        if key.startswith(('PYTHON', 'MEMORY_', 'EMBED_', 'SENTENCE_TRANSFORMERS_', 'FASTEMBED_', 'HF_', 'HUGGINGFACE_', 'TRANSFORMERS_', 'TORCH_')):
            os.environ.pop(key, None)
    policy = json.loads((root / 'beta-policy.json').read_text(encoding='utf-8'))
    if policy['storage'] not in ('sqlite', 'dual'):
        raise RuntimeError('Invalid Beta storage policy')
    os.environ['REMEMBER_ME_INSTALL_METHOD'] = 'WINDOWS_SETUP'
    os.environ.update(MEMORY_DATA_DIR=str(data), MEMORY_MODEL_DIR=str(root / 'models'),
                      MEMORY_STORAGE=policy['storage'], EMBED_PROVIDER='sentence_transformers',
                      SENTENCE_TRANSFORMERS_MODEL='BAAI/bge-m3', SENTENCE_TRANSFORMERS_DEVICE='cpu',
                      HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', DISABLE_SAFETENSORS_CONVERSION='1',
                      PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    data.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'win32':
        if data.drive.startswith('\\\\'):
            raise RuntimeError('Windows Beta data must use local NTFS, not a network share')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetVolumePathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        kernel.GetVolumeInformationW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                               ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
                                               ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p, ctypes.c_uint32]
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        volume = ctypes.create_unicode_buffer(32768)
        filesystem = ctypes.create_unicode_buffer(32)
        if not kernel.GetVolumePathNameW(str(data), volume, len(volume)):
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.GetDriveTypeW(volume.value) != 3:
            raise RuntimeError('Windows Beta persistent data requires a local fixed disk')
        if not kernel.GetVolumeInformationW(volume.value, None, 0, None, None, None, filesystem, len(filesystem)):
            raise ctypes.WinError(ctypes.get_last_error())
        if filesystem.value != 'NTFS':
            raise RuntimeError('Windows Beta persistent data requires local NTFS')
    config = data / 'config.jsonc'
    if not config.exists() and not (data / 'config.json').exists():
        # Exclusive creation cannot overwrite a concurrent launch's config.
        from app.config.configuration import atomic_text
        try:
            atomic_text(config, json.dumps({'port':1314, 'local_only':True, 'auto_download_model':False}) + '\n', exclusive=True)
        except FileExistsError:
            pass
    return data


def _run_service():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    config_begin = time.perf_counter()
    data = configure(root)
    logs = data / 'logs'
    logs.mkdir(exist_ok=True)
    os.environ['REMEMBER_ME_STARTUP_LOG'] = str(logs / 'startup-timing.jsonl')
    os.environ['REMEMBER_ME_PERFORMANCE_LOG'] = str(logs / 'performance-timing.jsonl')
    from app.startup_timing import begin, mark
    config_done = time.perf_counter()
    begin(_PYTHON_ENTRY, os.environ.get('REMEMBER_ME_STARTUP_CMD_TIME'))
    mark('python_entry', at=_PYTHON_ENTRY)
    mark('bootstrap_imports_done', at=_BOOTSTRAP_IMPORTS_DONE, since=_PYTHON_ENTRY)
    mark('beta_config_begin', at=config_begin)
    mark('beta_config_done', at=config_done, since=config_begin)
    print(f'Beta data/logs: {data}', flush=True)
    sys.dont_write_bytecode = True
    os.chdir(root)
    try:
        from app.server import main as serve
        # Keep the same owned Windows process and service guard across Web
        # Restart. Each cycle first closes Runtime (workers, stores/locks,
        # provider, audit and logging); crashes never trigger an automatic loop.
        while asyncio.run(serve(exec_restart=False)):
            logging.getLogger('beta_start').info(
                'Web restart: runtime cleanup complete; starting a fresh runtime in the owned service process')
    except Exception:
        logs = data / 'logs'
        logs.mkdir(exist_ok=True)
        with (logs / 'startup-error.log').open('a', encoding='utf-8') as stream:
            traceback.print_exc(file=stream)
        raise


def main():
    if sys.platform == 'win32':
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from tray_launcher import service_guard
        with service_guard():
            return _run_service()
    return _run_service()


if __name__ == '__main__':
    main()
