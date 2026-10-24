"""The dashboard against a real detector process."""
import json
import re
import urllib.request

from surfaces.render import esc
from surfaces.tests.conftest import http


def decision_id_from(url):
    return url.rstrip("/").rsplit("/d/", 1)[1]


def test_queue_shows_detector_status(surfaces):
    status, html, _, _ = http("GET", surfaces.dash_url + "/")
    assert status == 200 and "Detector connected" in html and "Send the demo bank-change email" in html


def test_queue_when_detector_is_down(tmp_path):
    import threading
    from surfaces.app import Surfaces, make_servers
    from surfaces.config import Settings
    app = Surfaces(Settings(detector_url="http://127.0.0.1:9", port=0, hooks_port=0, data_dir=tmp_path))
    dash, hooks = make_servers(app)
    threading.Thread(target=dash.serve_forever, daemon=True).start()
    status, html, _, _ = http("GET", f"http://127.0.0.1:{dash.server_address[1]}/")
    assert status == 200 and "not reachable" in html
    status, html, _, _ = http("GET", f"http://127.0.0.1:{dash.server_address[1]}/d/dec_x")
    assert status == 502 and "detector said" in html
    dash.shutdown(); dash.server_close(); hooks.server_close()


def test_demo_email_to_callback_screen_then_manual_denial_flips_to_blocked(surfaces):
    status, html, _, url = http("POST", surfaces.dash_url + "/demo/send")
    assert status == 200 and "Calling the vendor" in html and "+1 (415) 555-0123" in html
    first = decision_id_from(url)
    assert esc("Telephony isn't configured") in html
    st = json.loads(http("GET", f"{surfaces.dash_url}/d/{first}/state")[1])
    assert st["latest_id"] == first and st["decision"] == "CALLBACK"
    status, html, _, url = http("POST", f"{surfaces.dash_url}/d/{first}/manual_callback", {"confirmed": "no", "notes": "we never changed our bank"})
    assert status == 200 and "blocked-screen" in html and "we never changed our bank" in html
    follow = decision_id_from(url)
    assert follow != first
    st = json.loads(http("GET", f"{surfaces.dash_url}/d/{first}/state")[1])
    assert st["latest_id"] == follow and st["decision"] == "BLOCKED"       # the polling page flips
    status, _, headers, _ = http("GET", f"{surfaces.dash_url}/d/{first}", follow=False)
    assert status == 303 and headers["Location"] == f"/d/{follow}"
    assert "Chain verified" in http("GET", f"{surfaces.dash_url}/d/{follow}")[1]


def test_manual_callback_requires_a_choice(surfaces):
    _, _, _, url = http("POST", surfaces.dash_url + "/demo/send")
    first = decision_id_from(url)
    status, html, _, _ = http("POST", f"{surfaces.dash_url}/d/{first}/manual_callback", {"notes": "x"})
    assert status == 200 and "Choose what the vendor said" in html and "Calling the vendor" in html


def test_review_on_a_hold(surfaces, detector):
    raw = (__import__("pathlib").Path(__file__).resolve().parents[2] / "detector/fixtures/emails/new_vendor_northwind.eml").read_bytes()
    req = urllib.request.Request(detector + "/check", data=raw, method="POST", headers={"Content-Type": "message/rfc822"})
    held = json.loads(urllib.request.urlopen(req, timeout=30).read())
    html = http("GET", f"{surfaces.dash_url}/d/{held['decision_id']}")[1]
    assert "On hold" in html and "Approve payment" in html
    status, html, _, _ = http("POST", f"{surfaces.dash_url}/d/{held['decision_id']}/review", {"approve": "yes"})
    assert "Give your name" in html
    status, html, _, url = http("POST", f"{surfaces.dash_url}/d/{held['decision_id']}/review", {"approve": "yes", "reviewer": "Judge"})
    assert status == 200 and "Paid" in html and "Judge" in html


def test_detector_refusal_is_shown_not_crashed(surfaces):
    _, _, _, url = http("POST", surfaces.dash_url + "/demo/send")
    first = decision_id_from(url)
    status, html, _, _ = http("POST", f"{surfaces.dash_url}/d/{first}/review", {"approve": "yes", "reviewer": "x"})
    assert status == 400 and "detector said" in html


def test_call_without_telephony_explains(surfaces):
    _, _, _, url = http("POST", surfaces.dash_url + "/demo/send")
    status, html, _, _ = http("POST", url + "/call")
    assert status == 200 and esc("Telephony isn't configured") in html


def test_samples_gallery_and_unknown_pages(surfaces):
    status, html, _, _ = http("GET", surfaces.dash_url + "/samples")
    names = re.findall(r'href="/samples/([\w\-]+)"', html)
    assert status == 200 and len(names) >= 6
    for n in names:
        s, page, _, _ = http("GET", f"{surfaces.dash_url}/samples/{n}")
        assert s == 200 and "data-poll" not in page and "Audit trail" in page
    assert http("GET", surfaces.dash_url + "/samples/nope")[0] == 404
    assert http("GET", surfaces.dash_url + "/d/dec_nope")[0] == 404
    assert http("GET", surfaces.dash_url + "/nowhere")[0] == 404
