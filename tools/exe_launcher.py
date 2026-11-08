"""Sorgente di DataExplorer.exe: avvia l'app senza terminale usando il venv del progetto (NiceGuiApp).

Rigenerare l'exe con app\\tools\\build_exe.ps1. L'exe va tenuto accanto alla cartella app/.
"""
import ctypes
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

PORT = 8090
CREATE_NO_WINDOW = 0x08000000


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, text, "Data Explorer", 0x10)


def main() -> None:
    if getattr(sys, "frozen", False):  # DataExplorer.exe sits next to the app/ folder
        root = Path(sys.executable).resolve().parent / "app"
    else:
        root = Path(__file__).resolve().parent.parent
    python = root / "NiceGuiApp" / "Scripts" / "pythonw.exe"
    script = root / "main.py"
    if not python.exists() or not script.exists():
        message(f"Impossibile avviare l'app: file mancanti.\n\n{python}\n{script}")
        return

    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:  # already running: just open the browser
            webbrowser.open(f"http://127.0.0.1:{PORT}")
            return

    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    with open(logs / "app.log", "ab") as log:
        subprocess.Popen([str(python), str(script), "--port", str(PORT)], cwd=root, stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)


if __name__ == "__main__":
    main()
