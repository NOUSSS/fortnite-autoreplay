"""Point d'entrée ``python -m fortnite_auto_replay``.

Sortie dure via :func:`os._exit` : DXGI/COM (dxcam) plante parfois dans les
destructeurs comtypes pendant l'arrêt de l'interpréteur, ce qui donnerait un
code de retour non nul alors que le bot s'est arrêté proprement.
"""

from __future__ import annotations

import logging
import os
import sys

from .cli import main

if __name__ == "__main__":
    code = main()
    logging.getLogger("fortnite_auto_replay").info("sortie (code %d)", code)
    logging.shutdown()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)
