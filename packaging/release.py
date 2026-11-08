"""Publication d'une version : ``python packaging/release.py <commande>``.

``keygen``
    Crée la clé de signature des mises à jour, une seule fois. La clé privée
    est chiffrée par DPAPI dans ``%USERPROFILE%\\.secrets\\fortnite-autoreplay-release.dpapi``
    (format de ``secret.ps1`` : lisible par ce compte Windows sur ce PC
    seulement) ; la clé publique s'affiche, à inscrire dans ``updater.PUBLIC_KEY``.
    Sans cette clé privée, aucune version publiée ne peut plus être installée
    par les applications déjà distribuées.

``site``
    Construit le site seul dans ``dist/site/`` (landing), avec la version et
    le zip déjà présents dans ``dist/`` s'il y en a : pour travailler la page.

``build``
    Construit le zip (``packaging/build.py``), le manifeste signé ``latest.json``
    et le site, le tout dans ``dist/site/`` tel qu'il sera servi.

``publish``
    Envoie ``dist/site/`` sur le VPS (``ssh coolify``) : l'archive d'abord, la
    page ensuite, le manifeste en dernier (une application ne voit jamais un
    manifeste dont l'archive manque encore).
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packaging"))

import build  # noqa: E402
from fortnite_auto_replay import updater  # noqa: E402

SITE_URL = "https://autoreplay.feitanx.com"
SOURCE_URL = "https://github.com/NOUSSS/fortnite-autoreplay"
SITE_SRC = ROOT / "site"
SITE_OUT = build.DIST / "site"
CHANGELOG = ROOT / "CHANGELOG.md"
SECRET = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".secrets"
KEY_FILE = SECRET / "fortnite-autoreplay-release.dpapi"
SSH_HOST = "coolify"
REMOTE_DIR = "/data/autoreplay"
MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)  # fmt: skip
NBSP = "\u00a0"


# --- DPAPI (même format que secret.ps1 : ConvertFrom-SecureString) ------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(function: str, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    call = getattr(crypt32, function)
    call.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]  # fmt: skip
    call.restype = wintypes.BOOL
    if not call(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), f"{function} a échoué")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def store_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = _crypt("CryptProtectData", value.encode("utf-16-le"))
    path.write_text(blob.hex(), encoding="ascii")


def load_secret(path: Path) -> str:
    blob = bytes.fromhex(path.read_text(encoding="ascii").strip())
    return _crypt("CryptUnprotectData", blob).decode("utf-16-le")


# --- clé et signature -------------------------------------------------------------
def cmd_keygen(_args: argparse.Namespace) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PrivateKey,
    )

    if KEY_FILE.exists():
        print(f"La clé existe déjà : {KEY_FILE}. Rien n'est remplacé.")
        return 1
    key = Ed25519PrivateKey.generate()
    store_secret(KEY_FILE, base64.b64encode(key.private_bytes_raw()).decode("ascii"))
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
    print(f"Clé privée chiffrée dans {KEY_FILE}")
    print(f"Clé publique, à inscrire dans updater.PUBLIC_KEY : {public}")
    return 0


def sign(manifest: dict[str, object]) -> dict[str, object]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PrivateKey,
    )

    if not KEY_FILE.exists():
        raise SystemExit(f"Clé de signature absente ({KEY_FILE}) : lancer « release.py keygen ».")
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(load_secret(KEY_FILE)))
    signature = base64.b64encode(key.sign(updater.canonical(manifest))).decode("ascii")
    return {**manifest, "signature": signature}


# --- notes de version --------------------------------------------------------------
def french_spaces(text: str) -> str:
    """Espaces insécables du français : avant « : ; ? ! » et à l'intérieur des guillemets."""
    text = re.sub(r" +([:;?!\u00bb])", NBSP + r"\1", text)
    return re.sub(r"(\u00ab) +", r"\1" + NBSP, text)


def read_changelog() -> list[tuple[str, str, list[str]]]:
    """``CHANGELOG.md`` : [(version, date ISO, [notes])], de la plus récente à la plus ancienne."""
    releases: list[tuple[str, str, list[str]]] = []
    for line in CHANGELOG.read_text("utf-8").splitlines():
        heading = re.fullmatch(r"## (\d+(?:\.\d+)*) \((\d{4}-\d{2}-\d{2})\)", line.strip())
        if heading:
            releases.append((heading.group(1), heading.group(2), []))
        elif line.startswith("- ") and releases:
            releases[-1][2].append(french_spaces(line[2:].strip()))
        elif line.startswith("  ") and releases and releases[-1][2]:
            note = releases[-1][2][-1] + " " + line.strip()  # suite d'une note sur plusieurs lignes
            releases[-1][2][-1] = french_spaces(note)
    return releases


def long_date(iso: str) -> str:
    day = dt.date.fromisoformat(iso)
    return f"{day.day}{NBSP}{MONTHS[day.month - 1]} {day.year}"


def size_text(size: int) -> str:
    return f"{round(size / 1_000_000)}{NBSP}Mo"


# --- site ------------------------------------------------------------------------
def changelog_html(releases: list[tuple[str, str, list[str]]]) -> str:
    blocks = []
    for version, date, notes in releases:
        when = long_date(date)
        items = "\n".join(f"          <li>{html.escape(note, quote=False)}</li>" for note in notes)
        blocks.append(
            f"""      <article class="release">
        <h3 class="release-title">Version {version}<time datetime="{date}">{when}</time></h3>
        <ul class="release-notes">
{items}
        </ul>
      </article>"""
        )
    return "\n".join(blocks)


def render_site(version: str, archive_name: str, size: int, date: str) -> Path:
    """Copie ``site/`` dans ``dist/site/`` et remplit ``index.html``."""
    if SITE_OUT.exists():
        releases = SITE_OUT / "releases"
        keep = releases if releases.exists() else None
        for item in SITE_OUT.iterdir():
            if item != keep:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
    shutil.copytree(SITE_SRC, SITE_OUT, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.md"))
    styles = (SITE_SRC / "styles.css").read_bytes()
    values = {
        "version": version,
        "download_url": f"releases/{archive_name}",
        "size": size_text(size),
        "date": long_date(date),
        "source_url": SOURCE_URL,
        "site_url": SITE_URL,
        "styles_version": hashlib.sha256(styles).hexdigest()[:10],
        "changelog": changelog_html(read_changelog()),
    }
    page = (SITE_SRC / "index.html").read_text("utf-8")
    for name, value in values.items():
        page = page.replace("{{" + name + "}}", value)
    left = re.findall(r"\{\{\w+\}\}", page)
    if left:
        raise SystemExit(f"index.html : valeurs manquantes {left}")
    (SITE_OUT / "index.html").write_text(page, encoding="utf-8")
    return SITE_OUT


def current_release() -> tuple[str, str]:
    """Version des sources et date de sa section dans CHANGELOG.md."""
    version = build.read_version()
    for number, date, notes in read_changelog():
        if number == version:
            if not notes:
                raise SystemExit(f"CHANGELOG.md : aucune note pour la version {version}")
            return version, date
    raise SystemExit(f"CHANGELOG.md : ajouter une section « ## {version} (AAAA-MM-JJ) »")


def cmd_site(_args: argparse.Namespace) -> int:
    version, date = current_release()
    archive = build.DIST / f"FortniteAutoReplay-{version}.zip"
    size = archive.stat().st_size if archive.exists() else 75_000_000
    out = render_site(version, archive.name, size, date)
    print(out / "index.html")
    return 0


def cmd_build(_args: argparse.Namespace) -> int:
    version, date = current_release()
    notes = next(n for v, _, n in read_changelog() if v == version)
    app = build.build_app()
    build.add_files(app, version)
    archive = build.make_zip(app, version)
    data = archive.read_bytes()

    out = render_site(version, archive.name, len(data), date)
    releases = out / "releases"
    releases.mkdir(exist_ok=True)
    shutil.copy2(archive, releases / archive.name)
    manifest = sign(
        {
            "version": version,
            "date": date,
            "notes": notes,
            "url": f"{SITE_URL}/releases/{archive.name}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
    )
    raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    # la clé publique inscrite dans l'application doit accepter ce manifeste
    updater.verify_manifest(raw, updater.public_key_bytes())
    (out / "latest.json").write_bytes(raw)
    print(f"{out} : version {version}, {size_text(len(data))}, manifeste signé et vérifié")
    return 0


# --- envoi -------------------------------------------------------------------------
def _send(names: list[str]) -> None:
    """``tar`` local vers ``tar`` distant, à travers ``ssh`` : rien en clair dans une URL."""
    pack = ["tar", "-C", str(SITE_OUT), "-cf", "-", *names]
    with subprocess.Popen(pack, stdout=subprocess.PIPE) as tar:
        subprocess.run(
            ["ssh", SSH_HOST, f"mkdir -p {REMOTE_DIR} && tar -C {REMOTE_DIR} -xf -"],
            stdin=tar.stdout,
            check=True,
        )
    if tar.returncode:
        raise SystemExit(f"tar a échoué ({tar.returncode})")


def cmd_publish(_args: argparse.Namespace) -> int:
    manifest = SITE_OUT / "latest.json"
    if not manifest.exists():
        raise SystemExit("dist/site/latest.json absent : lancer « release.py build » d'abord.")
    version = json.loads(manifest.read_text("utf-8"))["version"]
    page = [p.name for p in SITE_OUT.iterdir() if p.name not in ("releases", "latest.json")]
    _send(["releases"])
    _send(page)
    _send(["latest.json"])
    print(f"Version {version} publiée sur {SITE_URL}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (
        ("keygen", cmd_keygen),
        ("site", cmd_site),
        ("build", cmd_build),
        ("publish", cmd_publish),
    ):
        sub.add_parser(name).set_defaults(func=func)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
