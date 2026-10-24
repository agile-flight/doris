"""Settings: model, thresholds, weights, paths. Read once from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WEIGHTS: dict[str, float] = {
    "highest": 0.60,
    "high": 0.30,
    "medium-high": 0.20,
    "medium": 0.12,
    "low-medium": 0.06,
}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model: str = "claude-opus-5-5"
    offline: bool = False
    extract_effort: str = "low"
    judge_effort: str = "medium"
    db_path: Path = PROJECT_ROOT / "data" / "doris.db"
    outbox_dir: Path = PROJECT_ROOT / "data" / "outbox"
    on_file_days: int = 90          # "on file" means unchanged for this long
    new_cert_days: int = 30         # a domain whose first certificate is younger than this is suspicious
    crtsh_timeout: float = 6.0
    block_threshold: float = 0.85
    hold_threshold: float = 0.30
    amount_anomaly_ratio: float = 2.5
    llm_weight: float = 0.5         # how much of the residual the LLM judgment can claim
    weights: dict[str, float] = field(default_factory=lambda: dict(WEIGHTS))

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        env = os.environ
        values = dict(
            model=env.get("DORIS_MODEL", cls.model),
            offline=_truthy(env.get("DORIS_OFFLINE")),
            extract_effort=env.get("DORIS_EXTRACT_EFFORT", cls.extract_effort),
            judge_effort=env.get("DORIS_JUDGE_EFFORT", cls.judge_effort),
            db_path=Path(env["DORIS_DB"]) if env.get("DORIS_DB") else cls.db_path,
            outbox_dir=Path(env["DORIS_OUTBOX"]) if env.get("DORIS_OUTBOX") else cls.outbox_dir,
            crtsh_timeout=float(env.get("DORIS_CRTSH_TIMEOUT", cls.crtsh_timeout)),
        )
        values.update(overrides)
        return cls(**values)
