"""Explicit opt-in removal of a verified Beta data root; never follows links."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import stat
import sys

# Deliberately refuse directories containing unrelated entries. Nonstandard
# storage names/layouts require manual cleanup rather than broader deletion.
FILES = {'config.jsonc', 'config.json', 'memories.db', 'memories.db-wal',
         'memories.db-shm', 'memories.db.writer.lock', 'memories.jsonl',
         'memories.jsonl.writer.lock'}
DIRECTORIES = {'logs', 'cache', 'models', 'tmp', 'backups'}


def linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def validate(data, program):
    data, program = Path(data), Path(program).resolve()
    if not data.is_absolute() or '..' in data.parts:
        raise ValueError('An absolute data directory without parent traversal is required')
    for part in (data, *data.parents):
        if part.exists() and linked(part):
            raise ValueError('Data paths through links/junctions are not removed')
    data = data.resolve()
    if not data.exists():
        return data
    if data == Path(data.anchor) or data == Path.home() or len(data.parts) < 3:
        raise ValueError('Refusing a root or home directory')
    if data == program or data in program.parents or program in data.parents:
        raise ValueError('Data and program directories must be separate')
    if not data.is_dir() or not (data/'config.jsonc').is_file() or not (data/'memories.db').is_file():
        raise ValueError('Not a recognized Beta data root (config.jsonc and memories.db required)')
    for entry in data.iterdir():
        allowed = entry.name in DIRECTORIES if entry.is_dir() else (
            entry.name in FILES or entry.name == 'audit.jsonl' or
            (entry.name.startswith('audit.jsonl.') and entry.name[12:].isdigit()))
        if not allowed:
            raise ValueError('Unrecognized entry; remove data manually: ' + entry.name)
    for parent, dirs, files in os.walk(data, followlinks=False):
        for name in dirs + files:
            if linked(Path(parent)/name):
                raise ValueError('Data contains a link/junction; remove it manually')
    return data


def remove(data, program):
    data = validate(data, program)
    if not data.exists():
        return
    # Same locks as the service, without loading embeddings or changing storage.
    sys.path.insert(0, str(Path(program).resolve()))
    from app.platform_files import acquire_file_lock, release_file_lock
    names = ('memories.db.writer.lock', 'memories.jsonl.writer.lock')
    with ExitStack() as stack:
        for name in names:
            handle = stack.enter_context((data/name).open('a+', encoding='utf-8'))
            acquire_file_lock(handle)
            stack.callback(release_file_lock, handle)
        validate(data, program)
        for entry in data.iterdir():
            if entry.name in names:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    for name in names:
        (data/name).unlink()
    data.rmdir()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--program', required=True)
    parser.add_argument('--delete', action='store_true')
    args = parser.parse_args()
    try:
        if args.delete:
            remove(args.data, args.program)
        else:
            validate(args.data, args.program)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
