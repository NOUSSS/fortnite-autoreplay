"""Publication : notes de version, page rendue complète, sauvegarde de la clé."""

from __future__ import annotations

import base64
import importlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PASSPHRASE = "phrase de passe d'essai"
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI : Windows seulement")
PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
NBSP = "\u00a0"


@pytest.fixture(scope="module")
def release():
    sys.path.insert(0, str(PACKAGING))
    try:
        yield importlib.import_module("release")
    finally:
        sys.path.remove(str(PACKAGING))


def test_french_spaces(release) -> None:
    text = release.french_spaces("Prêt : oui ; « Jouer » ? non !")
    assert text == f"Prêt{NBSP}: oui{NBSP}; «{NBSP}Jouer{NBSP}»{NBSP}? non{NBSP}!"
    assert release.french_spaces(text) == text  # déjà corrigé : inchangé


def test_changelog_has_the_current_version(release) -> None:
    version, date = release.current_release()
    notes = next(n for v, d, n in release.read_changelog() if v == version)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
    assert notes
    assert all(" :" not in note and "« " not in note for note in notes)


def test_long_date_and_size(release) -> None:
    assert release.long_date("2026-11-08") == f"8{NBSP}novembre 2026"
    assert release.size_text(74_600_000) == f"75{NBSP}Mo"


def test_site_is_fully_rendered(release, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(release, "SITE_OUT", tmp_path / "site")
    version, date = release.current_release()
    out = release.render_site(version, f"FortniteAutoReplay-{version}.zip", 75_000_000, date)
    page = (out / "index.html").read_text("utf-8")
    assert "{{" not in page
    assert f'href="releases/FortniteAutoReplay-{version}.zip"' in page
    assert f"Version {version}" in page
    assert (out / "styles.css").exists()
    assert not list(out.glob("*.md"))


def test_site_keeps_published_archives(release, tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "site"
    monkeypatch.setattr(release, "SITE_OUT", out)
    (out / "releases").mkdir(parents=True)
    (out / "releases" / "FortniteAutoReplay-0.1.0.zip").write_bytes(b"zip")
    (out / "ancien.html").write_text("x", "utf-8")
    version, date = release.current_release()
    release.render_site(version, "a.zip", 1, date)
    assert (out / "releases" / "FortniteAutoReplay-0.1.0.zip").exists()
    assert not (out / "ancien.html").exists()


# --- sauvegarde de la clé ----------------------------------------------------------
def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def typed(*replies: str) -> Callable[[str], str]:
    """Remplace ``getpass`` : rend les phrases dans l'ordre, échoue s'il en manque."""
    queue = list(replies)

    def fake(_prompt: str = "") -> str:
        return queue.pop(0)

    return fake


@pytest.fixture
def dpapi_key(release, tmp_path: Path, monkeypatch) -> tuple[Ed25519PrivateKey, Path]:
    """Clé d'essai sous DPAPI dans ``tmp_path`` ; la vraie clé n'est jamais lue."""
    monkeypatch.setattr(release, "KEY_FILE", tmp_path / "defaut-absente.dpapi")
    monkeypatch.setattr(release, "SCRYPT_N", release.SCRYPT_N_RANGE[0])  # rapide, et accepté
    key = Ed25519PrivateKey.generate()
    source = tmp_path / "source.dpapi"
    release.store_secret(source, b64(key.private_bytes_raw()))
    return key, source


def export(release, monkeypatch, source: Path, target: Path) -> None:
    monkeypatch.setattr(release, "getpass", typed(PASSPHRASE, PASSPHRASE))
    assert release.main(["export-key", str(target), "--key", str(source)]) == 0


def test_seal_then_open(release, monkeypatch) -> None:
    monkeypatch.setattr(release, "SCRYPT_N", release.SCRYPT_N_RANGE[0])
    key = Ed25519PrivateKey.generate()
    sealed = release.seal_key(key.private_bytes_raw(), PASSPHRASE)
    assert b64(key.private_bytes_raw()).encode() not in sealed
    backup = release.read_backup(sealed)
    assert backup.public == key.public_key().public_bytes_raw()
    assert release.open_key(backup, PASSPHRASE) == key.private_bytes_raw()
    with pytest.raises(SystemExit, match="Phrase de passe fausse"):
        release.open_key(backup, PASSPHRASE + " ")


def test_written_backup_uses_strong_scrypt(release) -> None:
    sealed = json.loads(release.seal_key(Ed25519PrivateKey.generate().private_bytes_raw(), "x"))
    assert sealed["n"] == 2**17 and (sealed["r"], sealed["p"]) == (8, 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("public_key", b64(Ed25519PrivateKey.generate().public_key().public_bytes_raw())),
        ("salt", b64(bytes(16))),
        ("label", "ajouté après coup"),
    ],
)
def test_modified_backup_refused(release, monkeypatch, field: str, value: str) -> None:
    monkeypatch.setattr(release, "SCRYPT_N", release.SCRYPT_N_RANGE[0])
    doc = json.loads(release.seal_key(Ed25519PrivateKey.generate().private_bytes_raw(), PASSPHRASE))
    doc[field] = value
    backup = release.read_backup(json.dumps(doc).encode())
    with pytest.raises(SystemExit, match="sauvegarde modifiée"):
        release.open_key(backup, PASSPHRASE)


@pytest.mark.parametrize(
    "data",
    [
        b"-----BEGIN ENCRYPTED PRIVATE KEY-----\n",
        b'{"format": "autre"}',
        b"\xff\xfe",
        b"[]",
    ],
)
def test_not_a_backup(release, data: bytes) -> None:
    with pytest.raises(SystemExit, match="pas une sauvegarde"):
        release.read_backup(data)


def test_backup_parameters_bounded(release, monkeypatch) -> None:
    monkeypatch.setattr(release, "SCRYPT_N", release.SCRYPT_N_RANGE[0])
    doc = json.loads(release.seal_key(Ed25519PrivateKey.generate().private_bytes_raw(), PASSPHRASE))
    for n in (2**30, 3 * 2**15, True, 2**17 + 0.0):
        with pytest.raises(SystemExit, match="pas une sauvegarde"):
            release.read_backup(json.dumps({**doc, "n": n}).encode())


@windows_only
def test_export_then_import(release, dpapi_key, tmp_path: Path, monkeypatch, capsys) -> None:
    key, source = dpapi_key
    secret = b64(key.private_bytes_raw())
    backup, restored = tmp_path / "sauvegarde.json", tmp_path / "restauree.dpapi"
    export(release, monkeypatch, source, backup)
    text = backup.read_text("ascii")
    assert secret not in text and key.private_bytes_raw().hex() not in text

    monkeypatch.setattr(release, "getpass", typed(PASSPHRASE))
    assert release.main(["import-key", str(backup), "--key", str(restored)]) == 0
    assert release.load_secret(restored) == secret
    out = capsys.readouterr().out
    assert secret not in out
    assert b64(key.public_key().public_bytes_raw()) in out
    signed = release.sign({"version": "9.9.9"}, restored)
    signature = base64.b64decode(signed.pop("signature"))
    key.public_key().verify(signature, release.updater.canonical(signed))


@windows_only
def test_import_refuses_a_wrong_passphrase(release, dpapi_key, tmp_path: Path, monkeypatch) -> None:
    _key, source = dpapi_key
    backup, restored = tmp_path / "sauvegarde.json", tmp_path / "restauree.dpapi"
    export(release, monkeypatch, source, backup)
    monkeypatch.setattr(release, "getpass", typed("une tout autre phrase"))
    with pytest.raises(SystemExit, match="Phrase de passe fausse"):
        release.main(["import-key", str(backup), "--key", str(restored)])
    assert not restored.exists()


@windows_only
@pytest.mark.parametrize(
    "case",
    [((PASSPHRASE, PASSPHRASE + "!"), "diffèrent"), (("courte", "courte"), "trop courte")],
)
def test_export_refuses_a_bad_passphrase(
    release, dpapi_key, tmp_path: Path, monkeypatch, case: tuple[tuple[str, str], str]
) -> None:
    _key, source = dpapi_key
    replies, message = case
    monkeypatch.setattr(release, "getpass", typed(*replies))
    with pytest.raises(SystemExit, match=message):
        release.main(["export-key", str(tmp_path / "s.json"), "--key", str(source)])
    assert not (tmp_path / "s.json").exists()


@windows_only
def test_nothing_is_replaced(release, dpapi_key, tmp_path: Path, monkeypatch) -> None:
    _key, source = dpapi_key
    backup = tmp_path / "sauvegarde.json"
    export(release, monkeypatch, source, backup)
    before = backup.read_bytes()
    monkeypatch.setattr(release, "getpass", typed(PASSPHRASE, PASSPHRASE))
    with pytest.raises(SystemExit, match="existe déjà"):
        release.main(["export-key", str(backup), "--key", str(source)])
    assert backup.read_bytes() == before

    kept = source.read_bytes()
    monkeypatch.setattr(release, "getpass", typed(PASSPHRASE))
    assert release.main(["import-key", str(backup), "--key", str(source)]) == 1
    assert source.read_bytes() == kept


@windows_only
def test_export_without_key(release, dpapi_key, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="absente"):
        release.main(["export-key", str(tmp_path / "s.json")])  # KEY_FILE d'essai, absent
    assert not (tmp_path / "s.json").exists()


def test_manifest_checked_with_the_first_key(release, monkeypatch) -> None:
    installed, next_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    keys = tuple(b64(k.public_key().public_bytes_raw()) for k in (installed, next_key))
    monkeypatch.setattr(release.updater, "PUBLIC_KEYS", keys)

    def manifest(key: Ed25519PrivateKey) -> bytes:
        data: dict[str, object] = {
            "version": "9.9.9",
            "url": "https://autoreplay.example/a.zip",
            "sha256": "0" * 64,
            "size": 1,
        }
        data["signature"] = b64(key.sign(release.updater.canonical(data)))
        return json.dumps(data).encode()

    release.verify_for_installed(manifest(installed))
    with pytest.raises(SystemExit, match="première clé"):
        release.verify_for_installed(manifest(next_key))
