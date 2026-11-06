"""Chargement des templates (images de référence) par état.

Arborescence attendue ::

    templates/
      replay/bouton_rejouer_1080p.png
      continue/bouton_continuer_1080p.png
      ready/bouton_pret_1080p.png

Plusieurs images par état = plusieurs variantes (résolution, langue, thème).
Les images sont converties en niveaux de gris et mises à l'échelle de capture
une seule fois, au chargement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import vision

log = logging.getLogger(__name__)

SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


class TemplateError(RuntimeError):
    """Aucun template exploitable."""


@dataclass(slots=True, frozen=True)
class Template:
    state_slug: str
    path: Path
    image: np.ndarray  # niveaux de gris, déjà à l'échelle de capture

    @property
    def name(self) -> str:
        return self.path.stem


class TemplateLibrary:
    """Ensemble des templates chargés, indexés par état."""

    def __init__(self, root: Path, *, scale: float = 1.0) -> None:
        self._root = root
        self._scale = scale
        self._by_state: dict[str, list[Template]] = {}
        self._missing: list[str] = []
        self._errors: list[str] = []

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
        return self

    def _load_one(self, slug: str, path: Path) -> Template:
        image = vision.load_image(path)
        gray = vision.to_gray(image)
        if self._scale < 1.0:
            gray = vision.resize(gray, self._scale)
        if min(gray.shape) < 6:
            raise vision.VisionError(f"{path.name} trop petit ({gray.shape[1]}x{gray.shape[0]} px)")
        if float(gray.std()) < 3.0:
            raise vision.VisionError(f"{path.name} quasi uniforme (peu discriminant)")
        return Template(state_slug=slug, path=path, image=gray)

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
        if self._missing:
            text += " | états sans template : " + ", ".join(self._missing)
        return text
