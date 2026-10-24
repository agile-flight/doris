"""Versioned vendor records.

Every field value is a version with a `since` timestamp; the current value is the latest
version. "On file" means unchanged for Settings.on_file_days (VendorOnFile.trusted_phone).
Every creation and edit is a record in the audit chain, so the trail can show
"phone on file since March 2021".
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .audit import AuditLog
from .checks.payee import names_match, normalise_name
from .models import E164, VendorOnFile

FIELDS = ("domain", "phone", "email", "contact_name", "bank_last4", "routing_last4")
CONTACT_FIELDS = ("phone", "email", "contact_name", "domain", "bank_last4")


def validate_field(field: str, value: Optional[str]) -> Optional[str]:
    """Reject values that would break the contract when the Decision is sealed."""
    if value is None or value == "":
        return value
    text = str(value)
    if field == "phone" and not E164.fullmatch(text):
        raise ValueError(f"phone {text!r} is not E.164 (example: +14155550123)")
    if field == "bank_last4" and not (text.isalnum() and 2 <= len(text) <= 4):
        raise ValueError(f"bank_last4 {text!r} must be 2 to 4 alphanumerics")
    if field == "routing_last4" and not (text.isalnum() and 2 <= len(text) <= 4):
        raise ValueError(f"routing_last4 {text!r} must be 2 to 4 alphanumerics")
    return text


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class VendorStore:
    def __init__(self, path: str | Path, *, audit: AuditLog, now: Optional[Callable[[], datetime]] = None):
        self.audit = audit
        self._now = now or (lambda: datetime.now(timezone.utc))
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        self._lock = threading.RLock()
        with self._lock:
            self._con.executescript(
                "CREATE TABLE IF NOT EXISTS vendors (vendor_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS vendor_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT NOT NULL,"
                " field TEXT NOT NULL, value TEXT, since TEXT NOT NULL, recorded_at TEXT NOT NULL, source TEXT);"
                "CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT NOT NULL,"
                " amount REAL NOT NULL, invoice_id TEXT, paid_at TEXT NOT NULL);"
            )
            self._con.commit()

    def close(self) -> None:
        self._con.close()

    # --- writes -----------------------------------------------------------------------

    def exists(self, vendor_id: str) -> bool:
        with self._lock:
            return self._con.execute("SELECT 1 FROM vendors WHERE vendor_id = ?", (vendor_id,)).fetchone() is not None

    def create(self, vendor_id: str, name: str, *, domain: Optional[str], phone: Optional[str], email: Optional[str],
               contact_name: Optional[str], bank_last4: Optional[str], routing_last4: Optional[str] = None,
               since: datetime, source: str = "seed") -> VendorOnFile:
        if self.exists(vendor_id):
            raise ValueError(f"vendor {vendor_id} already exists")
        values = {"domain": domain.lower() if domain else None, "phone": validate_field("phone", phone),
                  "email": email.lower() if email else None, "contact_name": contact_name,
                  "bank_last4": validate_field("bank_last4", bank_last4),
                  "routing_last4": validate_field("routing_last4", routing_last4)}
        with self._lock:
            self._con.execute("INSERT INTO vendors (vendor_id, name, created_at) VALUES (?, ?, ?)", (vendor_id, name, iso_z(since)))
            for field, value in values.items():
                if value is not None:
                    self._con.execute(
                        "INSERT INTO vendor_versions (vendor_id, field, value, since, recorded_at, source) VALUES (?, ?, ?, ?, ?, ?)",
                        (vendor_id, field, value, iso_z(since), iso_z(since), source))
            self._con.commit()
        self.audit.append("vendor_created", {"vendor_id": vendor_id, "name": name, **values, "since": iso_z(since),
                                             "source": source, "created_at": iso_z(self._now())})
        return self.get(vendor_id)

    def update_field(self, vendor_id: str, field: str, value: Optional[str], *, at: datetime, source: str) -> dict:
        """Record a new version of one field. Every edit is a high-risk event and lands in the audit chain."""
        if field not in FIELDS:
            raise ValueError(f"unknown field {field!r}; one of {FIELDS}")
        if not self.exists(vendor_id):
            raise KeyError(vendor_id)
        if field in ("domain", "email") and value:
            value = value.lower()
        value = validate_field(field, value)
        old = self._current(vendor_id).get(field, (None, None))[0]
        with self._lock:
            self._con.execute(
                "INSERT INTO vendor_versions (vendor_id, field, value, since, recorded_at, source) VALUES (?, ?, ?, ?, ?, ?)",
                (vendor_id, field, value, iso_z(at), iso_z(self._now()), source))
            self._con.commit()
        record = self.audit.append("vendor_edit", {"vendor_id": vendor_id, "field": field, "old": old, "new": value,
                                                   "since": iso_z(at), "source": source, "created_at": iso_z(self._now())})
        return record.payload

    def record_payment(self, vendor_id: str, amount: float, *, invoice_id: Optional[str], at: datetime) -> None:
        with self._lock:
            self._con.execute("INSERT INTO payments (vendor_id, amount, invoice_id, paid_at) VALUES (?, ?, ?, ?)",
                              (vendor_id, float(amount), invoice_id, iso_z(at)))
            self._con.commit()

    def seed(self, path: str | Path) -> int:
        """Load vendors from a JSON fixture. Idempotent: existing vendor_ids are skipped."""
        data = json.loads(Path(path).read_text())
        added = 0
        for v in data["vendors"]:
            if self.exists(v["vendor_id"]):
                continue
            since = parse_iso(v["since"])
            self.create(v["vendor_id"], v["name"], domain=v.get("domain"), phone=v.get("phone"), email=v.get("email"),
                        contact_name=v.get("contact_name"), bank_last4=v.get("bank_last4"),
                        routing_last4=v.get("routing_last4"), since=since, source="seed")
            for p in v.get("payments", []):
                self.record_payment(v["vendor_id"], p["amount"], invoice_id=p.get("invoice_id"), at=parse_iso(p["paid_at"]))
            added += 1
        return added

    # --- reads ------------------------------------------------------------------------

    def _current(self, vendor_id: str) -> dict[str, tuple[Optional[str], datetime, datetime]]:
        """field -> (current value, since, recorded_at) using the latest version by since, then insertion."""
        with self._lock:
            rows = self._con.execute("SELECT field, value, since, recorded_at FROM vendor_versions WHERE vendor_id = ? ORDER BY since, id", (vendor_id,)).fetchall()
        current: dict[str, tuple[Optional[str], datetime, datetime]] = {}
        for field, value, since, recorded_at in rows:
            current[field] = (value, parse_iso(since), parse_iso(recorded_at))
        return current

    def history(self, vendor_id: str, field: str) -> list[dict]:
        with self._lock:
            rows = self._con.execute(
                "SELECT value, since, recorded_at, source FROM vendor_versions WHERE vendor_id = ? AND field = ? ORDER BY since, id",
                (vendor_id, field)).fetchall()
        return [{"value": r[0], "since": r[1], "recorded_at": r[2], "source": r[3]} for r in rows]

    def get(self, vendor_id: str) -> Optional[VendorOnFile]:
        with self._lock:
            row = self._con.execute("SELECT name, created_at FROM vendors WHERE vendor_id = ?", (vendor_id,)).fetchone()
            if not row:
                return None
            vendor_created_at = parse_iso(row[1])
            payments = [r[0] for r in self._con.execute("SELECT amount FROM payments WHERE vendor_id = ? ORDER BY paid_at, id", (vendor_id,)).fetchall()]
        current = self._current(vendor_id)
        last_change: Optional[datetime] = None
        last_change_recorded: Optional[datetime] = None
        last_field: Optional[str] = None
        for field in CONTACT_FIELDS:
            versions = self.history(vendor_id, field)
            for version in versions:  # every version after creation is a change
                since = parse_iso(version["since"])
                recorded_at = parse_iso(version["recorded_at"])
                # Skip only the initial creation version (recorded at the same time as vendor creation)
                if recorded_at <= vendor_created_at:
                    continue
                if last_change is None or since > last_change:
                    last_change, last_field = since, field
                if last_change_recorded is None or recorded_at > last_change_recorded:
                    last_change_recorded = recorded_at

        def val(field):
            return current.get(field, (None, None, None))

        return VendorOnFile(
            vendor_id=vendor_id, name=row[0], domain=val("domain")[0],
            phone=val("phone")[0], phone_since=val("phone")[1], phone_recorded_at=val("phone")[2],
            email=val("email")[0], email_since=val("email")[1],
            contact_name=val("contact_name")[0],
            bank_last4=val("bank_last4")[0], bank_since=val("bank_last4")[1],
            routing_last4=val("routing_last4")[0], routing_since=val("routing_last4")[1],
            payment_history=payments,
            last_contact_change_at=last_change,
            last_contact_change_recorded_at=last_change_recorded,
            created_at=vendor_created_at,
            last_contact_change_field=last_field,
        )

    def list(self) -> list[VendorOnFile]:
        with self._lock:
            ids = [r[0] for r in self._con.execute("SELECT vendor_id FROM vendors ORDER BY vendor_id").fetchall()]
        return [v for v in (self.get(i) for i in ids) if v]

    def known_domains(self) -> list[str]:
        return sorted({v.domain for v in self.list() if v.domain})

    def match(self, *, vendor_claimed: Optional[str], sender_domain: Optional[str], payee_name: Optional[str]) -> Optional[VendorOnFile]:
        """Find the record the message is about. Exact domain first, then the claimed name,
        then the payee. A lookalike domain never matches by domain; it has to earn the match
        by name, which is what lets the bank-change check compare against the real record."""
        vendors = self.list()
        if sender_domain:
            for v in vendors:
                if v.domain and v.domain == sender_domain.lower():
                    return v
        if vendor_claimed:
            claimed = normalise_name(vendor_claimed)
            for v in vendors:
                if names_match(vendor_claimed, v.name) or (claimed and claimed.startswith(normalise_name(v.name) + " ")):
                    return v
        if payee_name:
            for v in vendors:
                if names_match(payee_name, v.name):
                    return v
        return None
