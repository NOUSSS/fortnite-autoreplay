"""Construit l'application à distribuer : ``python packaging/build.py``.

1. PyInstaller construit ``dist/Fortnite AutoReplay/`` (exécutable et ``_internal``).
2. ``config/default.json``, ``templates/`` et ``Lisez-moi.txt`` sont posés à côté,
   avec le lecteur de replays (``replay-reader/``, voir ``build_reader.py``).
3. Le tout est zippé dans ``dist/FortniteAutoReplay-<version>.zip``, sous un
   dossier « Fortnite AutoReplay » : extraire le zip donne l'application prête.

Le zip est fait juste après la construction, avant tout lancement : il ne
contient ni logs, ni captures, ni ``config/local.json``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import build_reader

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
NAME = "Fortnite AutoReplay"


def read_version() -> str:
    source = (ROOT / "src" / "fortnite_auto_replay" / "__init__.py").read_text("utf-8")
    match = re.search(r'__version__ = "([^"]+)"', source)
    if match is None:
        raise SystemExit("__version__ introuvable dans fortnite_auto_replay/__init__.py")
    return match.group(1)


def build_app() -> Path:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(ROOT / "build"),
            str(PACKAGING / "fortnite-autoreplay.spec"),
        ],
        check=True,
    )
    return DIST / NAME


def add_files(app: Path, version: str) -> None:
    (app / "config").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "config" / "default.json", app / "config" / "default.json")
    shutil.copytree(
        ROOT / "templates",
        app / "templates",
        ignore=shutil.ignore_patterns("README.md"),
        dirs_exist_ok=True,
    )
    build_reader.publish(app / "replay-reader")
    readme = (PACKAGING / "Lisez-moi.txt").read_text("utf-8").replace("{version}", version)
    # BOM : le Bloc-notes reconnaît l'UTF-8 à coup sûr
    (app / "Lisez-moi.txt").write_text(readme, encoding="utf-8-sig", newline="\r\n")


def make_zip(app: Path, version: str) -> Path:
    target = DIST / f"FortniteAutoReplay-{version}.zip"
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(app.rglob("*")):
            archive.write(path, Path(NAME) / path.relative_to(app))
    return target


def main() -> int:
    version = read_version()
    app = build_app()
    add_files(app, version)
    archive = make_zip(app, version)
    size_mb = archive.stat().st_size / 1_000_000
    print(f"{archive} ({size_mb:.1f} Mo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
