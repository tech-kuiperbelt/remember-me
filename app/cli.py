import argparse
import logging
import os

from .storage import create_storage
from .embed import create_embedder
from .summarize import summarize

logger = logging.getLogger("cli")


def main():
    parser = argparse.ArgumentParser(description="Import memories from a matching-profile SQLite database")
    parser.add_argument("source", help="Source SQLite .db/.sqlite/.sqlite3 file")
    parser.add_argument("--namespace-prefix", default="",
                        help="Prefix for imported namespaces")
    parser.add_argument("--target", default=None,
                        help="Target SQLite file (default: from env or ./data/memories.db)")
    parser.add_argument("--no-summarize", action="store_true",
                        help="Skip summary generation for imported records without one")
    args = parser.parse_args()

    if not args.source.lower().endswith((".db", ".sqlite", ".sqlite3")):
        parser.error("source must be a SQLite .db/.sqlite/.sqlite3 file")
    target_path = args.target or os.environ.get("MEMORY_STORAGE_PATH") or "data/memories.db"
    provider = create_embedder()
    src_storage = create_storage("sqlite", path=args.source, read_only=True)
    dst_storage = create_storage(
        "sqlite", path=target_path, embedding_spec=provider.spec.as_dict()
    )

    try:
        result = dst_storage.merge(src_storage, namespace_prefix=args.namespace_prefix)
    finally:
        src_storage.conn.close()

    summarized = 0
    summarize_failed = 0
    if not args.no_summarize:
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.environ.get("SUMMARIZE_MODEL", "qwen3:latest")
        for mid in result.get("imported_ids", []):
            mem = dst_storage.get(mid)
            if mem is None or mem.get("summary"):
                continue
            try:
                summary = summarize(mem["text"], base_url=base_url, model=model)
            except Exception:
                logger.warning("summarize failed for id=%s", mid, exc_info=True)
                summarize_failed += 1
                continue
            dst_storage.update_summary(mid, summary)
            summarized += 1

    print(f"Imported: {result['imported']}, Renamed (ID conflict): {result['renamed']}, "
          f"Total in source: {result['total']}")
    if not args.no_summarize:
        print(f"Summarized: {summarized}, Failed: {summarize_failed}")


if __name__ == "__main__":
    main()
