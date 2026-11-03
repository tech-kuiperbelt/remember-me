"""Native, relocated, offline checks; no service or storage imports."""
import ctypes
from ctypes import wintypes
import importlib.metadata
import json
import multiprocessing
import os
from pathlib import Path
import platform
import site
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import shutil


def prefix(runtime):
    assert Path(sys.prefix).resolve() == runtime.resolve()
    assert sys.prefix == sys.base_prefix and site.ENABLE_USER_SITE is False
    assert Path(sys.executable).resolve().is_relative_to(runtime.resolve())
    assert all(Path(p).resolve().is_relative_to(runtime.resolve()) for p in sys.path)


def spawn(runtime):
    prefix(Path(runtime))
    assert sqlite3.connect(':memory:').execute('select 1').fetchone() == (1,)


def modules():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.GetCurrentProcess()
    psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    array = (wintypes.HMODULE * 4096)()
    count = wintypes.DWORD()
    assert psapi.EnumProcessModules(handle, array, ctypes.sizeof(array), ctypes.byref(count))
    assert count.value <= ctypes.sizeof(array)
    paths = []
    for module in array[:count.value // ctypes.sizeof(wintypes.HMODULE)]:
        buf = ctypes.create_unicode_buffer(32768)
        assert psapi.GetModuleFileNameExW(handle, module, buf, len(buf))
        paths.append(buf.value)
    return paths


def memory():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    kernel = ctypes.WinDLL('kernel32')
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    api = ctypes.WinDLL('psapi').GetProcessMemoryInfo
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    c = Counters(); c.cb = ctypes.sizeof(c)
    assert api(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return {name: getattr(c, name) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'PagefileUsage', 'PeakPagefileUsage')}


def main():
    assert sys.platform == 'win32' and platform.machine().lower() == 'amd64'
    bundle, evidence = map(lambda x: Path(x).resolve(), sys.argv[1:])
    runtime = bundle / 'runtime/python'
    prefix(runtime)
    # No executable lookup outside PBS; no user caches, data, or inherited models.
    os.environ['PATH'] = str(runtime) + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32')
    isolated = evidence.parent / 'isolated'
    isolated.mkdir()
    for key in ('HF_HOME', 'HF_HUB_CACHE', 'HUGGINGFACE_HUB_CACHE', 'TORCH_HOME',
                'TRANSFORMERS_CACHE', 'SENTENCE_TRANSFORMERS_HOME', 'XDG_CACHE_HOME',
                'MEMORY_DATA_DIR', 'USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA'):
        os.environ[key] = str(isolated / key)
    for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_XET', 'DISABLE_SAFETENSORS_CONVERSION'):
        os.environ[key] = '1'
    def blocked(*args, **kwargs):
        raise AssertionError('Network attempted during offline validation')
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    ssl.create_default_context()
    ctypes.WinDLL('kernel32')
    with sqlite3.connect(':memory:') as db:
        db.execute('create virtual table probe using fts5(text)')
        db.execute("insert into probe values ('portable runtime')")
        assert db.execute("select count(*) from probe where probe match 'portable'").fetchone() == (1,)
    subprocess.run([sys.executable, '-I', '-c',
        'import sys; from pathlib import Path; assert Path(sys.prefix).resolve() == Path(sys.argv[1]).resolve()', str(runtime)], check=True, timeout=30)
    child = multiprocessing.get_context('spawn').Process(target=spawn, args=(str(runtime),))
    try:
        child.start(); child.join(30)
        assert child.exitcode == 0, child.exitcode
    finally:
        if child.is_alive():
            child.terminate(); child.join(5)
        child.close()
    subprocess.run([sys.executable, '-I', '-m', 'pip', 'check'], check=True)
    report = json.loads((evidence / 'resolve.json').read_text(encoding="utf-8"))
    for entry in report['install']:
        assert importlib.metadata.version(entry['metadata']['name']) == entry['metadata']['version']
    start = time.perf_counter()
    import torch
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.models import Pooling, Normalize
    assert torch.__version__ == '2.14.1+cpu'
    assert torch.version.cuda is None and not torch.cuda.is_available()
    assert not any(d.metadata['Name'].lower().startswith(('nvidia-', 'triton')) for d in importlib.metadata.distributions())
    manifest = json.loads((bundle / 'models/manifest.json').read_text(encoding="utf-8"))
    model = SentenceTransformer(str(bundle / 'models' / manifest['revision']), device='cpu', local_files_only=True)
    load_seconds = time.perf_counter() - start
    assert any(isinstance(m, Pooling) and m.pooling_mode_cls_token and not m.pooling_mode_mean_tokens for m in model)
    assert isinstance(model[-1], Normalize)
    texts = ['Remember this portable memory.', '请记住这段中文。']
    start = time.perf_counter()
    vectors = model.encode(texts, convert_to_tensor=True, normalize_embeddings=False)
    inference_seconds = time.perf_counter() - start
    assert tuple(vectors.shape) == (2, 1024) and torch.isfinite(vectors).all()
    assert torch.allclose(vectors.norm(dim=1), torch.ones(2), atol=1e-5)
    # Independently compare output against normalized Transformer CLS tokens.
    with torch.no_grad():
        features = model[0](model.tokenize(texts))
        expected = torch.nn.functional.normalize(features['token_embeddings'][:, 0], dim=1)
    assert torch.allclose(vectors, expected, atol=1e-5)
    # Reuse the existing metadata identity algorithm without starting a provider,
    # service or storage. Physical revision remains distinct from logical model ID.
    sys.path.insert(0, str(bundle))
    try:
        from app.embedding.embed import EmbeddingSpec
    finally:
        sys.path.pop(0)
    profile = EmbeddingSpec(provider='sentence_transformers', model=manifest['model'],
                            dimension=int(vectors.shape[1]), normalized=True)
    assert profile.profile_id == 'ep_6134c0b15d2bb471', profile.as_dict()
    dlls = modules()
    (evidence / 'loaded-modules.json').write_text(json.dumps(dlls, indent=2), encoding="utf-8")
    windows = Path(os.environ['SystemRoot']).resolve()
    assert all(Path(p).resolve().is_relative_to(runtime) or Path(p).resolve().is_relative_to(windows) for p in dlls), 'External DLL; inspect loaded-modules.json'
    assert any(Path(p).name.lower() == 'torch_cpu.dll' for p in dlls)
    assert any(Path(p).name.lower().startswith('python312') for p in dlls)
    assert not any('cuda' in Path(p).name.lower() or 'cudnn' in Path(p).name.lower() for p in dlls)
    result = {'status': 'passed', 'python': sys.version, 'prefix': sys.prefix,
              'ssl': ssl.OPENSSL_VERSION, 'sqlite': sqlite3.sqlite_version,
              'model_revision': manifest['revision'], 'shape': list(vectors.shape),
              'profile': profile.as_dict(), 'profile_validation': 'metadata_identity_and_direct_model_inference',
              'provider_storage_integration': 'pending_S1-B',
              'load_seconds': load_seconds, 'inference_seconds': inference_seconds,
              'memory_bytes': memory(), 'disk_free_bytes': shutil.disk_usage(bundle).free,
              'bundle_bytes': sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()),
              'packages': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}}
    (evidence / 'native-results.json').write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == '__main__':
    main()
