"""Doris moves the money: when a decision ends in PAY, surfaces sends the payment through Capital One's Nessie
sandbox, once; when it ends in BLOCKED, nothing is sent and the record says so. The receipt lands in the trace and on
the shared desk. Only decisions made after the server starts are paid, so restarting never pays history again."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .client import DetectorError

log = logging.getLogger("doris.payments")


class Payments:
    def __init__(self, app, poll_seconds: float = 2.0, retries: int = 5):
        s = app.settings
        self.app, self.base, self.key = app, s.nessie_url.rstrip("/"), s.nessie_key
        self.poll_seconds, self.retries = poll_seconds, retries
        self.state_file = s.data_dir / "nessie.json"
        self.started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._done: set[str] = set()
        self._tries: dict[str, int] = {}
        self._stop = threading.Event()

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(f"{self.base}{path}?key={self.key}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())

    def payer_account(self) -> str:
        """The company's operating account in the sandbox, created once and remembered."""
        try:
            saved = json.loads(self.state_file.read_text())
            if saved.get("payer_account"):
                return saved["payer_account"]
        except (OSError, ValueError):
            pass
        company = self.app.settings.company
        customer = self._post("/customers", {"name": f"{company} (Doris AP)", "first_name": company, "last_name": "AP",
                                             "address": {"street_number": "2495", "street_name": "Bancroft Way", "city": "Berkeley",
                                                         "state": "CA", "zip": "94704"}})["objectCreated"]["_id"]
        account = self._post(f"/customers/{customer}/accounts", {"type": "Checking", "nickname": f"{company} operating",
                                                                  "rewards": 0, "balance": 500000})["objectCreated"]["_id"]
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"payer_account": account}))
        return account

    def pay(self, d: dict) -> Optional[str]:
        amount = d.get("amount")
        if not isinstance(amount, (int, float)) or amount <= 0:
            self.app.store.add_event(d["decision_id"], "system", "Approved, but the invoice has no amount, so nothing was sent")
            return None
        account = (d.get("bank_account_new") or d.get("bank_account_on_file") or "").strip("*")
        description = f"Doris PAY {d.get('invoice_id')} to {d.get('payee_name') or d.get('vendor_claimed') or 'payee'}" \
                      + (f" ****{account}" if account else "")
        made = self._post(f"/accounts/{self.payer_account()}/transfers",
                          {"amount": round(float(amount), 2), "status": "pending", "description": description[:200],
                           "transaction_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")})
        transfer = made["objectCreated"]["_id"]
        self.app.store.add_event(d["decision_id"], "system",
                                 f"Paid through Capital One (Nessie sandbox): {amount:,.2f} {d.get('currency') or ''} to "
                                 f"{d.get('payee_name') or 'the payee'}, transfer {transfer[:8]}")
        return transfer

    def once(self) -> int:
        try:
            decisions = self.app.client.list(limit=30)
        except DetectorError:
            return 0
        n = 0
        for d in sorted(decisions, key=lambda x: int(x.get("seq") or 0)):
            did = d.get("decision_id")
            if not did or did in self._done or str(d.get("created_at") or "") < self.started:
                continue
            if d.get("decision") == "BLOCKED":
                self._done.add(did)
                self.app.store.add_event(did, "system", "Nothing sent: no payment was created at Capital One")
                continue
            if d.get("decision") != "PAY":
                continue
            try:
                self.pay(d)
                self._done.add(did)
                n += 1
            except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
                self._tries[did] = self._tries.get(did, 0) + 1
                log.warning("payment for %s failed (%s): %s", did, self._tries[did], exc)
                if self._tries[did] >= self.retries:
                    self._done.add(did)
                    self.app.store.add_event(did, "system", "Approved, but the payment could not be sent to Capital One; pay it by hand")
        return n

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.once()
            self._stop.wait(self.poll_seconds)

    def start(self) -> "Payments":
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()


def wire(app) -> Optional[Payments]:
    if not app.settings.nessie_key:
        return None
    app.payments = Payments(app).start()
    return app.payments
