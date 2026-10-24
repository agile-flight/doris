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
        allowed = {"status", "attempts", "result", "notes", "twilio_sid"}
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

    def count_checks(self, *, since: float, sender_hash: Optional[str] = None) -> int:
        sql, args = "SELECT COUNT(*) FROM door_checks WHERE created_at >= ?", [since]
        if sender_hash:
            sql += " AND sender_hash = ?"
            args.append(sender_hash)
        with self._lock:
            return self._con.execute(sql, args).fetchone()[0]

    def set_feedback(self, token: str, value: str) -> bool:
        with self._lock:
            cur = self._con.execute("UPDATE door_checks SET feedback = ? WHERE token = ?", (value, token))
            self._con.commit()
        return cur.rowcount == 1

    def stats(self) -> dict:
        with self._lock:
            rows = self._con.execute("SELECT verdict, feedback, COUNT(*) n FROM door_checks GROUP BY verdict, feedback").fetchall()
        out = {"checks": 0, "safe": 0, "suspicious": 0, "scam": 0, "up": 0, "down": 0}
        for r in rows:
            out["checks"] += r["n"]
            out[r["verdict"]] = out.get(r["verdict"], 0) + r["n"]
            if r["feedback"] in ("up", "down"):
                out[r["feedback"]] += r["n"]
        return out
