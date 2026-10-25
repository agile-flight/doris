"""The audit packet: every Decision for an invoice, each hash recomputed here from the contract's formula
(not taken on the detector's word), the call transcript, and how anyone can check it themselves."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

HOW_TO_VERIFY = (
    "Each Decision's audit_hash = 'sha256:' + sha256(prev_hash + '\\n' + payload), where payload is the Decision without "
    "audit_hash as canonical JSON (sort_keys=True, separators=(',', ':'), UTF-8). Every record's prev_hash is the hash "
    "of the record before it in the detector's log, so editing any record breaks every hash after it."
)
PYTHON = (
    "import hashlib, json\n"
    "def check(d):\n"
    "    payload = {k: v for k, v in d.items() if k != 'audit_hash'}\n"
    "    body = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)\n"
    "    return d['audit_hash'] == 'sha256:' + hashlib.sha256((d['prev_hash'] + '\\n' + body).encode()).hexdigest()"
)


def recompute(decision: dict) -> bool:
    try:
        payload = {k: v for k, v in decision.items() if k != "audit_hash"}
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return decision["audit_hash"] == "sha256:" + hashlib.sha256((decision["prev_hash"] + "\n" + body).encode("utf-8")).hexdigest()
    except (KeyError, TypeError):
        return False


def packet(chain: list[dict], events: list[dict], detector_verify: Any) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "invoice_id": chain[0].get("invoice_id") if chain else None,
        "outcome": chain[-1].get("decision") if chain else None,
        "decisions": chain,
        "recomputed_here": [{"decision_id": d.get("decision_id"), "seq": d.get("seq"), "audit_hash": d.get("audit_hash"),
                             "matches": recompute(d)} for d in chain],
        "detector_log_verification": detector_verify,
        "call_transcript": [{"at": datetime.fromtimestamp(e["at"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "who": e["who"], "text": e["text"]} for e in events],
        "how_to_verify": HOW_TO_VERIFY,
        "verify_in_python": PYTHON,
    }
