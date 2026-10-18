"""Offline embedding maintenance commands.

Stop the memory service before mutating the SQLite database with this module.
Examples:

    python -m app.cli.embedding_cli status data/memories.db
    EMBED_PROVIDER=fastembed python -m app.cli.embedding_cli download-model
    EMBED_PROVIDER=fastembed python -m app.cli.embedding_cli reindex data/memories.db
    python -m app.cli.embedding_cli reindex-index-values data/memories.db
    python -m app.cli.embedding_cli activate data/memories.db ep_0123456789abcdef
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import nullcontext

from app import audit
from app.embedding.embed import create_embedder
from app.embedding.provider_resolution import resolve_embedding_environment
from app.storage.sqlite import SQLiteStorage
from app.storage.database_lock import DatabaseWriterLock


def _storage(path: str, read_only: bool = False) -> SQLiteStorage:
    return SQLiteStorage(
        path,
        embedding_dtype=os.environ.get("EMBED_DTYPE", "float16"),
        read_only=read_only,
    )


def _database_maintenance_lock(path: str):
    """Compatibility seam for the shared SQLite primary writer lock."""
    return DatabaseWriterLock(path)


def main() -> None:
    if any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        _main(None)
        return
    journal = audit.AuditJournal()
    try:
        _main(journal)
    finally:
        journal.close()


def _main(journal) -> None:
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

    reindex_values = sub.add_parser(
        "reindex-index-values",
        help="Build keyword/category vectors for an existing profile",
    )
    reindex_values.add_argument("database")
    reindex_values.add_argument("--profile-id")
    reindex_values.add_argument("--batch-size", type=int, default=50)

    activate = sub.add_parser("activate", help="Activate a complete retained profile")
    activate.add_argument("database")
    activate.add_argument("profile_id")

    args = parser.parse_args()
    from app.config.configuration import output_path
    if hasattr(args, "database"):
        args.database = output_path(args.database)

    if args.command == "download-model":
        # An explicit provider is a download target; otherwise adopt the profile.
        environment = resolve_embedding_environment(
            os.environ, db_path=os.environ.get("MEMORY_STORAGE_PATH"),
            explicit_is_target=True)
        provider = create_embedder(environment=environment, allow_download=True)
        provider.warmup()
        print(json.dumps(provider.spec.as_dict(), ensure_ascii=False, indent=2))
        return

    mutating = args.command in (
        "migrate-codec", "reindex", "reindex-index-values", "activate"
    )
    lock = _database_maintenance_lock(args.database) if mutating else nullcontext()
    with lock:
        storage = _storage(args.database, read_only=args.command == "status")
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
            elif args.command == "reindex-index-values":
                # Repair the target profile's index values, loading the model that
                # profile defines rather than the current active provider.
                provider = create_embedder(environment=resolve_embedding_environment(
                    os.environ, db_path=args.database, profile_id=args.profile_id))
                target_profile = args.profile_id or storage.get_stats()["embedding_profile"]["active"]
                if not target_profile:
                    raise RuntimeError("database has no active embedding profile")
                if provider.spec.profile_id != target_profile:
                    raise RuntimeError(
                        "configured provider does not match target profile: "
                        f"configured={provider.spec.profile_id}, target={target_profile}"
                    )
                started = time.perf_counter()

                def show_index_progress(completed: int, total: int) -> None:
                    print(json.dumps({
                        "event": "index_value_reindex_progress",
                        "completed": completed,
                        "total": total,
                    }), file=sys.stderr, flush=True)

                try:
                    result = storage.reindex_index_values(
                        target_profile, provider.embed_documents, args.batch_size,
                        progress_callback=show_index_progress,
                    )
                    journal.record(
                        "index_value_reindex",
                        {"database": args.database, "profile": provider.spec.as_dict(),
                         "batch_size": args.batch_size},
                        result, (time.perf_counter() - started) * 1000,
                    )
                except Exception as exc:
                    journal.record(
                        "index_value_reindex",
                        {"database": args.database, "profile": provider.spec.as_dict(),
                         "batch_size": args.batch_size},
                        None, (time.perf_counter() - started) * 1000,
                        error={"type": type(exc).__name__, "message": str(exc)},
                    )
                    raise
            else:
                # `reindex` treats an explicit provider as the migration target, so
                # the same provider can switch model/revision. Without an explicit
                # provider it adopts the active profile (a no-op).
                provider = create_embedder(environment=resolve_embedding_environment(
                    os.environ, db_path=args.database, explicit_is_target=True))
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
                    journal.record(
                        "embedding_reindex",
                        {"database": args.database, "profile": provider.spec.as_dict(),
                         "batch_size": args.batch_size},
                        result, (time.perf_counter() - started) * 1000,
                    )
                except Exception as exc:
                    journal.record(
                        "embedding_reindex",
                        {"database": args.database, "profile": provider.spec.as_dict(),
                         "batch_size": args.batch_size},
                        None, (time.perf_counter() - started) * 1000,
                        error={"type": type(exc).__name__, "message": str(exc)},
                    )
                    raise
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            storage.close()


def entrypoint():
    """Run the command with its configuration and exit-status policy."""
    from app.config.configuration import cli_environment
    if not any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        cli_environment()
    main()


if __name__ == "__main__":
    entrypoint()
