"""Confusion matrix on the labelled test set: catch rate AND false-positive rate.

Catch rate alone is meaningless (blocking everything scores 100%). "Caught" means the
money did not move: BLOCKED, CALLBACK or HOLD. For legitimate invoices the report separates
"blocked outright" (a real error) from "held for review" and "verified by callback" (by design).

Run: .venv/bin/python -m detector evaluate        -> prints the table, writes data/evidence/confusion_matrix.json
"""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import PROJECT_ROOT, Settings
from .models import Attachment
from .pipeline import FIXTURES, Doris

DEFAULT_TESTSET = Path(__file__).resolve().parent / "testset" / "cases.json"
DEFAULT_OUT = PROJECT_ROOT / "data" / "evidence" / "confusion_matrix.json"
CAUGHT = ("BLOCKED", "CALLBACK", "HOLD")


def load_testset(path: Path = DEFAULT_TESTSET) -> list[dict]:
    data = json.loads(Path(path).read_text())
    cases = data["cases"]
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids in the test set")
    for c in cases:
        if c["label"] not in ("fraud", "legit"):
            raise ValueError(f"{c['id']}: label must be fraud or legit")
    return cases


def compute_metrics(rows: list[dict]) -> dict:
    fraud = [r for r in rows if r["label"] == "fraud"]
    legit = [r for r in rows if r["label"] == "legit"]
    tp = sum(r["decision"] in CAUGHT for r in fraud)
    fn = len(fraud) - tp
    fp = sum(r["decision"] in CAUGHT for r in legit)
    tn = len(legit) - fp
    legit_blocked = sum(r["decision"] == "BLOCKED" for r in legit)
    by_decision: dict[str, int] = {}
    for r in legit:
        by_decision[r["decision"]] = by_decision.get(r["decision"], 0) + 1

    def rate(n, d):
        return round(n / d, 4) if d else None

    return {
        "total": len(rows), "fraud_total": len(fraud), "legit_total": len(legit),
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "catch_rate": rate(tp, len(fraud)),
        "false_positive_rate": rate(fp, len(legit)),
        "legit_blocked": legit_blocked,
        "legit_blocked_rate": rate(legit_blocked, len(legit)),
        "legit_by_decision": by_decision,
    }


@dataclass
class Report:
    metrics: dict
    rows: list[dict]
    clock: str
    testset: str
    offline: bool
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def to_dict(self) -> dict:
        return {"generated_at": self.generated_at, "testset": self.testset, "clock": self.clock, "offline": self.offline,
                "metrics": self.metrics, "rows": self.rows}

    def render(self) -> str:
        m = self.metrics
        pct = lambda x: "n/a" if x is None else f"{x * 100:.1f}%"
        lines = [
            f"Doris confusion matrix  ({'offline: rules only, Claude skipped' if self.offline else 'rules + Claude judgment'})",
            f"test set: {self.testset}  cases: {m['total']} ({m['fraud_total']} fraud, {m['legit_total']} legitimate)",
            "",
            f"                     caught (not paid)   paid",
            f"  fraud                 {m['tp']:>5}            {m['fn']:>4}",
            f"  legitimate            {m['fp']:>5}            {m['tn']:>4}",
            "",
            f"Catch rate (fraud stopped before payment): {m['tp']}/{m['fraud_total']} = {pct(m['catch_rate'])}",
            f"False-positive rate (legitimate invoices not paid): {m['fp']}/{m['legit_total']} = {pct(m['false_positive_rate'])}",
            f"  of which blocked outright: {m['legit_blocked']} ({pct(m['legit_blocked_rate'])}); by decision: "
            + ", ".join(f"{k} {v}" for k, v in sorted(m["legit_by_decision"].items())),
            "",
            f"  {'case':38} {'label':6} {'decision':9} {'risk':>5}  fired",
        ]
        for r in self.rows:
            mark = " " if r["correct"] else "!"
            lines.append(f"{mark} {r['id']:38} {r['label']:6} {r['decision']:9} {r['risk_score']:>5.2f}  {', '.join(r['fired']) or '-'}")
        misses = [r["id"] for r in self.rows if r["label"] == "fraud" and r["decision"] not in CAUGHT]
        if misses:
            lines += ["", "Missed fraud (!): " + ", ".join(misses)]
        return "\n".join(lines)


def run_evaluation(testset: Optional[Path] = None, out: Optional[Path] = None, offline: bool = True) -> Report:
    testset = Path(testset or DEFAULT_TESTSET)
    out = Path(out or DEFAULT_OUT)
    data = json.loads(testset.read_text())
    cases = load_testset(testset)
    clock = datetime.fromisoformat(data.get("clock", "2026-10-23T12:00:00Z").replace("Z", "+00:00"))
    tmp = Path(tempfile.mkdtemp(prefix="doris-eval-"))
    rows: list[dict] = []
    try:
        settings = Settings.from_env(offline=offline, db_path=tmp / "eval.db", outbox_dir=tmp / "outbox")
        doris = Doris(settings, seed=FIXTURES / "vendors.json", now=lambda: clock)
        emails, invoices = FIXTURES / "emails", FIXTURES / "invoices"
        for case in cases:
            if case.get("eml"):
                raw = (emails / case["eml"]).read_bytes()
            elif case.get("txt"):
                raw = (emails / case["txt"]).read_text()
            else:
                raw = case["text"]
            extra = [Attachment(case["pdf"], "application/pdf", (invoices / case["pdf"]).read_bytes())] if case.get("pdf") else []
            d = doris.check(raw, extra_attachments=extra)
            caught = d["decision"] in CAUGHT
            rows.append({
                "id": case["id"], "label": case["label"], "note": case.get("note", ""),
                "decision": d["decision"], "risk_score": d["risk_score"],
                "fired": [s["name"] for s in d["signals"] if s["fired"]],
                "caught": caught, "correct": caught if case["label"] == "fraud" else not caught,
                "decision_id": d["decision_id"],
            })
        doris.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report = Report(metrics=compute_metrics(rows), rows=rows, clock=clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    testset=str(testset.relative_to(PROJECT_ROOT) if testset.is_relative_to(PROJECT_ROOT) else testset), offline=offline)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return report
