"""The same engine for a person: a direct-deposit change is checked against the employee's record on file and
verified by calling the phone payroll already had."""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from surfaces import callback
from surfaces.tests.conftest import ROOT, free_port, http
from surfaces.tests.test_callback import FakeVoice

TABLE_PHONE = "+15005550006"


@pytest.fixture
def demo_stack(tmp_path):
    """A detector on a demo-seeded database (Acme and Kayla Brennan on file since 2021), and wired surfaces."""
    db = tmp_path / "demo.db"
    env = {**os.environ, "DORIS_OFFLINE": "1"}
    env.pop("ANTHROPIC_API_KEY", None)
    seeded = subprocess.run([sys.executable, "-m", "surfaces", "demo-seed", "--phone", TABLE_PHONE, "--db", str(db)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert seeded.returncode == 0, seeded.stderr
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "detector", "--offline", "serve", "--port", str(port)], cwd=ROOT,
                            env={**env, "DORIS_DB": str(db), "DORIS_OUTBOX": str(tmp_path / "o")},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    from surfaces.app import Surfaces, make_servers
    from surfaces.config import Settings
    s = Settings(detector_url=url, port=0, hooks_port=0, data_dir=tmp_path / "surf", twilio_account_sid="AC" + "0" * 32,
                 twilio_auth_token="t" * 32, twilio_from="+15005550001")
    app = Surfaces(s)
    dash, hooks = make_servers(app)
    for srv in (dash, hooks):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    app.dash_url, app.hooks_url = f"http://127.0.0.1:{dash.server_address[1]}", f"http://127.0.0.1:{hooks.server_address[1]}"
    s.public_base_url = app.hooks_url
    manager = callback.wire(app)
    manager.voice = FakeVoice(s.data_dir / "audio")
    app.dialed = []
    manager._twilio_create = lambda **kw: app.dialed.append(kw) or {"sid": "CA1", "status": "queued"}
    yield app
    for srv in (dash, hooks):
        srv.shutdown(); srv.server_close()
    proc.terminate(); proc.wait(timeout=10)


def send(app, scenario):
    status, html, _, url = http("POST", f"{app.dash_url}/demo/send?scenario={scenario}")
    return status, html, url.rsplit("/d/", 1)[1]


def test_payroll_change_is_checked_against_the_employee_on_file(demo_stack):
    status, html, first = send(demo_stack, "payroll")
    assert status == 200 and "Calling to verify" in html
    d = json.loads(urllib.request.urlopen(f"{demo_stack.client.base}/decisions/{first}", timeout=10).read())
    assert d["decision"] == "CALLBACK" and d["vendor_id"] == "emp_kayla_brennan" and d["callback_number"] == TABLE_PHONE
    assert d["sender_domain"] == "example.com" and d["bank_account_new"] == "****9930" and d["bank_account_on_file"] == "****4417"
    fired = {s["name"] for s in d["signals"] if s["fired"]}
    assert {"bank_account_changed", "urgency_language"} <= fired


def test_doris_asks_the_employee_and_a_no_keeps_their_pay(demo_stack):
    _, _, first = send(demo_stack, "payroll")
    http("POST", f"{demo_stack.dash_url}/d/{first}/call")
    assert demo_stack.dialed and demo_stack.dialed[0]["to"] == TABLE_PHONE
    token = re.search(r"/twilio/([^/]+)/voice", demo_stack.dialed[0]["url"]).group(1)
    http("POST", f"{demo_stack.hooks_url}/twilio/{token}/voice")
    events = demo_stack.store.events([first])
    prompt = next(e["text"] for e in events if e["who"] == "doris")
    assert "from payroll at" in prompt and "did you ask to change your direct deposit" in prompt and "your company" not in prompt
    _, twiml, _, _ = http("POST", f"{demo_stack.hooks_url}/twilio/{token}/gather", {"SpeechResult": "No, I didn't ask for that."})
    assert "<Hangup/>" in twiml
    state = json.loads(http("GET", f"{demo_stack.dash_url}/d/{first}/state")[1])
    assert state["decision"] == "BLOCKED"
    page = http("GET", f"{demo_stack.dash_url}/d/{state['latest_id']}")[1]
    assert "At the number on file, they said" in page and "Vendor AP" not in page
    assert "Your pay stays where it is" in http("GET", f"{demo_stack.dash_url}/d/{first}/trace")[1]


def test_the_vendor_scenario_still_speaks_to_a_company(demo_stack):
    _, _, first = send(demo_stack, "vendor")
    d = json.loads(urllib.request.urlopen(f"{demo_stack.client.base}/decisions/{first}", timeout=10).read())
    assert d["vendor_id"] == "ven_acme_supply"
    s = callback.lines(d, "Cal Hacks Robotics")
    assert "did your company change its bank account" in s["prompt"] and "accounts payable" in s["prompt"]


def test_employee_script_never_reads_out_a_number_and_offers_a_call_back():
    s = callback.lines({"vendor_id": "emp_x", "bank_account_new": "****9930"}, "Cal Hacks Robotics")
    assert s["prompt"].startswith("Hi, this is Doris, an automated assistant from payroll at Cal Hacks Robotics.")
    for key in ("verify", "verify_failed", "unclear"):
        assert "the number you already have for Cal Hacks Robotics" in s[key]
    assert not any(re.search(r"\d{3}\D?\d{3}\D?\d{4}", line) for line in s.values())
    assert set(s) == set(callback.lines({}, "X")), "both scripts have every line the call needs"


def test_demo_seed_add_keeps_existing_records(tmp_path):
    db = tmp_path / "d.db"
    env = {**os.environ, "DORIS_OFFLINE": "1"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "surfaces", "demo-seed", "--phone", TABLE_PHONE, "--db", str(db), *a],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert run().returncode == 0
    again = run("--add")
    assert again.returncode == 0 and "Added any missing demo records" in again.stdout
