"""Regenerate contract/samples from the real pipeline, deterministically (fixed clock, offline,
fixture CT, seeded vendors). Run: .venv/bin/python -m detector samples"""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_ROOT, Settings
from .pipeline import FIXTURES, Doris

SAMPLE_CLOCK = datetime(2026, 10, 22, 9, 3, 20, tzinfo=timezone.utc)


def make_samples(out_dir: Path = PROJECT_ROOT / "contract" / "samples") -> list[Path]:
    tmp = Path(tempfile.mkdtemp(prefix="doris-samples-"))
    try:
        settings = Settings(offline=True, db_path=tmp / "doris.db", outbox_dir=tmp / "outbox")
        doris = Doris(settings, seed=FIXTURES / "vendors.json", now=lambda: SAMPLE_CLOCK)
        emails = FIXTURES / "emails"
        written = []
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.json"):
            old.unlink()

        def write(name: str, decision: dict) -> None:
            path = out_dir / name
            path.write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n")
            written.append(path)

        write("clean_pay.json", doris.check((emails / "clean_acme.eml").read_bytes()))
        callback = doris.check((emails / "bank_change_lookalike.eml").read_bytes())
        write("bank_change_callback.json", callback)
        write("prompt_injection_blocked.json", doris.check((emails / "prompt_injection.eml").read_bytes()))
        write("new_vendor_hold.json", doris.check((emails / "new_vendor_northwind.eml").read_bytes()))
        write("forwarded_check_callback.json", doris.check((emails / "forwarded_check.txt").read_text()))
        write("callback_denied_blocked.json", doris.resolve_callback(callback["decision_id"], vendor_confirmed=False, notes="we never changed our bank"))
        doris.close()
        return written
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
