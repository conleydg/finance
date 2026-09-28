"""Finance as a Mac app: runs the server inside this process and shows it in a native
window (WebKit, via pywebview). Closing the window or pressing Cmd+Q stops everything.

    .venv/bin/python -m finance.desktop          # your data, port 8770
    .venv/bin/python -m finance.desktop --demo   # made-up data in data-demo/, port 8771
"""
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = "--demo" in sys.argv
if DEMO:
    os.environ["FINANCE_DATA"] = str(ROOT / "data-demo")
PORT = 8771 if DEMO else 8770
URL = f"http://127.0.0.1:{PORT}/"


def port_in_use() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def serve() -> None:
    import uvicorn
    from finance.app import app
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main() -> None:
    import webview

    if DEMO:
        import shutil
        shutil.rmtree(ROOT / "data-demo", ignore_errors=True)
        from demo import seed
        seed.main()
    # If a server from start.sh or demo.sh is already running, use it rather than fight for the port.
    if not port_in_use():
        threading.Thread(target=serve, daemon=True).start()
        for _ in range(80):
            if port_in_use():
                break
            time.sleep(0.1)

    data_dir = Path(os.environ.get("FINANCE_DATA", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    webview.create_window("Finance (demo)" if DEMO else "Finance", URL,
                          width=1320, height=900, min_size=(380, 600), text_select=True)
    # Not private mode, so the page can remember its last tab; storage stays in the data folder.
    webview.start(private_mode=False, storage_path=str(data_dir / "webview"))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
