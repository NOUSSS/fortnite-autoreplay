"""Construit le lecteur de replays : ``python packaging/build_reader.py``.

Publie ``replay-reader/`` (C#, .NET 10) en un seul exécutable autonome,
sans .NET à installer chez le joueur. Sans argument, il est posé dans
``replay-reader/replay-reader.exe``, là où l'application le cherche depuis les
sources ; ``packaging/build.py`` le pose dans l'application à distribuer.

Le SDK .NET 10 est cherché dans ``DOTNET``, puis dans le ``PATH``, puis dans
l'installation par utilisateur (``%LOCALAPPDATA%\\Microsoft\\dotnet``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "replay-reader" / "ReplayReader.csproj"
EXE = "replay-reader.exe"


def find_dotnet() -> str:
    candidates = [os.environ.get("DOTNET"), shutil.which("dotnet")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "Microsoft" / "dotnet" / "dotnet.exe"))
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        sdks = subprocess.run(
            [candidate, "--list-sdks"], capture_output=True, text=True, check=False
        ).stdout
        if any(line.startswith("10.") for line in sdks.splitlines()):
            return candidate
    raise SystemExit("SDK .NET 10 introuvable (https://dotnet.microsoft.com/download/dotnet/10.0)")


def publish(target: Path) -> Path:
    """Publie le lecteur dans ``target`` ; chemin de l'exécutable."""
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            [
                find_dotnet(),
                "publish",
                str(PROJECT),
                "-c",
                "Release",
                "-r",
                "win-x64",
                "--self-contained",
                "true",
                "-p:PublishSingleFile=true",
                "-p:IncludeNativeLibrariesForSelfExtract=true",
                "-p:EnableCompressionInSingleFile=true",
                "-p:DebugType=none",
                "-o",
                out,
            ],
            check=True,
            env=env,
        )
        target.mkdir(parents=True, exist_ok=True)
        exe = target / EXE
        shutil.copy2(Path(out) / EXE, exe)
    version = subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=True)
    print(f"{exe} (analyse {version.stdout.strip()})")
    return exe


def main() -> int:
    publish(ROOT / "replay-reader")
    return 0


if __name__ == "__main__":
    sys.exit(main())
