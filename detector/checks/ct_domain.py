"""new_domain_certificate: Certificate Transparency as an early fingerprint of a lookalike domain.

Every public TLS certificate is logged within seconds of issuance, so a domain whose
first certificate is days old is a tell. crt.sh is the source; a fixture provider keeps
tests and the offline demo deterministic.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional, Protocol

from . import CheckContext, Signal
from ..models import InboundMessage, VendorOnFile

NAME, WEIGHT = "new_domain_certificate", "medium-high"
log = logging.getLogger("doris.ct")


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CTProvider(Protocol):
    def certificates(self, domain: str) -> Optional[list[datetime]]:
        """Issue times (not_before) of every certificate seen for the domain, or None if the lookup failed."""


class FixtureCTProvider:
    def __init__(self, data: dict[str, list[str]]):
        self._data = {k.lower(): sorted(parse_iso(v) for v in vals) for k, vals in data.items()}

    def certificates(self, domain: str) -> Optional[list[datetime]]:
        return list(self._data.get(domain.lower(), []))


class CrtShProvider:
    """Live lookups against crt.sh, cached per process. Any failure returns None (unavailable)."""

    def __init__(self, timeout: float = 6.0, fallback: Optional[CTProvider] = None):
        self.timeout = timeout
        self.fallback = fallback
        self._cache: dict[str, Optional[list[datetime]]] = {}

    def _query(self, q: str) -> list[datetime]:
        url = "https://crt.sh/?" + urllib.parse.urlencode({"q": q, "output": "json"})
        req = urllib.request.Request(url, headers={"User-Agent": "doris-ap-agent/0.1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            rows = json.loads(resp.read().decode("utf-8") or "[]")
        out = []
        for row in rows:
            nb = row.get("not_before")
            if nb:
                out.append(parse_iso(nb))
        return out

    def certificates(self, domain: str) -> Optional[list[datetime]]:
        domain = domain.lower()
        if domain in self._cache:
            return self._cache[domain]
        try:
            certs = sorted(set(self._query(domain) + self._query("%." + domain)))
        except Exception as exc:  # network, timeout, bad JSON: the signal reports "unavailable"
            log.warning("crt.sh lookup failed for %s: %s", domain, exc)
            certs = self.fallback.certificates(domain) if self.fallback else None
        self._cache[domain] = certs
        return certs


def check_new_domain_certificate(ctx: CheckContext) -> Signal:
    domain = ctx.message.sender_domain
    if ctx.ct is None:
        return Signal(NAME, False, WEIGHT, "Certificate Transparency lookup unavailable", {"status": "unavailable", "domain": domain})
    if not domain:
        return Signal(NAME, False, WEIGHT, "No sender domain to look up", {"status": "no_domain", "domain": None})
    certs = ctx.ct.certificates(domain)
    if certs is None:
        return Signal(NAME, False, WEIGHT, f"Certificate Transparency lookup unavailable for {domain}", {"status": "unavailable", "domain": domain})
    if not certs:
        return Signal(NAME, False, WEIGHT, f"No certificate has ever been logged for {domain}", {"status": "ok", "domain": domain, "none_found": True, "count": 0})
    first = min(certs)
    age_days = (ctx.now - first).days
    evidence = {"status": "ok", "domain": domain, "none_found": False, "first_seen": iso_z(first), "age_days": age_days, "count": len(certs)}
    if age_days < ctx.settings.new_cert_days:
        return Signal(NAME, True, WEIGHT, f"First certificate for {domain} seen {first:%Y-%m-%d %H:%M} UTC, {age_days} days before the invoice", evidence)
    return Signal(NAME, False, WEIGHT, f"First certificate seen {first:%Y-%m-%d}", evidence)


def build_ct_timeline(provider: Optional[CTProvider], message: InboundMessage, vendor: Optional[VendorOnFile], invoice_id: str) -> list[dict]:
    """The strip for the review screen: first certificates, a few renewals, when the vendor
    went on file, and when the invoice arrived. Sorted by time."""
    events: list[dict] = []
    domains: list[str] = []
    if message.sender_domain:
        domains.append(message.sender_domain)
    if vendor and vendor.domain and vendor.domain not in domains:
        domains.append(vendor.domain)
    for domain in domains:
        certs = provider.certificates(domain) if provider else None
        if not certs:
            continue
        certs = sorted(certs)
        events.append({"domain": domain, "kind": "cert_first_seen", "at": iso_z(certs[0]), "label": f"{domain} first certificate"})
        for dt in certs[1:][-3:]:
            events.append({"domain": domain, "kind": "cert_issued", "at": iso_z(dt), "label": f"{domain} certificate issued"})
    if vendor and vendor.domain and vendor.phone_since:
        events.append({"domain": vendor.domain, "kind": "vendor_on_file_since", "at": iso_z(vendor.phone_since), "label": f"{vendor.name} phone on file since"})
    events.append({"domain": message.sender_domain or (vendor.domain if vendor and vendor.domain else "unknown"),
                   "kind": "invoice_received", "at": iso_z(message.received_at), "label": f"Invoice {invoice_id} received"})
    events.sort(key=lambda e: e["at"])
    return events
