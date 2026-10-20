"""Bootstrap configuration and paths for application-owned runtime files."""
import json
import os
import sys
from pathlib import Path
import tempfile
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit
from app.config import jsonc


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR_NAME = "remember-me"
DEFAULTS = {"port": 1314, "language": "zh-CN", "auto_download_model": True,
            "local_only": True, "debug": False, "proxy": {"enabled": False, "url": ""}}
PROXY_VARIABLES = tuple(name for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
                        for name in (key, key.lower()))
PATH_DEFAULTS = {
    "MEMORY_STORAGE_PATH": "memories.db",
    "MEMORY_AUDIT_PATH": "audit.jsonl",
    "FASTEMBED_CACHE_DIR": "models/fastembed",
    "SENTENCE_TRANSFORMERS_CACHE_DIR": "models/sentence-transformers",
}
# Model root selection: explicit --model-dir / MEMORY_MODEL_DIR wins, otherwise
# `<data-dir>/models`. The internal marker records whether the active value came
# from the user, so re-resolving an already-normalized environment stays
# idempotent instead of treating our own derived value as an explicit override.
MODEL_DIR_ENV = "MEMORY_MODEL_DIR"
_MODEL_DIR_EXPLICIT_ENV = "MEMORY_MODEL_DIR_EXPLICIT"
# Which Provider cache paths were derived from a default (rather than supplied by
# the user). This preserves the origin across repeated normalization so a value
# that merely equals the old default is not mistaken for a derived one.
_DERIVED_CACHE_ENV = "MEMORY_MODEL_DERIVED_CACHES"


def default_data_dir(environment=None, *, system=None, home=None):
    """Per-user default data directory for the current platform.

    Pure path computation: importing, help and read-only commands must never
    create the directory. Callers still resolve relative explicit overrides
    against PROJECT_ROOT; only this OS default is derived from the user home.
    """
    env = os.environ if environment is None else environment
    system = sys.platform if system is None else system
    if home is None:
        home = env.get("HOME") or env.get("USERPROFILE")
    home = Path(home).expanduser() if home else Path.home()
    if system == "win32":
        base = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(base).expanduser() / DEFAULT_DIR_NAME
    if system == "darwin":
        return home / "Library" / "Application Support" / DEFAULT_DIR_NAME
    return home / f".{DEFAULT_DIR_NAME}"


def resolve_data_dir(data_dir=None, *, environment=None):
    """Resolve the active data root with no side effects.

    An explicit ``data_dir``/``MEMORY_DATA_DIR`` wins (relative values are
    resolved against PROJECT_ROOT); otherwise the per-user OS default is used.
    Reads no config file and creates nothing, so offline/read-only entry points
    can share it safely.
    """
    env = os.environ if environment is None else environment
    selected = data_dir or env.get("MEMORY_DATA_DIR")
    if selected:
        root = Path(selected).expanduser()
        if not root.is_absolute():
            root = PROJECT_ROOT / root
    else:
        root = default_data_dir(env)
    return root.resolve()


def resolve_model_dir(model_dir=None, *, data_dir=None, environment=None):
    """Resolve the effective model root with no side effects.

    Priority: explicit ``model_dir`` (CLI ``--model-dir``) > ``MEMORY_MODEL_DIR``
    > ``<data_dir>/models``. A relative path or ``~`` resolves like the data
    directory (against PROJECT_ROOT), independent of the working directory.
    A blank explicit CLI value is rejected; a blank environment value is unset.
    The default root stays inside the data directory; an explicit root may live
    outside it. Importing, help and status never create the directory.
    """
    env = os.environ if environment is None else environment
    if model_dir is not None and str(model_dir).strip() == "":
        raise ValueError("--model-dir requires a path")
    selected = model_dir if model_dir is not None else env.get(MODEL_DIR_ENV)
    if selected is None or str(selected).strip() == "":
        return rooted_path(resolve_data_dir(data_dir, environment=env), "models")
    path = Path(selected).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def contained_path(root, value, *, label="path"):
    """Resolve ``value`` (relative to ``root``) and keep it inside ``root``.

    Absolute values are used as-is; relative values resolve against ``root``.
    Existing symlink components are resolved, so a link cannot escape ``root``.
    """
    root = Path(root).resolve()
    path = Path(value).expanduser()
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{label} outside model dir: {value}")
    return resolved


def model_dir_is_explicit(environment=None, model_dir=None):
    """True when the model root came from the user rather than our derived default.

    A newly supplied ``model_dir`` always wins (and must not be blank); otherwise
    an internal marker records the original decision so re-resolving a normalized
    environment stays idempotent. A blank environment value is treated as unset,
    matching :func:`resolve_model_dir`.
    """
    env = os.environ if environment is None else environment
    if model_dir is not None:
        if str(model_dir).strip() == "":
            raise ValueError("--model-dir requires a path")
        return True
    marker = env.get(_MODEL_DIR_EXPLICIT_ENV)
    if marker is not None:
        return marker == "1"
    return bool(str(env.get(MODEL_DIR_ENV) or "").strip())


def atomic_text(path, text, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def default_document(values):
    encoded = json.dumps(values, ensure_ascii=False, indent=2)
    comments = {
        '  "port":': '  // HTTP 端口（1–65535），修改后重启生效。\n',
        '  "language":': '  // 语言：zh-CN 或 en；目前仅保存选择。\n',
        '  "auto_download_model":': '  // 模型缓存缺失时自动下载；也可在配置页手动重试。\n',
        '  "local_only":': '  // true=仅监听 127.0.0.1；false=监听 0.0.0.0 并对局域网开放全部 HTTP 接口（修改后重启生效）。\n',
        '  "debug":': '  // Admin UI Debug 模式；配置页保存后刷新页面生效。\n',
        '  "proxy":': '  // 代理关闭时直连；只支持 HTTP/HTTPS。\n',
        '    "url":': '    // 可包含端口及用户名/密码，例如 http://127.0.0.1:7890。\n',
    }
    for prefix, comment in comments.items():
        encoded = encoded.replace(prefix, comment + prefix, 1)
    return encoded + "\n"


def atomic_config(path, values, *, source_text=None):
    path = Path(path)
    original = path.read_text(encoding="utf-8") if path.exists() else source_text
    text = jsonc.update(original, values) if original is not None else default_document(values)
    atomic_text(path, text)


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
    auto_download_model: bool = True
    local_only: bool = True
    proxy_enabled: bool = False
    proxy_url: str = ""
    debug: bool = False
    source_text: str | None = field(default=None, repr=False, compare=False)

    def save(self):
        atomic_config(self.path, self.as_dict(), source_text=self.source_text)

    def as_dict(self, *, redact=False):
        url = self.proxy_url
        if redact and "@" in url:
            parts = urlsplit(url)
            url = f"{parts.scheme}://***@{parts.netloc.rsplit('@', 1)[1]}"
        return {"port": self.port, "language": self.language,
                "auto_download_model": self.auto_download_model,
                "local_only": self.local_only, "debug": self.debug,
                "proxy": {"enabled": self.proxy_enabled, "url": url}}

    @classmethod
    def load(cls, data_dir=None, *, create=False, environment=None):
        env = os.environ if environment is None else environment
        root = resolve_data_dir(data_dir, environment=env)
        location = rooted_path(root, "config.jsonc")
        legacy = rooted_path(root, "config.json") if not location.exists() else None
        if (root / ".config-migration.json").exists():
            raise ValueError("Interrupted data migration; rerun bin/manage --data-dir PATH config migrate before starting")
        source = location if location.exists() else legacy
        text = None
        if source is not None and source.exists():
            try:
                text = source.read_text(encoding="utf-8")
                values = jsonc.loads(text)
            except (ValueError, OSError) as exc:
                raise ValueError(f"Cannot read config {source}: {exc}") from exc
        else:
            values = dict(DEFAULTS)
        config = replace(cls.from_values(location, root, values), source_text=text)
        if create and not location.exists():
            # Preserve the legacy source and never overwrite a concurrent launch.
            try:
                atomic_text(location, text if text is not None else default_document(config.as_dict()), exclusive=True)
            except FileExistsError:
                return cls.load(root, environment=env)
        return config

    @classmethod
    def from_values(cls, location, root, values):
        """Validate file and admin configuration through the same rules."""
        if not isinstance(values, dict) or set(values) - set(DEFAULTS):
            raise ValueError("Config contains unknown fields or is not an object")
        values = {**DEFAULTS, **values}
        if type(values["port"]) is not int or not 1 <= values["port"] <= 65535:
            raise ValueError("Config port must be an integer from 1 to 65535")
        if values["language"] not in ("zh-CN", "en"):
            raise ValueError("Config language must be zh-CN or en")
        if type(values["auto_download_model"]) is not bool:
            raise ValueError("auto_download_model must be a boolean")
        if type(values["local_only"]) is not bool:
            raise ValueError("local_only must be a boolean")
        if type(values["debug"]) is not bool:
            raise ValueError("debug must be a boolean")
        proxy = values["proxy"]
        if not isinstance(proxy, dict) or set(proxy) - {"enabled", "url"}:
            raise ValueError("proxy must contain only enabled and url")
        proxy = {**DEFAULTS["proxy"], **proxy}
        if type(proxy["enabled"]) is not bool or not isinstance(proxy["url"], str):
            raise ValueError("proxy.enabled must be a boolean and proxy.url a string")
        url = proxy["url"]
        if proxy["enabled"] and not url:
            raise ValueError("Enabled proxy requires a HTTP/HTTPS URL")
        if url:
            try:
                parts = urlsplit(url)
                port = parts.port
                valid = (parts.scheme in ("http", "https") and parts.hostname
                         and (port is None or 1 <= port <= 65535)
                         and parts.path in ("", "/") and not parts.query and not parts.fragment
                         and not any(c.isspace() for c in url))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("proxy.url must be a HTTP/HTTPS proxy address")
        return cls(location, root.resolve(), values["port"], values["language"],
                   values["auto_download_model"], values["local_only"], proxy["enabled"], url,
                   debug=values["debug"])

    def runtime_environment(self, environment=None, *, model_dir=None):
        env = dict(os.environ if environment is None else environment)
        for key in PROXY_VARIABLES:
            env.pop(key, None)
        if self.proxy_enabled:
            env.update(HTTP_PROXY=self.proxy_url, HTTPS_PROXY=self.proxy_url)
        env["NO_PROXY"] = "localhost,127.0.0.1,::1"
        env["MEMORY_AUTO_DOWNLOAD_MODEL"] = str(self.auto_download_model).lower()
        env["MEMORY_LOCAL_ONLY"] = str(self.local_only).lower()
        # Explicit local_files_only controls each load; stale offline globals must
        # not prevent the subsequent permitted download. Disable the separate Xet
        # transport so all downloads follow the configured HTTP proxy policy.
        env.update(HF_HUB_OFFLINE="0", TRANSFORMERS_OFFLINE="0", HF_HUB_DISABLE_XET="1")
        env["FASTEMBED_OFFLINE"] = env["SENTENCE_TRANSFORMERS_OFFLINE"] = str(
            not self.auto_download_model).lower()
        explicit_model = model_dir_is_explicit(env, model_dir)
        model_root = resolve_model_dir(model_dir, data_dir=self.data_dir, environment=env)
        env[MODEL_DIR_ENV] = str(model_root)
        env[_MODEL_DIR_EXPLICIT_ENV] = "1" if explicit_model else "0"
        for key, default in PATH_DEFAULTS.items():
            value = env.get(key) or default
            if key == "MEMORY_STORAGE_PATH" and value == ":memory:":
                env[key] = value
            elif key in ("FASTEMBED_CACHE_DIR", "SENTENCE_TRANSFORMERS_CACHE_DIR"):
                continue  # resolved against the model root below
            else:
                env[key] = str(rooted_path(self.data_dir, value))
        derived_caches = {key for key in str(env.get(_DERIVED_CACHE_ENV, "")).split(",") if key}
        next_derived = set()
        for key, sub in (("SENTENCE_TRANSFORMERS_CACHE_DIR", "sentence-transformers"),
                         ("FASTEMBED_CACHE_DIR", "fastembed")):
            value = env.get(key)
            derived = key in derived_caches or not value
            if explicit_model:
                resolved = contained_path(model_root, sub if derived else value, label=key)
                scan_root = model_root
            else:
                # Default mode keeps the original data-directory containment,
                # including cache directories that are symlinks to data/cache.
                resolved = rooted_path(
                    self.data_dir, f"models/{sub}" if derived else value)
                scan_root = self.data_dir
            if derived:
                next_derived.add(key)
            env[key] = str(resolved)
            for entry in resolved.rglob("*"):
                if entry.is_symlink():
                    contained_path(scan_root, entry, label=key)
        env[_DERIVED_CACHE_ENV] = ",".join(sorted(next_derived))
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
        # Dependency caches: the default keeps the historical data-dir layout; an
        # external model root splits writable metadata (data-dir) from model
        # resources (model root).
        if explicit_model:
            hub_cache = contained_path(model_root, "huggingface/hub", label="HF_HUB_CACHE")
            home_cache = rooted_path(self.data_dir, "cache/huggingface")
            torch_cache = rooted_path(self.data_dir, "cache/torch")
        else:
            hub_cache = rooted_path(self.data_dir, "models/huggingface/hub")
            home_cache = rooted_path(self.data_dir, "models/huggingface")
            torch_cache = rooted_path(self.data_dir, "models/torch")
        for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
            env[key] = str(hub_cache)
        env["HF_HOME"] = str(home_cache)
        env["XDG_CACHE_HOME"] = str(rooted_path(self.data_dir, "cache"))
        env["TORCH_HOME"] = str(torch_cache)
        env["TMPDIR"] = str(rooted_path(self.data_dir, "tmp"))
        for key in ("FASTEMBED_MODEL", "SENTENCE_TRANSFORMERS_MODEL"):
            value = env.get(key, "")
            # Repository IDs remain provider identifiers; explicit local models are paths.
            if value and (Path(value).is_absolute() or value.startswith((".", "~"))):
                env[key] = str(contained_path(model_root, value, label=key) if explicit_model
                               else rooted_path(self.data_dir, value))
        return env


def activate_environment(env):
    """Process entrypoints only; low-level Settings construction has no side effects."""
    for key in PROXY_VARIABLES:
        os.environ.pop(key, None)
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
