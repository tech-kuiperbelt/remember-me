"""Resolve the effective embedding provider before model or storage creation.

Priority for every entry point that loads a model:

1. an explicit ``EMBED_PROVIDER`` plus its provider-specific parameters;
2. otherwise the database's **active embedding profile**;
3. otherwise the new-database default (Sentence Transformers + BAAI/bge-m3 + CPU).

The database probe is strictly read-only: it opens an existing file with
``mode=ro`` and never creates a database, migrates data or acquires a writer
lock. A database that already holds memories but has no active profile is an
explicit error instead of silently adopting the new default — the caller must
name the original provider.

Callers distinguish two situations through the keyword options:

* normal startup keeps strict conflict checking (an explicit provider that
  matches the active provider adopts the profile's unspecified identity, and a
  differing model is refused);
* an **explicit migration** target (``reindex`` / ``download-model`` / recovery)
  uses the explicit configuration as-is, never merges or conflicts with the
  active profile, and skips the database probe entirely — so the same provider
  can switch model or revision and a damaged target does not block the rebuild;
* ``profile_id`` resolves against one specific profile row (any status) instead
  of the active profile, so a retained/historical profile can be repaired.

Without an explicit provider, an existing database that cannot be read or holds
memories without an active profile is an error: an unidentified profile is never
silently replaced by the new default.

This module only rewrites the environment mapping consumed by
``app.embedding.embed.create_embedder``; it never imports a provider or performs
inference. The final provider/database consistency check stays in
``SQLiteStorage.configure_embedding_profile``.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROVIDER = "sentence_transformers"
DEFAULT_SENTENCE_TRANSFORMERS_MODEL = "BAAI/bge-m3"
DEFAULT_SENTENCE_TRANSFORMERS_DEVICE = "cpu"

_PROFILE_COLUMNS = ("profile_id, provider, model, dimension, normalized, "
                    "query_prefix, document_prefix")


class EmbeddingResolutionError(RuntimeError):
    """The effective embedding provider cannot be determined safely."""


@dataclass(frozen=True)
class ProfileProbe:
    """Read-only snapshot of one database's profiling state."""

    exists: bool
    has_data: bool
    profile: dict | None


def _is_memory_path(db_path: str | None) -> bool:
    return not db_path or db_path == ":memory:"


def _inspect_profile(db_path: str | None, profile_id: str | None = None) -> ProfileProbe:
    """Inspect an existing database without creating or locking it.

    Returns ``exists=False`` for ``:memory:``, a blank path or a missing file
    (all treated as a new database). Without ``profile_id`` the active profile
    is returned; with it the exact row is returned regardless of status. Tolerant
    of legacy databases without the ``embedding_profiles`` table, but raises
    :class:`EmbeddingResolutionError` when an existing file cannot be read.
    """
    if _is_memory_path(db_path) or not os.path.isfile(db_path):
        return ProfileProbe(exists=False, has_data=False, profile=None)
    uri = Path(db_path).expanduser().resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise EmbeddingResolutionError(
            f"cannot open database {db_path} read-only to read its embedding profile: {exc}"
        ) from exc
    try:
        connection.row_factory = sqlite3.Row
        tables = {
            row["name"] for row in
            connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        has_data = False
        if "memories" in tables:
            try:
                has_data = connection.execute(
                    "SELECT 1 FROM memories WHERE deleted = 0 LIMIT 1"
                ).fetchone() is not None
            except sqlite3.OperationalError:
                has_data = connection.execute(
                    "SELECT 1 FROM memories LIMIT 1"
                ).fetchone() is not None
        profile = None
        if "embedding_profiles" in tables:
            if profile_id is None:
                row = connection.execute(
                    f"SELECT {_PROFILE_COLUMNS} FROM embedding_profiles "
                    "WHERE status = 'active'"
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT {_PROFILE_COLUMNS} FROM embedding_profiles "
                    "WHERE profile_id = ?", (profile_id,)
                ).fetchone()
            profile = dict(row) if row is not None else None
        return ProfileProbe(exists=True, has_data=has_data, profile=profile)
    except sqlite3.Error as exc:
        raise EmbeddingResolutionError(
            f"cannot read embedding profile from {db_path}: {exc}"
        ) from exc
    finally:
        connection.close()


def probe_embedding_profile(db_path: str | None) -> ProfileProbe:
    """Read-only snapshot of an existing database's active profile and data."""
    return _inspect_profile(db_path)


def read_embedding_profile(db_path: str | None, profile_id: str | None = None) -> dict | None:
    """Read one profile row (active when ``profile_id`` is None); None if absent."""
    return _inspect_profile(db_path, profile_id).profile


def _normalize_provider(provider: str) -> str:
    return str(provider).strip().lower().replace("-", "_")


def _set_conflict(environment: dict, key: str, value: str) -> None:
    """Set an adopted identity value, refusing a conflicting explicit one."""
    current = environment.get(key)
    if current is not None and str(current) != value:
        raise EmbeddingResolutionError(
            f"explicit {key}={current!r} conflicts with the database embedding "
            f"profile value {value!r}; unset it or run an offline reindex to change provider"
        )
    environment[key] = value


def _split_revision(model: str) -> tuple[str, str | None]:
    """Inverse of the ``model@revision`` identity string used by ST profiles."""
    if "@" in model:
        base, revision = model.rsplit("@", 1)
        return base, revision or None
    return model, None


def _adopt_profile(environment: dict, profile: dict) -> dict:
    provider = _normalize_provider(profile["provider"])
    model = str(profile["model"])
    dimension = str(int(profile["dimension"]))
    query_prefix = str(profile.get("query_prefix") or "")
    document_prefix = str(profile.get("document_prefix") or "")
    if provider == "ollama":
        _set_conflict(environment, "EMBED_MODEL", model)
        _set_conflict(environment, "EMBED_DIM", dimension)
    elif provider == "fastembed":
        _set_conflict(environment, "FASTEMBED_MODEL", model)
        _set_conflict(environment, "FASTEMBED_QUERY_PREFIX", query_prefix)
        _set_conflict(environment, "FASTEMBED_DOCUMENT_PREFIX", document_prefix)
    elif provider == "sentence_transformers":
        base, revision = _split_revision(model)
        _set_conflict(environment, "SENTENCE_TRANSFORMERS_MODEL", base)
        if revision is not None:
            _set_conflict(environment, "SENTENCE_TRANSFORMERS_REVISION", revision)
        _set_conflict(environment, "SENTENCE_TRANSFORMERS_QUERY_PREFIX", query_prefix)
        _set_conflict(environment, "SENTENCE_TRANSFORMERS_DOCUMENT_PREFIX", document_prefix)
    else:
        raise EmbeddingResolutionError(
            f"database active embedding profile uses an unknown provider: {profile['provider']!r}"
        )
    environment["EMBED_PROVIDER"] = provider
    return environment


def _apply_new_database_default(environment: dict) -> dict:
    # Reached only when no provider was set explicitly, so a blank value is
    # replaced while an explicit model override is preserved.
    environment["EMBED_PROVIDER"] = DEFAULT_PROVIDER
    environment.setdefault("SENTENCE_TRANSFORMERS_MODEL", DEFAULT_SENTENCE_TRANSFORMERS_MODEL)
    return environment


def _apply_sentence_transformers_device_default(environment: dict) -> dict:
    # Device is not part of the profile identity; the product default is CPU
    # while an explicit device override is preserved.
    if _normalize_provider(environment.get("EMBED_PROVIDER", "")) == "sentence_transformers":
        environment.setdefault("SENTENCE_TRANSFORMERS_DEVICE", DEFAULT_SENTENCE_TRANSFORMERS_DEVICE)
    return environment


def resolve_embedding_environment(environment=None, *, db_path: str | None = None,
                                  provider: str | None = None,
                                  explicit_is_target: bool = False,
                                  profile_id: str | None = None) -> dict:
    """Return a copy of ``environment`` with the effective provider resolved.

    ``db_path`` overrides the ``MEMORY_STORAGE_PATH`` used for the probe.

    * Without ``explicit_is_target`` an explicit provider that names the active
      profile's provider adopts the profile's unspecified identity parameters and
      refuses a conflicting explicit value (normal startup).
    * With ``explicit_is_target`` an explicit provider is an intended migration
      target: the explicit configuration is used as-is, the database is never
      probed, and no profile conflict is raised.
    * ``profile_id`` resolves against that exact row (any status) instead of the
      active profile.

    A database that exists but cannot be read (corrupt) or holds memories without
    an active profile is an error when no provider was set explicitly, so an
    unidentified profile is never silently replaced by the default.
    """
    env = dict(os.environ if environment is None else environment)
    explicit = (provider or env.get("EMBED_PROVIDER") or "").strip()
    if explicit and explicit_is_target:
        # The explicit configuration is the rebuild/migration target; there is no
        # need to read (and no reason to fail on) the existing database.
        return _apply_sentence_transformers_device_default(env)
    path = db_path if db_path is not None else env.get("MEMORY_STORAGE_PATH")
    probe = _inspect_profile(path, profile_id)
    if profile_id is not None and probe.profile is None:
        raise EmbeddingResolutionError(f"unknown embedding profile: {profile_id}")
    if explicit:
        # An explicit provider wins. When it names the profile's own provider,
        # adopt the profile's unspecified identity parameters (so e.g. a pinned
        # revision is not silently dropped) while refusing a conflicting value.
        if (probe.profile is not None
                and _normalize_provider(probe.profile["provider"]) == _normalize_provider(explicit)):
            result = _adopt_profile(env, probe.profile)
        else:
            result = env
    elif probe.profile is not None:
        result = _adopt_profile(env, probe.profile)
    elif probe.exists and probe.has_data:
        raise EmbeddingResolutionError(
            "database has memories but no active embedding profile; set EMBED_PROVIDER "
            "(and its model parameters) to the original provider before starting, or "
            "restore/activate the profile with the offline embedding commands"
        )
    else:
        result = _apply_new_database_default(env)
    return _apply_sentence_transformers_device_default(result)
