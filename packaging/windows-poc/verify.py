"""Native, relocated, offline checks; no service or storage imports."""
import ctypes
from ctypes import wintypes
import importlib.metadata
import json
import multiprocessing
import os
from pathlib import Path, PureWindowsPath
import platform
import re
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


def defender_candidate(path, program_data):
    relative = PureWindowsPath(path).relative_to(PureWindowsPath(program_data) / 'Microsoft/Windows Defender/Platform')
    return (len(relative.parts) == 2 and relative.name.lower() == 'mpoav.dll'
            and re.fullmatch(r'\d+\.\d+\.\d+\.\d+-\d+', relative.parts[0]) is not None)


def verified_defender(path, program_data, identity):
    try:
        if not defender_candidate(path, program_data):
            return False
    except ValueError:
        return False
    if not isinstance(identity, dict) or not isinstance(identity.get('amsi_providers'), list):
        return False
    fields = ('path', 'sha256', 'sha256_after', 'signature_status', 'signer_name',
              'signer_subject', 'signer_thumbprint', 'company_name', 'original_filename', 'file_version')
    if not all(isinstance(identity.get(k), str) for k in fields):
        return False
    version = PureWindowsPath(path).parent.name.split('-')[0]
    return (PureWindowsPath(identity.get('path', '')) == PureWindowsPath(path)
            and re.fullmatch('[0-9a-f]{64}', identity.get('sha256', '')) is not None
            and identity['sha256'] == identity.get('sha256_after')
            and identity.get('signature_status') == 'Valid'
            and identity.get('signer_name') in ('Microsoft Windows', 'Microsoft Windows Publisher', 'Microsoft Corporation')
            and re.search(r'(^|,\s*)O=Microsoft Corporation(,|$)', identity.get('signer_subject', '')) is not None
            and bool(identity.get('signer_thumbprint'))
            and identity.get('company_name') == 'Microsoft Corporation'
            and identity.get('original_filename', '').lower() == 'mpoav.dll'
            and re.match(re.escape(version) + r'(?:\s|$)', identity.get('file_version', '')) is not None
            and any(PureWindowsPath(p.get('path', '')) == PureWindowsPath(path)
                    and re.fullmatch(r'\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}', p.get('clsid', ''))
                    for p in identity['amsi_providers'] if isinstance(p, dict)))


def audit_dlls(dlls, runtime, windows, program_data, evidence):
    classifications = []
    for index, path in enumerate(dlls):
        resolved = Path(path).resolve()
        if resolved.is_relative_to(runtime):
            kind = 'private_runtime'
        elif resolved.is_relative_to(windows):
            kind = 'windows_system'
        else:
            kind = 'unknown_external'
            identity_file = evidence / f'dll-identity-{index}.json'
            try:
                if defender_candidate(str(resolved), program_data):
                    shell = windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'
                    subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-File',
                                    str(Path(__file__).with_name('defender_identity.ps1')),
                                    '-Dll', str(resolved), '-Output', str(identity_file)],
                                   check=True, timeout=60, capture_output=True)
                    identity = json.loads(identity_file.read_text(encoding='utf-8'))
                    if verified_defender(str(resolved), program_data, identity):
                        kind = 'verified_host_defender'
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                (evidence / f'dll-identity-{index}-error.txt').write_text(str(exc) + '\n' + str(getattr(exc, 'stderr', '')), encoding='utf-8')
        classifications.append({'path': str(resolved), 'classification': kind})
    (evidence / 'dll-audit.json').write_text(json.dumps(classifications, indent=2), encoding='utf-8')
    assert all(p['classification'] != 'unknown_external' for p in classifications), 'Unverified external DLL; inspect dll-audit.json'


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
    # Preserve native module evidence even when a model assertion below fails.
    (evidence / 'loaded-modules-before-model-checks.json').write_text(
        json.dumps(modules(), indent=2), encoding="utf-8")
    poolings = [m for m in model if isinstance(m, Pooling)]
    assert len(poolings) == 1 and poolings[0].get_config_dict()["pooling_mode"] == "cls", 'Expected pure CLS pooling'
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
    result = {'status': 'partial', 'model_validation': 'passed', 'dll_audit': 'pending', 'python': sys.version, 'prefix': sys.prefix,
              'ssl': ssl.OPENSSL_VERSION, 'sqlite': sqlite3.sqlite_version,
              'model_revision': manifest['revision'], 'shape': list(vectors.shape),
              'pooling': poolings[0].get_config_dict(), 'norms': vectors.norm(dim=1).tolist(),
              'max_cls_difference': (vectors - expected).abs().max().item(),
              'profile': profile.as_dict(), 'profile_validation': 'metadata_identity_and_direct_model_inference',
              'provider_storage_integration': 'pending_S1-B',
              'load_seconds': load_seconds, 'inference_seconds': inference_seconds,
              'memory_bytes': memory(), 'disk_free_bytes': shutil.disk_usage(bundle).free,
              'bundle_bytes': sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file()),
              'packages': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}}
    (evidence / 'model-results.json').write_text(json.dumps(result, indent=2), encoding="utf-8")
    dlls = modules()
    (evidence / 'loaded-modules.json').write_text(json.dumps(dlls, indent=2), encoding="utf-8")
    windows = Path(os.environ['SystemRoot']).resolve()
    audit_dlls(dlls, runtime, windows, os.environ['ProgramData'], evidence)
    assert any(Path(p).name.lower() == 'torch_cpu.dll' for p in dlls)
    assert any(Path(p).name.lower().startswith('python312') for p in dlls)
    assert not any('cuda' in Path(p).name.lower() or 'cudnn' in Path(p).name.lower() for p in dlls)
    result.update(status='passed', dll_audit='passed')
    (evidence / 'native-results.json').write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == '__main__':
    main()
