"""Surfaces' own state: calls in progress and door checks with their feedback. No email bodies."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._con.executescript(
                "CREATE TABLE IF NOT EXISTS calls (token TEXT PRIMARY KEY, decision_id TEXT NOT NULL, to_number TEXT,"
                " status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, notes TEXT, twilio_sid TEXT,"
                " created_at REAL NOT NULL, updated_at REAL NOT NULL);"
                "CREATE INDEX IF NOT EXISTS calls_by_decision ON calls(decision_id, created_at);"
                "CREATE TABLE IF NOT EXISTS door_checks (token TEXT PRIMARY KEY, decision_id TEXT, verdict TEXT NOT NULL,"
                " sender_hash TEXT NOT NULL, created_at REAL NOT NULL, feedback TEXT);"
                "CREATE INDEX IF NOT EXISTS door_by_sender ON door_checks(sender_hash, created_at);")
            cols = {r[1] for r in self._con.execute("PRAGMA table_info(calls)")}
            if "stage" not in cols:
                self._con.execute("ALTER TABLE calls ADD COLUMN stage TEXT")
            self._con.commit()

    def close(self) -> None:
        self._con.close()

    # calls
    def add_call(self, token: str, decision_id: str, to_number: str, status: str = "starting") -> None:
        now = time.time()
        with self._lock:
            self._con.execute("INSERT INTO calls (token, decision_id, to_number, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                              (token, decision_id, to_number, status, now, now))
            self._con.commit()

    def update_call(self, token: str, **fields: Any) -> None:
        allowed = {"status", "attempts", "result", "notes", "twilio_sid", "stage"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        with self._lock:
            self._con.execute(f"UPDATE calls SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ? WHERE token = ?",
                              (*sets.values(), time.time(), token))
            self._con.commit()

    def call(self, token: str) -> Optional[dict]:
        with self._lock:
            row = self._con.execute("SELECT * FROM calls WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None

    def calls_since(self, since: float) -> int:
        with self._lock:
            return self._con.execute("SELECT COUNT(*) FROM calls WHERE created_at >= ?", (since,)).fetchone()[0]

    def latest_call(self, decision_id: str) -> Optional[dict]:
        with self._lock:
            row = self._con.execute("SELECT * FROM calls WHERE decision_id = ? ORDER BY created_at DESC LIMIT 1", (decision_id,)).fetchone()
        return dict(row) if row else None

    # door
    def add_check(self, token: str, decision_id: Optional[str], verdict: str, sender_hash: str) -> None:
        with self._lock:
            self._con.execute("INSERT INTO door_checks (token, decision_id, verdict, sender_hash, created_at) VALUES (?,?,?,?,?)",
                              (token, decision_id, verdict, sender_hash, time.time()))
            self._con.commit()

    def reserve_check(self, token: str, sender_hash: str, verdict: str, *, per_sender_hour: int, per_minute: int,
                      per_day: Optional[int] = None) -> Optional[str]:
        """Count and insert in one locked step, so a burst can't slip past the limits. None = reserved."""
        now = time.time()
        with self._lock:
            if per_day is not None and self._con.execute(
                    "SELECT COUNT(*) FROM door_checks WHERE created_at >= ? AND verdict NOT IN ('frozen')", (now - 86400,)).fetchone()[0] >= per_day:
                return "daily budget"
            n_sender = self._con.execute("SELECT COUNT(*) FROM door_checks WHERE created_at >= ? AND sender_hash = ?",
                                         (now - 3600, sender_hash)).fetchone()[0]
            if n_sender >= per_sender_hour:
                return "sender rate limit"
            if self._con.execute("SELECT COUNT(*) FROM door_checks WHERE created_at >= ?", (now - 60,)).fetchone()[0] >= per_minute:
                return "global rate limit"
            self._con.execute("INSERT INTO door_checks (token, decision_id, verdict, sender_hash, created_at) VALUES (?,?,?,?,?)",
                              (token, None, verdict, sender_hash, now))
            self._con.commit()
        return None

    def count_checks(self, *, since: float, sender_hash: Optional[str] = None) -> int:
        sql, args = "SELECT COUNT(*) FROM door_checks WHERE created_at >= ?", [since]
        if sender_hash:
            sql += " AND sender_hash = ?"
            args.append(sender_hash)
        with self._lock:
            return self._con.execute(sql, args).fetchone()[0]

    def set_verdict(self, token: str, verdict: str, decision_id: Optional[str]) -> None:
        with self._lock:
            self._con.execute("UPDATE door_checks SET verdict = ?, decision_id = ? WHERE token = ?", (verdict, decision_id, token))
            self._con.commit()

    def set_feedback(self, token: str, value: str) -> bool:
        with self._lock:
            cur = self._con.execute("UPDATE door_checks SET feedback = ? WHERE token = ? AND feedback IS NULL AND verdict IN ('safe','suspicious','scam')", (value, token))
            self._con.commit()
        return cur.rowcount == 1

    def stats(self) -> dict:
        with self._lock:
            rows = self._con.execute("SELECT verdict, feedback, COUNT(*) n FROM door_checks WHERE verdict IN ('safe','suspicious','scam')"
                                     " GROUP BY verdict, feedback").fetchall()
        with self._lock:
            senders = self._con.execute("SELECT COUNT(DISTINCT sender_hash) FROM door_checks WHERE verdict IN ('safe','suspicious','scam')").fetchone()[0]
        out = {"checks": 0, "safe": 0, "suspicious": 0, "scam": 0, "up": 0, "down": 0, "senders": senders}
        for r in rows:
            out["checks"] += r["n"]
            out[r["verdict"]] = out.get(r["verdict"], 0) + r["n"]
            if r["feedback"] in ("up", "down"):
                out[r["feedback"]] += r["n"]
        return out
