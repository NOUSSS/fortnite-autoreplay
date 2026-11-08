"""Point d'entrée de l'exécutable « Fortnite AutoReplay.exe ».

Sans commande, il ouvre le panneau ; les options seules (``--dry-run``) vont
au panneau, et toute commande de la CLI reste possible (``window``, ``check``…).
"""

from __future__ import annotations

import sys

from fortnite_auto_replay.__main__ import run

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        args = ["panel", *args]
    run(args)
