"""Offline embedding maintenance commands.

Stop the memory service before mutating the SQLite database with this module.
Examples:

    python -m app.embedding_cli status data/memories.db
    EMBED_PROVIDER=fastembed python -m app.embedding_cli download-model
    EMBED_PROVIDER=fastembed python -m app.embedding_cli reindex data/memories.db
    python -m app.embedding_cli activate data/memories.db ep_0123456789abcdef
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager, nullcontext

from . import audit
from .embed import create_embedder
from .storage_sqlite import SQLiteStorage


def _storage(path: str) -> SQLiteStorage:
    return SQLiteStorage(
        path,
        embedding_dtype=os.environ.get("EMBED_DTYPE", "float16"),
    )


@contextmanager
def _database_maintenance_lock(path: str):
    """Prevent concurrent offline embedding mutations of one database."""
    lock_path = os.path.abspath(path) + ".embedding.lock"
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"another embedding maintenance command is already running for {path}"
            ) from exc
        yield
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline embedding maintenance")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show embedding profiles and storage format")
    status.add_argument("database")

    migrate = sub.add_parser("migrate-codec", help="Re-encode the active profile")
    migrate.add_argument("database")
    migrate.add_argument("--dtype", choices=("float16", "float32"), default="float16")

    sub.add_parser("download-model", help="Download/load the configured provider model")

    reindex = sub.add_parser("reindex", help="Build and atomically activate a new profile")
    reindex.add_argument("database")
    reindex.add_argument("--batch-size", type=int, default=50)

    activate = sub.add_parser("activate", help="Activate a complete retained profile")
    activate.add_argument("database")
    activate.add_argument("profile_id")

    args = parser.parse_args()

    if args.command == "download-model":
        provider = create_embedder()
        provider.warmup()
        print(json.dumps(provider.spec.as_dict(), ensure_ascii=False, indent=2))
        return

    mutating = args.command in ("migrate-codec", "reindex", "activate")
    lock = _database_maintenance_lock(args.database) if mutating else nullcontext()
    with lock:
        storage = _storage(args.database)
        try:
            if args.command == "status":
                stats = storage.get_stats()
                result = {
                    "embedding_storage": stats["embedding_storage"],
                    "embedding_profile": stats["embedding_profile"],
                }
            elif args.command == "migrate-codec":
                result = storage.reencode_active_embeddings(args.dtype)
            elif args.command == "activate":
                result = storage.activate_embedding_profile(args.profile_id)
            else:
                provider = create_embedder()
                started = time.perf_counter()

                def show_progress(completed: int, total: int) -> None:
                    elapsed = time.perf_counter() - started
                    print(json.dumps({
                        "event": "embedding_reindex_progress",
                        "completed": completed,
                        "total": total,
                        "elapsed_seconds": round(elapsed, 1),
                    }), file=sys.stderr, flush=True)

                try:
                    result = storage.reindex_embeddings(
                        provider.spec.as_dict(), provider.embed_documents, args.batch_size,
                        progress_callback=show_progress,
                    )
                    audit.record(
                        "embedding_reindex",
                        {"database": args.database, "profile": provider.spec.as_dict(),
                         "batch_size": args.batch_size},
                        result, (time.perf_counter() - started) * 1000,
                    )
                except Exception as exc:
                    audit.record(
                        "embedding_reindex",
                        {"database": args.database, "profile": provider.spec.as_dict(),
                         "batch_size": args.batch_size},
                        None, (time.perf_counter() - started) * 1000,
                        error={"type": type(exc).__name__, "message": str(exc)},
                    )
                    raise
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            storage.conn.close()


if __name__ == "__main__":
    main()
