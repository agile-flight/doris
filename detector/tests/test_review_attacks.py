"""Replays the adversarial review's attacks end to end (API, HTML, PDF, LLM path, vendor edits).
Every attack must fail. The script prints PASS/FAIL per attack; see review_attacks.py."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_review_attack_is_stopped():
    out = subprocess.run([sys.executable, str(Path(__file__).with_name("review_attacks.py")), str(ROOT)],
                         capture_output=True, text=True, timeout=300)
    failures = [line for line in out.stdout.splitlines() if line.startswith("FAIL")]
    assert out.returncode == 0 and not failures and "passed" in out.stdout, "\n".join(failures) or out.stderr[-2000:]
