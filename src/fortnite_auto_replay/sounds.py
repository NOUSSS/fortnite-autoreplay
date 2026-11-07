"""Sons courts pour suivre le bot sans le regarder (jeu en plein écran).

Deux notes montantes à la reprise, descendantes à la pause. Jouées dans un
thread à part : ``winsound.Beep`` bloque le temps de la note, et le raccourci
ou le panneau qui déclenche le son ne doit pas attendre.
"""

from __future__ import annotations

import logging
import threading
from typing import Literal

log = logging.getLogger(__name__)

Cue = Literal["resume", "pause"]

_NOTES: dict[Cue, tuple[tuple[int, int], ...]] = {
    "resume": ((660, 70), (990, 90)),
    "pause": ((990, 70), (660, 90)),
}


def _play(notes: tuple[tuple[int, int], ...]) -> None:
    try:
        import winsound  # noqa: PLC0415 - Windows uniquement

        for frequency, duration_ms in notes:
            winsound.Beep(frequency, duration_ms)
    except Exception:  # pas de son disponible : on s'en passe
        log.debug("son impossible")


def cue(kind: Cue) -> None:
    threading.Thread(target=_play, args=(_NOTES[kind],), name="son", daemon=True).start()
