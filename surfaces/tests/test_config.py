"""Key files: NAME=value lines, loose labels, several files, and both Twilio auth styles."""
from surfaces.config import Settings, load_key_file, load_key_files


def test_named_lines_win(tmp_path):
    f = tmp_path / "k.txt"
    f.write_text("TWILIO_ACCOUNT_SID=AC" + "1" * 32 + "\nTWILIO_AUTH_TOKEN=" + "a" * 32 + "\nTWILIO_FROM=+15005550006\nNOT_A_KEY=x\n")
    got = load_key_file(str(f))
    assert got == {"TWILIO_ACCOUNT_SID": "AC" + "1" * 32, "TWILIO_AUTH_TOKEN": "a" * 32, "TWILIO_FROM": "+15005550006"}


def test_loose_labels_still_work(tmp_path):
    f = tmp_path / "k.txt"
    f.write_text("sk-ant-api03-abc\n\nTwilio api key SK" + "0" * 32 + "\nTwilio client secret " + "B" * 32 + "\nCartesian sk_car_xyz\n")
    got = load_key_file(str(f))
    assert got["ANTHROPIC_API_KEY"] == "sk-ant-api03-abc" and got["TWILIO_API_SECRET"] == "B" * 32 and got["CARTESIA_API_KEY"] == "sk_car_xyz"


def test_later_files_override_earlier(tmp_path, monkeypatch):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("sk-ant-api03-abc\nTwilio api key SK" + "0" * 32 + "\n")
    b.write_text("TWILIO_ACCOUNT_SID=AC" + "2" * 32 + "\nTWILIO_AUTH_TOKEN=" + "c" * 32 + "\nTWILIO_FROM=+15005550006\n")
    merged = load_key_files(f"{a}:{b}")
    assert merged["ANTHROPIC_API_KEY"] == "sk-ant-api03-abc" and merged["TWILIO_ACCOUNT_SID"] == "AC" + "2" * 32
    monkeypatch.setenv("DORIS_KEYS_FILE", f"{a}:{b}")
    monkeypatch.setenv("DORIS_PUBLIC_URL", "https://x.ts.net")
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "TWILIO_API_KEY", "TWILIO_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert s.twilio_ready and s.twilio_auth_token == "c" * 32 and s.twilio_from == "+15005550006"


def test_auth_token_is_used_for_the_call(monkeypatch):
    import base64
    from surfaces import callback
    seen = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"sid": "CA1", "status": "queued"}'

    def fake_open(req, timeout=0):
        seen["auth"] = req.headers["Authorization"]
        return Resp()
    monkeypatch.setattr(callback.urllib.request, "urlopen", fake_open)
    app = type("A", (), {"settings": Settings(twilio_account_sid="AC9", twilio_auth_token="tok", twilio_api_key="SK1", twilio_api_secret="sec",
                                                twilio_from="+1", public_base_url="https://x")})()
    callback.CallManager.__new__(callback.CallManager).__dict__.update(app=app)
    m = callback.CallManager.__new__(callback.CallManager)
    m.app = app
    assert m._twilio_create(to="+1", url="u", status_url="s")["sid"] == "CA1"
    assert seen["auth"] == "Basic " + base64.b64encode(b"AC9:tok").decode()
