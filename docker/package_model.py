"""Verify the pinned model and produce an internal-only HF cache layout.

Both source paths use the same size/SHA256/Git-or-LFS verification. No runtime
dependencies or host Python environment are needed by this build-only script.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
from urllib.parse import quote
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "bge-m3-manifest.json").read_text())


def verify(path, record):
    if path.stat().st_size != record["size"]:
        raise ValueError(f"Size mismatch: {record['path']}")
    sha = hashlib.sha256()
    git = hashlib.sha1(f"blob {record['size']}\0".encode())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            sha.update(chunk)
            git.update(chunk)
    if sha.hexdigest() != record["sha256"]:
        raise ValueError(f"SHA256 mismatch: {record['path']}")
    if record["source_kind"] == "git_blob":
        valid_source = git.hexdigest() == record["source_oid"]
    else:
        valid_source = sha.hexdigest() == record["source_oid"]
    if not valid_source:
        raise ValueError(f"Official source digest mismatch: {record['path']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--download-cache", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    hub = args.output / "sentence-transformers/models--BAAI--bge-m3"
    snapshot = hub / "snapshots" / MANIFEST["revision"]
    blobs = hub / "blobs"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    for record in MANIFEST["files"]:
        if args.source:
            path = args.source / record["path"]
            # Named context must contain real snapshot files or internal links.
            if not path.resolve().is_relative_to(args.source.resolve()):
                raise ValueError(f"Source link escapes context: {record['path']}")
        else:
            path = args.download_cache / record["sha256"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                try:
                    verify(path, record)
                except ValueError:
                    path.unlink()
            if not path.exists():
                url = (f"https://huggingface.co/{MANIFEST['model']}/resolve/"
                       f"{MANIFEST['revision']}/{quote(record['path'])}")
                temporary = path.with_suffix(".partial")
                try:
                    with urlopen(url, timeout=120) as response, temporary.open("wb") as stream:
                        shutil.copyfileobj(response, stream, length=8 * 1024 * 1024)
                    verify(temporary, record)
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
        verify(path, record)
        blob = blobs / record["source_oid"]
        shutil.copyfile(path, blob)
        destination = snapshot / record["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.path.relpath(blob, destination.parent))
        print(f"Verified {record['path']}: {record['sha256']}", flush=True)
    (hub / "refs").mkdir()
    # Hugging Face reads the ref verbatim as a snapshot directory name. A line
    # ending makes modules.json invisible and ST silently falls back to mean
    # pooling even though the underlying Transformer weights can still load.
    (hub / "refs/main").write_text(MANIFEST["revision"])
    shutil.copytree(HERE / "licenses", args.output / "licenses")
    shutil.copyfile(HERE / "bge-m3-manifest.json", args.output / "bge-m3-manifest.json")
    for path in args.output.rglob("*"):
        if path.is_symlink():
            if not path.resolve().is_relative_to(args.output.resolve()):
                raise ValueError(f"Output link escapes model root: {path}")
        else:
            path.chmod(0o555 if path.is_dir() else 0o444)
    args.output.chmod(0o555)


if __name__ == "__main__":
    main()
