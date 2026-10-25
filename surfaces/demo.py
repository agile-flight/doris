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


# The same rule covers people as well as companies: an employee's direct-deposit change is checked against their
# record on file and verified by calling the phone HR already had. Ids starting "emp_" are people. Kayla is CRS sandbox
# persona P13, so the CRS check (surfaces/crs.py) gets the sandbox's canned answer for her.
EMPLOYEE = {
    "vendor_id": "emp_kayla_brennan", "name": "Kayla Brennan", "domain": "calhacksrobotics.com",
    "email": "kayla.brennan@calhacksrobotics.com", "contact_name": "Kayla Brennan", "bank_last4": "4417", "routing_last4": "0358",
    "since": "2021-06-01T16:00:00Z",
    "payments": [{"amount": 3184.62, "paid_at": f"2026-{m:02d}-{d:02d}T16:00:00Z", "invoice_id": f"PAYROLL-2026-{m:02d}{d:02d}"}
                 for m, d in ((8, 14), (8, 28), (9, 11), (9, 25), (10, 9))],
}


def seed_demo(phone: str, db: str, add: bool = False) -> str:
    if not E164.fullmatch(phone or ""):
        raise SystemExit("--phone must be E.164, like +14155550123")
    db_path = Path(db) if Path(db).is_absolute() else ROOT / db
    if db_path.exists() and not add:
        raise SystemExit(f"{db_path} already exists; demo seeding needs a fresh database (delete it, pick another --db, or pass --add)")
    vendors = json.loads((ROOT / "detector" / "fixtures" / "vendors.json").read_text())
    vendors["vendors"].append(dict(EMPLOYEE, phone=phone))
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
    if add:
        return f"Added any missing demo records to {db_path} (records already there are left as they are)."
    return (f"Seeded {db_path} (Acme's and Kayla Brennan's phone on file since 2021 is {phone[:2]}…{phone[-4:]}).\n"
            f"Start the detector on it with: DORIS_DB={db_path} .venv/bin/python -m detector serve")
