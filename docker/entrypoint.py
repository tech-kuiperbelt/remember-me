"""Initialize only a new data root, then hand signals directly to the server."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config.configuration import DEFAULTS, default_document


def main():
    root = Path(os.environ["MEMORY_DATA_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "tmp").mkdir(exist_ok=True)
    config = root / "config.jsonc"
    legacy = root / "config.json"
    if not config.exists() and not legacy.exists():
        values = {**DEFAULTS, "auto_download_model": False, "local_only": True}
        descriptor, temporary = tempfile.mkstemp(prefix=".docker-config-", dir=root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(default_document(values))
                stream.flush()
                os.fsync(stream.fileno())
            if not legacy.exists():
                try:
                    # Atomic publication without overwriting a concurrent initializer.
                    os.link(temporary, config)
                except FileExistsError:
                    pass
        finally:
            Path(temporary).unlink(missing_ok=True)
    os.execv(sys.executable, [sys.executable, "-m", "app.server", *sys.argv[1:]])


if __name__ == "__main__":
    main()
