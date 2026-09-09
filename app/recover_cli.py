"""CLI: Offline recovery of SQLite from a vector-free NDJSON backup.

Usage:
    python -m app.recover_cli <source> <target>

Examples:
    # Stop the server first, then rebuild SQLite from its JSONL backup.
    python -m app.recover_cli data/memories.jsonl data/memories.db
"""

import sys

from .embed import embed
from .recovery import recover_ndjson_to_sqlite


def main():
    if len(sys.argv) != 3:
        print("Usage: python -m app.recover_cli <source.jsonl> <target.db>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Examples:", file=sys.stderr)
        print("  Stop the server first, then run:", file=sys.stderr)
        print("  python -m app.recover_cli data/memories.jsonl data/memories.db", file=sys.stderr)
        sys.exit(1)

    src_path = sys.argv[1]
    tgt_path = sys.argv[2]

    if not src_path.endswith((".jsonl", ".ndjson")) or not tgt_path.endswith(".db"):
        print("Recovery requires a .jsonl/.ndjson source and .db target.", file=sys.stderr)
        sys.exit(2)

    result = recover_ndjson_to_sqlite(src_path, tgt_path, embed)

    print(f"Recovered: {result['recovered']}, Errors: {result['errors']}")
    print(f"  Source: {src_path}")
    print(f"  Target: {tgt_path}")


if __name__ == "__main__":
    main()
