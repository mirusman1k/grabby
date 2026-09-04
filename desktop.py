"""Desktop entry point: runs the server in-process and shows a native window.

Launched by the .app bundle. There is no Terminal window and no browser tab --
this opens a real macOS window backed by the system WebKit view.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path
import urllib.request

import uvicorn
import webview

from backend import config
from backend.app import app

PORT = config.PORT
ICON = str(Path(__file__).resolve().parent / "icon.icns")


def brand_as_app() -> None:
    """Present as "Grabby" rather than "Python".

    pywebview builds a plain Cocoa app, so without this the Dock, the menu bar
    and Cmd-Tab all show the Python framework's own name and rocket icon.
    Both have to be overridden before the first window is created.
    """
    try:
        from AppKit import NSApplication, NSBundle, NSImage
    except ImportError:
        return
    bundle = NSBundle.mainBundle()
    info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
    if info is not None:
        info["CFBundleName"] = "Grabby"
        info["CFBundleDisplayName"] = "Grabby"
    try:
        image = NSImage.alloc().initByReferencingFile_(ICON)
        if image:
            NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        pass


def server_already_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1):
            return True
    except Exception:
        return False


def free_port(start: int) -> int:
    """Find a usable port, walking upward if the preferred one is taken."""
    for port in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def serve(port: int) -> None:
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> int:
    port = PORT
    # Reuse a server that's already running (e.g. the autostart LaunchAgent)
    # instead of fighting it for the port.
    if not server_already_up(port):
        port = free_port(PORT)
        threading.Thread(target=serve, args=(port,), daemon=True).start()
        for _ in range(60):
            if server_already_up(port):
                break
            time.sleep(0.25)
        else:
            print("Server failed to start", file=sys.stderr)
            return 1

    brand_as_app()
    webview.create_window(
        "Grabby",
        f"http://127.0.0.1:{port}/",
        width=900,
        height=760,
        min_size=(560, 480),
    )
    webview.start()  # blocks until the window is closed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
