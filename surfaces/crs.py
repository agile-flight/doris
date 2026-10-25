"""CRS Credit API (sandbox): a second, independent opinion on the channel a request came in on.

When a bank change claims to come from a person on file (an employee's direct deposit), Doris asks CRS Fraud Finder
(AtData data) whether the email it came from, and the IP that sent it, belong to the person HR already has on file:
their name, phone and home address. CRS answers from data the attacker doesn't control. A high-risk answer goes in
the trace, and it stops the payment even after the phone call says yes, so a person has to release it.

Credentials are the user's own: CRS_USERNAME and CRS_PASSWORD in a key file outside the repo (DORIS_KEYS_FILE).
Doris logs in once and never retries a failed login, because repeated failures lock the sandbox account.
The sandbox only knows CRS's fake personas, so only records with identity details on file are ever sent.
"""
from __future__ import annotations

import email
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from email import policy
from typing import Optional

log = logging.getLogger("doris.crs")

SANDBOX = "https://api-sandbox.stitchcredit.com/api"
HIGH = 60          # CRS's own label: the PDF shows score x 0.1, and above 6 is "high"

# Identity details HR keeps on file for each person, keyed by the detector's record id. In production this comes from
# the HR system; in the demo it's CRS sandbox persona P13, whose canned answer is what a real attack looks like.
PEOPLE_ON_FILE: dict[str, dict] = {
    "emp_kayla_brennan": {"firstName": "KAYLA", "lastName": "BRENNAN", "phoneNumber": "5550101013",
                          "address": {"addressLine1": "9021 KINGSTON PIKE", "city": "KNOXVILLE", "state": "TN", "postalCode": "37923"}},
}


class CRSError(Exception):
    pass


class CRS:
    def __init__(self, username: str, password: str, base: str = SANDBOX, timeout: float = 20):
        self.base, self.timeout = base.rstrip("/"), timeout
        self._user, self._pass = username, password
        self._token: Optional[str] = None
        self._login_failed: Optional[str] = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:          # never shows the credentials
        return f"CRS({self.base})"

    def _request(self, path: str, body: dict, token: Optional[str]) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {}

    def token(self) -> str:
        with self._lock:
            if self._token:
                return self._token
            if self._login_failed:
                raise CRSError(self._login_failed)
            if not self._user or not self._pass:
                raise CRSError("CRS credentials are missing: put CRS_USERNAME and CRS_PASSWORD in your key file")
            try:
                status, data = self._request("/users/login", {"username": self._user, "password": self._pass}, None)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise CRSError(f"CRS could not be reached: {exc.__class__.__name__}") from None
            token = data.get("token") if isinstance(data, dict) else None
            if status != 200 or not token:
                codes = ",".join(map(str, data.get("codes") or [])) if isinstance(data, dict) else ""
                self._login_failed = (f"CRS login failed (HTTP {status}{', ' + codes if codes else ''}). Not retrying: repeated "
                                      "failed logins lock the sandbox account. Fix the key file, then restart.")
                raise CRSError(self._login_failed)
            self._token = token
            return token

    def post(self, path: str, body: dict) -> dict:
        try:
            status, data = self._request(path, body, self.token())
            if status == 401:                  # an expired token: log in again once (the login itself already worked)
                with self._lock:
                    self._token = None
                status, data = self._request(path, body, self.token())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise CRSError(f"CRS could not be reached: {exc.__class__.__name__}") from None
        if status != 200:
            codes = ",".join(map(str, data.get("codes") or [])) if isinstance(data, dict) else ""
            raise CRSError(f"CRS answered HTTP {status}{' ' + codes if codes else ''}")
        return data

    def fraud_finder(self, query: dict) -> dict:
        return self.post("/fraud-finder/fraud-finder", query)


# --- reading the request and CRS's answer -------------------------------------------------------------------------

_IP = re.compile(r"\[?((?:\d{1,3}\.){3}\d{1,3})\]?")


def sender_ip(raw: bytes) -> Optional[str]:
    """The IP the message was sent from: X-Originating-IP, else the earliest Received hop. Evidence about the channel."""
    try:
        msg = email.message_from_bytes(raw, policy=policy.compat32)
    except Exception:
        return None
    for value in [msg.get("X-Originating-IP", "")] + list(reversed(msg.get_all("Received") or [])):
        m = _IP.search(str(value))
        if m and all(0 <= int(p) <= 255 for p in m.group(1).split(".")):
            return m.group(1)
    return None


def _no_matches(node, path=()) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                out += _no_matches(v, path + (k,))
            elif str(k).lower().endswith("match") and str(v).lower() == "no_match":
                name = str(k)[:-len("match")].strip("_") or (path[-1] if path else "")
                out.append(name.replace("_", " ") or "match")
    elif isinstance(node, list):
        for v in node:
            out += _no_matches(v, path)
    return out


def summarize(result: dict, sender: str, today: Optional[date] = None) -> dict:
    risk = result.get("risk") if isinstance(result.get("risk"), dict) else {}
    try:
        score: Optional[int] = int(float(risk.get("score")))
    except (TypeError, ValueError):
        score = None
    level = None if score is None else ("high" if score > HIGH else "medium" if score > 30 else "low")
    first_seen = str(((result.get("eam") or {}).get("date_first_seen")) or "")
    age = None
    try:
        age = ((today or datetime.now(timezone.utc).date()) - date.fromisoformat(first_seen[:10])).days
    except ValueError:
        pass
    status = str(((result.get("email_validation") or {}).get("status")) or "")
    misses = sorted(set(_no_matches(result)))
    lines = [f"CRS Fraud Finder on the email this came from ({sender}): "
             + (f"risk {score} of 100, {level}." if score is not None else "no risk score returned.")]
    if age is not None:
        lines.append(f"CRS first saw that email address {age} day{'s' if age != 1 else ''} ago ({first_seen[:10]}).")
    if status and status != "valid":
        lines.append(f"CRS rates the address itself {status}.")
    if misses:
        lines.append("CRS found no match with the person on file for: " + ", ".join(misses) + ".")
    return {"score": score, "level": level, "email_age_days": age, "email_status": status or None, "no_match": misses,
            "high": level == "high", "lines": lines}


# --- wiring into surfaces ------------------------------------------------------------------------------------------

class Checker:
    def __init__(self, app, crs: CRS):
        self.app, self.crs = app, crs
        self.state_file = app.settings.data_dir / "crs.json"
        self._lock = threading.Lock()
        try:
            self._flags: dict[str, dict] = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            self._flags = {}

    def flagged(self, invoice_id: Optional[str]) -> Optional[dict]:
        with self._lock:
            return self._flags.get(invoice_id or "")

    def _flag(self, invoice_id: str, summary: dict) -> None:
        with self._lock:
            self._flags[invoice_id] = {k: summary[k] for k in ("score", "level", "email_age_days")}
            try:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                self.state_file.write_text(json.dumps(self._flags))
            except OSError:
                pass

    def check(self, decision: dict, raw: bytes) -> Optional[dict]:
        person = PEOPLE_ON_FILE.get(decision.get("vendor_id") or "")
        sender = (decision.get("sender_email") or "").strip()
        if not person or not sender or not decision.get("bank_account_new"):
            return None
        did = decision["decision_id"]
        query = {**person, "email": sender}
        ip = sender_ip(raw)
        if ip:
            query["ipAddress"] = ip
        try:
            summary = summarize(self.crs.fraud_finder(query), sender)
        except CRSError as exc:
            log.warning("CRS check for %s failed: %s", did, exc)
            self.app.store.add_event(did, "system", f"CRS check didn't run: {exc}")
            return None
        for line in summary["lines"]:
            self.app.store.add_event(did, "system", line)
        if summary["high"]:
            self._flag(decision.get("invoice_id") or did, summary)
            self.app.store.add_event(did, "system", "CRS rates this request high risk, so no money moves on a phone yes alone: "
                                                    "a person has to release it.")
        return summary

    def after_check(self, decision: dict, raw: bytes) -> None:
        threading.Thread(target=self.check, args=(decision, raw), daemon=True).start()


def wire(app) -> Optional[Checker]:
    s = app.settings
    if not (s.crs_username and s.crs_password):
        return None
    app.crs = Checker(app, CRS(s.crs_username, s.crs_password, s.crs_url))
    return app.crs


def after_check(app, decision: dict, raw: bytes) -> None:
    checker = getattr(app, "crs", None)
    if checker:
        checker.after_check(decision, raw)


def selftest(settings) -> int:
    """`python -m surfaces crs-check`: sign in with your credentials and run two sandbox personas."""
    if not (settings.crs_username and settings.crs_password):
        print("No CRS credentials found. Put CRS_USERNAME=... and CRS_PASSWORD=... in a key file and point DORIS_KEYS_FILE at it.")
        return 2
    crs = CRS(settings.crs_username, settings.crs_password, settings.crs_url)
    cases = [("P13, the payroll demo's attacker", {**PEOPLE_ON_FILE["emp_kayla_brennan"], "email": "kb.9921x@example.com",
                                                   "ipAddress": "198.51.100.13"}),
             ("P01, a low-risk control", {"firstName": "MARGARET", "lastName": "OKAFOR", "phoneNumber": "5550101001",
                                          "email": "margaret.okafor@example.com", "ipAddress": "203.0.113.11",
                                          "address": {"addressLine1": "1420 WILLOW CREEK DR", "city": "SAN JOSE", "state": "CA",
                                                      "postalCode": "95125"}})]
    try:
        crs.token()
        print("Signed in to the CRS sandbox.")
        for label, query in cases:
            s = summarize(crs.fraud_finder(query), query["email"])
            print(f"\n{label}:")
            for line in s["lines"]:
                print("  " + line)
    except CRSError as exc:
        print(str(exc))
        return 1
    return 0
