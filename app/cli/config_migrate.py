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

from app.config.configuration import (Configuration, DEFAULTS, MODEL_DIR_ENV, PATH_DEFAULTS,
                            PROJECT_ROOT, atomic_json, atomic_config, rooted_path,
                            default_data_dir, model_dir_is_explicit, resolve_model_dir)
from app.storage.database_lock import DatabaseWriterLock


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
    env_path = project_root / ".env"
    config_path = rooted_path(target_root, state.get("config_file", "config.json"))
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


def migrate(data_dir=None, source_data_dir=None, *, model_dir=None, dry_run=False,
            environment=None, project_root=PROJECT_ROOT):
    env = dict(os.environ if environment is None else environment)
    project_root = Path(project_root).resolve()
    def directory(value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else project_root / path).resolve()
    default_root = directory(default_data_dir(env))
    source_root = directory(source_data_dir or env.get("MEMORY_DATA_DIR") or default_root)
    target_root = directory(data_dir or env.get("MEMORY_DATA_DIR") or default_root)
    # An explicit model root lives outside the data migration: its path, caches
    # and local model files are preserved rather than rewritten into the target.
    explicit_model = model_dir_is_explicit(env, model_dir)
    # Whether the *source* installation already ran with an explicit model root.
    # Only then is `models/<sub>` a model-relative override rather than the old
    # data-directory default. A CLI --model-dir alone may be a new root.
    source_explicit_model = bool(str(env.get(MODEL_DIR_ENV) or "").strip())
    selected_model = (resolve_model_dir(model_dir, data_dir=target_root, environment=env)
                      if explicit_model else None)

    def env_model_value(path):
        return str(path.relative_to(project_root)) if path.is_relative_to(project_root) else str(path)

    journal = rooted_path(target_root, ".config-migration.json")
    if journal.exists():
        return _recover(journal, project_root, target_root, dry_run)
    location = target_root / "config.jsonc"
    existed = location.exists()
    has_source_config = any((source_root / name).exists() for name in ("config.jsonc", "config.json"))
    has_target_config = existed or (target_root / "config.json").exists()
    config = Configuration.load(target_root, environment={}) if has_target_config else (
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
        if explicit_model and key in ("FASTEMBED_CACHE_DIR", "SENTENCE_TRANSFORMERS_CACHE_DIR"):
            continue
        old_default = source_root / default
        sources.append((legacy(env.get(key) or str(old_default)), key, default))
    if not explicit_model:
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
    strip_keys = {"MEMORY_STORAGE_PATH_DUAL", "MEMORY_DATA_DIR", "MEMORY_CONFIG_PATH"}
    if explicit_model:
        # Persist the explicit model root; its contents are not copied or rewritten.
        path_updates[MODEL_DIR_ENV] = env_model_value(selected_model)
        strip_keys.update(("FASTEMBED_CACHE_DIR", "SENTENCE_TRANSFORMERS_CACHE_DIR"))

        def model_scoped(value):
            """Resolve an override exactly like runtime does under an explicit root."""
            path = Path(value).expanduser()
            return (path if path.is_absolute() else selected_model / path).resolve()

        for key, sub in (("SENTENCE_TRANSFORMERS_CACHE_DIR", "sentence-transformers"),
                         ("FASTEMBED_CACHE_DIR", "fastembed")):
            value = env.get(key)
            if not value:
                continue
            # A value emitted by default-mode startup/migration is derived from the
            # old data-dir default; the new root's default applies instead. When the
            # source already used an explicit root, `models/<sub>` is a genuine
            # model-relative override and must be preserved.
            legacy_value = Path(value).expanduser()
            legacy_resolved = (legacy_value if legacy_value.is_absolute()
                               else source_root / legacy_value).resolve()
            if (not source_explicit_model
                    and legacy_resolved == (source_root / "models" / sub).resolve()):
                continue
            resolved = model_scoped(value)
            if not resolved.is_relative_to(selected_model):
                raise ValueError(
                    f"{key}={value} resolves outside the explicit model dir "
                    f"{selected_model}; remove or correct it before migration")
            path_updates[key] = resolved.relative_to(selected_model).as_posix()
        for key in ("FASTEMBED_MODEL", "SENTENCE_TRANSFORMERS_MODEL"):
            value = env.get(key, "")
            if not value or not (Path(value).is_absolute() or value.startswith((".", "~"))):
                continue
            strip_keys.add(key)
            resolved = model_scoped(value)
            if not resolved.is_relative_to(selected_model):
                raise ValueError(
                    f"{key}={value} resolves outside the explicit model dir "
                    f"{selected_model}; remove or correct it before migration")
            relative = resolved.relative_to(selected_model).as_posix()
            # Runtime treats a bare relative value as a repository id, so keep the
            # local-path form; an absolute path is the fallback for the root itself.
            path_updates[key] = f"./{relative}" if relative not in ("", ".") else str(resolved)
    def resolved(path):
        """Resolve a path, turning resolution failures into an actionable error."""
        try:
            return path.resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Cannot resolve {path} during migration: {exc}") from exc

    def validated_destination(relative):
        """Absolute, contained destination for a target-root-relative path."""
        return rooted_path(target_root, str(relative))

    def revalidate_destination(destination):
        """Abort if an existing component no longer resolves to the same place."""
        checked = rooted_path(target_root, str(destination))
        if checked != destination:
            raise ValueError(
                f"Migration destination changed before publishing: "
                f"{destination} -> {checked}")

    def in_model_tree(path):
        target = resolved(path)
        return target == selected_model or target.is_relative_to(selected_model)

    def _walk_error(exc):
        raise ValueError(f"Cannot read data directory during migration: {exc}") from exc

    def contains_model_alias(source):
        """True if a descendant is the model tree, or any symlink needs handling.

        Symlinked directories are not followed here (cycle-safe); they force the
        selective traversal so each link can be resolved and checked individually.
        """
        if not source.is_dir():
            return False
        for root, dirs, files in os.walk(source, followlinks=False, onerror=_walk_error):
            root_path = Path(root)
            for name in dirs + files:
                child = root_path / name
                if child.is_symlink() or in_model_tree(child):
                    return True
        return False

    def needs_selective(path):
        target = resolved(path)
        return (target == selected_model or target.is_relative_to(selected_model)
                or contains_model_alias(path))

    def selective_mapping(source, destination_relative):
        """Map a tree while excluding the model tree and preserving other data.

        Traversal and copying share one rule: every child is judged by its
        resolved path, every destination goes through ``rooted_path``, and the
        active traversal chain rejects directory cycles. Non-model symlinks are
        dereferenced like ``copytree(..., symlinks=False)``; model aliases are
        skipped; unreadable entries and broken links are reported.
        """
        result = {}
        active = set()

        def build(path, dest_relative):
            real = resolved(path)
            if real in active:
                raise ValueError(
                    f"Symlink cycle in data directory: {path} resolves back to {real}")
            active.add(real)
            try:
                try:
                    children = list(path.iterdir())
                except OSError as exc:
                    raise ValueError(f"Cannot read {path} during migration: {exc}") from exc
                for child in children:
                    if in_model_tree(child):
                        continue
                    child_relative = str(Path(dest_relative) / child.name)
                    if child.is_symlink():
                        if not child.exists():
                            raise ValueError(f"Broken symlink in data directory: {child}")
                        if child.is_dir():
                            target = resolved(child)
                            if needs_selective(target):
                                build(target, child_relative)
                            else:
                                result[validated_destination(child_relative)] = target
                        else:
                            result[validated_destination(child_relative)] = child
                    elif child.is_dir():
                        if needs_selective(child):
                            build(child, child_relative)
                        else:
                            result[validated_destination(child_relative)] = child
                    else:
                        result[validated_destination(child_relative)] = child
            finally:
                active.discard(real)

        build(source, destination_relative)
        return result

    # Exclude the model tree only when it is not the data root itself (or an
    # ancestor of it). Otherwise its files are ordinary data content.
    model_contains_data = explicit_model and source_root.is_relative_to(selected_model)
    exclude_model_tree = explicit_model and not model_contains_data
    if source_root.exists() and source_root != target_root:
        for source in source_root.iterdir():
            if source.name in ("config.jsonc", "config.json", "tmp") or source.name.endswith(".lock"):
                continue
            if source.is_symlink() and not source.exists():
                raise ValueError(f"Broken symlink in data directory: {source}")
            if exclude_model_tree and source.name == "models":
                # The legacy in-data models directory is intentionally left in place.
                continue
            destination = validated_destination(source.name)
            if exclude_model_tree:
                if in_model_tree(source):
                    # Exclude the selected model root by resolved path, including
                    # aliases/symlinks that use another name.
                    continue
                if needs_selective(source):
                    # The model root or an alias to it is nested somewhere in this
                    # entry: copy siblings but not the model subtree.
                    mappings.update(selective_mapping(source, source.name))
                    continue
            if source.is_symlink() and source.is_dir():
                # Dereference non-model directory links consistently with the
                # selective path so planning, copying and verification agree.
                mappings.setdefault(destination, resolved(source))
            else:
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
    # Re-check every destination before preview or any write. An existing
    # symlink component must not redirect the migration outside the target root.
    for destination in mappings:
        checked = rooted_path(target_root, str(destination))
        if checked != destination:
            raise ValueError(
                f"Migration destination resolves outside or changed: {destination} -> {checked}")
    conflicts = [str(path) for path in mappings if path.exists()]
    preview = {"config": str(location), "source_data_dir": str(source_root),
               "target_data_dir": str(target_root), "copies": [
                   {"source": str(source), "target": str(target)} for target, source in mappings.items()],
               "conflicts": conflicts, "path_settings": path_updates,
               "model_dir_preserved": str(selected_model) if explicit_model else None,
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
                 "config_file": "config.jsonc",
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
                # Re-check the destination (and its parents) before creating
                # directories and again immediately before publishing. A link
                # introduced after planning must abort, not redirect the write.
                revalidate_destination(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                revalidate_destination(destination)
                if destination.exists():
                    raise ValueError(f"Migration target appeared: {destination}")
                state["published"].append(str(destination))
                atomic_json(journal, state)
                revalidate_destination(destination)
                os.replace(temporary, destination)
            # Preserve all provider parameters; replace only application path settings.
            keys = set(path_updates) | strip_keys
            lines = [line for line in (old_env or "").splitlines()
                     if not any(re.match(r"^\s*(?:export\s+)?" + re.escape(key) + r"=", line) for key in keys)]
            lines.extend(f"{key}={shlex.quote(value)}" for key, value in path_updates.items())
            if target_root != default_root:
                # A custom target stays explicit; the platform default needs no
                # override so future starts follow the shared resolution rule.
                lines.append(f"MEMORY_DATA_DIR={shlex.quote(os.path.relpath(target_root, project_root))}")
            _atomic_text(project_root / ".env", "\n".join(lines) + "\n")
            atomic_config(location, config.as_dict(), source_text=config.source_text)
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
