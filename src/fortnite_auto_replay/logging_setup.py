"""Configuration du logging console + fichier."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "logs"
FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"


def setup_logging(level: str = "INFO", *, to_file: bool = True) -> Path | None:
    """Initialise le logging. Retourne le chemin du fichier ou ``None``."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    if sys.stderr is not None:  # None sous pythonw (panneau lancé sans console)
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(numeric)
        console.setFormatter(logging.Formatter(FORMAT, datefmt="%H:%M:%S"))
        root.addHandler(console)

    # bibliothèques bavardes en DEBUG (dxcam lancé hors du thread principal par le panneau)
    for noisy in ("comtypes", "dxcam"):
        logging.getLogger(noisy).setLevel(logging.INFO)

    if not to_file:
        return None

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / "bot.log"
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(file_handler)
    return path
