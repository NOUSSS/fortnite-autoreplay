"""Traces des arrêts que le journal ne voit pas.

Le panneau tourne sous ``pythonw`` : sans console, une exception non rattrapée
ou un plantage natif (DXGI, pilote, Tk) ferme le processus sans rien laisser.
Ce module écrit :

- dans ``logs/crash.log``, la pile de tous les threads lors d'un plantage natif
  (``faulthandler``) ou d'un blocage signalé par :func:`dump_threads` ;
- dans le journal, toute exception non rattrapée, du thread principal ou non.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import TextIO

log = logging.getLogger(__name__)

_crash: TextIO | None = None  # gardé ouvert : faulthandler y écrit au moment du plantage
_crash_path: Path | None = None


def _excepthook(kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
    log.critical("exception non rattrapée", exc_info=(kind, value, tb))


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "?"
    log.critical(
        "exception non rattrapée dans le thread %s",
        name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),  # type: ignore[arg-type]
    )


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def install(log_dir: Path) -> Path | None:
    """Active les traces ; à appeler une fois, après la configuration du journal."""
    global _crash, _crash_path  # noqa: PLW0603 - un seul fichier par processus
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    log.info("démarrage : pid %d, Python %s", os.getpid(), sys.version.split()[0])
    if _crash is not None:
        return _crash_path
    path = log_dir / "crash.log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        _crash = path.open("a", encoding="utf-8")  # fermé par l'OS à la sortie
        _crash.write(f"--- {_stamp()} démarrage, pid {os.getpid()}\n")
        _crash.flush()
    except OSError as exc:
        log.warning("traces de plantage désactivées (%s)", exc)
        _crash = None
        return None
    faulthandler.enable(file=_crash, all_threads=True)
    _crash_path = path
    return path


def dump_threads(reason: str) -> None:
    """Écrit la pile de chaque thread dans ``crash.log`` : ce que fait un thread bloqué."""
    if _crash is None:
        log.warning("%s (pile des threads non écrite : traces désactivées)", reason)
        return
    try:
        _crash.write(f"--- {_stamp()} {reason}, pid {os.getpid()}\n")
        _crash.flush()
        faulthandler.dump_traceback(file=_crash, all_threads=True)
        _crash.flush()
    except (OSError, ValueError) as exc:
        log.warning("%s (pile des threads non écrite : %s)", reason, exc)
        return
    log.warning("%s : pile des threads dans %s", reason, _crash_path)
