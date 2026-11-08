"""Mises à jour : manifeste signé, téléchargement vérifié, installation tout ou rien."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from typing_extensions import Self

from fortnite_auto_replay import updater
from fortnite_auto_replay.updater import UpdateError

KEY = Ed25519PrivateKey.generate()
PUBLIC = KEY.public_key().public_bytes_raw()
PAYLOAD = b"nouvelle version"
SITE = "https://autoreplay.example"


def manifest(key: Ed25519PrivateKey = KEY, **changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "version": "0.3.0",
        "date": "2026-11-08",
        "notes": ["Une note."],
        "url": "https://autoreplay.example/releases/FortniteAutoReplay-0.3.0.zip",
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "size": len(PAYLOAD),
    }
    data.update(changes)
    data["signature"] = base64.b64encode(key.sign(updater.canonical(data))).decode()
    return data


def raw(data: dict[str, object]) -> bytes:
    return json.dumps(data).encode()


class FakeResponse(io.BytesIO):
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def serving(pages: dict[str, bytes]) -> updater.Opener:
    def opener(url: str, _timeout: float) -> FakeResponse:
        if url not in pages:
            raise OSError(f"introuvable : {url}")
        return FakeResponse(pages[url])

    return opener


# --- versions et manifeste ------------------------------------------------------
def test_versions() -> None:
    assert updater.parse_version("0.2") == (0, 2, 0, 0)
    assert updater.is_newer("0.10.0", "0.9.9")
    assert not updater.is_newer("0.2.0", "0.2")
    for bad in ("", "v0.2", "0.2-beta", "1.2.3.4.5"):
        with pytest.raises(UpdateError):
            updater.parse_version(bad)


def test_manifest_signed() -> None:
    release = updater.verify_manifest(raw(manifest()), PUBLIC)
    assert release.version == "0.3.0"
    assert release.size == len(PAYLOAD)
    assert release.notes == ("Une note.",)


def test_manifest_tampered_after_signing() -> None:
    data = manifest()
    data["url"] = "https://ailleurs.example/autre.zip"
    with pytest.raises(UpdateError, match="signature"):
        updater.verify_manifest(raw(data), PUBLIC)


def test_manifest_from_another_key() -> None:
    with pytest.raises(UpdateError, match="signature"):
        updater.verify_manifest(raw(manifest(Ed25519PrivateKey.generate())), PUBLIC)


@pytest.mark.parametrize(
    "changes",
    [
        {"sha256": "abc"},
        {"size": 0},
        {"size": True},
        {"version": "demain"},
        {"url": "http://autoreplay.example/a.zip"},
        {"url": "file:///C:/a.zip"},
        {"notes": "pas une liste"},
    ],
)
def test_manifest_signed_but_invalid(changes: dict[str, object]) -> None:
    with pytest.raises(UpdateError):
        updater.verify_manifest(raw(manifest(**changes)), PUBLIC)


def test_manifest_without_signature() -> None:
    data = manifest()
    del data["signature"]
    with pytest.raises(UpdateError):
        updater.verify_manifest(raw(data), PUBLIC)


def test_public_key_placeholder_refused() -> None:
    with pytest.raises(UpdateError, match="clé publique"):
        updater.public_key_bytes("REMPLACER")


def test_check_only_newer() -> None:
    url = "https://autoreplay.example/latest.json"
    opener = serving({url: raw(manifest())})
    assert updater.check(url, "0.2.0", public_key=PUBLIC, opener=opener) is not None
    assert updater.check(url, "0.3.0", public_key=PUBLIC, opener=opener) is None
    assert updater.check(url, "0.4.0", public_key=PUBLIC, opener=opener) is None


def test_check_unreachable() -> None:
    with pytest.raises(UpdateError, match="injoignable"):
        updater.check(SITE + "/latest.json", public_key=PUBLIC, opener=serving({}))


def test_check_refuses_plain_http() -> None:
    with pytest.raises(UpdateError, match="refusée"):
        updater.check("http://autoreplay.example/x.json", public_key=PUBLIC, opener=serving({}))


# --- téléchargement -------------------------------------------------------------
def test_download_verified(tmp_path: Path) -> None:
    release = updater.verify_manifest(raw(manifest()), PUBLIC)
    seen: list[tuple[int, int]] = []
    target = updater.download(
        release,
        tmp_path / "a.zip",
        progress=lambda done, total: seen.append((done, total)),
        opener=serving({release.url: PAYLOAD}),
    )
    assert target.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))


@pytest.mark.parametrize("body", [b"nouvelle versioN", b"nouvelle", PAYLOAD + b"!"])
def test_download_altered(tmp_path: Path, body: bytes) -> None:
    release = updater.verify_manifest(raw(manifest()), PUBLIC)
    with pytest.raises(UpdateError):
        updater.download(release, tmp_path / "a.zip", opener=serving({release.url: body}))
    assert list(tmp_path.iterdir()) == []  # ni archive ni morceau laissé


# --- extraction et installation ------------------------------------------------
def make_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return path


APP = updater.APP_NAME


def new_version_files(tag: bytes) -> dict[str, bytes]:
    return {
        f"{APP}/{updater.EXE_NAME}": b"exe " + tag,
        f"{APP}/_internal/base_library.zip": b"lib " + tag,
        f"{APP}/templates/ready/ready_01.png": b"png " + tag,
        f"{APP}/config/default.json": b"{} " + tag,
        f"{APP}/Lisez-moi.txt": b"txt " + tag,
    }


def test_extract_refuses_path_escape(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "a.zip", {**new_version_files(b"2"), "../evil.txt": b"x"})
    with pytest.raises(UpdateError, match="refusé"):
        updater.extract(archive, tmp_path / "new")
    assert not (tmp_path / "evil.txt").exists()


def test_extract_refuses_incomplete(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "a.zip", {f"{APP}/Lisez-moi.txt": b"x"})
    with pytest.raises(UpdateError, match="incomplète"):
        updater.extract(archive, tmp_path / "new")


def installed(tmp_path: Path) -> Path:
    """Dossier de l'application en version 1, avec réglages et logs de l'utilisateur."""
    root = tmp_path / "app"
    for name, content in new_version_files(b"1").items():
        path = root / name.split("/", 1)[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (root / "config" / "local.json").write_text('{"engine": {}}', "utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "bot.log").write_text("journal", "utf-8")
    return root


def test_install_replaces_program_and_keeps_user_files(tmp_path: Path) -> None:
    root = installed(tmp_path)
    archive = make_zip(tmp_path / "v2.zip", new_version_files(b"2"))
    package = updater.extract(archive, root / updater.UPDATE_DIR / "0.3.0" / "new")

    updater.install(package, root)

    assert (root / updater.EXE_NAME).read_bytes() == b"exe 2"
    assert (root / "_internal" / "base_library.zip").read_bytes() == b"lib 2"
    assert (root / "config" / "default.json").read_bytes() == b"{} 2"
    assert (root / "config" / "local.json").read_text("utf-8") == '{"engine": {}}'
    assert (root / "logs" / "bot.log").read_text("utf-8") == "journal"
    assert (root / updater.UPDATE_DIR / "old" / updater.EXE_NAME).read_bytes() == b"exe 1"


def test_install_rolls_back_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = installed(tmp_path)
    archive = make_zip(tmp_path / "v2.zip", new_version_files(b"2"))
    package = updater.extract(archive, root / updater.UPDATE_DIR / "0.3.0" / "new")
    real_rename = Path.rename

    def flaky(self: Path, target: Path) -> Path:
        if self.name == "templates" and package in self.parents:
            raise PermissionError("fichier verrouillé")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(UpdateError, match="installation impossible"):
        updater.install(package, root)
    monkeypatch.undo()

    for name, content in new_version_files(b"1").items():
        assert (root / name.split("/", 1)[1]).read_bytes() == content
    assert (root / "config" / "local.json").exists()


def test_program_files_skip_user_paths(tmp_path: Path) -> None:
    package = tmp_path / APP
    for name in ("config/default.json", "config/local.json", "logs/x", "_internal/y"):
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text("x", "utf-8")
    assert updater.program_files(package) == ["_internal", "config/default.json"]


def test_cleanup(tmp_path: Path) -> None:
    (tmp_path / updater.UPDATE_DIR / "old").mkdir(parents=True)
    assert updater.cleanup(tmp_path, attempts=1)
    assert not (tmp_path / updater.UPDATE_DIR).exists()


# --- suivi pour le panneau -------------------------------------------------------
def wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "délai dépassé"
        time.sleep(0.01)


def test_updates_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = installed(tmp_path)
    release = updater.verify_manifest(raw(manifest()), PUBLIC)
    archive = make_zip(tmp_path / "v2.zip", new_version_files(b"2"))

    def fetch(_r: updater.Release, target: Path, progress: Callable[[int, int], None]) -> Path:
        progress(5, 10)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read_bytes())
        return target

    launched: list[list[str]] = []
    monkeypatch.setattr(updater, "relaunch", lambda _root, args: launched.append(args))
    updates = updater.Updates(
        root, "https://x", checker=lambda: release, fetcher=fetch, relaunch_args=["--dry-run"]
    )

    assert not updates.install_pending()  # rien de prêt, rien de demandé
    updates.check_now()
    assert updates.state().phase == "available"
    updates.download()
    wait_for(lambda: updates.state().phase == "ready")
    assert updates.state().version == "0.3.0"

    updates.request_install(resume=True)
    assert updates.install_pending()
    assert (root / updater.EXE_NAME).read_bytes() == b"exe 2"
    assert launched == [["--dry-run", "--no-pause"]]  # simulation gardée, bot relancé


def test_updates_failure_is_reported(tmp_path: Path) -> None:
    release = updater.verify_manifest(raw(manifest()), PUBLIC)

    def broken(_release: updater.Release, _target: Path, _progress: object) -> Path:
        raise UpdateError("téléchargement interrompu (réseau)")

    updates = updater.Updates(tmp_path, "https://x", checker=lambda: release, fetcher=broken)
    updates.check_now()
    updates.download()
    wait_for(lambda: updates.state().phase == "failed")
    assert "téléchargement interrompu" in updates.state().error
    updates.download()  # « Réessayer » repart de la version annoncée
    wait_for(lambda: updates.state().phase == "failed")


def test_check_failure_stays_silent(tmp_path: Path) -> None:
    def offline() -> updater.Release | None:
        raise UpdateError("manifeste injoignable")

    updates = updater.Updates(tmp_path, "https://x", checker=offline)
    updates.check_now()
    assert updates.state().phase == "idle"


def test_relaunch_keeps_the_panel_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fortnite_auto_replay import cli  # noqa: PLC0415 - tkinter et capture inutiles ailleurs

    monkeypatch.chdir(tmp_path)
    config = str(tmp_path / "essai.json")
    assert cli.relaunch_options(["panel"]) == []
    assert cli.relaunch_options(["panel", "--dry-run", "--no-pause"]) == ["--dry-run"]
    assert cli.relaunch_options(["panel", "--config", "essai.json", "--no-local"]) == [
        "--config",
        config,
        "--no-local",
    ]
    assert cli.relaunch_options(["panel", "--config=essai.json"]) == [f"--config={config}"]
