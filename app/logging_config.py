# app/logging_config.py
import logging
import sys
from pathlib import Path

def configure_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "app.log"

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root.addHandler(handler)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)
