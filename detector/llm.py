"""The only place that talks to Claude. Structured outputs via messages.parse.

Degrades gracefully: offline mode, missing credentials, refusals and API errors all
return None, and the caller falls back to the deterministic path.
"""
from __future__ import annotations

import logging
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from .config import Settings

log = logging.getLogger("doris.llm")
T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Claude:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        self._disabled: Optional[str] = "offline mode (DORIS_OFFLINE=1)" if settings.offline else None
        self.last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self._disabled is None

    @property
    def disabled_reason(self) -> Optional[str]:
        return self._disabled

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def parse(self, *, system: str, user: str, output_model: Type[T], effort: str, max_tokens: int = 4096) -> Optional[T]:
        """One structured-output call. Returns the parsed model or None."""
        if not self.available:
            return None
        import anthropic
        client = self._get_client()
        kwargs = dict(
            model=self.settings.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_model, output_config={"effort": effort},
        )
        try:
            try:
                # Server-side refusal fallbacks: a policy decline re-runs on a fallback model.
                message = client.beta.messages.parse(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            except anthropic.BadRequestError as exc:
                text = str(exc).lower()
                if "fallback" in text or "beta" in text:
                    log.warning("fallbacks rejected, retrying without: %s", exc)
                    message = client.messages.parse(**kwargs)
                else:
                    raise
        except anthropic.AuthenticationError as exc:
            self._disabled = f"no valid Anthropic credentials ({exc.__class__.__name__})"
            log.warning("Claude disabled: %s", self._disabled)
            return None
        except anthropic.AnthropicError as exc:
            self.last_error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
            if "authentication" in str(exc).lower() or "api_key" in str(exc).lower():
                self._disabled = f"no valid Anthropic credentials ({exc.__class__.__name__})"
            log.warning("Claude call failed: %s", self.last_error)
            return None
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            self.last_error = f"refusal: {getattr(details, 'category', None)}"
            log.warning("Claude refused: %s", self.last_error)
            return None
        return message.parsed_output
