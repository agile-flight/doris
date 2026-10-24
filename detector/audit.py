"""The tamper-evident audit log.

Every record's hash covers the previous record's hash, so nobody can quietly rewrite
history. A Decision is one record: its payload is the Decision without audit_hash, and

    audit_hash = "sha256:" + sha256(prev_hash + "\\n" + canonical(payload))

exactly as contract/README.md documents, so anyone can recompute it from the Decision alone.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

GENESIS = "sha256:" + "0" * 64


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256((prev_hash + "\n" + canonical(payload)).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def verify_decision(decision: dict[str, Any]) -> bool:
    """Recompute one Decision's audit_hash from the Decision alone."""
    try:
        payload = {k: v for k, v in decision.items() if k != "audit_hash"}
        return compute_hash(decision["prev_hash"], payload) == decision["audit_hash"]
    except (KeyError, TypeError):
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class AuditRecord:
    seq: int
    ts: str
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


@dataclass
class VerifyResult:
    ok: bool
    records: int
    first_bad_seq: Optional[int] = None
    reason: Optional[str] = None


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self._lock = threading.RLock()
        with self._lock:
            self._con.execute(
                "CREATE TABLE IF NOT EXISTS audit_log ("
                " seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,"
                " payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL UNIQUE)"
            )
            self._con.commit()

    def close(self) -> None:
        self._con.close()

    def head(self) -> tuple[int, str]:
        with self._lock:
            row = self._con.execute("SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else (0, GENESIS)

    def append(self, kind: str, fields: dict[str, Any]) -> AuditRecord:
        """Seal fields into the chain. Sets seq, prev_hash and (if missing) created_at on the payload."""
        with self._lock:
            seq, prev = self.head()
            seq += 1
            payload = dict(fields)
            if kind != "decision":
                # A Decision's payload is fixed by the contract; every other record seals its kind,
                # so relabelling a row (say, hiding a decision as "intake") breaks verification.
                payload["record_kind"] = kind
            payload["seq"] = seq
            payload["prev_hash"] = prev
            payload.setdefault("created_at", now_iso())
            digest = compute_hash(prev, payload)
            self._con.execute(
                "INSERT INTO audit_log (seq, ts, kind, payload, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?)",
                (seq, payload["created_at"], kind, canonical(payload), prev, digest),
            )
            self._con.commit()
        return AuditRecord(seq=seq, ts=payload["created_at"], kind=kind, payload=payload, prev_hash=prev, hash=digest)

    @staticmethod
    def _row(row) -> AuditRecord:
        return AuditRecord(seq=row[0], ts=row[1], kind=row[2], payload=json.loads(row[3]), prev_hash=row[4], hash=row[5])

    def get(self, seq: int) -> Optional[AuditRecord]:
        with self._lock:
            row = self._con.execute("SELECT seq, ts, kind, payload, prev_hash, hash FROM audit_log WHERE seq = ?", (seq,)).fetchone()
        return self._row(row) if row else None

    def records(self, *, kind: Optional[str] = None, limit: Optional[int] = None, newest_first: bool = False) -> list[AuditRecord]:
        sql = "SELECT seq, ts, kind, payload, prev_hash, hash FROM audit_log"
        args: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            args.append(kind)
        sql += " ORDER BY seq " + ("DESC" if newest_first else "ASC")
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._lock:
            rows = self._con.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def verify(self) -> VerifyResult:
        """Walk the chain from genesis; the first inconsistent record fails it."""
        expected_prev, expected_seq, count = GENESIS, 1, 0
        for rec in self.records():
            count += 1
            if rec.seq != expected_seq:
                return VerifyResult(False, count, rec.seq, f"record {rec.seq}: expected seq {expected_seq} (a record is missing)")
            if rec.prev_hash != expected_prev:
                return VerifyResult(False, count, rec.seq, f"record {rec.seq}: prev_hash does not match the previous record's hash")
            if rec.kind != rec.payload.get("record_kind", "decision"):
                return VerifyResult(False, count, rec.seq, f"record {rec.seq}: kind column '{rec.kind}' does not match the sealed record")
            if rec.payload.get("seq") != rec.seq or rec.payload.get("prev_hash") != rec.prev_hash:
                return VerifyResult(False, count, rec.seq, f"record {rec.seq}: payload seq/prev_hash disagree with the row")
            if compute_hash(rec.prev_hash, rec.payload) != rec.hash:
                return VerifyResult(False, count, rec.seq, f"record {rec.seq}: stored hash does not match its payload (edited after the fact)")
            expected_prev, expected_seq = rec.hash, rec.seq + 1
        return VerifyResult(True, count)
