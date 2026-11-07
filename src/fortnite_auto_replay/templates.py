"""Chargement des templates (images de référence) par état.

Arborescence attendue ::

    templates/
      replay/replay_01.png
      replay/replay_01.json      ← facultatif, écrit par l'outil de capture
      ready/ready_01.png

Plusieurs images par état = plusieurs variantes (résolution, langue, thème).

Le JSON d'un template dit où il a été pris dans la zone client du jeu ::

    {"window": [1920, 1080], "box": [1500, 950, 320, 90]}

Avec lui, le template est mis à l'échelle de la fenêtre courante (autre
résolution) et le bouton n'est cherché que dans sa zone attendue, ce qui
divise le coût du matching. Sans lui, le template sert tel quel sur toute la
frame.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import vision

log = logging.getLogger(__name__)

SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
MIN_SIZE = 6  # pixels, après mise à l'échelle
_CACHE_LIMIT = 64

Rect = tuple[int, int, int, int]  # left, top, right, bottom dans la frame


class TemplateError(RuntimeError):
    """Aucun template exploitable."""


@dataclass(slots=True, frozen=True)
class Template:
    state_slug: str
    path: Path
    image: np.ndarray  # niveaux de gris, à la résolution de capture
    window: tuple[int, int] | None = None  # taille de la zone client lors de la capture
    box: tuple[int, int, int, int] | None = None  # x, y, largeur, hauteur dans cette zone

    @property
    def name(self) -> str:
        return self.path.stem


@dataclass(slots=True, frozen=True)
class Prepared:
    """Template prêt pour une taille de frame donnée."""

    template: Template
    image: np.ndarray  # niveaux de gris, à l'échelle de la frame
    zone: Rect | None  # zone de recherche dans la frame, None = toute la frame

    @property
    def name(self) -> str:
        return self.template.name


def prepare(
    template: Template,
    frame_size: tuple[int, int],
    window_size: tuple[int, int] | None,
    scale: float,
    zone_margin: float,
) -> Prepared | None:
    """Met le template à l'échelle de la frame et calcule sa zone attendue.

    ``frame_size`` = (largeur, hauteur) de la frame analysée ; ``window_size``
    = taille de la zone client du jeu si la frame la couvre entière ; ``scale``
    = réduction appliquée à la capture. Renvoie ``None`` si le template devient
    trop petit.
    """
    known = template.window is not None and window_size is not None
    factor = scale * window_size[1] / template.window[1] if known else scale  # type: ignore[index]
    image = template.image if abs(factor - 1.0) < 1e-6 else vision.resize(template.image, factor)
    if min(image.shape[:2]) < MIN_SIZE:
        return None

    zone: Rect | None = None
    if known and template.box is not None:
        ref_w, ref_h = template.window  # type: ignore[misc]
        x, y, w, h = template.box
        frame_w, frame_h = frame_size
        tpl_h, tpl_w = image.shape[:2]
        cx = (x + w / 2) / ref_w * frame_w
        cy = (y + h / 2) / ref_h * frame_h
        margin_x = max(tpl_w, zone_margin * frame_w)
        margin_y = max(tpl_h, zone_margin * frame_h)
        zone = (
            max(0, math.floor(cx - tpl_w / 2 - margin_x)),
            max(0, math.floor(cy - tpl_h / 2 - margin_y)),
            min(frame_w, math.ceil(cx + tpl_w / 2 + margin_x)),
            min(frame_h, math.ceil(cy + tpl_h / 2 + margin_y)),
        )
        if zone[2] - zone[0] < tpl_w or zone[3] - zone[1] < tpl_h:
            zone = None  # zone hors frame : on cherche partout
    return Prepared(template=template, image=image, zone=zone)


def _read_metadata(path: Path) -> tuple[tuple[int, int] | None, tuple[int, int, int, int] | None]:
    sidecar = path.with_suffix(".json")
    if not sidecar.exists():
        return None, None
    try:
        raw: Any = json.loads(sidecar.read_text(encoding="utf-8"))
        window = tuple(int(v) for v in raw["window"])
        box = tuple(int(v) for v in raw["box"])
        if len(window) != 2 or len(box) != 4 or min(window) <= 0 or min(box[2:]) <= 0:
            raise ValueError("window = [largeur, hauteur], box = [x, y, largeur, hauteur]")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("%s ignoré (%s) : template utilisé sans zone", sidecar.name, exc)
        return None, None
    return window, box  # type: ignore[return-value]


class TemplateLibrary:
    """Ensemble des templates chargés, indexés par état."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._by_state: dict[str, list[Template]] = {}
        self._missing: list[str] = []
        self._errors: list[str] = []
        self._prepared: dict[tuple[Any, ...], list[Prepared]] = {}

    def load(self, state_slugs: list[str]) -> TemplateLibrary:
        for slug in state_slugs:
            folder = self._root / slug
            if not folder.is_dir():
                self._missing.append(slug)
                continue
            templates: list[Template] = []
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() not in SUFFIXES:
                    continue
                try:
                    template = self._load_one(slug, path)
                except vision.VisionError as exc:
                    self._errors.append(f"{path.name}: {exc}")
                    log.warning("template ignoré (%s)", exc)
                    continue
                templates.append(template)
            if templates:
                self._by_state[slug] = templates
            else:
                self._missing.append(slug)
        self._prepared.clear()
        return self

    def _load_one(self, slug: str, path: Path) -> Template:
        gray = vision.to_gray(vision.load_image(path))
        if min(gray.shape) < MIN_SIZE:
            raise vision.VisionError(f"{path.name} trop petit ({gray.shape[1]}x{gray.shape[0]} px)")
        if float(gray.std()) < 3.0:
            raise vision.VisionError(f"{path.name} quasi uniforme (peu discriminant)")
        window, box = _read_metadata(path)
        return Template(state_slug=slug, path=path, image=gray, window=window, box=box)

    def prepared(
        self,
        slug: str,
        frame_size: tuple[int, int],
        window_size: tuple[int, int] | None,
        scale: float,
        zone_margin: float,
    ) -> list[Prepared]:
        """Templates de l'état mis à l'échelle de la frame (calculés une fois par taille)."""
        key = (slug, frame_size, window_size, scale, zone_margin)
        cached = self._prepared.get(key)
        if cached is not None:
            return cached
        result: list[Prepared] = []
        for template in self.for_state(slug):
            item = prepare(template, frame_size, window_size, scale, zone_margin)
            if item is None:
                log.warning("%s trop petit à cette échelle : ignoré", template.name)
                continue
            result.append(item)
        if len(self._prepared) >= _CACHE_LIMIT:
            self._prepared.clear()
        self._prepared[key] = result
        return result

    # --- introspection -------------------------------------------------
    def for_state(self, slug: str) -> list[Template]:
        return self._by_state.get(slug, [])

    @property
    def states(self) -> list[str]:
        return list(self._by_state)

    @property
    def missing(self) -> list[str]:
        return list(self._missing)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def count(self) -> int:
        return sum(len(items) for items in self._by_state.values())

    def summary(self) -> str:
        parts = [f"{slug}={len(items)}" for slug, items in self._by_state.items()]
        text = "templates chargés : " + (", ".join(parts) if parts else "aucun")
        zoned = sum(1 for items in self._by_state.values() for t in items if t.box is not None)
        if zoned:
            text += f" ({zoned} avec zone attendue)"
        if self._missing:
            text += " | états sans template : " + ", ".join(self._missing)
        return text
