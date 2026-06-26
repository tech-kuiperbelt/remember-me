import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=_log_format,
    handlers=[
        logging.StreamHandler(sys.stderr),
        RotatingFileHandler(LOG_DIR / "memory-server.log", maxBytes=10 * 1024 * 1024, backupCount=5,
                            encoding="utf-8"),
    ],
)
