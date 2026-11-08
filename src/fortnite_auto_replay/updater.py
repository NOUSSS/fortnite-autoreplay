"""Mises à jour : manifeste signé, archive vérifiée, remplacement en place.

Le manifeste ``latest.json``, servi avec la landing, décrit la dernière version ::

    {"version": "0.2.0", "date": "2026-11-08", "notes": ["…"], "size": 78000000,
     "sha256": "…", "url": "https://…/FortniteAutoReplay-0.2.0.zip", "signature": "…"}

La signature Ed25519 couvre le manifeste sans ``signature`` (JSON canonique :
clés triées, sans espaces, UTF-8). La clé privée reste sur la machine qui
publie (``packaging/release.py``), la clé publique est inscrite ici. Un
manifeste mal signé, une archive dont la taille ou l'empreinte diffère, ou une
version qui n'est pas plus récente que celle qui tourne, sont refusés : un
hébergement compromis ne peut ni pousser un autre programme ni faire revenir
à une version ancienne.

Installation, depuis l'exécutable empaqueté seulement : l'archive est extraite
dans ``.update/`` pendant que le bot tourne ; une fois le panneau fermé et le
moteur arrêté, chaque fichier du programme (exécutable, ``_internal``,
``templates``, ``config/default.json``, Lisez-moi) passe dans ``.update/old`` et
celui de la nouvelle version prend sa place. Windows permet de renommer les
fichiers d'un programme en cours d'exécution, pas de les écraser : aucun
script annexe n'est nécessaire. Au moindre échec, tout est remis en place.
Réglages (``config/local.json``), logs et captures ne sont jamais touchés. La
nouvelle version efface ``.update/`` à son démarrage.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from typing_extensions import Self

from . import __version__

log = logging.getLogger(__name__)

# clé publique Ed25519 des versions publiées (packaging/release.py keygen)
PUBLIC_KEY = "zhSlfsXnF06BYo0q5PaVPVTf2OZn3FgTYkEuWKhmz5Q="

APP_NAME = "Fortnite AutoReplay"
EXE_NAME = f"{APP_NAME}.exe"
UPDATE_DIR = ".update"
# jamais remplacés par une mise à jour : réglages et données de l'utilisateur
USER_PATHS = frozenset({"config/local.json", "logs", "captures", UPDATE_DIR})
MANIFEST_MAX_BYTES = 64 * 1024
CHUNK = 256 * 1024
_VERSION = re.compile(r"\d+(\.\d+){0,3}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class UpdateError(Exception):
    """Manifeste invalide, téléchargement ou installation impossible."""


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int
    date: str
    notes: tuple[str, ...]


# --- versions et manifeste ------------------------------------------------------
def parse_version(text: str) -> tuple[int, ...]:
    """« 0.2.0 » → (0, 2, 0) ; lève :class:`UpdateError` pour toute autre forme."""
    if not isinstance(text, str) or not _VERSION.fullmatch(text):
        raise UpdateError(f"version invalide\u00a0: {text!r}")
    parts = tuple(int(part) for part in text.split("."))
    return parts + (0,) * (4 - len(parts))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def canonical(payload: dict[str, Any]) -> bytes:
    """Octets signés : le manifeste sans sa signature, sous une forme unique."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def public_key_bytes(text: str = PUBLIC_KEY) -> bytes:
    try:
        key = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UpdateError("clé publique des mises à jour absente ou invalide") from exc
    if len(key) != 32:
        raise UpdateError("clé publique des mises à jour absente ou invalide")
    return key


def _check_url(url: object, what: str) -> str:
    """https, ou http vers la machine elle-même (essais) : le reste est refusé."""
    if not isinstance(url, str):
        raise UpdateError(f"{what}\u00a0: adresse manquante")
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname in _LOOPBACK:
        return url
    raise UpdateError(f"{what}\u00a0: adresse refusée ({url})")


def verify_manifest(raw: bytes, public_key: bytes) -> Release:
    """Vérifie la signature puis la forme du manifeste ; lève :class:`UpdateError`."""
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PublicKey,
    )

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("manifeste illisible") from exc
    if not isinstance(data, dict) or not isinstance(data.get("signature"), str):
        raise UpdateError("manifeste sans signature")
    try:
        signature = base64.b64decode(data.pop("signature"), validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical(data))
    except (binascii.Error, ValueError, InvalidSignature) as exc:
        raise UpdateError("signature du manifeste invalide") from exc

    version, sha256, size = data.get("version"), data.get("sha256"), data.get("size")
    notes = data.get("notes", [])
    parse_version(version)  # type: ignore[arg-type]
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise UpdateError("empreinte de l\u2019archive invalide")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise UpdateError("taille de l\u2019archive invalide")
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise UpdateError("notes de version invalides")
    date = data.get("date", "")
    return Release(
        version=version,  # type: ignore[arg-type]
        url=_check_url(data.get("url"), "archive"),
        sha256=sha256,
        size=size,
        date=date if isinstance(date, str) else "",
        notes=tuple(notes),
    )


# --- réseau ---------------------------------------------------------------------
class Response(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> object: ...


Opener = Callable[[str, float], Response]
USER_AGENT = f"FortniteAutoReplay/{__version__}"


def _open(url: str, timeout: float) -> Response:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)  # type: ignore[no-any-return]


def check(
    manifest_url: str,
    current: str = __version__,
    *,
    public_key: bytes | None = None,
    opener: Opener = _open,
    timeout: float = 15.0,
) -> Release | None:
    """La version publiée si elle est plus récente que ``current``, sinon ``None``."""
    key = public_key if public_key is not None else public_key_bytes()
    url = _check_url(manifest_url, "manifeste")
    try:
        with opener(url, timeout) as response:
            raw = response.read(MANIFEST_MAX_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"manifeste injoignable ({exc})") from exc
    if len(raw) > MANIFEST_MAX_BYTES:
        raise UpdateError("manifeste trop gros")
    release = verify_manifest(raw, key)
    return release if is_newer(release.version, current) else None


def download(
    release: Release,
    target: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    opener: Opener = _open,
    timeout: float = 30.0,
) -> Path:
    """Télécharge l'archive dans ``target``, taille et empreinte vérifiées."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(release.url, timeout) as response, partial.open("wb") as handle:
            while chunk := response.read(CHUNK):
                received += len(chunk)
                if received > release.size:
                    raise UpdateError("archive plus grosse qu\u2019annoncé")
                digest.update(chunk)
                handle.write(chunk)
                if progress is not None:
                    progress(received, release.size)
    except (OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"téléchargement interrompu ({exc})") from exc
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    if received != release.size or digest.hexdigest() != release.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("archive altérée\u00a0: taille ou empreinte différente du manifeste")
    partial.replace(target)
    return target


# --- installation ---------------------------------------------------------------
def extract(archive: Path, destination: Path) -> Path:
    """Extrait l'archive dans ``destination`` ; rend le dossier de l'application."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                name = PurePosixPath(member.filename)
                if name.is_absolute() or ".." in name.parts or ":" in member.filename:
                    raise UpdateError(f"chemin refusé dans l\u2019archive\u00a0: {member.filename}")
            bundle.extractall(destination)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError(f"archive illisible ({exc})") from exc
    package = destination / APP_NAME
    if not (package / EXE_NAME).is_file() or not (package / "_internal").is_dir():
        raise UpdateError("archive incomplète\u00a0: exécutable ou _internal absent")
    return package


def program_files(package: Path) -> list[str]:
    """Chemins relatifs (``/``) à remplacer : le contenu de la nouvelle version.

    Le dossier ``config`` est pris fichier par fichier, pour garder ``local.json``.
    """
    entries: list[str] = []
    for item in sorted(package.iterdir()):
        if item.name == "config" and item.is_dir():
            entries += [f"config/{f.name}" for f in sorted(item.iterdir()) if f.is_file()]
        else:
            entries.append(item.name)
    return [entry for entry in entries if entry not in USER_PATHS]


def install(package: Path, root: Path) -> None:
    """Remplace le programme de ``root`` par ``package`` ; tout ou rien.

    À n'appeler qu'une fois le moteur arrêté et juste avant de quitter : le
    processus qui tourne lit encore ses modules dans ``_internal``.
    """
    backup = root / UPDATE_DIR / "old"
    if backup.exists():
        shutil.rmtree(backup)
    done: list[tuple[Path, Path]] = []  # (où il est, où il était), pour tout remettre
    try:
        for relative in program_files(package):
            current, saved, new = root / relative, backup / relative, package / relative
            if current.exists():
                saved.parent.mkdir(parents=True, exist_ok=True)
                current.rename(saved)
                done.append((saved, current))
            current.parent.mkdir(parents=True, exist_ok=True)
            new.rename(current)
            done.append((current, new))
    except OSError as exc:
        for where, origin in reversed(done):
            try:
                where.rename(origin)
            except OSError:
                log.exception("mise à jour : %s non remis en place", origin)
        raise UpdateError(f"installation impossible ({exc})") from exc


def relaunch(root: Path, args: list[str]) -> None:
    """Lance la version installée, détachée de ce processus qui va quitter."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen([str(root / EXE_NAME), *args], cwd=root, creationflags=flags, close_fds=True)


def cleanup(root: Path, *, attempts: int = 20, delay_s: float = 0.5) -> bool:
    """Efface ``.update/`` (ancienne version, archive), le temps que l'ancien processus quitte."""
    folder = root / UPDATE_DIR
    for _ in range(attempts):
        if not folder.exists():
            return True
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            log.info("mise à jour : fichiers de l'ancienne version effacés")
            return True
        time.sleep(delay_s)
    log.warning("mise à jour : %s non effacé, nouvel essai au prochain lancement", folder)
    return False


def frozen_root() -> Path | None:
    """Dossier de l'exécutable empaqueté ; ``None`` depuis les sources (mise à jour par git)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


# --- suivi pour le panneau -------------------------------------------------------
UpdatePhase = Literal["idle", "available", "downloading", "ready", "failed"]


@dataclass(frozen=True, slots=True)
class UpdateState:
    phase: UpdatePhase = "idle"
    version: str = ""
    progress: float = 0.0  # de 0 à 1 pendant le téléchargement
    error: str = ""


class Updates:
    """Vérification en arrière-plan, puis téléchargement et préparation sur demande.

    Le panneau lit :meth:`state` à chaque tour ; l'installation elle-même se fait
    après sa fermeture (:meth:`install_pending`).
    """

    def __init__(
        self,
        root: Path,
        manifest_url: str,
        *,
        current: str = __version__,
        interval_s: float = 6 * 3600,
        checker: Callable[[], Release | None] | None = None,
        fetcher: Callable[[Release, Path, Callable[[int, int], None]], Path] | None = None,
        relaunch_args: Sequence[str] = (),
    ) -> None:
        self.root = root
        # options du lancement d'origine (--dry-run, --config…), reprises à la relance
        self.relaunch_args = list(relaunch_args)
        self.manifest_url = manifest_url
        self.current = current
        self.interval_s = interval_s
        self._check = checker or (lambda: check(self.manifest_url, self.current))
        self._fetch = fetcher or (lambda r, t, p: download(r, t, progress=p))
        self._lock = threading.Lock()
        self._state = UpdateState()
        self._release: Release | None = None
        self._package: Path | None = None
        self._stop = threading.Event()
        self.install_requested = False
        self.resume = False  # relancer le bot sans pause s'il tournait

    # lecture
    def state(self) -> UpdateState:
        with self._lock:
            return self._state

    def _set(self, **changes: Any) -> None:
        with self._lock:
            values = {
                "phase": self._state.phase,
                "version": self._state.version,
                "progress": self._state.progress,
                "error": self._state.error,
            }
            values.update(changes)
            self._state = UpdateState(**values)

    # vérification
    def start(self) -> None:
        threading.Thread(target=self._watch, name="updates", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        cleanup(self.root)
        while not self._stop.is_set():
            if self.state().phase in ("idle", "failed", "available"):
                self.check_now()
            if self._stop.wait(self.interval_s):
                return

    def check_now(self) -> None:
        try:
            release = self._check()
        except UpdateError as exc:
            log.info("mise à jour : vérification impossible (%s)", exc)
            return
        if release is None:
            log.info("mise à jour : version %s à jour", self.current)
            return
        with self._lock:
            if self._state.phase in ("downloading", "ready"):
                return
            self._release = release
            self._state = UpdateState(phase="available", version=release.version)
        log.info("mise à jour : version %s disponible", release.version)

    # téléchargement
    def download(self) -> None:
        """Lance le téléchargement de la version annoncée (rappel du bouton du panneau)."""
        with self._lock:
            release = self._release
            if release is None or self._state.phase not in ("available", "failed"):
                return
            self._state = UpdateState(phase="downloading", version=release.version)
        threading.Thread(target=self._prepare, args=(release,), name="update", daemon=True).start()

    def _prepare(self, release: Release) -> None:
        folder = self.root / UPDATE_DIR / release.version
        try:
            archive = self._fetch(
                release,
                folder / f"FortniteAutoReplay-{release.version}.zip",
                lambda done, total: self._set(progress=done / total if total else 0.0),
            )
            package = extract(archive, folder / "new")
        except UpdateError as exc:
            log.warning("mise à jour : %s", exc)
            self._set(phase="failed", error=str(exc))
            return
        except Exception as exc:  # disque plein, droits : le panneau doit le dire
            log.exception("mise à jour : préparation interrompue")
            self._set(phase="failed", error=str(exc))
            return
        with self._lock:
            self._package = package
            self._state = UpdateState(phase="ready", version=release.version, progress=1.0)
        log.info("mise à jour : version %s prête à installer", release.version)

    # installation, après la fermeture du panneau
    def request_install(self, *, resume: bool) -> None:
        self.install_requested = True
        self.resume = resume

    def install_pending(self) -> bool:
        """Installe la version préparée et relance ; faux si rien n'était demandé."""
        with self._lock:
            package = self._package if self.install_requested else None
            version = self._state.version
        if package is None:
            return False
        install(package, self.root)
        log.info("mise à jour : version %s installée, relance", version)
        relaunch(self.root, [*self.relaunch_args, *(["--no-pause"] if self.resume else [])])
        return True
