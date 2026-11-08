# Spec PyInstaller de « Fortnite AutoReplay.exe » (dossier, sans console).
# Lancé par packaging/build.py, qui ajoute ensuite config/ et templates/
# à côté de l'exécutable et fait le zip.
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

NAME = "Fortnite AutoReplay"
ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
VERSION = re.search(
    r'__version__ = "([^"]+)"', (SRC / "fortnite_auto_replay" / "__init__.py").read_text("utf-8")
).group(1)
NUMBERS = (*(int(part) for part in VERSION.split(".")), 0, 0, 0, 0)[:4]

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040C04B0",
                    [
                        StringStruct("FileDescription", NAME),
                        StringStruct("ProductName", NAME),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("ProductVersion", VERSION),
                        StringStruct("InternalName", NAME),
                        StringStruct("OriginalFilename", f"{NAME}.exe"),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
    ],
)

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(SRC)],
    # repli de dernier recours des clics, absent des dépendances
    excludes=["pyautogui", "pytest", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon="NONE",
    upx=False,
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
