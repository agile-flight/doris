"""lookalike_domain: edit distance, homoglyphs, punycode, TLD swap, appended labels, subdomain tricks."""
from __future__ import annotations

from typing import Optional

from . import CheckContext, Signal

NAME, WEIGHT = "lookalike_domain", "high"

# Multi-character confusables first, then single characters. Applied to both sides, so this
# only ever makes two labels compare equal; it never changes what is displayed.
_MULTI = [("rn", "m"), ("vv", "w"), ("cl", "d")]
_SINGLE = str.maketrans({
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g", "|": "l", "!": "i", "i": "l",
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "l", "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d", "ɡ": "g", "ո": "n", "ս": "u",
    # Greek
    "ν": "v", "ο": "o", "α": "a", "ε": "e", "ι": "l", "κ": "k", "ρ": "p", "τ": "t", "υ": "u", "ω": "w",
})
_TWO_LEVEL_TLDS = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.nz", "co.jp", "co.in", "com.br", "com.mx", "co.za", "com.sg", "com.hk"}


def normalise_label(label: str) -> str:
    text = label.lower()
    for a, b in _MULTI:
        text = text.replace(a, b)
    return text.translate(_SINGLE)


def registrable(domain: str) -> tuple[str, str]:
    """(registrable label, public suffix): acmesupply-billing.com -> ('acmesupply-billing', 'com')."""
    labels = domain.lower().strip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL_TLDS:
        return labels[-3], ".".join(labels[-2:])
    if len(labels) >= 2:
        return labels[-2], labels[-1]
    return labels[0], ""


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _decode_punycode(domain: str) -> str:
    try:
        return domain.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        return domain


def is_lookalike(candidate: str, target: str) -> Optional[str]:
    """The method by which candidate imitates target, or None if it does not."""
    c, t = candidate.lower().strip("."), target.lower().strip(".")
    if not c or not t or c == t:
        return None
    tl, tt = registrable(t)
    if any(label.startswith("xn--") for label in c.split(".")):
        dl, _ = registrable(_decode_punycode(c))
        if levenshtein(normalise_label(dl), normalise_label(tl)) <= 1:
            return "punycode"
    if c.startswith(t + ".") or ("." + t + ".") in ("." + c):
        return "subdomain"
    cl, ct = registrable(c)
    if cl != tl and normalise_label(cl) == normalise_label(tl):
        return "homoglyph"
    if cl == tl and ct != tt:
        return "tld-swap"
    threshold = 2 if len(tl) >= 6 else 1
    distance = levenshtein(cl, tl)
    if 0 < distance <= threshold:
        return "edit-distance"
    if len(tl) >= 4 and tl in cl:
        return "label-append"
    return None


def check_lookalike_domain(ctx: CheckContext) -> Signal:
    sender = ctx.message.sender_domain
    candidates = [("sender", sender), ("reply_to", ctx.message.reply_to_domain)]
    candidates = [(f, d) for f, d in candidates if d]
    targets = [ctx.vendor.domain] if ctx.vendor and ctx.vendor.domain else list(ctx.known_domains)
    for field_name, domain in candidates:
        for target in targets:
            method = is_lookalike(domain, target)
            if method:
                return Signal(NAME, True, WEIGHT, f"{domain} ({field_name.replace('_', '-')}) imitates {target}, the vendor domain on file, by {method}",
                              {"field": field_name, "domain": domain, "sender_domain": sender, "on_file_domain": target, "method": method, "differs": True})
    if ctx.vendor and ctx.vendor.domain:
        if sender == ctx.vendor.domain:
            return Signal(NAME, False, WEIGHT, "Sender domain is the vendor domain on file",
                          {"field": "sender", "domain": sender, "sender_domain": sender, "on_file_domain": ctx.vendor.domain, "method": None, "differs": False})
        return Signal(NAME, False, WEIGHT, f"Sender domain {sender} differs from the vendor domain on file {ctx.vendor.domain} (not a lookalike)",
                      {"field": "sender", "domain": sender, "sender_domain": sender, "on_file_domain": ctx.vendor.domain, "method": None, "differs": True})
    return Signal(NAME, False, WEIGHT, f"No vendor domain on file; {sender or 'the sender'} is not near any vendor on file",
                  {"field": "sender", "domain": sender, "sender_domain": sender, "on_file_domain": None, "method": None, "differs": None})
