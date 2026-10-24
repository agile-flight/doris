"""Demo set-up: a detector database whose Acme Supply record carries the judges'-table phone,
on file since 2021, so the live callback rings a real handset and still honours the 90-day rule."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .config import ROOT

E164 = re.compile(r"\+[1-9][0-9]{6,14}")


def seed_demo(phone: str, db: str) -> str:
    if not E164.fullmatch(phone or ""):
        raise SystemExit("--phone must be E.164, like +14155550123")
    db_path = Path(db) if Path(db).is_absolute() else ROOT / db
    if db_path.exists():
        raise SystemExit(f"{db_path} already exists; demo seeding needs a fresh database (delete it or pick another --db)")
    vendors = json.loads((ROOT / "detector" / "fixtures" / "vendors.json").read_text())
    for v in vendors["vendors"]:
        if v["vendor_id"] == "ven_acme_supply":
            v["phone"] = phone
    out = ROOT / "data" / "demo" / "vendors.json"     # under data/, which git ignores: the phone never reaches the repo
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(vendors, indent=2))
    env = {**os.environ, "DORIS_DB": str(db_path), "DORIS_OFFLINE": "1"}
    # Through the detector's public entry point. (`python -m detector seed` loads the fixture vendors first
    # on an empty database, which would shadow the demo record; reported to Agent A in coord/from_B.md.)
    code = "import sys; from detector import Doris; Doris(seed=sys.argv[1]).close()"
    done = subprocess.run([sys.executable, "-c", code, str(out)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise SystemExit(f"detector seed failed: {done.stderr.strip()[-300:]}")
    return (f"Seeded {db_path} (Acme's phone on file since 2021 is now {phone[:2]}…{phone[-4:]}).\n"
            f"Start the detector on it with: DORIS_DB={db_path} .venv/bin/python -m detector serve")
