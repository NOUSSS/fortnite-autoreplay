"""Mise à jour de bout en bout : ``python packaging/update_e2e.py <ancien.zip> <nouveau.zip>``.

Extrait l'ancienne version dans un dossier temporaire, sert la nouvelle en local
(manifeste signé par la clé de publication, ``release.sign``), ouvre le panneau
sur un bureau Windows caché, clique sur « Mettre à jour » (panneau Tk jusqu'à la
0.5.0, Qt ensuite), puis vérifie que la
nouvelle version est installée et relancée, ``.update/`` effacé et les fichiers
de l'utilisateur gardés.

Le bureau caché ne voit ni le jeu ni les fenêtres de l'utilisateur : le panneau
d'essai ne peut rien cliquer en jeu. Le mutex des panneaux est commun à la
session : ne pas lancer l'essai pendant qu'un panneau tourne.
"""

from __future__ import annotations

import ctypes
import functools
import hashlib
import http.server
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packaging"))

import release  # noqa: E402

from fortnite_auto_replay import updater  # noqa: E402

DESKTOP = "fnar-e2e"
TIMEOUT_S = 90
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
    ]  # fmt: skip


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]  # fmt: skip


user32.CreateDesktopW.restype = wintypes.HANDLE
user32.CreateDesktopW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_void_p,
]  # fmt: skip
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetKeyboardState.argtypes = [ctypes.c_void_p]
user32.SetKeyboardState.argtypes = [ctypes.c_void_p]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
    wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfo),
    ctypes.POINTER(_ProcessInfo),
]  # fmt: skip


# --- sur le bureau caché : le clic --------------------------------------------------
def _panel_window() -> int | None:
    found: list[int] = []

    def visit(hwnd: int, _: int) -> bool:
        title = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, title, 64)
        if title.value == "Fortnite AutoReplay" and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found[0] if found else None


def _update_button(panel: int) -> tuple[int, int, int] | None:
    """Le bouton de 28 px le plus large : « Mettre à jour » dépasse Lancer et Quitter."""
    scale = user32.GetDpiForWindow(panel) / 96
    buttons: list[tuple[int, int, int]] = []

    def visit(hwnd: int, _: int) -> bool:
        box = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(box))
        width, height = box.right - box.left, box.bottom - box.top
        if height == round(28 * scale) and user32.IsWindowVisible(hwnd):
            buttons.append((width, height, hwnd))
        return True

    user32.EnumChildWindows(panel, WNDENUMPROC(visit), 0)
    widest = max(buttons, default=None)
    return widest if widest and widest[0] > round(100 * scale) else None


def _set_left_button(down: bool) -> None:
    keys = (ctypes.c_ubyte * 256)()
    user32.GetKeyboardState(keys)
    keys[0x01] = 0x80 if down else 0  # VK_LBUTTON
    user32.SetKeyboardState(keys)


def _post_click(hwnd: int, x: int, y: int) -> None:
    point = y << 16 | x
    user32.PostMessageW(hwnd, 0x0200, 0, point)  # WM_MOUSEMOVE
    user32.PostMessageW(hwnd, 0x0201, 1, point)  # WM_LBUTTONDOWN, MK_LBUTTON
    time.sleep(0.15)
    user32.PostMessageW(hwnd, 0x0202, 0, point)  # WM_LBUTTONUP


def click_update_qt(wait_s: float) -> int:
    """Qt dessine ses boutons dans une seule fenêtre : pas de bouton à trouver.

    Carte toujours ouverte (``panel.rest`` à ``panel``) : « Mettre à jour » est au
    bas du bandeau, juste au-dessus du pied, à 80 px du bas. Clics répétés, sans
    effet tant que le bandeau n'est pas là (le journal occupe cette place),
    jusqu'à ce que le panneau se ferme pour s'installer.
    """
    deadline = time.time() + wait_s
    clicked = False
    while time.time() < deadline:
        panel = _panel_window()
        if panel is None:
            if clicked:
                return 0
        else:
            scale = user32.GetDpiForWindow(panel) / 96
            box = wintypes.RECT()
            user32.GetWindowRect(panel, ctypes.byref(box))
            height = box.bottom - box.top
            if height > round(200 * scale):
                _post_click(panel, round(70 * scale), height - round(80 * scale))
                clicked = True
        time.sleep(1.5)
    return 0 if clicked else 1


def click_update(wait_s: float) -> int:
    """Attend le bouton puis clique : Tk lit l'état du bouton (``GetKeyState``), d'où
    le partage de l'état d'entrée avec son fil avant de poster les messages."""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        panel = _panel_window()
        button = _update_button(panel) if panel else None
        if button:
            width, height, hwnd = button
            point = (height // 2) << 16 | (width // 2)
            mine = kernel32.GetCurrentThreadId()
            target = user32.GetWindowThreadProcessId(hwnd, None)
            user32.AttachThreadInput(mine, target, True)
            try:
                _set_left_button(True)
                user32.PostMessageW(hwnd, 0x0200, 0, point)  # WM_MOUSEMOVE
                user32.PostMessageW(hwnd, 0x0201, 1, point)  # WM_LBUTTONDOWN
                time.sleep(0.3)
                _set_left_button(False)
                user32.PostMessageW(hwnd, 0x0202, 0, point)  # WM_LBUTTONUP
                time.sleep(0.3)
            finally:
                user32.AttachThreadInput(mine, target, False)
            return 0
        time.sleep(0.5)
    return 1


# --- côté appelant ----------------------------------------------------------------
def _start_hidden(command: list[str], cwd: Path) -> None:
    info = _StartupInfo()
    info.cb = ctypes.sizeof(info)
    info.lpDesktop = f"WinSta0\\{DESKTOP}"
    process = _ProcessInfo()
    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
    ok = kernel32.CreateProcessW(
        None, line, None, None, False, 0, None, str(cwd), ctypes.byref(info), ctypes.byref(process)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CreateProcessW")
    kernel32.CloseHandle(process.hThread)
    kernel32.CloseHandle(process.hProcess)


def _powershell(script: str) -> str:
    command = ["powershell", "-NoProfile", "-Command", script]
    return subprocess.run(command, capture_output=True, text=True, check=False).stdout.strip()


def _file_version(exe: Path) -> str:
    return _powershell(f"(Get-Item -LiteralPath '{exe}').VersionInfo.FileVersion")


def _pids_under(folder: Path) -> list[int]:
    out = _powershell(
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '"
        f"{folder}*' }} | ForEach-Object {{ $_.ProcessId }}"
    )
    return [int(pid) for pid in out.split()]


def _serve(folder: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

    handler = functools.partial(Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _contains(found: object, wanted: object) -> bool:
    """``wanted`` se retrouve dans ``found`` ; des clés en plus sont permises."""
    if isinstance(wanted, dict):
        return isinstance(found, dict) and all(
            key in found and _contains(found[key], value) for key, value in wanted.items()
        )
    return found == wanted


def _version_of(archive: Path) -> str:
    match = re.fullmatch(r"FortniteAutoReplay-(\d+(?:\.\d+)*)\.zip", archive.name)
    if match is None:
        raise SystemExit(f"{archive.name} : nom attendu FortniteAutoReplay-X.Y.Z.zip")
    return match.group(1)


def run(old_zip: Path, new_zip: Path) -> int:
    old, new = _version_of(old_zip), _version_of(new_zip)
    if not updater.is_newer(new, old):
        raise SystemExit(f"{new} n'est pas plus récente que {old}")
    work = Path(tempfile.mkdtemp(prefix="fnar-e2e-"))
    with zipfile.ZipFile(old_zip) as archive:
        archive.extractall(work)
    app = work / release.build.NAME
    served = work / "srv"
    served.mkdir()
    data = new_zip.read_bytes()
    (served / new_zip.name).write_bytes(data)
    server, port = _serve(served)
    manifest = release.sign(
        {
            "version": new,
            "date": time.strftime("%Y-%m-%d"),
            "notes": ["Version d'essai."],
            "url": f"http://127.0.0.1:{port}/{new_zip.name}",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
    )
    (served / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    local: dict[str, object] = {"update": {"manifest_url": f"http://127.0.0.1:{port}/latest.json"}}
    qt = any(app.rglob("Qt6Core.dll"))  # panneau Qt : carte toujours ouverte, voir click_update_qt
    if qt:
        local["panel"] = {"rest": "panel"}
    (app / "config" / "local.json").write_text(json.dumps(local), "utf-8")
    (app / "logs").mkdir(exist_ok=True)
    (app / "logs" / "marqueur.txt").write_text("à garder", "utf-8")

    exe = app / updater.EXE_NAME
    print(f"{work} : version {_file_version(exe)} installée, {new} servie sur le port {port}")
    desktop = user32.CreateDesktopW(DESKTOP, None, None, 0, 0x10000000, None)  # GENERIC_ALL
    if not desktop:
        raise OSError(ctypes.get_last_error(), "CreateDesktopW")
    installed = False
    try:
        _start_hidden([str(exe)], app)
        _start_hidden([sys.executable, __file__, "--click-qt" if qt else "--click", "40"], ROOT)
        deadline = time.time() + TIMEOUT_S
        while time.time() < deadline and not installed:
            time.sleep(1)
            installed = (
                _file_version(exe) == new
                and not (app / updater.UPDATE_DIR).exists()
                and bool(_pids_under(app))  # la nouvelle version tourne
            )
    finally:
        for pid in _pids_under(app):
            kill = ["taskkill", "/PID", str(pid), "/T", "/F"]
            subprocess.run(kill, capture_output=True, check=False)
        user32.CloseDesktop(desktop)
        server.shutdown()

    kept = json.loads((app / "config" / "local.json").read_text("utf-8"))
    checks = {
        f"version {new} installée et relancée, .update/ effacé": installed,
        # le panneau peut y ajouter les siens (position enregistrée après un clic Qt)
        "config/local.json gardé": _contains(kept, local),
        "logs/ gardé": (app / "logs" / "marqueur.txt").exists(),
    }
    for label, ok in checks.items():
        print(f"{'ok ' if ok else 'ÉCHEC'} {label}")
    log = app / "logs" / "bot.log"
    if log.exists():
        lines = log.read_text("utf-8", errors="replace").splitlines()
        print("\n".join(line for line in lines if "mise à jour" in line))
    if not all(checks.values()):
        print(f"dossier gardé pour examen : {work}")
        return 1
    shutil.rmtree(work, ignore_errors=True)
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--click"]:
        return click_update(float(argv[1]))
    if argv[:1] == ["--click-qt"]:
        return click_update_qt(float(argv[1]))
    if len(argv) != 2:
        print(__doc__.splitlines()[0])
        return 2
    return run(Path(argv[0]).resolve(), Path(argv[1]).resolve())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
