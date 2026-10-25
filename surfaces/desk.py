"""The shared AP desk on SpacetimeDB.

Surfaces mirrors every Decision and every line of a callback into a SpacetimeDB database (module:
surfaces/spacetime). Every open dashboard subscribes, so the moment the vendor answers, every screen showing that
invoice flips at once, and several clerks see the same queue and the same call live. The detector stays the source
of truth: a screen that hears about a change still asks the dashboard for the decision itself.

Writes go through a queue on a background thread, so a slow or absent SpacetimeDB never delays a Twilio webhook.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .client import DetectorError

log = logging.getLogger("doris.desk")


def decision_row(d: dict) -> dict:
    reasons = d.get("reasons") or [""]
    amount = d.get("amount")
    return {
        "decisionId": str(d.get("decision_id") or ""), "invoiceId": str(d.get("invoice_id") or ""),
        "decision": str(d.get("decision") or ""), "supersedes": str(d.get("supersedes") or ""),
        "riskScore": float(d.get("risk_score") or 0.0), "headline": str(reasons[0] or "")[:300],
        "vendorClaimed": str(d.get("vendor_claimed") or "")[:120],
        "amount": f"{float(amount):,.2f} {d.get('currency') or ''}".strip() if isinstance(amount, (int, float)) else "",
        "createdAt": str(d.get("created_at") or ""), "seq": int(d.get("seq") or 0),
    }


class Desk:
    def __init__(self, app, poll_seconds: float = 1.0):
        s = app.settings
        self.app = app
        self.base = f"{s.spacetime_url}/v1/database/{s.spacetime_db}/call"
        self.token = s.spacetime_token
        self.poll_seconds = poll_seconds
        self._q: "queue.Queue[tuple[str, dict]]" = queue.Queue(maxsize=5000)
        self._seen: set[str] = set()
        self._stop = threading.Event()
        self.failures = 0

    def call(self, reducer: str, args: dict) -> bool:
        req = urllib.request.Request(f"{self.base}/{reducer}", data=json.dumps(args).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.failures += 1
            if self.failures in (1, 10, 100):
                log.warning("desk write failed (%s): %s", reducer, exc)
            return False

    def push(self, reducer: str, args: dict) -> None:
        try:
            self._q.put_nowait((reducer, args))
        except queue.Full:
            log.warning("desk queue full; dropping a %s", reducer)

    def on_event(self, decision_id: str, who: str, text: str, at: float) -> None:
        self.push("add_call_event", {"decisionId": decision_id, "who": who, "text": text, "at": float(at)})

    def mirror_once(self) -> int:
        """Copy any decision the desk hasn't seen. Returns how many were queued."""
        try:
            decisions = self.app.client.list(limit=50)
        except DetectorError:
            return 0
        n = 0
        for d in sorted(decisions, key=lambda x: int(x.get("seq") or 0)):   # chain order: a follow-up lands after what it supersedes
            if d.get("decision_id") and d["decision_id"] not in self._seen:
                self._seen.add(d["decision_id"])
                self.push("upsert_decision", decision_row(d))
                n += 1
        return n

    def _writer(self) -> None:
        while not self._stop.is_set():
            try:
                reducer, args = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not self.call(reducer, args) and reducer == "upsert_decision":
                self._seen.discard(args["decisionId"])        # try that one again on the next pass

    def _poller(self) -> None:
        while not self._stop.is_set():
            self.mirror_once()
            self._stop.wait(self.poll_seconds)

    def start(self) -> "Desk":
        self.app.store.listeners.append(self.on_event)
        for target in (self._writer, self._poller):
            threading.Thread(target=target, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def browser_config(self) -> Optional[dict[str, Any]]:
        s = self.app.settings
        if not s.spacetime_url:
            return None
        return {"uri": s.spacetime_url.replace("http://", "ws://").replace("https://", "wss://"), "db": s.spacetime_db}


def wire(app) -> Optional[Desk]:
    s = app.settings
    if not (s.spacetime_url and s.spacetime_token):
        return None
    app.desk = Desk(app).start()
    return app.desk
