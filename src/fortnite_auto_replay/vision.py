"""Détection de templates (boutons du jeu) tolérante à l'échelle.

Backend ``opencv`` = ``cv2.matchTemplate`` (rapide). Repli ``numpy`` = corrélation
croisée normalisée maison, plus lente mais sans dépendance binaire.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

try:  # pragma: no cover - dépend de l'environnement
    import cv2
except Exception:
    cv2 = None  # type: ignore[assignment]

try:  # pragma: no cover
    from PIL import Image
except Exception:
    Image = None  # type: ignore[assignment]


class VisionError(RuntimeError):
    """Template invalide ou taille incompatible."""


@dataclass(slots=True)
class Match:
    label: str
    score: float
    x: int  # coin haut-gauche dans l'image analysée
    y: int
    width: int
    height: int
    scale: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def available_backend(requested: str = "auto") -> str:
    if requested != "auto":
        if requested == "opencv" and cv2 is None:
            log.warning("backend vision 'opencv' demandé mais absent -> numpy")
            return "numpy"
        return requested
    return "opencv" if cv2 is not None else "numpy"


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if cv2 is not None:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # pondération luma avec les canaux BGR
    weights = np.array([0.114, 0.587, 0.299], dtype=np.float32)
    return (image[:, :, :3].astype(np.float32) @ weights).astype(np.uint8)


def resize(image: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1.0) < 1e-6:
        return image
    height = max(1, round(image.shape[0] * factor))
    width = max(1, round(image.shape[1] * factor))
    if cv2 is not None:
        interpolation = cv2.INTER_AREA if factor < 1.0 else cv2.INTER_LINEAR
        return cv2.resize(image, (width, height), interpolation=interpolation)
    if Image is None:  # pragma: no cover
        raise VisionError("aucun backend de redimensionnement disponible")
    pil = Image.fromarray(image)
    resized = pil.resize((width, height), Image.Resampling.BILINEAR)
    return np.asarray(resized)


def load_image(path: str | Path) -> np.ndarray:
    """Charge une image depuis le disque en BGR."""
    path = Path(path)
    if cv2 is not None:
        data = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if data is None:
            raise VisionError(f"image illisible : {path}")
        return data
    if Image is None:  # pragma: no cover
        raise VisionError("Pillow absent : impossible de charger un template")
    with Image.open(path) as image:
        rgb = np.asarray(image.convert("RGB"))
    return rgb[:, :, ::-1].copy()


def save_image(path: str | Path, image: np.ndarray) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if cv2 is not None:
        if not cv2.imwrite(str(path), image):
            raise VisionError(f"écriture impossible : {path}")
        return
    if Image is None:  # pragma: no cover
        raise VisionError("Pillow absent : impossible d'écrire une image")
    Image.fromarray(image[:, :, ::-1]).save(path)


def _ncc(image_gray: np.ndarray, template_gray: np.ndarray) -> np.ndarray:
    """Carte de corrélation croisée normalisée (repli sans OpenCV)."""
    img = image_gray.astype(np.float32)
    tpl = template_gray.astype(np.float32)
    th, tw = tpl.shape
    if th > img.shape[0] or tw > img.shape[1]:
        raise VisionError("template plus grand que l'image")

    windows = np.lib.stride_tricks.sliding_window_view(img, (th, tw))
    tpl_centered = tpl - tpl.mean()
    tpl_norm = float(np.sqrt((tpl_centered**2).sum()))
    if tpl_norm < 1e-6:
        raise VisionError("template uniforme (aucun contraste)")

    # sum(tpl_centered) == 0 -> le terme de moyenne disparaît du numérateur.
    numerator = np.einsum("ijhw,hw->ij", windows, tpl_centered, optimize=True)
    win_std = windows.std(axis=(2, 3))
    denominator = win_std * np.sqrt(th * tw) * tpl_norm
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(denominator > 1e-6, numerator / denominator, 0.0)
    return np.nan_to_num(score, nan=0.0, posinf=0.0, neginf=0.0)


def _score_map(image: np.ndarray, template: np.ndarray, backend: str) -> np.ndarray:
    gray = to_gray(image)
    tpl = to_gray(template)
    if min(tpl.shape) < 4:
        raise VisionError("template trop petit (< 4 px)")
    if tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1]:
        raise VisionError("template plus grand que la zone analysée")
    if backend == "opencv" and cv2 is not None:
        result = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    return _ncc(gray, tpl)


def best_match(
    image: np.ndarray,
    template: np.ndarray,
    label: str,
    *,
    threshold: float = 0.82,
    scales: tuple[float, ...] = (1.0,),
    backend: str = "auto",
) -> Match | None:
    """Meilleure correspondance au-dessus du seuil, toutes échelles confondues."""
    chosen = available_backend(backend)
    best: Match | None = None
    for scale in scales:
        try:
            tpl = resize(template, scale) if scale != 1.0 else template
            scores = _score_map(image, tpl, chosen)
        except VisionError as exc:
            log.debug("%s: échelle %.2f ignorée (%s)", label, scale, exc)
            continue
        _, max_val, _, max_loc = _locate(scores)
        if max_val < threshold:
            continue
        if best is None or max_val > best.score:
            best = Match(
                label=label,
                score=float(max_val),
                x=int(max_loc[0]),
                y=int(max_loc[1]),
                width=int(tpl.shape[1]),
                height=int(tpl.shape[0]),
                scale=scale,
            )
    return best


def _locate(scores: np.ndarray) -> tuple[float, float, tuple[int, int], tuple[int, int]]:
    if cv2 is not None and scores.size:
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(scores)
        return float(min_val), float(max_val), min_loc, max_loc
    index = int(np.argmax(scores))
    y, x = np.unravel_index(index, scores.shape)
    return 0.0, float(scores[y, x]), (0, 0), (int(x), int(y))


def crop(image: np.ndarray, left: int, top: int, right: int, bottom: int) -> np.ndarray:
    h, w = image.shape[:2]
    left, top = max(0, left), max(0, top)
    right, bottom = min(w, right), min(h, bottom)
    return image[top:bottom, left:right]


def annotate(image: np.ndarray, matches: list[Match]) -> np.ndarray:
    """Copie de l'image avec les détections encadrées (frames de debug)."""
    out = image.copy()
    for match in matches:
        color = (0, 220, 0) if match.score >= 0.9 else (0, 180, 255)
        x1, y1 = match.x, match.y
        x2, y2 = x1 + match.width, y1 + match.height
        if cv2 is not None:
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                out,
                f"{match.label} {match.score:.2f}",
                (x1, max(12, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )
        else:  # pragma: no cover - fallback minimaliste
            out[y1 : y1 + 2, x1:x2] = color
            out[max(0, y2 - 2) : y2, x1:x2] = color
            out[y1:y2, x1 : x1 + 2] = color
            out[y1:y2, max(0, x2 - 2) : x2] = color
    return out
