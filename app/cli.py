import os
import sys
import argparse

from .storage import create_storage


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
    args = parser.parse_args()

    src_backend = _detect_backend(args.source)
    src_storage = create_storage(src_backend, path=args.source)

    target_backend = os.environ.get("MEMORY_STORAGE", "sqlite")
    target_path = args.target or os.environ.get("MEMORY_STORAGE_PATH") or (
        "data/memories.json" if target_backend == "json" else "data/memories.db"
    )
    dst_storage = create_storage(target_backend, path=target_path)

    result = dst_storage.merge(src_storage, namespace_prefix=args.namespace_prefix)

    print(f"Imported: {result['imported']}, Renamed (ID conflict): {result['renamed']}, "
          f"Total in source: {result['total']}")


if __name__ == "__main__":
    main()
