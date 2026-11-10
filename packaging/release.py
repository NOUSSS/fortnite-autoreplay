"""Publication d'une version : ``python packaging/release.py <commande>``.

``keygen``
    Crée la clé de signature des mises à jour, une seule fois. La clé privée
    est chiffrée par DPAPI dans ``%USERPROFILE%\\.secrets\\fortnite-autoreplay-release.dpapi``
    (format de ``secret.ps1`` : lisible par ce compte Windows sur ce PC
    seulement) ; la clé publique s'affiche, à inscrire dans ``updater.PUBLIC_KEYS``.
    Sans cette clé privée, aucune version publiée ne peut plus être installée
    par les applications déjà distribuées.

``export-key <fichier>``
    Sauvegarde la clé privée dans ``<fichier>``, chiffrée par une phrase de
    passe tapée deux fois au clavier (scrypt, puis AES-256-GCM), à garder hors
    de ce PC. Le fichier écrit est relu et déchiffré avant de rendre la main.

``import-key <fichier>``
    Remet une sauvegarde sous DPAPI (Windows réinstallé, autre PC), s'il n'y a
    pas déjà de clé.

``site``
    Construit le site seul dans ``dist/site/`` (landing), avec la version et
    le zip déjà présents dans ``dist/`` s'il y en a : pour travailler la page.

``build``
    Construit le zip (``packaging/build.py``), le manifeste signé ``latest.json``
    et le site, le tout dans ``dist/site/`` tel qu'il sera servi.

``keygen``, ``export-key``, ``import-key`` et ``build`` prennent ``--key
<fichier.dpapi>`` pour une autre clé que celle par défaut (changement de clé,
README). Aucune commande n'affiche ni n'écrit la clé privée en clair.

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
import unicodedata
from ctypes import wintypes
from getpass import getpass
from pathlib import Path
from typing import Any, NamedTuple

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
# sauvegarde de la clé : scrypt (128 Mio, environ 0,3 s par essai) puis AES-256-GCM
BACKUP_FORMAT = "fortnite-autoreplay-release-key/1"
SCRYPT_N = 2**17
SCRYPT_N_RANGE = (2**14, 2**18)  # accepté à la lecture
SCRYPT_R, SCRYPT_P = 8, 1
PASSPHRASE_MIN = 12
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
def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _public_of(private_raw: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PrivateKey,
    )

    return Ed25519PrivateKey.from_private_bytes(private_raw).public_key().public_bytes_raw()


def load_private_key(key_file: Path) -> bytes:
    """Les 32 octets de la clé privée gardée par DPAPI dans ``key_file``."""
    if not key_file.exists():
        raise SystemExit(
            f"Clé de signature absente ({key_file}) : lancer « release.py keygen », "
            "ou « release.py import-key » depuis une sauvegarde."
        )
    try:
        private_raw = base64.b64decode(load_secret(key_file), validate=True)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Clé de signature illisible ({key_file}) : {exc}") from exc
    if len(private_raw) != 32:
        raise SystemExit(f"Clé de signature illisible ({key_file}) : pas une clé Ed25519.")
    return private_raw


def describe_public(private_raw: bytes) -> str:
    public = _b64(_public_of(private_raw))
    known = "inscrite" if public in updater.PUBLIC_KEYS else "absente de"
    return f"Clé publique : {public} ({known} updater.PUBLIC_KEYS)"


def cmd_keygen(args: argparse.Namespace) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PrivateKey,
    )

    key_file: Path = args.key
    if key_file.exists():
        print(f"La clé existe déjà : {key_file}. Rien n'est remplacé.")
        return 1
    key = Ed25519PrivateKey.generate()
    store_secret(key_file, _b64(key.private_bytes_raw()))
    public = _b64(key.public_key().public_bytes_raw())
    print(f"Clé privée chiffrée dans {key_file}")
    print(f"Clé publique, à inscrire dans updater.PUBLIC_KEYS : {public}")
    print("À sauvegarder hors de ce PC : « release.py export-key <fichier> » (README).")
    return 0


def sign(manifest: dict[str, object], key_file: Path | None = None) -> dict[str, object]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PrivateKey,
    )

    key = Ed25519PrivateKey.from_private_bytes(load_private_key(key_file or KEY_FILE))
    signature = _b64(key.sign(updater.canonical(manifest)))
    return {**manifest, "signature": signature}


# --- sauvegarde de la clé ------------------------------------------------------------
class Backup(NamedTuple):
    """Sauvegarde lue et contrôlée, pas encore déchiffrée."""

    header: dict[str, Any]  # tout sauf ``ciphertext`` : authentifié par AES-GCM
    n: int
    salt: bytes
    nonce: bytes
    public: bytes
    sealed: bytes


def _derive(passphrase: str, salt: bytes, n: int) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt  # noqa: PLC0415

    secret = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    return Scrypt(salt=salt, length=32, n=n, r=SCRYPT_R, p=SCRYPT_P).derive(secret)


def seal_key(private_raw: bytes, passphrase: str) -> bytes:
    """Fichier de sauvegarde : JSON dont seul ``ciphertext`` est secret.

    La clé AES-256-GCM vient de la phrase de passe par scrypt ; le reste du
    fichier (paramètres, clé publique) est authentifié avec le chiffré, si bien
    qu'un fichier modifié ne se déchiffre plus.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    salt, nonce = os.urandom(16), os.urandom(12)
    header: dict[str, Any] = {
        "format": BACKUP_FORMAT,
        "kdf": "scrypt",
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "public_key": _b64(_public_of(private_raw)),
    }
    cipher = AESGCM(_derive(passphrase, salt, SCRYPT_N))
    sealed = cipher.encrypt(nonce, private_raw, updater.canonical(header))
    return (json.dumps({**header, "ciphertext": _b64(sealed)}, indent=2) + "\n").encode("ascii")


def _field(doc: dict[str, Any], name: str, size: int) -> bytes:
    value = doc.get(name)
    if not isinstance(value, str):
        raise ValueError(name)
    data = base64.b64decode(value, validate=True)
    if len(data) != size:
        raise ValueError(name)
    return data


def read_backup(data: bytes) -> Backup:
    """Contrôle la forme d'une sauvegarde de :func:`seal_key`, sans phrase de passe."""
    try:
        doc = json.loads(data.decode("utf-8"))
        if not isinstance(doc, dict) or doc.get("format") != BACKUP_FORMAT:
            raise ValueError("format")
        n = doc.get("n")
        if type(n) is not int or n & (n - 1) or not SCRYPT_N_RANGE[0] <= n <= SCRYPT_N_RANGE[1]:
            raise ValueError("n")
        if doc.get("kdf") != "scrypt" or (doc.get("r"), doc.get("p")) != (SCRYPT_R, SCRYPT_P):
            raise ValueError("kdf")
        return Backup(
            header={k: v for k, v in doc.items() if k != "ciphertext"},
            n=n,
            salt=_field(doc, "salt", 16),
            nonce=_field(doc, "nonce", 12),
            public=_field(doc, "public_key", 32),
            sealed=_field(doc, "ciphertext", 32 + 16),
        )
    except ValueError as exc:  # JSON, UTF-8 et base64 compris
        message = "Ce fichier n'est pas une sauvegarde de « release.py export-key »."
        raise SystemExit(message) from exc


def open_key(backup: Backup, passphrase: str) -> bytes:
    """Les 32 octets de la clé privée ; ``SystemExit`` si la phrase est fausse."""
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    cipher = AESGCM(_derive(passphrase, backup.salt, backup.n))
    try:
        private_raw = cipher.decrypt(backup.nonce, backup.sealed, updater.canonical(backup.header))
    except InvalidTag:
        raise SystemExit("Phrase de passe fausse, ou sauvegarde modifiée.") from None
    if _public_of(private_raw) != backup.public:
        raise SystemExit("Sauvegarde incohérente : la clé ne correspond pas à sa clé publique.")
    return private_raw


def ask_new_passphrase() -> str:
    first = getpass("Phrase de passe de la sauvegarde : ")
    if len(unicodedata.normalize("NFC", first)) < PASSPHRASE_MIN:
        raise SystemExit(f"Phrase de passe trop courte : au moins {PASSPHRASE_MIN} caractères.")
    if getpass("La même, une seconde fois : ") != first:
        raise SystemExit("Les deux phrases de passe diffèrent : rien n'est écrit.")
    return first


def cmd_export_key(args: argparse.Namespace) -> int:
    target: Path = args.file
    if target.exists():
        raise SystemExit(f"{target} existe déjà : rien n'est remplacé.")
    private_raw = load_private_key(args.key)
    passphrase = ask_new_passphrase()
    data = seal_key(private_raw, passphrase)
    try:
        handle = target.open("xb")
    except OSError as exc:
        raise SystemExit(f"Sauvegarde impossible : {exc}") from exc
    try:
        with handle:
            handle.write(data)
        # relue depuis le disque : la sauvegarde doit redonner la clé
        if open_key(read_backup(target.read_bytes()), passphrase) != private_raw:
            raise SystemExit("La sauvegarde relue ne redonne pas la clé.")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    print(f"Clé sauvegardée dans {target}, relue et déchiffrée avec la phrase de passe.")
    print(describe_public(private_raw))
    print("Gardez ce fichier hors de ce PC, et la phrase de passe ailleurs que lui.")
    return 0


def cmd_import_key(args: argparse.Namespace) -> int:
    key_file: Path = args.key
    if key_file.exists():
        print(f"La clé existe déjà : {key_file}. Rien n'est remplacé.")
        return 1
    try:
        backup = read_backup(args.file.read_bytes())
    except OSError as exc:
        raise SystemExit(f"Sauvegarde illisible : {exc}") from exc
    private_raw = open_key(backup, getpass("Phrase de passe de la sauvegarde : "))
    try:
        store_secret(key_file, _b64(private_raw))
        if load_private_key(key_file) != private_raw:
            raise SystemExit("La clé relue sous DPAPI diffère de la sauvegarde.")
    except BaseException:
        key_file.unlink(missing_ok=True)
        raise
    print(f"Clé importée dans {key_file}, chiffrée par DPAPI pour ce compte Windows.")
    print(describe_public(private_raw))
    return 0


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


def verify_for_installed(raw: bytes) -> None:
    """Le manifeste doit passer la première clé de ``updater.PUBLIC_KEYS``.

    C'est celle que connaissent les applications déjà distribuées ; une clé
    suivante, ajoutée le temps d'un changement de clé, ne leur est pas encore
    connue et ne suffit donc pas.
    """
    try:
        updater.verify_manifest(raw, updater.public_keys()[:1])
    except updater.UpdateError as exc:
        raise SystemExit(
            f"Manifeste refusé par la première clé de updater.PUBLIC_KEYS ({exc}) : "
            "signé par une autre clé (--key) ?"
        ) from exc


def cmd_build(args: argparse.Namespace) -> int:
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
        },
        args.key,
    )
    raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    verify_for_installed(raw)
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
    key = argparse.ArgumentParser(add_help=False)
    key.add_argument(
        "--key", type=Path, default=KEY_FILE, help="clé privée sous DPAPI (défaut : %(default)s)"
    )
    backup = argparse.ArgumentParser(add_help=False)
    backup.add_argument("file", type=Path, help="fichier de sauvegarde, chiffré")
    for name, func, parents in (
        ("keygen", cmd_keygen, [key]),
        ("export-key", cmd_export_key, [key, backup]),
        ("import-key", cmd_import_key, [key, backup]),
        ("site", cmd_site, []),
        ("build", cmd_build, [key]),
        ("publish", cmd_publish, []),
    ):
        sub.add_parser(name, parents=parents).set_defaults(func=func)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
