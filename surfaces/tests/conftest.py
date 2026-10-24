"""A real detector, started as its own process on a free port, offline, with a throwaway database.
Surfaces only ever reach it over HTTP, exactly as in production."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def detector(tmp_path):
    port = free_port()
    env = {**os.environ, "DORIS_OFFLINE": "1", "DORIS_DB": str(tmp_path / "det.db"), "DORIS_OUTBOX": str(tmp_path / "outbox")}
    env.pop("ANTHROPIC_API_KEY", None)
    proc = subprocess.Popen([sys.executable, "-m", "detector", "--offline", "serve", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("detector did not start")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def surfaces(detector, tmp_path):
    import threading
    from surfaces.app import Surfaces, make_servers
    from surfaces.config import Settings
    settings = Settings(detector_url=detector, port=0, hooks_port=0, data_dir=tmp_path / "surf")
    app = Surfaces(settings)
    dash, hooks = make_servers(app)
    for s in (dash, hooks):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    app.dash_url = f"http://127.0.0.1:{dash.server_address[1]}"
    app.hooks_url = f"http://127.0.0.1:{hooks.server_address[1]}"
    yield app
    for s in (dash, hooks):
        s.shutdown()
        s.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http(method, url, form=None, follow=True):
    import urllib.error
    import urllib.parse
    data = urllib.parse.urlencode(form).encode() if form is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.headers, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers, url
