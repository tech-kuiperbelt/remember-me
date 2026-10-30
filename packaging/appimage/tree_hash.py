"""Record a canonical inventory of files, symlinks and mode bits in an AppDir."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

root = Path(sys.argv[1])
result = hashlib.sha256()
count = 0
for path in sorted(root.rglob('*'), key=lambda p: str(p.relative_to(root))):
    info = path.lstat()
    record = {'path': str(path.relative_to(root)), 'mode': stat.S_IMODE(info.st_mode)}
    if path.is_symlink():
        record.update(type='symlink', target=os.readlink(path))
    elif path.is_file():
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(block)
        record.update(type='file', sha256=digest.hexdigest(), size=info.st_size)
    elif path.is_dir():
        record.update(type='directory')
    else:
        raise SystemExit(f'Unsupported AppDir entry: {path}')
    result.update((json.dumps(record, sort_keys=True, ensure_ascii=True) + '\n').encode())
    count += 1
print(json.dumps({'algorithm': 'sha256 over sorted JSONL path/type/mode/content-or-link',
                  'sha256': result.hexdigest(), 'entries': count}, indent=2))
