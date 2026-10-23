"""Read the shipped release.jsonc, without network access or Git probing.

Call explicitly at startup (Runtime) or from an offline caller. Imports and
construction do no file work; a Runtime owns its immutable startup snapshot.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config import jsonc
from app.config.configuration import PROJECT_ROOT
from app.domain.releases import MAX_RELEASE_BYTES, ReleaseInfo, validate_release


INSTALL_METHODS = frozenset(("SOURCE", "DOCKER"))


def identify_install_method(environment: Mapping[str, str] | None = None) -> str:
    """Only distribution entrypoints declare the method; future methods opt in."""
    env = os.environ if environment is None else environment
    value = env.get("REMEMBER_ME_INSTALL_METHOD", "")
    method = value.strip().upper() if isinstance(value, str) else ""
    return method if method in INSTALL_METHODS else "UNKNOWN"


@dataclass(frozen=True)
class LocalRelease:
    install_method: str
    release: ReleaseInfo | None = None
    error_code: str | None = None

    @property
    def version(self) -> str | None:
        return self.release.version if self.release is not None else None

    @property
    def display_version(self) -> str:
        return self.version if self.version is not None else "Unknown"

    def as_dict(self):
        """The future API's current object; no paths, Commit IDs or raw errors."""
        return {"version": self.version, "install_method": self.install_method,
                "release": self.release.as_dict() if self.release is not None else None,
                "error_code": self.error_code}


def read_local_release(project_root: Path | None = None, *,
                       environment: Mapping[str, str] | None = None) -> LocalRelease:
    """Return a safe local snapshot; bad metadata must not prevent startup.

    project_root is an explicit caller/test seam, never a data-dir or version
    environment override. SOURCE and DOCKER both use PROJECT_ROOT by default.
    """
    method = identify_install_method(environment)
    path = (PROJECT_ROOT if project_root is None else Path(project_root)) / "release.jsonc"
    try:
        with path.open("rb") as source:
            body = source.read(MAX_RELEASE_BYTES + 1)
        if len(body) > MAX_RELEASE_BYTES:
            raise ValueError("release file exceeds byte limit")
        release = validate_release(jsonc.loads(body.decode("utf-8")))
    except FileNotFoundError:
        return LocalRelease(method, error_code="local_release_missing")
    except (OSError, ValueError, RecursionError):
        return LocalRelease(method, error_code="local_release_invalid")
    return LocalRelease(method, release=release)
