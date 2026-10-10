"""Entry point of the packaged desktop app (the PyInstaller build).

Double-clicked (no arguments): start the web app on a free local port, open
it in the default browser, and keep running until the window is closed or
Ctrl+C. With arguments it is the regular command line, so the packaged
executable also does `moldgen generate part.stl --out molds/` etc.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from moldgen import __version__

HOST = "127.0.0.1"  # local only: the app is a single-user tool, never exposed
PREFERRED_PORTS = range(8000, 8020)


def free_port() -> int:
    """First free port in PREFERRED_PORTS (stable URLs across runs), else
    any free port the OS hands out."""
    for port in PREFERRED_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
            except OSError:
                continue
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _open_when_ready(url: str, timeout: float = 60.0) -> None:
    """Open the browser once the server answers (first start of a frozen
    app can take several seconds while libraries load)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url + "api/health", timeout=1).close()
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.25)
    print(f"moldgen did not start within {timeout:.0f} s — try opening {url} yourself")


def run_app(open_browser: bool = True) -> int:
    import uvicorn

    from moldgen.main import WORKDIR, app

    port = free_port()
    url = f"http://{HOST}:{port}/"
    print(f"moldgen {__version__}")
    print(f"  running at  {url}")
    print(f"  files in    {WORKDIR}")
    print("  close this window (or press Ctrl+C) to quit\n", flush=True)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    from moldgen.cli import _tolerant_stdio

    _tolerant_stdio()  # the banner prints the user's data path
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        from moldgen.cli import main as cli_main

        return cli_main(argv)
    try:
        return run_app()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # keep the console open so the user can read it
        print(f"\nmoldgen failed to start: {type(exc).__name__}: {exc}")
        if getattr(sys, "frozen", False):
            input("Press Enter to close…")
        return 1


if __name__ == "__main__":
    sys.exit(main())
