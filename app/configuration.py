"""Bootstrap configuration and paths for application-owned runtime files."""
import json
import os
from pathlib import Path
import tempfile
from dataclasses import dataclass


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {"port": 1314, "language": "zh-CN"}
PATH_DEFAULTS = {
    "MEMORY_STORAGE_PATH": "memories.db",
    "MEMORY_AUDIT_PATH": "audit.jsonl",
    "FASTEMBED_CACHE_DIR": "models/fastembed",
    "SENTENCE_TRANSFORMERS_CACHE_DIR": "models/sentence-transformers",
}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
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


def rooted_path(root, value):
    """Resolve and contain a path, including existing symlink components."""
    root = Path(root).resolve()
    path = Path(value).expanduser()
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path outside data_dir: {value}; run bin/manage config migrate first")
    return resolved


@dataclass(frozen=True)
class Configuration:
    path: Path
    data_dir: Path
    port: int
    language: str

    @classmethod
    def load(cls, data_dir=None, *, create=False, environment=None):
        env = os.environ if environment is None else environment
        selected = data_dir or env.get("MEMORY_DATA_DIR")
        root = Path(selected).expanduser() if selected else PROJECT_ROOT / "data"
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        root = root.resolve()
        location = rooted_path(root, "config.json")
        if (root / ".config-migration.json").exists():
            raise ValueError("Interrupted data migration; rerun bin/manage --data-dir PATH config migrate before starting")
        if location.exists():
            try:
                values = json.loads(location.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise ValueError(f"Cannot read config {location}: {exc}") from exc
        else:
            values = dict(DEFAULTS)
            if create:
                # Publish a complete file without overwriting a concurrent launcher's config.
                location.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".config-", dir=location.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(values, stream, ensure_ascii=False, indent=2)
                        stream.write("\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, location)
                except FileExistsError:
                    return cls.load(root, environment=env)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        if not isinstance(values, dict) or set(values) - set(DEFAULTS):
            raise ValueError("Config must be an object containing only port and language")
        values = {**DEFAULTS, **values}
        if type(values["port"]) is not int or not 1 <= values["port"] <= 65535:
            raise ValueError("Config port must be an integer from 1 to 65535")
        if values["language"] not in ("zh-CN", "en"):
            raise ValueError("Config language must be zh-CN or en")
        return cls(location, root.resolve(), values["port"], values["language"])

    def runtime_environment(self, environment=None):
        env = dict(os.environ if environment is None else environment)
        for key, default in PATH_DEFAULTS.items():
            value = env.get(key) or default
            if key == "MEMORY_STORAGE_PATH" and value == ":memory:":
                env[key] = value
            else:
                env[key] = str(rooted_path(self.data_dir, value))
                if key in ("FASTEMBED_CACHE_DIR", "SENTENCE_TRANSFORMERS_CACHE_DIR"):
                    for entry in Path(env[key]).rglob("*"):
                        if entry.is_symlink():
                            rooted_path(self.data_dir, entry)
        primary = env["MEMORY_STORAGE_PATH"]
        replica = env.get("MEMORY_REPLICA_PATH") or env.get("MEMORY_STORAGE_PATH_DUAL")
        replica = replica or ("memories.jsonl" if primary == ":memory:" else str(Path(primary).with_suffix(".jsonl")))
        env["MEMORY_REPLICA_PATH"] = str(rooted_path(self.data_dir, replica))
        env.pop("MEMORY_STORAGE_PATH_DUAL", None)
        env.update(MEMORY_DATA_DIR=str(self.data_dir),
                   MEMORY_PATHS_ACTIVE="1",
                   MEMORY_PORT=str(self.port), MEMORY_LANGUAGE=self.language,
                   MEMORY_LOG_DIR=str(rooted_path(self.data_dir, "logs")),
                   MEMORY_TEMP_DIR=str(rooted_path(self.data_dir, "tmp")))
        # Dependency caches and multipart spill files use these at process startup.
        for key, default in {"HF_HOME": "models/huggingface", "XDG_CACHE_HOME": "cache",
                             "TORCH_HOME": "models/torch", "TMPDIR": "tmp"}.items():
            env[key] = str(rooted_path(self.data_dir, default))
        for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
            env[key] = str(rooted_path(self.data_dir, "models/huggingface/hub"))
        for key in ("FASTEMBED_MODEL", "SENTENCE_TRANSFORMERS_MODEL"):
            value = env.get(key, "")
            # Repository IDs remain provider identifiers; explicit local models are paths.
            if value and (Path(value).is_absolute() or value.startswith((".", "~"))):
                env[key] = str(rooted_path(self.data_dir, value))
        return env


def activate_environment(env):
    """Process entrypoints only; low-level Settings construction has no side effects."""
    os.environ.update(env)
    root = Path(env["MEMORY_DATA_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    Path(env["MEMORY_TEMP_DIR"]).mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = env["MEMORY_TEMP_DIR"]


def cli_environment():
    config = Configuration.load(create=False)
    env = config.runtime_environment()
    activate_environment(env)
    return config


def output_path(value):
    """Contain CLI outputs when launched through the application entrypoints."""
    root = os.environ.get("MEMORY_DATA_DIR") if os.environ.get("MEMORY_PATHS_ACTIVE") == "1" else None
    if value == ":memory:":
        return value
    return str(rooted_path(root, value)) if root else value
