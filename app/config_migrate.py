"""Explicit, offline copy-and-verify migration of legacy runtime files."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import tempfile

from .configuration import (Configuration, DEFAULTS, PATH_DEFAULTS,
                            PROJECT_ROOT, atomic_json, rooted_path)
from .database_lock import DatabaseWriterLock


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(path):
    path = Path(path)
    if path.is_file():
        return {"": path}
    return {str(item.relative_to(path)): item for item in path.rglob("*") if item.is_file()}


def _verify(source, target):
    before, after = _files(source), _files(target)
    if before.keys() != after.keys():
        raise ValueError(f"Migration file list mismatch: {source}")
    for name, original in before.items():
        if _digest(original) != _digest(after[name]):
            raise ValueError(f"Migration checksum mismatch: {original}")


def _atomic_text(path, text):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".config-migrate-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _rollback(state, project_root, target_root):
    if state["project_root"] != str(project_root):
        raise ValueError("Migration journal belongs to a different installation")
    env_path, config_path = project_root / ".env", target_root / "config.json"
    for path, original in ((env_path, state["old_env"]), (config_path, state["old_config"])):
        if original is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_text(path, original)
    for destination in reversed(state["published"]):
        path = rooted_path(target_root, destination)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def _recover(journal, project_root, target_root, dry_run):
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state["project_root"] != str(project_root):
        raise ValueError("Migration journal belongs to a different installation")
    if dry_run:
        return {"interrupted_migration": True, "phase": state["phase"],
                "target_data_dir": str(target_root), "action": "rerun without --dry-run to recover"}
    with ExitStack() as stack:
        for database in sorted(set(state["databases"])):
            stack.enter_context(DatabaseWriterLock(database))
        if state["phase"] != "committed":
            _rollback(state, project_root, target_root)
        staging = rooted_path(target_root, state["staging"])
        if staging.exists():
            shutil.rmtree(staging)
        journal.unlink()
    return {"recovered": True, "committed": state["phase"] == "committed",
            "message": "Recovery complete; rerun migration preview before continuing"}


def migrate(data_dir=None, source_data_dir=None, *, dry_run=False, environment=None,
            project_root=PROJECT_ROOT):
    env = dict(os.environ if environment is None else environment)
    project_root = Path(project_root).resolve()
    def directory(value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else project_root / path).resolve()
    source_root = directory(source_data_dir or env.get("MEMORY_DATA_DIR") or project_root / "data")
    target_root = directory(data_dir or env.get("MEMORY_DATA_DIR") or project_root / "data")
    journal = rooted_path(target_root, ".config-migration.json")
    if journal.exists():
        return _recover(journal, project_root, target_root, dry_run)
    location = target_root / "config.json"
    existed = location.exists()
    source_config = source_root / "config.json"
    has_source_config = source_config.exists()
    config = Configuration.load(target_root, environment={}) if existed else (
        Configuration.load(source_root, environment={}) if has_source_config else Configuration(
            location, target_root, DEFAULTS["port"], DEFAULTS["language"]))
    if target_root != source_root and (target_root.is_relative_to(source_root)
                                       or source_root.is_relative_to(target_root)):
        raise ValueError("Source and target data directories cannot contain each other")

    def legacy(value):
        path = Path(value).expanduser()
        if path.is_absolute():
            return path.resolve()
        candidate = source_root / path
        old_candidate = project_root / path
        # A first failed startup may already have created config while .env is still legacy.
        if has_source_config and (candidate.exists() or not old_candidate.exists()):
            return candidate.resolve()
        return old_candidate.resolve()

    primary = legacy(env.get("MEMORY_STORAGE_PATH") or str(source_root / "memories.db"))
    replica = legacy(env.get("MEMORY_REPLICA_PATH") or env.get("MEMORY_STORAGE_PATH_DUAL")
                     or str(primary.with_suffix(".jsonl")))
    path_updates = {}
    sources = [(primary, "MEMORY_STORAGE_PATH", "memories.db"),
               (replica, "MEMORY_REPLICA_PATH", "memories.jsonl")]
    for key, default in PATH_DEFAULTS.items():
        if key == "MEMORY_STORAGE_PATH":
            continue
        old_default = source_root / default
        sources.append((legacy(env.get(key) or str(old_default)), key, default))
    for key in ("FASTEMBED_MODEL", "SENTENCE_TRANSFORMERS_MODEL"):
        value = env.get(key, "")
        if value and (Path(value).is_absolute() or value.startswith((".", "~"))):
            sources.append((legacy(value), key, f"models/local/{key.lower()}"))
    mappings = {}
    for source, key, default in sources:
        relative = str(source.relative_to(source_root)) if source.is_relative_to(source_root) else default
        destination = rooted_path(target_root, relative)
        path_updates[key] = relative
        if source.exists() and source != destination:
            mappings[destination] = source
        if key == "MEMORY_STORAGE_PATH":
            target_primary = destination
            for suffix in ("-wal", "-shm", ".online-recover"):
                sidecar = Path(str(source) + suffix)
                if sidecar.exists() and source != destination:
                    mappings[Path(str(destination) + suffix)] = sidecar
        if key == "MEMORY_AUDIT_PATH":
            for rotated in source.parent.glob(source.name + ".*"):
                suffix = rotated.name[len(source.name):]
                if suffix[1:].isdigit() and source != destination:
                    mappings[Path(str(destination) + suffix)] = rotated
    if source_root.exists() and source_root != target_root:
        for source in source_root.iterdir():
            if source.name in ("config.json", "tmp") or source.name.endswith(".lock"):
                continue
            destination = rooted_path(target_root, source.name)
            mappings.setdefault(destination, source)
    old_logs = project_root / "logs"
    if old_logs.exists() and old_logs.resolve() != target_root / "logs" and not (
            has_source_config and (source_root / "logs").exists()):
        destination = rooted_path(target_root, "logs")
        if destination in mappings and mappings[destination].resolve() != old_logs.resolve():
            raise ValueError("Both legacy logs and data/logs exist; resolve the conflict before migration")
        mappings[destination] = old_logs
    # A whole directory copy already includes any nested configured paths.
    for destination in list(mappings):
        for parent, source in list(mappings.items()):
            if parent != destination and destination.is_relative_to(parent):
                if mappings[destination] != source / destination.relative_to(parent):
                    raise ValueError(f"Overlapping migration targets: {parent}, {destination}")
                mappings.pop(destination, None)
                break
    conflicts = [str(path) for path in mappings if path.exists()]
    preview = {"config": str(location), "source_data_dir": str(source_root),
               "target_data_dir": str(target_root), "copies": [
                   {"source": str(source), "target": str(target)} for target, source in mappings.items()],
               "conflicts": conflicts, "path_settings": path_updates,
               "source_files_retained": True}
    if dry_run:
        return preview
    if conflicts:
        raise ValueError("Migration targets already exist: " + ", ".join(conflicts))
    with ExitStack() as stack:
        for database in sorted({primary, target_primary}, key=str):
            stack.enter_context(DatabaseWriterLock(str(database)))
        # Flush recovery state into the source database before copying SQLite files.
        if primary.exists():
            connection = sqlite3.connect(str(primary))
            try:
                check = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise ValueError(f"Source SQLite integrity check failed: {check}")
            finally:
                connection.close()
        # Closing SQLite can remove checkpointed WAL/SHM files.
        mappings = {target: source for target, source in mappings.items() if source.exists()}
        target_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".migration-", dir=target_root))
        old_env = (project_root / ".env").read_text(encoding="utf-8") if (project_root / ".env").exists() else None
        old_config = location.read_text(encoding="utf-8") if existed else None
        state = {"project_root": str(project_root), "old_env": old_env, "old_config": old_config,
                 "databases": [str(primary), str(target_primary)], "staging": str(staging),
                 "phase": "copying", "published": []}
        try:
            atomic_json(journal, state)
            staged = {}
            for index, (destination, source) in enumerate(mappings.items()):
                temporary = staging / str(index)
                if source.is_dir():
                    # Dereference model-cache symlinks so migrated files cannot escape the root.
                    shutil.copytree(source, temporary, symlinks=False)
                else:
                    shutil.copy2(source, temporary)
                _verify(source, temporary)
                for copied in _files(temporary).values():
                    with copied.open("rb") as stream:
                        os.fsync(stream.fileno())
                staged[destination] = temporary
            for destination, temporary in staged.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise ValueError(f"Migration target appeared: {destination}")
                state["published"].append(str(destination))
                atomic_json(journal, state)
                os.replace(temporary, destination)
            # Preserve all provider parameters; replace only application path settings.
            keys = set(path_updates) | {"MEMORY_STORAGE_PATH_DUAL", "MEMORY_DATA_DIR", "MEMORY_CONFIG_PATH"}
            lines = [line for line in (old_env or "").splitlines()
                     if not any(re.match(r"^\s*(?:export\s+)?" + re.escape(key) + r"=", line) for key in keys)]
            lines.extend(f"{key}={shlex.quote(value)}" for key, value in path_updates.items())
            lines.append(f"MEMORY_DATA_DIR={shlex.quote(os.path.relpath(target_root, project_root))}")
            _atomic_text(project_root / ".env", "\n".join(lines) + "\n")
            atomic_json(location, {"port": config.port, "language": config.language})
            state["phase"] = "committed"
            atomic_json(journal, state)
        except BaseException:
            _rollback(state, project_root, target_root)
            journal.unlink(missing_ok=True)
            raise
        else:
            journal.unlink()
        finally:
            shutil.rmtree(staging)
    return {**preview, "migrated": True}
