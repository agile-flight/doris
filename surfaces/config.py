"""Surfaces settings. Secrets come from the environment, or from a key file kept outside the repo
(DORIS_KEYS_FILE), never from git."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def load_key_file(path: Optional[str]) -> dict[str, str]:
    """Pick credentials out of a loosely labelled text file by their shapes."""
    if not path or not Path(path).is_file():
        return {}
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    found: dict[str, str] = {}
    for name, pattern in (("ANTHROPIC_API_KEY", r"sk-ant-[A-Za-z0-9_\-]+"), ("TWILIO_ACCOUNT_SID", r"\bAC[0-9a-f]{32}\b"),
                          ("TWILIO_API_KEY", r"\bSK[0-9a-f]{32}\b"), ("CARTESIA_API_KEY", r"sk_car_[A-Za-z0-9]+")):
        m = re.search(pattern, text)
        if m:
            found[name] = m.group(0)
    for line in text.splitlines():
        if "secret" in line.lower():
            m = [t for t in re.findall(r"\b[A-Za-z0-9]{32}\b", line) if not t.startswith(("AC", "SK"))]
            if m:
                found["TWILIO_API_SECRET"] = m[-1]
    return found


def _bool(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    detector_url: str = "http://127.0.0.1:8787"
    host: str = "127.0.0.1"
    port: int = 8788              # dashboard: localhost only
    hooks_port: int = 8789        # Twilio + inbound email webhooks: the only port to expose publicly
    public_base_url: str = ""     # e.g. https://<machine>.<tailnet>.ts.net (Tailscale Funnel to hooks_port)
    data_dir: Path = ROOT / "data" / "surfaces"
    twilio_account_sid: str = ""
    twilio_api_key: str = ""
    twilio_api_secret: str = ""
    twilio_from: str = ""         # the Twilio number Doris calls from (E.164)
    cartesia_api_key: str = ""
    cartesia_voice: str = "d1d9c946-7cfc-4378-85a4-07d09827cb7e"   # Jolene: honeyed Southern, i.e. Doris
    cartesia_model: str = "sonic-3"
    auto_call: bool = False       # dial as soon as a CALLBACK decision arrives
    company: str = "Cal Hacks Robotics"
    inbound_token: str = ""       # secret path segment for the inbound email webhook
    postmark_token: str = ""      # outbound replies; empty = write replies to data/surfaces/outbox
    door_from: str = ""
    door_per_sender_per_hour: int = 5
    door_per_minute: int = 30
    door_max_bytes: int = 5_000_000
    bounty_freeze_at: str = ""    # ISO time; after it the door stops checking (protects the demo)
    extra: dict = field(default_factory=dict)

    @property
    def twilio_ready(self) -> bool:
        return all([self.twilio_account_sid, self.twilio_api_key, self.twilio_api_secret, self.twilio_from, self.public_base_url])

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        env = {**load_key_file(os.environ.get("DORIS_KEYS_FILE")), **os.environ}
        s = cls(
            detector_url=env.get("DORIS_DETECTOR_URL", cls.detector_url).rstrip("/"),
            port=int(env.get("DORIS_SURFACES_PORT", cls.port)),
            hooks_port=int(env.get("DORIS_HOOKS_PORT", cls.hooks_port)),
            public_base_url=env.get("DORIS_PUBLIC_URL", "").rstrip("/"),
            data_dir=Path(env["DORIS_SURFACES_DATA"]) if env.get("DORIS_SURFACES_DATA") else cls.data_dir,
            twilio_account_sid=env.get("TWILIO_ACCOUNT_SID", ""), twilio_api_key=env.get("TWILIO_API_KEY", ""),
            twilio_api_secret=env.get("TWILIO_API_SECRET", ""), twilio_from=env.get("TWILIO_FROM", ""),
            cartesia_api_key=env.get("CARTESIA_API_KEY", ""),
            cartesia_voice=env.get("CARTESIA_VOICE", cls.cartesia_voice), cartesia_model=env.get("CARTESIA_MODEL", cls.cartesia_model),
            auto_call=_bool(env.get("DORIS_AUTO_CALL")), company=env.get("DORIS_COMPANY", cls.company),
            inbound_token=env.get("DORIS_INBOUND_TOKEN", ""), postmark_token=env.get("POSTMARK_SERVER_TOKEN", ""),
            door_from=env.get("DORIS_DOOR_FROM", ""), bounty_freeze_at=env.get("DORIS_BOUNTY_FREEZE_AT", ""),
        )
        if env.get("DORIS_AP_ADDRESS"):
            s.extra["ap_address"] = env["DORIS_AP_ADDRESS"].strip().lower()
        for k, v in overrides.items():
            setattr(s, k, v)
        return s
