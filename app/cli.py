import os
import sys
import argparse
import logging

from .storage import create_storage
from .summarize import summarize

logger = logging.getLogger("cli")


def _detect_backend(path: str) -> str:
    if path.endswith(".json"):
        return "json"
    return "sqlite"


def main():
    parser = argparse.ArgumentParser(description="Import memories from another database")
    parser.add_argument("source", help="Source DB/JSON file")
    parser.add_argument("--namespace-prefix", default="",
                        help="Prefix for imported namespaces")
    parser.add_argument("--target", default=None,
                        help="Target DB/JSON file (default: from env or ./data/memories.db)")
    parser.add_argument("--no-summarize", action="store_true",
                        help="Skip summary generation for imported records without one")
    args = parser.parse_args()

    src_backend = _detect_backend(args.source)
    src_storage = create_storage(src_backend, path=args.source)

    target_backend = os.environ.get("MEMORY_STORAGE", "sqlite")
    target_path = args.target or os.environ.get("MEMORY_STORAGE_PATH") or (
        "data/memories.json" if target_backend == "json" else "data/memories.db"
    )
    dst_storage = create_storage(target_backend, path=target_path)

    result = dst_storage.merge(src_storage, namespace_prefix=args.namespace_prefix)

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


if __name__ == "__main__":
    main()
