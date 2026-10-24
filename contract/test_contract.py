"""The contract test: the schema is valid, every sample validates, hashes recompute.

This is the check that ties both laptops together. Keep it fast and dependency-light
(jsonschema only); detector and surface tests live in their own folders.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "decision.schema.json").read_text())
SAMPLES = sorted((HERE / "samples").glob("*.json"))
GENESIS = "sha256:" + "0" * 64


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def audit_hash(decision: dict) -> str:
    payload = {k: v for k, v in decision.items() if k != "audit_hash"}
    digest = hashlib.sha256((decision["prev_hash"] + "\n" + canonical(payload)).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)


def test_there_are_samples():
    assert len(SAMPLES) >= 3, "contract/samples needs a clean pay, a bank-change block and a prompt-injection block"


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_sample_validates(path: Path):
    decision = json.loads(path.read_text())
    errors = sorted(Draft202012Validator(SCHEMA).iter_errors(decision), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_sample_timestamps_parse(path: Path):
    decision = json.loads(path.read_text())
    datetime.fromisoformat(decision["created_at"].replace("Z", "+00:00"))
    if decision["callback_number_on_file_since"]:
        datetime.fromisoformat(decision["callback_number_on_file_since"].replace("Z", "+00:00"))
    for event in decision["ct_timeline"]:
        datetime.fromisoformat(event["at"].replace("Z", "+00:00"))


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_sample_hash_recomputes(path: Path):
    decision = json.loads(path.read_text())
    assert decision["audit_hash"] == audit_hash(decision)


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_sample_decision_rules(path: Path):
    """The rules in contract/README.md hold on every sample."""
    d = json.loads(path.read_text())
    fired = {s["name"] for s in d["signals"] if s["fired"]}
    if "callback_result" in fired or "human_review" in fired:
        assert d["decision"] in ("PAY", "HOLD", "BLOCKED") and d["supersedes"]
    elif "prompt_injection" in fired:
        assert d["decision"] == "BLOCKED" and d["risk_score"] >= 0.95
    elif "bank_account_changed" in fired and d["callback_number"]:
        assert d["decision"] == "CALLBACK"
    elif d["risk_score"] >= 0.85:
        assert d["decision"] == "BLOCKED"
    elif "new_vendor" in fired:
        assert d["decision"] == "HOLD"
    elif "bank_account_changed" in fired:
        assert d["decision"] == "HOLD"
    else:
        assert d["decision"] == ("PAY" if d["risk_score"] < 0.30 else "HOLD")
    if d["callback_number"] is not None:
        assert d["callback_number_on_file_since"] is not None
