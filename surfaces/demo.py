"""Demo set-up: a detector database whose vendor record (Tannerfield Supply) carries the judges'-table phone,
on file since 2021, so the live callback rings a real handset and still honours the 90-day rule."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import ROOT

E164 = re.compile(r"\+[1-9][0-9]{6,14}")


# The same rule covers people as well as companies: an employee's direct-deposit change is checked against their
# record on file and verified by calling the phone HR already had. Ids starting "emp_" are people. Kayla is CRS sandbox
# persona P13, so the CRS check (surfaces/crs.py) gets the sandbox's canned answer for her.
EMPLOYEE = {
    "vendor_id": "emp_kayla_brennan", "name": "Kayla Brennan", "domain": "marlowerobotics.com",
    "email": "kayla.brennan@marlowerobotics.com", "contact_name": "Kayla Brennan", "bank_last4": "4417", "routing_last4": "0358",
    "since": "2021-06-01T16:00:00Z",
    "payments": [{"amount": 3184.62, "paid_at": f"2026-{m:02d}-{d:02d}T16:00:00Z", "invoice_id": f"PAYROLL-2026-{m:02d}{d:02d}"}
                 for m, d in ((8, 14), (8, 28), (9, 11), (9, 25), (10, 9))],
}


# The live demo's vendor: the fixture's long-standing supplier, renamed to a fictional company whose domain is
# unregistered. Same history, bank account and payments; only the names change (tests keep the fixture's names).
VENDOR = {"vendor_id": "ven_tannerfield_supply", "name": "Tannerfield Supply Co.", "domain": "tannerfieldsupply.com",
          "email": "billing@tannerfieldsupply.com", "contact_name": "Dana Whitfield"}


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
            v.update(VENDOR, phone=phone)
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
    return (f"Seeded {db_path} (Tannerfield's and Kayla Brennan's phone on file since 2021 is {phone[:2]}…{phone[-4:]}).\n"
            f"Start the detector on it with: DORIS_DB={db_path} .venv/bin/python -m detector serve")


def fresh_demo(phone: str, db: str, data_dir: Path) -> str:
    """A clean demo: the current demo database, its outbox and the dashboard's own records move to
    an archive/<time>/ folder next to the database (nothing is deleted), then a new database is seeded. Restart both servers afterwards."""
    if not E164.fullmatch(phone or ""):
        raise SystemExit("Give the phone on file: --phone +1..., or DORIS_DEMO_PHONE in your key file")
    db_path = Path(db) if Path(db).is_absolute() else ROOT / db
    archive = db_path.parent / "archive" / datetime.now().strftime("%Y%m%d-%H%M%S")
    moved = []
    targets = [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), db_path.parent / f"{db_path.stem}_outbox"]
    targets += [data_dir / n for n in ("surfaces.db", "surfaces.db-wal", "surfaces.db-shm", "crs.json", "nessie.json")]
    for f in targets:
        if f.exists():
            dest = archive / ("surfaces" if f.parent == data_dir else "detector") / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest))
            moved.append(f.name)
    seeded = seed_demo(phone, str(db_path))
    return (f"Archived {', '.join(moved) or 'nothing'} to {archive}.\n{seeded.splitlines()[0]}\n"
            "Now restart the detector and the dashboard so they open the new files.")
