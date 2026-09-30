"""Operator commands sharing the server's bootstrap configuration."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .configuration import Configuration, activate_environment, output_path
from .config_migrate import migrate


def main(argv=None):
    parser = argparse.ArgumentParser(description="remember-me offline maintenance")
    parser.add_argument("--data-dir", help="Data directory (default: project data)")
    parser.add_argument("command")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command not in ("status", "embedding", "migrate", "recover", "config"):
        parser.error(f"Unknown command: {args.command}")
    tail = args.arguments
    if args.command == "config" and tail and tail[0] == "migrate":
        migration = argparse.ArgumentParser(prog="bin/manage config migrate")
        migration.add_argument("--data-dir", default=args.data_dir)
        migration.add_argument("--source-data-dir")
        migration.add_argument("--dry-run", action="store_true")
        options = migration.parse_args(tail[1:])
        print(json.dumps(migrate(options.data_dir, options.source_data_dir, dry_run=options.dry_run),
                         ensure_ascii=False, indent=2))
        return 0
    if tail and tail[0] in ("--help", "-h"):
        if args.command == "migrate":
            print("Usage: bin/manage migrate {keywords|categories|anchors} [options]")
            return 0
        if args.command == "recover":
            print("Usage: bin/manage recover [SOURCE.jsonl] [TARGET.db]")
            return 0
        if args.command == "config":
            print("Usage: bin/manage [--data-dir PATH] config {status|migrate}")
            return 0
    # Help is delegated before activating file-producing runtime dependencies.
    if "--help" in tail or "-h" in tail:
        kinds = {"keywords": "keyword", "categories": "category", "anchors": "anchor"}
        modules = {"embedding": "app.embedding_cli", "migrate": "app." + kinds.get(tail[0], "unknown") + "_migrate_deepseek"}
        module = modules.get(args.command)
        if module:
            return subprocess.call([sys.executable, "-m", module, *(tail[1:] if args.command == "migrate" else tail)])
    config = Configuration.load(args.data_dir, create=False)
    env = config.runtime_environment()
    activate_environment(env)
    primary, replica = env["MEMORY_STORAGE_PATH"], env["MEMORY_REPLICA_PATH"]
    if args.command == "config":
        if tail != ["status"]:
            parser.error("config requires status or migrate")
        print(json.dumps({"config": str(config.path), **config.as_dict(redact=True),
                          "data_dir": str(config.data_dir)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        shown_primary = primary if primary == ":memory:" else str(Path(primary).relative_to(config.data_dir))
        shown_replica = str(Path(replica).relative_to(config.data_dir))
        print(f"data_dir: {config.data_dir}\nprimary: {shown_primary}\nreplica: {shown_replica}", flush=True)
        module, arguments = "app.embedding_cli", ["status", output_path(tail[0]) if tail else primary]
    elif args.command == "embedding":
        if not tail:
            parser.error("embedding requires a subcommand")
        command, arguments = tail[0], tail[1:]
        if command in ("status", "migrate-codec", "reindex", "reindex-index-values"):
            if not arguments or arguments[0].startswith("-"):
                arguments.insert(0, primary)
            else:
                arguments[0] = output_path(arguments[0])
        elif command == "activate":
            if not arguments:
                parser.error("embedding activate requires a profile ID")
            arguments = [output_path(arguments[1]) if len(arguments) > 1 else primary, arguments[0]]
        module, arguments = "app.embedding_cli", [command, *arguments]
    elif args.command == "migrate":
        if not tail or tail[0] not in ("keywords", "categories", "anchors"):
            parser.error("migrate requires keywords, categories or anchors")
        kinds = {"keywords": "keyword", "categories": "category", "anchors": "anchor"}
        module, arguments = "app." + kinds[tail[0]] + "_migrate_deepseek", tail[1:]
        if not any(arg == "--db" or arg.startswith("--db=") for arg in arguments):
            arguments.extend(["--db", primary])
    else:
        module = "app.recover_cli"
        arguments = [tail[0] if tail else replica, output_path(tail[1]) if len(tail) > 1 else primary]
    return subprocess.call([sys.executable, "-m", module, *arguments], env=env)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
