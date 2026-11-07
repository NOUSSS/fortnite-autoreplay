"""Outil interactif de capture de templates.

Place la souris au centre du bouton du jeu, appuie sur ``F7``, puis écarte la
souris : la zone est capturée ``--delay`` secondes plus tard, sans curseur ni
effet de survol (le bot voit le bouton dans cet état avant de cliquer).
``F8`` change d'état, ``F9`` quitte.

    python -m fortnite_auto_replay.tools.capture_templates --state replay

La capture passe par la même chaîne que le bot (backend, zone client du jeu).
Quand la fenêtre du jeu est trouvée, un JSON à côté du PNG note où le bouton
se trouvait : le bot le cherchera d'abord là, à n'importe quelle résolution.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from .. import vision
from ..capture import CaptureError, Frame, ScreenCapture
from ..config import PROJECT_ROOT, StateConfig, load_config
from ..logging_setup import setup_logging
from ..window import GameWindow, WindowError, ensure_dpi_awareness

log = logging.getLogger("capture_templates")

VK_F7, VK_F8, VK_F9 = 0x76, 0x77, 0x78


def _pressed(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _wait_release(vk: int, timeout: float = 2.0) -> None:
    end = time.time() + timeout
    while _pressed(vk) and time.time() < end:
        time.sleep(0.02)


def _cursor() -> tuple[int, int]:
    from ctypes import wintypes  # noqa: PLC0415

    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


def crop_template(
    frame: Frame, point: tuple[int, int], size: tuple[int, int]
) -> tuple[np.ndarray, dict[str, Any] | None]:
    """Zone ``size`` (largeur, hauteur) centrée sur ``point`` (écran) et ses métadonnées.

    La zone est décalée pour rester dans la frame. Les métadonnées
    (``window``, ``box``) ne sont données que si la frame couvre toute la zone
    client du jeu : ``box`` est alors exprimé dans cette zone.
    """
    width, height = size
    if frame.scale != 1.0:
        raise ValueError("la frame doit être à pleine résolution")
    if width > frame.width or height > frame.height:
        raise ValueError(f"--size {width}x{height} plus grand que la zone capturée")
    fx, fy = point[0] - frame.left, point[1] - frame.top
    if not (0 <= fx < frame.width and 0 <= fy < frame.height):
        raise ValueError("le curseur n'est pas sur la zone capturée")
    left = min(max(0, fx - width // 2), frame.width - width)
    top = min(max(0, fy - height // 2), frame.height - height)
    image = np.ascontiguousarray(frame.image[top : top + height, left : left + width])
    meta = None
    if frame.window_size is not None:
        meta = {"window": list(frame.window_size), "box": [left, top, width, height]}
    return image, meta


def next_index(folder: Path, slug: str) -> int:
    """Numéro libre suivant (le plus grand existant + 1 : rien n'est écrasé)."""
    numbers = [
        int(p.stem.rsplit("_", 1)[-1])
        for p in folder.glob(f"{slug}_*.png")
        if p.stem.rsplit("_", 1)[-1].isdigit()
    ]
    return max(numbers, default=0) + 1


def _next_state(states: list[StateConfig], current: str, step: int) -> str:
    slugs = [s.slug for s in states]
    index = slugs.index(current)
    return slugs[(index + step) % len(slugs)]


class _Grabber:
    """Frame pleine résolution : zone client du jeu si trouvée, sinon le moniteur."""

    def __init__(self, cfg: Any) -> None:
        self._capture_cfg = replace(cfg.capture, scale=1.0, roi=None)
        self._window: GameWindow | None = None
        if cfg.window.enabled:
            try:
                self._window = GameWindow(cfg.window)
            except WindowError as exc:
                log.warning("fenêtre du jeu non suivie (%s)", exc)
        self._on_window: ScreenCapture | None = None
        self._on_screen: ScreenCapture | None = None

    def grab(self) -> Frame:
        if self._window is not None and self._window.region() is not None:
            if self._on_window is None:
                self._on_window = ScreenCapture(self._capture_cfg, window=self._window)
            return self._on_window.grab()
        log.warning("fenêtre du jeu introuvable : capture du moniteur, sans zone attendue")
        if self._on_screen is None:
            self._on_screen = ScreenCapture(self._capture_cfg)
        return self._on_screen.grab()


def _save(folder: Path, slug: str, image: np.ndarray, meta: dict[str, Any] | None) -> Path:
    path = folder / f"{slug}_{next_index(folder, slug):02d}.png"
    vision.save_image(path, image)
    if meta is not None:
        path.with_suffix(".json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capturer des templates de boutons")
    parser.add_argument("--state", help="état de départ (défaut : le plus prioritaire)")
    parser.add_argument("--size", default="320x90", help="largeurxhauteur de la zone capturée")
    parser.add_argument("--delay", type=float, default=1.5, help="secondes entre F7 et la capture")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--count", type=int, default=0, help="quitter après N captures")
    args = parser.parse_args(argv)
    setup_logging("INFO", to_file=False)
    ensure_dpi_awareness()  # curseur et capture dans les mêmes pixels physiques

    cfg = load_config(args.config)
    states = cfg.enabled_states()  # les deux réponses au dialogue comprises
    if not states:
        log.error("aucun état activé dans la configuration")
        return 2
    try:
        width, height = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        log.error("--size doit ressembler à 320x90")
        return 2

    current = args.state or states[0].slug
    if current not in [s.slug for s in states]:
        log.error("état inconnu : %s (choix : %s)", current, ", ".join(s.slug for s in states))
        return 2

    root = cfg.resolved_template_dir()
    grabber = _Grabber(cfg)
    captured = 0
    log.info("F7 = capturer | F8 = état suivant | F9 = quitter")
    log.info("templates enregistrés dans %s", root)

    while True:
        slug = current
        label = next(s.label for s in states if s.slug == slug)
        folder = root / slug
        folder.mkdir(parents=True, exist_ok=True)
        sys.stdout.write(f"\r[{slug} : {label}] F7 pour capturer, F8 pour l'état suivant   ")
        sys.stdout.flush()

        if _pressed(VK_F9):
            print()
            break
        if _pressed(VK_F8):
            _wait_release(VK_F8)
            current = _next_state(states, slug, 1)
            continue
        if _pressed(VK_F7):
            _wait_release(VK_F7)
            point = _cursor()
            print()
            log.info("écartez la souris du bouton : capture dans %.1f s", args.delay)
            time.sleep(max(0.0, args.delay))
            try:
                image, meta = crop_template(grabber.grab(), point, (width, height))
            except (CaptureError, ValueError) as exc:
                log.error("capture impossible : %s", exc)
                continue
            path = _save(folder, slug, image, meta)
            captured += 1
            where = f", zone {meta['box']} dans {meta['window']}" if meta else ", sans zone"
            shown = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
            log.info("enregistré : %s%s", shown, where)
            if args.count and captured >= args.count:
                break
        time.sleep(0.06)

    print()
    log.info("%d template(s) capturé(s). Vérifiez-les ensuite avec : fnreplay check", captured)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
