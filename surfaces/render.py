"""HTML for the screens. Pure functions of contract Decisions, so every sample can be rendered in a test.

Every string from a Decision may have been written by an attacker (payee names, reasons, the hidden
text Doris found), so all of it goes through esc() before it reaches the page.

UI: Bootstrap 5 via CDN (no build step, no static directory). Secondary info lives inside Bootstrap
collapses so judges see the verdict first and can drill down.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("America/Los_Angeles")   # Cal Hacks is in Berkeley
DESK_CONFIG: Optional[dict] = None         # set when the shared desk (SpacetimeDB) is on; pages then subscribe to it
TAGLINE = "Doris only trusts what's on file."
COMPANY = ""                              # the customer this dashboard serves, set from settings at start-up

VERDICT = {
    "PAY": ("Paid", "pay", "Doris approved this payment."),
    "HOLD": ("On hold", "hold", "Waiting for a person to review it. No money moves."),
    "CALLBACK": ("Calling to verify", "callback", "Doris is verifying by phone at the number on file. No money moves."),
    "BLOCKED": ("Blocked", "blocked", "No money moves."),
}
SIGNAL_LABEL = {
    "bank_account_changed": "Bank account changed", "prompt_injection": "Hidden or AI-addressed text",
    "vendor_contact_changed": "Vendor record edited recently", "payee_name_mismatch": "Payee isn't the vendor on file",
    "lookalike_domain": "Lookalike sender domain", "new_domain_certificate": "Newly certificated domain",
    "amount_anomaly": "Amount out of line with history", "urgency_language": "Pressure language",
    "llm_judgment": "Claude's judgment", "new_vendor": "First-time vendor", "callback_result": "Callback result",
    "human_review": "Human review",
}

BOOTSTRAP_CSS = '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet" crossorigin="anonymous">'
BOOTSTRAP_JS = '<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js" crossorigin="anonymous"></script>'


def esc(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def when(value: Optional[str], style: str = "full") -> str:
    dt = parse_ts(value)
    if dt is None:
        return ""
    dt = dt.astimezone(LOCAL)
    hour = dt.strftime("%I:%M %p").lstrip("0")
    if style == "month":
        return dt.strftime("%b %Y")
    if style == "short":
        return f"{dt.strftime('%a %b')} {dt.day}, {hour}"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}, {hour}"


def money(amount: Any, currency: Optional[str]) -> str:
    if amount is None:
        return "Amount not found"
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return esc(amount)
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get((currency or "").upper())
    return f"{symbol}{value:,.2f}" if symbol else f"{value:,.2f} {esc(currency)}"


def pretty_phone(number: Optional[str]) -> str:
    n = number or ""
    if len(n) == 12 and n.startswith("+1") and n[2:].isdigit():
        return f"+1 ({n[2:5]}) {n[5:8]}-{n[8:]}"
    return n


VERIFY_METHODS = {
    "called": "Called them on a number I found myself, not one from this email",
    "record": "It matches a contract, PO or record we already had",
    "attachment": "I opened the attachment myself and checked it against our records",
    "in_person": "Verified in person",
}
YOUNG_DOMAIN_DAYS = 90


def domain_age_days(d: dict) -> Optional[int]:
    """Days between the sender domain's first certificate and when Doris checked the email, if known."""
    checked = parse_ts(d.get("created_at"))
    first = next((parse_ts(e.get("at")) for e in d.get("ct_timeline") or []
                  if e.get("kind") == "cert_first_seen" and e.get("domain") == d.get("sender_domain")), None)
    if not (checked and first):
        return None
    return max(0, (checked - first).days)


# --- the page shell ---------------------------------------------------------------------

CSS = """
:root {
  --doris-bg: #f8f9fa;
  --doris-panel: #ffffff;
  --doris-ink: #1f2328;
  --doris-quiet: #656d76;
  --doris-line: #dee2e6;
  --doris-pay: #198754;
  --doris-pay-tint: #d1e7dd;
  --doris-hold: #b06500;
  --doris-hold-tint: #fff3cd;
  --doris-callback: #0d6efd;
  --doris-callback-tint: #cff4fc;
  --doris-blocked: #dc3545;
  --doris-blocked-tint: #f8d7da;
  --doris-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --doris-bg: #0d1117;
    --doris-panel: #161b22;
    --doris-ink: #f0f6fc;
    --doris-quiet: #8b949e;
    --doris-line: #30363d;
    --doris-pay: #3fb950;
    --doris-pay-tint: #0d2818;
    --doris-hold: #d29922;
    --doris-hold-tint: #2d2206;
    --doris-callback: #58a6ff;
    --doris-callback-tint: #0c2d6b;
    --doris-blocked: #f85149;
    --doris-blocked-tint: #3b1219;
  }
  body {
    background-color: var(--doris-bg) !important;
    color: var(--doris-ink) !important;
  }
  .card {
    background-color: var(--doris-panel) !important;
    border-color: var(--doris-line) !important;
    color: var(--doris-ink) !important;
  }
  .table {
    --bs-table-bg: transparent;
    --bs-table-color: var(--doris-ink);
    --bs-table-border-color: var(--doris-line);
  }
  .table-light {
    --bs-table-bg: rgba(255, 255, 255, 0.05) !important;
    --bs-table-color: var(--doris-ink) !important;
  }
  .table-hover tbody tr:hover {
    color: var(--doris-ink) !important;
    background-color: rgba(255, 255, 255, 0.04) !important;
  }
  .navbar {
    background-color: var(--doris-panel) !important;
    border-color: var(--doris-line) !important;
  }
  .navbar-brand {
    color: var(--doris-ink) !important;
  }
  .form-control, .form-select {
    background-color: #0d1117 !important;
    border-color: var(--doris-line) !important;
    color: var(--doris-ink) !important;
  }
  .form-control:focus, .form-select:focus {
    background-color: #0d1117 !important;
    color: var(--doris-ink) !important;
    border-color: #58a6ff !important;
  }
  .form-control::placeholder {
    color: var(--doris-quiet) !important;
  }
  .text-muted {
    color: var(--doris-quiet) !important;
  }
  .bg-light {
    background-color: rgba(255, 255, 255, 0.04) !important;
    color: var(--doris-ink) !important;
  }
  .border {
    border-color: var(--doris-line) !important;
  }
  .list-group-item {
    background-color: var(--doris-panel) !important;
    border-color: var(--doris-line) !important;
    color: var(--doris-ink) !important;
  }
  .stat-card {
    background-color: var(--doris-panel) !important;
    border-color: var(--doris-line) !important;
  }
  .key-stat {
    color: var(--doris-ink) !important;
  }
  svg text { fill: var(--doris-ink); }
  svg .q { fill: var(--doris-quiet); }
  svg .ax { stroke: var(--doris-line); }
}

body {
  background: var(--doris-bg);
  color: var(--doris-ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  min-height: 100vh;
}

.verdict-hero {
  border-radius: 1rem;
  padding: 2.25rem 2rem;
  color: #fff;
  margin-bottom: 1.5rem;
}
.verdict-hero h1 {
  font-size: clamp(2.2rem, 5vw, 3.8rem);
  margin: 0;
  letter-spacing: .02em;
}
.verdict-hero .verdict-badge {
  font-size: clamp(2rem, 4vw, 3.2rem);
  font-weight: 800;
  letter-spacing: .04em;
  line-height: 1;
  text-transform: uppercase;
}
.verdict-hero p.sub {
  font-size: 1.2rem;
  margin: .4rem 0 0;
  opacity: .95;
}

.v-pay { background: linear-gradient(135deg, #198754, #146c43); }
.v-hold { background: linear-gradient(135deg, #b06500, #804800); }
.v-callback { background: linear-gradient(135deg, #0d6efd, #084298); }
.v-blocked { background: linear-gradient(135deg, #dc3545, #842029); }

.stat-card {
  border: 1px solid var(--doris-line);
  border-radius: .75rem;
  background: var(--doris-panel);
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
.key-stat {
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--doris-ink);
  line-height: 1.25;
}
.key-label {
  font-size: .75rem;
  font-weight: 700;
  color: var(--doris-quiet);
  text-transform: uppercase;
  letter-spacing: .08em;
}

.btn-verdict {
  min-width: 140px;
  padding: .65rem 1.4rem;
  font-size: 1.05rem;
  font-weight: 600;
  border-radius: .5rem;
}

.navbar {
  background: var(--doris-panel);
}
.navbar-brand {
  font-weight: 700;
  letter-spacing: .05em;
  color: var(--doris-ink);
}

.hash {
  font-family: var(--doris-mono);
  font-size: 12px;
  overflow-wrap: anywhere;
}

svg text { fill: var(--doris-ink); }
svg .q { fill: var(--doris-quiet); }
svg .ax { stroke: var(--doris-line); }
svg .bad { fill: var(--doris-blocked); }
svg .badline { stroke: var(--doris-blocked); }
svg .good { fill: var(--doris-pay); }
svg .mid { fill: var(--doris-quiet); }
svg .inv { stroke: var(--doris-callback); }
svg .invt { fill: var(--doris-callback); }

body.blocked-screen {
  background: #751410 !important;
  color: #fff !important;
}
body.blocked-screen .navbar {
  background: rgba(0, 0, 0, 0.3) !important;
  border-color: rgba(255, 255, 255, 0.15) !important;
}
body.blocked-screen .navbar-brand, body.blocked-screen .navbar a {
  color: #fff !important;
}
body.blocked-screen .btn-outline-secondary {
  color: #fff !important;
  border-color: rgba(255, 255, 255, 0.4) !important;
}
body.blocked-screen .btn-outline-secondary:hover {
  background: rgba(255, 255, 255, 0.2) !important;
}
body.blocked-screen .card {
  background: var(--doris-panel);
  color: var(--doris-ink);
}

ol.trace {
  list-style: none;
  margin: 0;
  padding: 0 0 0 1.25rem;
  border-left: 2px solid var(--doris-line);
}
ol.trace li {
  position: relative;
  margin: 0 0 0.75rem;
  padding-left: 0.75rem;
  animation: rise .45s both;
  animation-delay: calc(var(--i, 0) * 180ms);
}
ol.trace li::before {
  content: "";
  position: absolute;
  left: -1.65rem;
  top: 0.35rem;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--doris-line);
}
ol.trace li.doris::before { background: var(--doris-callback); }
ol.trace li.vendor::before { background: var(--doris-hold); }
ol.trace li.t-bad::before { background: var(--doris-blocked); }
ol.trace li.t-good::before { background: var(--doris-pay); }
ol.trace li.live::before {
  box-shadow: 0 0 0 0 var(--doris-callback);
  animation: pulse 1.4s infinite;
}
ol.trace .who {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .05em;
  color: var(--doris-quiet);
  margin-right: 0.4rem;
}
ol.trace .t {
  font-size: 0.75rem;
  color: var(--doris-quiet);
  margin-left: 0.4rem;
}
ol.trace li.vendor .say { font-style: italic; }
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(13, 110, 253, .6); } 70% { box-shadow: 0 0 0 10px rgba(13, 110, 253, 0); } 100% { box-shadow: 0 0 0 0 rgba(13, 110, 253, 0); } }
@media (prefers-reduced-motion: reduce) { ol.trace li, ol.trace li.live::before { animation: none; } }
@media print {
  body, body.blocked-screen { background: #fff !important; color: #000 !important; }
  .noprint, nav { display: none !important; }
  .card, .panel { border-color: #999 !important; break-inside: avoid; }
}
"""

POLL_JS = """
<script>
(function(){
  var el = document.body;
  var url = el.getAttribute('data-poll');
  if(!url) return;
  var me = el.getAttribute('data-id');
  function tick(){
    fetch(url, {cache: 'no-store'}).then(function(r){ return r.json(); }).then(function(s){
      if(s.latest_id && s.latest_id !== me){
        location.href = '/d/' + encodeURIComponent(s.latest_id);
        return;
      }
      var c = document.getElementById('call-status');
      if(c && s.call_text !== undefined) c.textContent = s.call_text;
      var pill = document.getElementById('live-pill');
      if(pill && s.decision) pill.textContent = s.decision;
      var ol = document.getElementById('trace');
      if(ol && s.trace_count !== undefined && s.trace_count !== ol.children.length){
        fetch('/d/' + encodeURIComponent(me) + '/trace', {cache: 'no-store'}).then(function(r){ return r.text(); }).then(function(h){
          var t = document.createElement('template');
          t.innerHTML = h;
          var items = t.content.querySelectorAll('li');
          for(var i = 0; i < ol.children.length; i++){ ol.children[i].classList.remove('live'); }
          for(var j = ol.children.length; j < items.length; j++){
            var li = items[j].cloneNode(true);
            li.style.setProperty('--i', '0');
            ol.appendChild(li);
          }
          if(items.length && items[items.length - 1].classList.contains('live') && ol.lastElementChild){
            ol.lastElementChild.classList.add('live');
          }
        });
      }
    }).catch(function(){});
  }
  window.addEventListener('doris:changed', tick);
  (function loop(){
    setTimeout(function(){ tick(); loop(); }, window.__dorisDeskLive ? 8000 : 1500);
  })();
})();
</script>"""


def page(title: str, body: str, *, body_class: str = "", poll: Optional[str] = None, decision_id: Optional[str] = None,
         chain_ids: Optional[list[str]] = None) -> str:
    poll_attr = f' data-poll="{esc(poll)}"' if poll else ""
    id_attr = f' data-id="{esc(decision_id)}"' if decision_id is not None else ""
    chain_attr = f' data-chain="{esc(",".join(chain_ids))}"' if chain_ids else ""
    desk_head = desk_tail = ""
    if DESK_CONFIG and poll:
        desk_head = f'<meta name="doris-desk" content="{esc(json.dumps(DESK_CONFIG))}">'
        desk_tail = '<script type="module" src="/static/desk.js"></script>'
    verdict_badge = ""
    if poll:
        verdict_badge = '<span id="live-pill" class="badge bg-secondary">Live</span>'
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'{desk_head}'
        f'<title>{esc(title)} — Doris</title>'
        f'{BOOTSTRAP_CSS}'
        f'<style>{CSS}</style>'
        f'</head><body class="py-4 {esc(body_class)}"{poll_attr}{id_attr}{chain_attr}>'
        '<div class="container" style="max-width:980px">'
        '<nav class="navbar navbar-expand rounded px-3 py-2 mb-4 border shadow-sm">'
        '<div class="container-fluid px-0">'
        '<a class="navbar-brand d-flex align-items-center gap-2" href="/">'
        '<span class="fs-4 fw-bold">Doris</span>'
                + (f'<span class="text-muted small d-none d-sm-inline">{esc(COMPANY)} &middot; Accounts payable</span>' if COMPANY else
           f'<span class="badge bg-secondary text-light fw-normal fs-6 d-none d-sm-inline">{esc(TAGLINE)}</span>')
        + '</a>'
        '<div class="d-flex align-items-center gap-2">'
        '<span id="desk-status" class="text-muted small"></span>'
        f'{verdict_badge}'
        '<a class="btn btn-outline-secondary btn-sm" href="/">Inbox</a>'
        '<a class="btn btn-outline-secondary btn-sm" href="/vendors">Vendors</a>'
        '</div></div></nav>'
        f'<main>{body}</main>'
        '<footer class="text-muted small mt-4 d-flex flex-wrap justify-content-between gap-2">'
        f'<span>{esc(TAGLINE)} Every decision is kept in a tamper-evident record.</span>'
        '</footer>'
        f'</div>{BOOTSTRAP_JS}{POLL_JS if poll else ""}{desk_tail}</body></html>'
    )


def safe(fn, *args, **kwargs) -> str:
    """Render one panel; if its data is too strange to draw, say so instead of failing the whole screen."""
    try:
        out = fn(*args, **kwargs)
        return "" if out is None else str(out)
    except Exception:
        import logging
        logging.getLogger("doris.render").exception("panel %s failed", getattr(fn, "__name__", fn))
        return '<div class="alert alert-warning mb-4">This panel could not be drawn from this decision.</div>'


def pill(decision: str) -> str:
    v = (decision or "").upper()
    mapping = {
        "PAY": ("success", "Paid"),
        "HOLD": ("warning text-dark", "On hold"),
        "CALLBACK": ("info text-dark", "Calling to verify"),
        "BLOCKED": ("danger", "Blocked"),
    }
    color, label = mapping.get(v, ("secondary", decision or "—"))
    return f'<span class="badge bg-{color}">{esc(label)}</span>'


def summary(d: dict) -> str:
    changed = bool(d.get("bank_account_new") and d.get("bank_account_on_file") and d.get("bank_account_new") != d.get("bank_account_on_file"))
    amount_str = money(d.get("amount"), d.get("currency"))
    invoice_id = d.get("invoice_id") or "—"
    payee_name = d.get("payee_name") or "Not found"
    vendor_claimed = d.get("vendor_claimed") or "—"
    vendor_id = d.get("vendor_id") or "None: nothing on file for this sender"
    sender = d.get("sender_email") or d.get("sender_domain") or "Unknown"
    acct_file = d.get("bank_account_on_file") or "None"
    acct_new = d.get("bank_account_new") or "Same as on file"

    alert_bank = ""
    if changed:
        alert_bank = (
            f'<div class="col-12">'
            f'<div class="alert alert-danger d-flex align-items-center mb-0 py-2 px-3 border-danger shadow-sm">'
            f'<div><strong>⚠️ Bank Account Changed:</strong> Requests payment to <code class="fw-bold">{esc(acct_new)}</code>, '
            f'but record on file is <code class="fw-bold">{esc(acct_file)}</code>.</div>'
            f'</div></div>'
        )

    return (
        f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
        f'<h5 class="card-title fw-bold mb-3">Transaction Summary</h5>'
        f'<div class="row g-3 mb-3">'
        f'<div class="col-md-3 col-sm-6"><div class="stat-card p-3 rounded border"><div class="key-label text-muted">Amount</div><div class="key-stat">{amount_str}</div></div></div>'
        f'<div class="col-md-3 col-sm-6"><div class="stat-card p-3 rounded border"><div class="key-label text-muted">Invoice</div><div class="key-stat">{esc(invoice_id)}</div></div></div>'
        f'<div class="col-md-3 col-sm-6"><div class="stat-card p-3 rounded border"><div class="key-label text-muted">Payee</div><div class="key-stat">{esc(payee_name)}</div></div></div>'
        f'<div class="col-md-3 col-sm-6"><div class="stat-card p-3 rounded border"><div class="key-label text-muted">Vendor Claimed</div><div class="key-stat">{esc(vendor_claimed)}</div></div></div>'
        f'</div>'
        f'<div class="row g-3">'
        f'{alert_bank}'
        f'<div class="col-md-6"><div class="p-3 rounded border bg-light">'
        f'<div class="fw-semibold mb-2">Banking Details</div>'
        f'<div class="d-flex justify-content-between mb-1"><span class="text-muted small">Account on file:</span><span class="font-monospace small fw-bold">{esc(acct_file)}</span></div>'
        f'<div class="d-flex justify-content-between mb-0"><span class="text-muted small">Account requested:</span><span class="font-monospace small fw-bold {"text-danger" if changed else ""}">{esc(acct_new)}</span></div>'
        f'</div></div>'
        f'<div class="col-md-6"><div class="p-3 rounded border bg-light">'
        f'<div class="fw-semibold mb-2">Vendor Identity</div>'
        f'<div class="d-flex justify-content-between mb-1"><span class="text-muted small">Sender:</span><span class="small fw-semibold">{esc(sender)}</span></div>'
        f'<div class="d-flex justify-content-between mb-0"><span class="text-muted small">Record on file:</span><span class="small fw-semibold">{esc(vendor_id)}</span></div>'
        f'</div></div>'
        f'</div>'
        f'</div></div>'
    )


def reasons(d: dict) -> str:
    rs = d.get("reasons") or []
    items = "".join(f'<li class="mb-2">{esc(r)}</li>' for r in rs)
    rationale = d.get("llm_rationale")
    extra = (
        f'<div class="mt-3 p-3 rounded bg-light border">'
        f'<div class="fw-semibold text-primary mb-1">Claude\'s Analysis</div>'
        f'<div class="small">{esc(rationale)}</div>'
        f'</div>'
    ) if rationale else ""
    return (
        f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
        f'<h5 class="card-title fw-bold mb-3">Why Doris Flagged This</h5>'
        f'<ol class="mb-0 ps-3">{items or "<li>No reasons given</li>"}</ol>'
        f'{extra}'
        f'</div></div>'
    )


def hidden_text(d: dict) -> str:
    sig = next((s for s in d.get("signals") or [] if s.get("name") == "prompt_injection" and s.get("fired")), None)
    if not sig:
        return ""
    findings = (sig.get("evidence") or {}).get("findings") or []
    quotes = "".join(
        f'<div class="p-3 mb-2 rounded bg-danger-subtle text-danger border border-danger-subtle font-monospace small">'
        f'<div class="fw-bold mb-1">Prompt Injection Payload:</div>'
        f'<div class="p-2 bg-white rounded border border-danger-subtle text-dark overflow-auto">{esc(f.get("text"))}</div>'
        f'<div class="text-muted small mt-1">{esc(str(f.get("kind", "")).replace("_", " "))}'
        f'{", line " + esc(f.get("line")) if f.get("line") else ""} ({esc(f.get("source"))})</div>'
        f'</div>'
        for f in findings[:3] if f.get("text"))
    return (
        f'<div class="card border-danger border-2 shadow-sm mb-4"><div class="card-body">'
        f'<h5 class="card-title text-danger fw-bold mb-1">Hidden text Doris found</h5>'
        f'<p class="text-muted small mb-3">A real vendor has no reason to hide instructions. Their prompt injection became our evidence.</p>'
        f'{quotes}'
        f'</div></div>'
    )


def ct_strip(d: dict) -> str:
    """One strip, readable in three seconds: when each domain got its first certificate, against
    when the invoice arrived. Years of history are compressed on the left; the last 30 days are
    stretched on the right so a days-old lookalike stands apart from a vendor with years of history."""
    events = [e for e in d.get("ct_timeline") or [] if parse_ts(e.get("at"))]
    if not events:
        return ""
    inv = next((e for e in events if e.get("kind") == "invoice_received"), None)
    # Ages are measured on Doris's clock (when she checked), never the sender's Date header.
    checked = parse_ts(d.get("created_at")) or datetime.now(timezone.utc)
    t_inv = min(parse_ts(inv["at"]), checked) if inv else min(max(parse_ts(e["at"]) for e in events), checked)
    domains: list[str] = []
    for e in events:
        if e.get("kind") != "invoice_received" and e["domain"] not in domains:
            domains.append(e["domain"])
    if inv and inv["domain"] not in domains:
        domains.insert(0, inv["domain"])
    sender = d.get("sender_domain")
    if sender in domains:
        domains.remove(sender)
        domains.insert(0, sender)
    first_seen = {e["domain"]: parse_ts(e["at"]) for e in events if e.get("kind") == "cert_first_seen"}
    young = {dom for dom, t in first_seen.items() if t and (t_inv - t) < timedelta(days=30)}

    W, LEFT, RIGHT = 760, 190, 740
    lane_h, top = 46, 34
    H = top + lane_h * len(domains) + 30
    # Stretch the recent window to fit the youngest certificate, so days-old and the invoice don't touch.
    ages = [(t_inv - first_seen[dom]).total_seconds() / 86400 for dom in young]
    window_days = min(30, max(7, int(max(ages) * 2.5) + 1)) if ages else 30
    recent_start = t_inv - timedelta(days=window_days)
    t_min = min(parse_ts(e["at"]) for e in events)
    split = t_min < recent_start
    mid = LEFT + (RIGHT - LEFT) * 0.45

    def x(t: datetime) -> float:
        end = t_inv + timedelta(days=1)
        if not split:
            span = (end - t_min).total_seconds() or 1
            return LEFT + (RIGHT - LEFT) * (t - t_min).total_seconds() / span
        if t < recent_start:
            span = (recent_start - t_min).total_seconds() or 1
            return LEFT + (mid - 8 - LEFT) * (t - t_min).total_seconds() / span
        span = (end - recent_start).total_seconds()
        return mid + 8 + (RIGHT - mid - 8) * min((t - recent_start).total_seconds(), span) / span

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Certificate Transparency timeline" font-size="12">']
    if split:
        parts.append(f'<text class="q" x="{LEFT}" y="16">{esc(when(t_min.isoformat(), "month"))} onward</text>'
                     f'<text class="q" x="{mid + 8:.0f}" y="16">Last {window_days} days</text>'
                     f'<text class="q" x="{mid - 4:.0f}" y="{H - 10}">//</text>')
    for i, dom in enumerate(domains):
        y = top + lane_h * i + 16
        cls = "bad" if dom in young else ""
        parts.append(f'<text x="8" y="{y + 4}" class="{cls}" font-weight="600">{esc(dom[:26])}</text>'
                     f'<line class="ax" x1="{LEFT}" y1="{y}" x2="{RIGHT}" y2="{y}" stroke-width="1"/>')
        for e in events:
            if e["domain"] != dom or e.get("kind") == "invoice_received":
                continue
            ex = x(min(parse_ts(e["at"]), t_inv + timedelta(days=1)))
            kind = e.get("kind")
            if kind == "cert_first_seen":
                c = "bad" if dom in young else "good"
                label = when(e["at"], "short" if dom in young else "month")
                anchor, tx = ("end", ex + 6) if ex > RIGHT - 70 else ("start", ex - 6) if ex < LEFT + 50 else ("middle", ex)
                parts.append(f'<circle cx="{ex:.1f}" cy="{y}" r="6" class="{c}"><title>{esc(e.get("label"))}</title></circle>'
                             f'<text x="{tx:.1f}" y="{y + 20}" text-anchor="{anchor}" class="{c}">{esc(label)}</text>')
            elif kind == "cert_issued":
                parts.append(f'<circle cx="{ex:.1f}" cy="{y}" r="3" class="mid"><title>{esc(e.get("label"))}</title></circle>')
            elif kind == "vendor_on_file_since":
                parts.append(f'<rect x="{ex - 4:.1f}" y="{y - 4}" width="8" height="8" class="good"><title>{esc(e.get("label"))}</title></rect>')
    if inv:
        ix = x(t_inv)
        parts.append(f'<line class="inv" x1="{ix:.1f}" y1="{top - 4}" x2="{ix:.1f}" y2="{H - 24}" stroke-dasharray="4 3" stroke-width="2"/>'
                     f'<text class="invt" x="{min(ix, RIGHT - 4):.1f}" y="{H - 8}" text-anchor="end">Invoice {esc(when(t_inv.isoformat(), "short"))}</text>')
    parts.append("</svg>")
    caption = []
    for dom in domains:
        t = first_seen.get(dom)
        if t is None:
            continue
        days = (t_inv - t).days
        caption.append(f"{esc(dom)} got its first certificate {days} days before this invoice" if dom in young
                       else f"{esc(dom)} has had certificates since {esc(when(t.isoformat(), 'month'))}")
    text = "; ".join(caption) + "." if caption else ""
    return (f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
            f'<h5 class="card-title fw-bold mb-3">Certificate Transparency Timeline</h5>'
            f'<div class="overflow-auto">{"".join(parts)}</div>'
            f'<p class="text-muted small mt-3 mb-0">{text}</p></div></div>')


def signals_table(d: dict) -> str:
    signals = d.get("signals") or []
    if not signals:
        return ""
    fired_count = sum(1 for s in signals if s.get("fired"))
    rows = []
    for s in signals:
        fired = bool(s.get("fired"))
        cls = ' class="table-danger"' if fired else ""
        badge = '<span class="badge bg-danger">Fired</span>' if fired else '<span class="badge bg-secondary">Quiet</span>'
        label = SIGNAL_LABEL.get(s.get("name"), s.get("name"))
        rows.append(
            f'<tr{cls}>'
            f'<td class="fw-semibold">{esc(label)}</td>'
            f'<td class="text-center">{badge}</td>'
            f'<td><span class="badge bg-light text-dark border">{esc(s.get("weight") or "")}</span></td>'
            f'<td class="small text-muted">{esc(s.get("detail") or "")}</td>'
            f'</tr>'
        )
    return (
        f'<div class="card border-0 shadow-sm mb-4">'
        f'<div class="card-header bg-transparent d-flex justify-content-between align-items-center py-3" '
        f'data-bs-toggle="collapse" data-bs-target="#collapseSignals" style="cursor:pointer">'
        f'<div class="d-flex align-items-center gap-2">'
        f'<h5 class="card-title fw-bold mb-0">Every Check That Ran</h5>'
        f'<span class="badge bg-{"danger" if fired_count else "success"}">{fired_count} fired</span>'
        f'<span class="badge bg-secondary">{len(signals)} total</span>'
        f'</div>'
        f'<span class="btn btn-sm btn-outline-secondary">Toggle</span>'
        f'</div>'
        f'<div class="collapse show" id="collapseSignals">'
        f'<div class="card-body p-0">'
        f'<div class="table-responsive">'
        f'<table class="table table-hover align-middle mb-0">'
        f'<thead class="table-light"><tr><th>Check</th><th class="text-center" style="width:100px">Result</th><th style="width:110px">Weight</th><th>Detail</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        f'</table>'
        f'</div></div></div></div>'
    )


def audit_trail(chain: list[dict], audit_ok: Optional[bool] = None) -> str:
    status_badge = (
        '<span class="badge bg-success">Chain verified</span>' if audit_ok else
        '<span class="badge bg-danger">Chain verification failed</span>' if audit_ok is False else
        '<span class="badge bg-secondary">Chain not checked</span>'
    )
    items = []
    for d in chain:
        items.append(
            f'<li class="mb-3 p-3 bg-light rounded border">'
            f'<div class="d-flex justify-content-between align-items-center mb-1">'
            f'{pill(d.get("decision", ""))}'
            f'<span class="text-muted small">#{esc(d.get("seq") or "")} · {esc(when(d.get("created_at")))} · {esc(d.get("decision_id"))}</span>'
            f'</div>'
            f'<div class="hash">'
            f'prev {esc(d.get("prev_hash") or "")}<br>'
            f'hash {esc(d.get("audit_hash") or "")}'
            f'</div>'
            f'</li>'
        )
    return (
        f'<div class="card border-0 shadow-sm mb-4">'
        f'<div class="card-header bg-transparent d-flex justify-content-between align-items-center py-3" '
        f'data-bs-toggle="collapse" data-bs-target="#collapseAudit" style="cursor:pointer">'
        f'<div class="d-flex align-items-center gap-2">'
        f'<h5 class="card-title fw-bold mb-0">Audit trail</h5>'
        f'{status_badge}'
        f'</div>'
        f'<span class="btn btn-sm btn-outline-secondary">Toggle</span>'
        f'</div>'
        f'<div class="collapse show" id="collapseAudit">'
        f'<div class="card-body">'
        f'<p class="text-muted small mb-3">Each record\'s hash covers the one before it, so nobody can quietly rewrite this history.</p>'
        f'<ol class="list-unstyled mb-0">{"".join(items)}</ol>'
        f'</div></div></div>'
    )


def callback_panel(d: dict, call_text: str = "", can_call: bool = False) -> str:
    phone = d.get("callback_number") or d.get("callback_phone") or d.get("phone_on_file") or ""
    ok = d.get("callback_ok")
    verdict = d.get("decision", "")
    since = when(d.get("callback_number_on_file_since"), "month")
    if phone and verdict != "BLOCKED":
        status = "Confirmed" if ok is True else ("Pending" if ok is None else "Failed")
        badge = {"Confirmed": "bg-success", "Pending": "bg-warning text-dark", "Failed": "bg-danger"}.get(status, "bg-secondary")
        button = (
            f'<form class="d-inline" method="post" action="/d/{esc(d.get("decision_id"))}/call">'
            f'<button class="btn btn-primary btn-verdict">Call the vendor now</button>'
            f'</form>'
        ) if can_call else ""
        manual = (
            f'<form class="d-inline mt-2 mt-md-0" method="post" action="/d/{esc(d.get("decision_id"))}/manual_callback">'
            f'<span class="text-muted small me-2">Called them yourself?</span>'
            f'<input type="text" name="notes" placeholder="What they said" aria-label="What the vendor said" class="form-control form-control-sm d-inline w-auto me-2" style="width:200px">'
            f'<button name="confirmed" value="no" class="btn btn-outline-danger btn-sm me-2">They never changed banks</button>'
            f'<button name="confirmed" value="yes" class="btn btn-outline-success btn-sm">They confirmed the change</button>'
            f'</form>'
        )
        return (
            f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
            f'<div class="d-flex justify-content-between align-items-center mb-2">'
            f'<h5 class="card-title fw-bold mb-0">Vendor Callback</h5>'
            f'<span class="badge {badge}">Status: {esc(status)}</span>'
            f'</div>'
            f'<p class="mb-2">Number on file: <strong>{esc(pretty_phone(phone))}</strong>'
            f'{", on file since " + esc(since) if since else ""}. Never a number from the email.</p>'
            f'<p id="call-status" class="text-muted small mb-3">{esc(call_text)}</p>'
            f'<div class="d-flex flex-wrap gap-2 align-items-center">{button}{manual}</div>'
            f'</div></div>'
        )
    return ""


def review_panel(d: dict) -> str:
    """Human review form: the reviewer says how they verified it, and from what."""
    new_vendor = any(s.get("name") == "new_vendor" and s.get("fired") for s in d.get("signals") or [])
    age = domain_age_days(d)
    lead = ("First-time vendor: Doris has nothing on file to check this against, and documents can be forged. "
            "Before approving, confirm it yourself using contact details you found independently." if new_vendor else
            "Before approving, confirm the payment details yourself through a channel you already trusted.")
    untrusted = (f'<p class="text-muted small mb-2">From this email, so untrusted: {esc(d.get("sender_email") or d.get("sender_domain") or "the sender")} '
                 "and any phone number, link or address in it.</p>")
    evidence = ""
    young = ""
    if age is not None:
        span = f"{age // 365} years" if age >= 730 else f"{age} days"
        evidence = f'<p class="small text-muted mb-2">{esc(d.get("sender_domain"))} got its first certificate {span} before this email.</p>'
        if age < YOUNG_DOMAIN_DAYS:
            young = (f'<div class="form-check my-3">'
                     f'<input class="form-check-input" type="checkbox" name="young_ok" value="yes" id="young_ok">'
                     f'<label class="form-check-label fw-semibold" for="young_ok">I know {esc(d.get("sender_domain"))} is only {age} days old</label>'
                     f'</div>')
    options = "".join(f'<option value="{esc(k)}">{esc(v)}</option>' for k, v in VERIFY_METHODS.items())
    return (
        f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
        f'<h5 class="card-title fw-bold mb-2">Human Review</h5>'
        f'<p class="mb-2">{esc(lead)}</p>{untrusted}{evidence}'
        f'<form method="post" action="/d/{esc(d.get("decision_id"))}/review">'
        f'<div class="row g-2 mb-3">'
        f'<div class="col-md-4"><input type="text" name="reviewer" placeholder="Your name" class="form-control" aria-label="Reviewer" required></div>'
        f'<div class="col-md-4"><select name="method" class="form-select" aria-label="How you verified it"><option value="">How did you verify it?</option>{options}</select></div>'
        f'<div class="col-md-4"><input type="text" name="source" placeholder="Where that number or record came from" class="form-control" aria-label="Source"></div>'
        f'<div class="col-12"><input type="text" name="notes" placeholder="Notes (optional)" class="form-control" aria-label="Notes"></div>'
        f'</div>'
        f'{young}'
        f'<div class="d-flex gap-2 align-items-center">'
        f'<button name="approve" value="yes" class="btn btn-success btn-verdict">Approve payment</button>'
        f'<button name="approve" value="no" class="btn btn-outline-danger btn-verdict">Reject</button>'
        f'</div>'
        f'<p class="text-muted small mt-2 mb-0">Approving needs how you verified it and where from. Rejecting needs only your name.</p>'
        f'</form></div></div>'
    )


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(LOCAL).strftime("%I:%M:%S %p").lstrip("0")


def trace_items(chain: list[dict], events: Optional[list[dict]] = None, live: bool = False) -> str:
    """What Doris did, in order: her steps on the invoice, then every line of the call, then the outcome."""
    if not chain:
        return ""
    d0, items = chain[0], []

    def add(cls: str, who: str, text: str, t: str = "") -> None:
        items.append((cls, who, text, t))

    fired = [s for s in d0.get("signals") or [] if s.get("fired") and s.get("name") not in ("llm_judgment",)]
    add("", "Email", f"Arrived from {d0.get('sender_email') or d0.get('sender_domain') or 'an unknown sender'}", when(d0.get("created_at"), "short"))
    add("", "Read", f"Pay {d0.get('payee_name') or 'an unnamed payee'} {money(d0.get('amount'), d0.get('currency'))}, "
                    f"to {'account ' + d0['bank_account_new'] if d0.get('bank_account_new') else 'the account on file'}")
    on_file = (f"Compared with the record on file for {d0['vendor_id']}: account {d0.get('bank_account_on_file') or 'none'}"
               + (f", phone on file since {when(d0.get('callback_number_on_file_since'), 'month')}" if d0.get("callback_number_on_file_since") else "")
               if d0.get("vendor_id") else "Nothing on file for this sender: a first-time vendor")
    add("", "Records", on_file)
    names = ", ".join(SIGNAL_LABEL.get(s["name"], s["name"]).lower() for s in fired[:5])
    add("t-bad" if fired else "t-good", "Checks", f"Ran {len(d0.get('signals') or [])} checks; {len(fired)} fired" + (f": {names}" if names else ""))
    llm = next((s for s in d0.get("signals") or [] if s.get("name") == "llm_judgment"), {}) or {}
    ev = llm.get("evidence") or {}
    if ev.get("status") == "ok":
        add("", "Claude", f"Risk {float(ev.get('risk') or 0):.2f}. {d0.get('llm_rationale') or ''}".strip())
    label = VERDICT.get(d0.get("decision"), (d0.get("decision"), "", ""))[0]
    add("t-bad" if d0.get("decision") == "BLOCKED" else "t-good" if d0.get("decision") == "PAY" else "doris", "Decision",
        f"{label}: {(d0.get('reasons') or [''])[0]}")
    person = str(d0.get("vendor_id") or "").startswith("emp_")
    for e in events or []:
        who = {"doris": "Doris", "vendor": "Employee" if person else "Vendor", "system": "Call"}.get(e.get("who"), "Call")
        text = e.get("text", "")
        if e.get("who") == "system" and (text.startswith("CRS") or text.startswith("Not paid: CRS")):
            who = "CRS"
        elif e.get("who") == "system" and text.startswith(("Paid through Capital One", "Nothing sent:", "Not paid again")):
            who = "Payment"
        add(e.get("who", "system"), who, text, _clock(e["at"]))
    for d in chain[1:]:
        cls = "t-bad" if d.get("decision") == "BLOCKED" else "t-good" if d.get("decision") == "PAY" else ""
        add(cls, "Result", f"{VERDICT.get(d.get('decision'), (d.get('decision'),))[0]}: {(d.get('reasons') or [''])[0]}", when(d.get("created_at"), "short"))
    out = []
    for i, (cls, who, text, t) in enumerate(items):
        c = " ".join(x for x in (cls, "live" if live and i == len(items) - 1 else "") if x)
        say = f'"{esc(text)}"' if cls == "vendor" else esc(text)
        out.append(f'<li class="{c}" style="--i:{i}"><span class="who">{esc(who)}</span><span class="say">{say}</span>'
                   f'{f"<span class=t>{esc(t)}</span>" if t else ""}</li>')
    return "".join(out)


def trace_panel(chain: list[dict], events: Optional[list[dict]] = None, live: bool = False) -> str:
    return (
        f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
        f'<h5 class="card-title fw-bold mb-3">What Doris did</h5>'
        f'<ol class="trace" id="trace">{trace_items(chain, events, live)}</ol>'
        f'</div></div>'
    )


def audit_links(d: dict) -> str:
    i = esc(d.get("decision_id"))
    return (
        f'<div class="card border-0 shadow-sm mb-4 noprint"><div class="card-body py-3">'
        f'<div class="d-flex flex-wrap gap-3 align-items-center small">'
        f'<a class="fw-semibold text-decoration-none" href="/d/{i}/audit">📄 Open the audit record</a>'
        f'<span class="text-muted">·</span>'
        f'<a class="fw-semibold text-decoration-none" href="/d/{i}/audit.json">📥 Download the audit packet (JSON)</a>'
        f'</div></div></div>'
    )


def audit_screen(packet: dict) -> str:
    rows = "".join(
        f"<tr><td>#{esc(r['seq'])}</td><td>{esc(r['decision_id'])}</td><td class=\"hash\">{esc(r['audit_hash'])}</td>"
        f"<td>{'<span class=\"badge bg-success\">matches</span>' if r['matches'] else '<span class=\"badge bg-danger\">does not match</span>'}</td></tr>"
        for r in packet["recomputed_here"])
    log = packet.get("detector_log_verification") or {}
    log_line = ('<span class="badge bg-success">The detector\'s whole log verifies</span>' if log.get("ok") else
                '<span class="badge bg-danger">The detector\'s log does not verify</span>' if log else '<span class="text-muted small">Detector log not checked</span>')
    chain = packet["decisions"]
    transcript_events = [{"at": _ts(e["at"]), "who": e["who"], "text": e["text"]} for e in packet.get("call_transcript") or []]
    body = (
        f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
        f'<h4 class="card-title fw-bold mb-2">Audit record: invoice {esc(packet["invoice_id"])}</h4>'
        f'<p class="text-muted small mb-3">Outcome: {pill(packet["outcome"] or "")} · generated {esc(when(packet["generated_at"]))}</p>'
        f'<p class="noprint mb-0">'
        f'<button class="btn btn-outline-secondary btn-sm me-2" onclick="window.print()">Print</button>'
        f'<a class="btn btn-primary btn-sm me-2" href="/d/{esc(chain[-1]["decision_id"])}/audit.json">Download JSON</a>'
        f'<a class="btn btn-link btn-sm text-decoration-none" href="/d/{esc(chain[-1]["decision_id"])}">Back to the decision</a>'
        f'</p></div></div>'
        + trace_panel(chain, transcript_events)
        + f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
          f'<h5 class="card-title fw-bold mb-2">Every record, recomputed by this dashboard</h5>'
          f'<p class="text-muted small mb-3">{log_line}. Each hash below was recomputed here from the Decision itself.</p>'
          f'<div class="table-responsive"><table class="table table-hover align-middle mb-0">'
          f'<thead class="table-light"><tr><th>Seq</th><th>Decision</th><th>audit_hash</th><th>Recomputed</th></tr></thead>'
          f'<tbody>{rows}</tbody></table></div></div></div>'
        + f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
          f'<h5 class="card-title fw-bold mb-2">Check it yourself</h5>'
          f'<p class="text-muted small mb-2">{esc(packet["how_to_verify"])}</p>'
          f'<pre class="hash bg-light p-3 rounded border" style="white-space:pre-wrap">{esc(packet["verify_in_python"])}</pre>'
          f'</div></div>'
    )
    return page(f"Audit record: {packet['invoice_id']}", body)


def _ts(iso: str) -> float:
    dt = parse_ts(iso)
    return dt.timestamp() if dt else 0.0


def review_screen(d: dict, *, chain: Optional[list[dict]] = None, audit_ok: Optional[bool] = None,
                  call_text: str = "", can_call: bool = False, poll: bool = True, flash: str = "",
                  events: Optional[list[dict]] = None, live: bool = False) -> str:
    verdict = d.get("decision", "")
    if verdict == "BLOCKED":
        return blocked_screen(d, chain=chain, audit_ok=audit_ok, poll=poll, events=events)
    label, cls, line = VERDICT.get(verdict, (verdict, "secondary", ""))
    hero = (
        f'<section class="verdict-hero v-{cls} mb-4 shadow-sm">'
        f'<div class="row align-items-center">'
        f'<div class="col-md-8">'
        f'<h1 class="display-4 fw-bold mb-1">{esc(label)}</h1>'
        f'<p class="sub mb-0">{esc(line)} Risk {float(d.get("risk_score") or 0):.2f}.</p>'
        f'</div>'
        f'<div class="col-md-4 text-md-end mt-3 mt-md-0">'
        f'<span class="verdict-badge">{esc(label)}</span>'
        f'</div>'
        f'</div>'
        f'</section>'
    )
    flash_html = f'<div class="alert alert-warning mb-4 shadow-sm">{esc(flash)}</div>' if flash else ""
    body_parts = [
        flash_html,
        hero,
        safe(trace_panel, chain or [d], events, live),
        safe(summary, d),
        safe(reasons, d),
    ]
    if verdict == "CALLBACK":
        body_parts.append(safe(callback_panel, d, call_text, can_call))
    if verdict == "HOLD":
        body_parts.append(safe(review_panel, d))
    body_parts += [
        safe(hidden_text, d),
        safe(ct_strip, d),
        safe(signals_table, d),
        safe(audit_trail, chain or [d], audit_ok),
        audit_links(d),
    ]
    url = f"/d/{d.get('decision_id')}/state" if poll else None
    return page(f"{label}: {d.get('invoice_id')}", "".join(body_parts), poll=url, decision_id=d.get("decision_id"),
                chain_ids=[x.get("decision_id") for x in (chain or [d]) if x.get("decision_id")])


def blocked_screen(d: dict, *, chain: Optional[list[dict]] = None, audit_ok: Optional[bool] = None, poll: bool = True,
                   events: Optional[list[dict]] = None) -> str:
    headline = (d.get("reasons") or ["Blocked"])[0]
    hero = (
        f'<section class="verdict-hero v-blocked mb-4 shadow-sm">'
        f'<div class="row align-items-center">'
        f'<div class="col-md-8">'
        f'<h1 class="display-4 fw-bold mb-1">BLOCKED</h1>'
        f'<p class="sub mb-1">{esc(headline)}</p>'
        f'<p class="mb-0" style="font-size:1.1rem;opacity:.95">'
        f'{money(d.get("amount"), d.get("currency"))} to {esc(d.get("payee_name") or "an unknown payee")} stays put. Risk {float(d.get("risk_score") or 0):.2f}.'
        f'</p>'
        f'</div>'
        f'<div class="col-md-4 text-md-end mt-3 mt-md-0">'
        f'<span class="verdict-badge">Blocked</span>'
        f'</div>'
        f'</div>'
        f'</section>'
    )
    body = (
        hero
        + safe(trace_panel, chain or [d], events)
        + "".join(safe(f, d) for f in (summary, reasons, hidden_text, ct_strip))
        + safe(audit_trail, chain or [d], audit_ok)
        + audit_links(d)
        + safe(signals_table, d)
    )
    url = f"/d/{d.get('decision_id')}/state" if poll else None
    return page(f"BLOCKED: {d.get('invoice_id')}", body, body_class="blocked-screen", poll=url, decision_id=d.get("decision_id"),
                chain_ids=[x.get("decision_id") for x in (chain or [d]) if x.get("decision_id")])


EMPTY_ROWS = '<tr><td colspan="6" class="text-center text-muted py-4">No decisions yet</td></tr>'


def _latest_per_invoice(decisions: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for d in decisions:
        key = d.get("invoice_id") or d.get("decision_id") or ""
        if key not in latest or int(d.get("seq") or 0) > int(latest[key].get("seq") or 0):
            latest[key] = d
    return list(latest.values())


def kpi_strip(decisions: list[dict]) -> str:
    """Where every payment request stands now (the newest decision per invoice)."""
    now = _latest_per_invoice(decisions)

    def total(kind: str) -> tuple[int, float]:
        ds = [d for d in now if d.get("decision") == kind]
        return len(ds), sum(float(d["amount"]) for d in ds if isinstance(d.get("amount"), (int, float)))
    blocked, paid = total("BLOCKED"), total("PAY")
    calling, holding = total("CALLBACK")[0], total("HOLD")[0]
    tiles = [
        ("Fraud stopped", money(blocked[1], "USD") if blocked[1] else str(blocked[0]),
         f"{blocked[0]} payment{'s' if blocked[0] != 1 else ''} blocked", "text-danger"),
        ("Paid", money(paid[1], "USD") if paid[1] else str(paid[0]), f"{paid[0]} paid exactly once", "text-success"),
        ("Verifying now", str(calling), "calling the number on file", "text-primary"),
        ("Waiting for a person", str(holding), "held for review", "text-warning"),
    ]
    cells = "".join(
        f'<div class="col-6 col-md-3"><div class="card border-0 shadow-sm h-100"><div class="card-body py-3">'
        f'<div class="text-muted small">{esc(label)}</div><div class="fs-4 fw-bold {cls}">{esc(value)}</div>'
        f'<div class="text-muted small">{esc(sub)}</div></div></div></div>'
        for label, value, sub, cls in tiles)
    return f'<div class="row g-3 mb-4">{cells}</div>'


def _masked_phone(number: Optional[str]) -> str:
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    return f"(•••) •••-{digits[-4:]}" if len(digits) >= 4 else "none on file"


def vendors_screen(vendors: list[dict], now: Optional[datetime] = None) -> str:
    """The records Doris verifies against: who we pay, the account and phone on file, and since when."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for v in sorted(vendors, key=lambda x: (str(x.get("vendor_id", "")).startswith("emp_"), str(x.get("name", "")))):
        since = parse_ts(v.get("phone_since"))
        trusted = since is not None and (now - since).days >= 90
        phone_note = (f'<span class="badge bg-success-subtle text-success-emphasis">Trusted for call-backs</span>' if trusted else
                      f'<span class="badge bg-warning-subtle text-warning-emphasis">Changed recently: not used</span>')
        history = v.get("payment_history") or []
        last = history[-1] if history else None
        kind = "Employee" if str(v.get("vendor_id", "")).startswith("emp_") else "Vendor"
        rows.append(
            f'<tr><td><div class="fw-semibold">{esc(v.get("name"))}</div><div class="text-muted small">{esc(kind)} &middot; {esc(v.get("domain"))}</div></td>'
            f'<td><div class="font-monospace">****{esc(v.get("bank_last4") or "????")}</div>'
            f'<div class="text-muted small">since {esc(when(v.get("bank_since"), "month"))}</div></td>'
            f'<td><div class="font-monospace">{esc(_masked_phone(v.get("phone")))}</div>'
            f'<div class="text-muted small">since {esc(when(v.get("phone_since"), "month"))}</div>{phone_note}</td>'
            f'<td>{money(last, "USD") if isinstance(last, (int, float)) else "none yet"}'
            f'<div class="text-muted small">{len(history)} payment{"s" if len(history) != 1 else ""} on file</div></td></tr>')
    body = (
        f'<div class="card border-0 shadow-sm"><div class="card-body">'
        f'<h5 class="card-title fw-bold mb-1">Vendors and employees on file</h5>'
        f'<p class="text-muted small">Doris checks every request against these records, and only calls a number that has been '
        f'on file, unchanged, for 90 days. Numbers are masked on this screen.</p>'
        f'<div class="table-responsive"><table class="table align-middle mb-0">'
        f'<thead class="table-light"><tr><th>Name</th><th>Bank account on file</th><th>Phone on file</th><th>Last payment</th></tr></thead>'
        f'<tbody>{"".join(rows) or "<tr><td colspan=4 class=text-muted>No records yet</td></tr>"}</tbody>'
        f'</table></div></div></div>'
    )
    return page("Vendors", body)


SEND_KEYS_JS = """<script>
document.addEventListener('keydown', function (e) {
  if (e.metaKey || e.ctrlKey || e.altKey || (e.target && e.target.closest && e.target.closest('input,textarea,select'))) return;
  var s = {'1': 'vendor', '2': 'payroll', '3': 'routine'}[e.key];
  if (!s) return;
  fetch('/demo/send?scenario=' + s, {method: 'POST'}).then(function (r) { if (r.redirected) location.href = r.url; });
});
</script>"""


def demo_controls() -> str:
    return (
        f'<form method="post" action="/demo/send" class="d-grid gap-2">'
        f'<button class="btn btn-primary btn-lg">Send the demo bank-change email</button>'
        f'<button class="btn btn-outline-primary btn-lg" formaction="/demo/send?scenario=payroll">Send the payroll direct-deposit email</button>'
        f'<button class="btn btn-outline-secondary btn-lg" formaction="/demo/send?scenario=routine">Send a routine invoice</button>'
        f'</form>'
    )


def demo_screen() -> str:
    """The presenter's remote: not linked from anywhere. Each button sends a sample email into the inbox."""
    body = (f'<div class="card border-0 shadow-sm"><div class="card-body">'
            f'<h5 class="card-title fw-bold mb-1">Send a sample email</h5>'
            f'<p class="text-muted small">Each one goes into the accounts payable inbox exactly as a real email would. '
            f'On the inbox page, the keys 1, 2 and 3 do the same.</p>{demo_controls()}</div></div>')
    return page("Send a sample email", body)


def queue_screen(decisions: Iterable[dict], *, detector_ok: bool, stats: Optional[dict] = None, demo: bool = False, flash: str = "",
                 live_from: Optional[str] = None) -> str:
    decisions = list(decisions)
    rows = []
    for d in sorted(_latest_per_invoice(decisions), key=lambda x: int(x.get("seq") or 0), reverse=True):
        rows.append(
            f'<tr>'
            f'<td class="text-nowrap">{esc(when(d.get("created_at"), "short"))}</td>'
            f'<td><a class="fw-semibold text-decoration-none" href="/d/{esc(d.get("decision_id"))}">{esc(d.get("invoice_id"))}</a></td>'
            f'<td>{esc(d.get("vendor_claimed") or "")}</td>'
            f'<td class="fw-semibold">{money(d.get("amount"), d.get("currency"))}</td>'
            f'<td>{pill(d.get("decision", ""))}</td>'
            f'<td class="text-end font-monospace">{float(d.get("risk_score") or 0):.2f}</td>'
            f'</tr>'
        )
    status_badge = '<span class="badge bg-success">Detector connected</span>' if detector_ok else '<span class="badge bg-danger">Detector not reachable</span>'
    demo_form = demo_controls() if demo else ""
    door = ""
    if stats and stats.get("checks"):
        door = (
            f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
            f'<h6 class="card-title text-uppercase text-muted small fw-bold">Forward-to-check door</h6>'
            f'<p class="mb-0">{esc(stats.get("checks", 0))} emails checked, '
            f'{esc(stats.get("scam", 0))} called a scam, {esc(stats.get("suspicious", 0))} suspicious. Feedback: '
            f'{esc(stats.get("up", 0))} right, {esc(stats.get("down", 0))} wrong.</p>'
            f'</div></div>'
        )
    flash_html = f'<div class="alert alert-warning mb-4 shadow-sm">{esc(flash)}</div>' if flash else ""
    header_card = (
        f'<div class="d-flex justify-content-between align-items-center mb-2">'
        f'<h5 class="fw-bold mb-0">This week</h5>{status_badge}</div>'
        f'{kpi_strip(decisions)}'
        + (f'<div class="card border-0 shadow-sm mb-4"><div class="card-body">'
           f'<h6 class="card-title text-uppercase text-muted small fw-bold mb-1">Simulator</h6>'
           f'{demo_form}'
           f'</div></div>' if demo_form else "")
    )
    table_card = (
        f'<div class="card border-0 shadow-sm"><div class="card-body">'
        f'<h5 class="card-title fw-bold mb-3">Payment requests, newest first</h5>'
        f'<div class="table-responsive">'
        f'<table class="table table-hover align-middle mb-0">'
        f'<thead class="table-light"><tr><th>When</th><th>Invoice</th><th>Claims to be</th><th>Amount</th><th>Decision</th><th class="text-end">Risk</th></tr></thead>'
        f'<tbody>{"".join(rows) or EMPTY_ROWS}</tbody>'
        f'</table>'
        f'</div></div></div>'
    )
    body = flash_html + header_card + door + table_card
    body += SEND_KEYS_JS
    if live_from is not None:
        return page("Inbox", body, poll="/latest/state", decision_id=live_from)
    return page("Inbox", body)


def samples_screen(names: list[str]) -> str:
    links = "".join(
        f'<li class="list-group-item d-flex justify-content-between align-items-center py-3">'
        f'<a class="text-decoration-none fw-semibold" href="/samples/{esc(n)}">{esc(n)}</a>'
        f'<span class="badge bg-secondary rounded-pill">sample</span>'
        f'</li>'
        for n in names
    )
    return page(
        "Contract samples",
        f'<div class="card border-0 shadow-sm"><div class="card-body">'
        f'<h5 class="card-title fw-bold mb-2">Contract Samples</h5>'
        f'<p class="text-muted small mb-3">Each renders exactly as a live Decision would. No detector needed.</p>'
        f'<ul class="list-group list-group-flush border-top">{links}</ul>'
        f'</div></div>'
    )


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
