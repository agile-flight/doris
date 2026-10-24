"""payee_name_mismatch: money going to a name that is not the vendor on file."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from . import CheckContext, Signal

NAME, WEIGHT = "payee_name_mismatch", "high"
_SUFFIXES = {"co", "inc", "llc", "ltd", "corp", "corporation", "company", "limited", "the", "plc", "gmbh", "llp", "lp", "pllc", "incorporated"}


def normalise_name(name: str) -> str:
    text = name.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = [w for w in text.split() if w not in _SUFFIXES]
    return " ".join(words)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalise_name(a), normalise_name(b)).ratio()


def names_match(a: str, b: str, threshold: float = 0.85) -> bool:
    na, nb = normalise_name(a), normalise_name(b)
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= threshold


def check_payee_mismatch(ctx: CheckContext) -> Signal:
    payee = ctx.invoice.payee_name
    if ctx.vendor is None:
        return Signal(NAME, False, WEIGHT, "No vendor record to compare", {"payee": payee, "similarity": None})
    if not payee:
        return Signal(NAME, False, WEIGHT, "No payee name found", {"payee": None, "similarity": None})
    score = round(similarity(payee, ctx.vendor.name), 2)
    if names_match(payee, ctx.vendor.name):
        return Signal(NAME, False, WEIGHT, "Payee matches vendor on file", {"payee": payee, "similarity": score})
    return Signal(NAME, True, WEIGHT, f'Payee "{payee}" does not match vendor on file "{ctx.vendor.name}"', {"payee": payee, "similarity": score})
