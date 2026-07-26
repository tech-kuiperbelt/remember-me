"""CLI: Recover/rebuild one storage file from another.

Usage:
    python -m app.recover_cli <source> <target>

Examples:
    # Rebuild SQLite DB from JSON backup
    python -m app.recover_cli data/memories.json data/memories.db

    # Rebuild JSON backup from SQLite DB
    python -m app.recover_cli data/memories.db data/memories.json
"""

import sys

from .storage import create_storage


def _detect_backend(path: str) -> str:
    return "json" if path.endswith(".json") else "sqlite"


def main():
    if len(sys.argv) != 3:
        print("Usage: python -m app.recover_cli <source> <target>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Examples:", file=sys.stderr)
        print("  python -m app.recover_cli data/memories.json data/memories.db", file=sys.stderr)
        print("  python -m app.recover_cli data/memories.db data/memories.json", file=sys.stderr)
        sys.exit(1)

    src_path = sys.argv[1]
    tgt_path = sys.argv[2]

    src = create_storage(_detect_backend(src_path), path=src_path)
    tgt = create_storage(_detect_backend(tgt_path), path=tgt_path)

    from .recovery import recover
    result = recover(tgt, src)

    print(f"Recovered: {result['recovered']}, Errors: {result['errors']}")
    print(f"  Source: {src_path} (total={src.get_stats()['total']})")
    print(f"  Target: {tgt_path}")


if __name__ == "__main__":
    main()
