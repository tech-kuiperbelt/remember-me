"""Minimal foreground Beta entrypoint; data survives replacement of this directory."""
import json
import ctypes
import os
from pathlib import Path
import runpy
import sys
import traceback


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
    data = configure(root)
    print(f'Beta data/logs: {data}', flush=True)
    sys.dont_write_bytecode = True
    os.chdir(root)
    try:
        runpy.run_module('app.server', run_name='__main__')
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
