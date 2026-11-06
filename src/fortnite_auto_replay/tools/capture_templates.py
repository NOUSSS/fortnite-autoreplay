"""Outil interactif de capture de templates.

Place la souris sur le bouton du jeu, appuie sur ``F7`` : la zone autour du
curseur est enregistrée dans ``templates/<etat>/``. ``F8`` change d'état,
``F9`` quitte.

    python -m fortnite_auto_replay.tools.capture_templates --state replay

Le lapse de capture dure ``--size`` pixels autour du curseur (centré).
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import time
from pathlib import Path

from ..config import PROJECT_ROOT, StateConfig, load_config
from ..logging_setup import setup_logging

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


def _grab_region(left: int, top: int, width: int, height: int):
    import mss  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _next_index(folder: Path, slug: str) -> int:
    existing = [p for p in folder.glob(f"{slug}_*.png") if p.stem.rsplit("_", 1)[-1].isdigit()]
    return len(existing) + 1


def _next_state(states: list[StateConfig], current: str, step: int) -> str:
    slugs = [s.slug for s in states]
    index = slugs.index(current)
    return slugs[(index + step) % len(slugs)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capturer des templates de boutons")
    parser.add_argument("--state", help="état de départ (défaut : le plus prioritaire)")
    parser.add_argument("--size", default="320x90", help="largeurxhauteur de la zone capturée")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--count", type=int, default=0, help="quitter après N captures")
    args = parser.parse_args(argv)
    setup_logging("INFO", to_file=False)

    cfg = load_config(args.config)
    states = cfg.active_states()
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
    captured = 0
    log.info("F7 = capturer | F8/F6 = changer d'état | F9 = quitter")
    log.info("templates enregistrés dans %s", root)

    while True:
        slug = current
        label = next(s.label for s in states if s.slug == slug)
        folder = root / slug
        folder.mkdir(parents=True, exist_ok=True)
        state_hint = f"[{slug} — {label}]"
        sys.stdout.write(f"\r{state_hint} F7 pour capturer, F8 pour l'état suivant   ")
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
            x, y = _cursor()
            left, top = x - width // 2, y - height // 2
            try:
                image = _grab_region(left, top, width, height)
            except Exception as exc:
                log.error("capture impossible : %s", exc)
                continue
            index = _next_index(folder, slug)
            path = folder / f"{slug}_{index:02d}.png"
            image.save(path)
            captured += 1
            print()
            log.info("enregistré : %s", path.relative_to(PROJECT_ROOT))
            if args.count and captured >= args.count:
                break
        time.sleep(0.06)

    print()
    log.info("%d template(s) capturé(s). Vérifie-les ensuite avec : fnreplay check", captured)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
