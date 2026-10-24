"""prompt_injection: hidden or AI-addressed text. A real vendor has no reason to hide instructions."""
from __future__ import annotations

import re

from . import CheckContext, Signal
from ..models import HiddenSpan

NAME, WEIGHT = "prompt_injection", "highest"

AI_ADDRESSED = re.compile(
    r"\b(?:ai|a\.i\.|assistant|language\s+model|llm|chatbot|gpt|claude|copilot|openai|anthropic)\b[^.\n]{0,80}?"
    r"\b(?:approve[ds]?|ignore|disregard|mark|override|skip|bypass|pay|release|auto-?approve|do\s+not\s+flag)\b"
    r"|ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+instructions"
    r"|\bsystem\s+prompt\b"
    r"|\byou\s+are\s+an?\s+(?:ai|assistant|helpful)\b"
    r"|\bpre-?approved\b[^.\n]{0,60}\b(?:mark|skip|do\s+not\s+flag)\b",
    re.IGNORECASE,
)


# Instruction-like wording. Hidden HTML (email preheaders, tracking text) is common in legitimate
# templated mail, so it counts as an attack only when it tries to steer the reader or the AI.
INSTRUCTION = re.compile(
    r"\b(?:approve[ds]?|approval|pre-?approved|release\s+(?:the\s+)?payment|mark\s+(?:it|this|as)|skip|bypass|override"
    r"|ignore|disregard|do\s+not\s+(?:flag|verify|check|review|escalate|call)|no\s+need\s+to\s+(?:verify|call|check)"
    r"|already\s+(?:approved|verified|confirmed)|verified\s+by|whitelist(?:ed)?|auto-?pay|pay\s+(?:immediately|now))\b",
    re.IGNORECASE,
)


def _meaningful(text: str) -> bool:
    return len(re.sub(r"\W", "", text)) >= 3 and re.search(r"[A-Za-z]", text) is not None


def _describe(span: HiddenSpan) -> tuple[str, str]:
    if span.source == "pdf":
        if span.reason == "white_text":
            return "white_text", f'white text, line {span.line}'
        if span.reason == "tiny_text":
            return "tiny_text", f'tiny text ({span.font_size}pt), line {span.line}'
        return span.reason, f'off-page text, line {span.line}'
    return "css_hidden", "hidden HTML text"


def check_prompt_injection(ctx: CheckContext) -> Signal:
    findings: list[dict] = []
    for doc in ctx.docs:
        for span in doc.hidden:
            if _meaningful(span.text):
                kind, detail = _describe(span)
                findings.append({"kind": kind, "source": "pdf", "line": span.line, "font_size": span.font_size,
                                 "color": span.color, "text": span.text.strip()[:200], "detail": detail})
    for span in ctx.message.body_hidden:
        if _meaningful(span.text) and (AI_ADDRESSED.search(span.text) or INSTRUCTION.search(span.text)):
            kind, detail = _describe(span)
            findings.append({"kind": kind, "source": "html", "line": None, "font_size": None, "color": None,
                             "text": span.text.strip()[:200], "detail": detail})
    visible = "\n".join([ctx.message.body_text, *(d.visible for d in ctx.docs)])
    for m in AI_ADDRESSED.finditer(visible):
        quote = m.group(0).strip()
        findings.append({"kind": "ai_addressed_text", "source": "visible", "line": None, "font_size": None,
                         "color": None, "text": quote[:200], "detail": f'AI-addressed text: "{quote[:90]}"'})
    if not findings:
        return Signal(NAME, False, WEIGHT, "No hidden or AI-addressed text", {"count": 0})
    first = findings[0]
    evidence = {
        "kind": first["kind"], "source": first["source"], "line": first["line"], "font_size": first["font_size"],
        "color": first["color"], "count": len(findings),
        "findings": [{k: f[k] for k in ("kind", "source", "line", "text")} for f in findings[:5]],
    }
    return Signal(NAME, True, WEIGHT, first["detail"], evidence)
