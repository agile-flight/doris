"""HTML for the screens. Pure functions of contract Decisions, so every sample can be rendered in a test.

Every string from a Decision may have been written by an attacker (payee names, reasons, the hidden
text Doris found), so all of it goes through esc() before it reaches the page.
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

VERDICT = {
    "PAY": ("Paid", "pay", "Doris approved this payment."),
    "HOLD": ("On hold", "hold", "Waiting for a person to review it. No money moves."),
    "CALLBACK": ("Calling the vendor", "callback", "Doris is verifying by phone at the number on file. No money moves."),
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


# --- the page shell ---------------------------------------------------------------------

CSS = """
:root{--bg:#f7f6f3;--panel:#fff;--ink:#1d1d1b;--quiet:#5f5e5a;--line:#e3e1db;--pay:#1f7a4d;--pay-tint:#e5f4e4;
--hold:#9a6400;--hold-tint:#f9eeda;--callback:#1a5fb4;--callback-tint:#e7f1fb;--blocked:#b3261e;--blocked-tint:#fbebeb;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--panel:#20201d;--ink:#ecebe6;--quiet:#a3a19a;--line:#34332f;
--pay:#5cc48d;--pay-tint:#183324;--hold:#e3a73a;--hold-tint:#33280f;--callback:#78aef0;--callback-tint:#162a44;
--blocked:#f0786f;--blocked-tint:#3d1715}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:20px 16px 48px}a{color:var(--callback)}
header.top{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:18px}
header.top .name{font-weight:700;font-size:20px}header.top .tag{color:var(--quiet)}
header.top nav{margin-left:auto;display:flex;gap:14px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px}
.banner{border-radius:10px;padding:18px 20px;margin-bottom:14px;border:1px solid transparent}
.banner h1{margin:0 0 4px;font-size:28px}.banner p{margin:0}
.v-pay{background:var(--pay-tint);color:var(--pay)}.v-hold{background:var(--hold-tint);color:var(--hold)}
.v-callback{background:var(--callback-tint);color:var(--callback)}.v-blocked{background:var(--blocked-tint);color:var(--blocked)}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px 18px}
.k{color:var(--quiet);font-size:12px;text-transform:uppercase;letter-spacing:.04em}.v{font-weight:600;overflow-wrap:anywhere}
.changed{color:var(--blocked)}h2{font-size:15px;margin:0 0 10px}
ol.reasons{margin:0;padding-left:20px}ol.reasons li{margin:3px 0}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px 8px;border-top:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--quiet);font-weight:600;border-top:0}tr.fired td:first-child{font-weight:600}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--line)}tr.fired .dot{background:var(--blocked)}
.hidden-text{font-family:var(--mono);background:var(--blocked-tint);color:var(--blocked);padding:8px 10px;border-radius:6px;overflow-wrap:anywhere}
.hash{font-family:var(--mono);font-size:12px;overflow-wrap:anywhere}.chain li{margin:6px 0}
.ok{color:var(--pay);font-weight:600}.bad{color:var(--blocked);font-weight:600}.quiet{color:var(--quiet)}
form.inline{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
button,input{font:inherit}button{padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--ink);cursor:pointer}
button.primary{background:var(--callback);border-color:var(--callback);color:#fff}button.danger{background:var(--blocked);border-color:var(--blocked);color:#fff}
input[type=text]{padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
svg text{fill:var(--ink)}svg .q{fill:var(--quiet)}svg .ax{stroke:var(--line)}svg .bad{fill:var(--blocked)}svg .badline{stroke:var(--blocked)}
svg .good{fill:var(--pay)}svg .mid{fill:var(--quiet)}svg .inv{stroke:var(--callback)}svg .invt{fill:var(--callback)}
.scroll{overflow-x:auto}
body.blocked-screen{background:#9f1f19;color:#fff}body.blocked-screen .panel{color:var(--ink)}body.blocked-screen header.top .tag,body.blocked-screen header.top a{color:#fff;opacity:.9}
.blocked-hero{text-align:center;padding:48px 16px 28px}.blocked-hero h1{font-size:clamp(56px,12vw,120px);margin:0;letter-spacing:.04em}
.blocked-hero p{font-size:20px;margin:8px auto 0;max-width:720px}
.flash{padding:10px 14px;border-radius:8px;background:var(--hold-tint);color:var(--hold);margin-bottom:12px}
ol.trace{list-style:none;margin:0;padding:0 0 0 18px;border-left:2px solid var(--line)}
ol.trace li{position:relative;margin:0 0 10px;padding-left:10px;animation:rise .45s both;animation-delay:calc(var(--i,0) * 180ms)}
ol.trace li::before{content:"";position:absolute;left:-25px;top:6px;width:10px;height:10px;border-radius:50%;background:var(--line)}
ol.trace li.doris::before{background:var(--callback)}ol.trace li.vendor::before{background:var(--hold)}
ol.trace li.t-bad::before{background:var(--blocked)}ol.trace li.t-good::before{background:var(--pay)}
ol.trace li.live::before{box-shadow:0 0 0 0 var(--callback);animation:pulse 1.4s infinite}
ol.trace .who{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--quiet);margin-right:6px}
ol.trace .t{font-size:12px;color:var(--quiet);margin-left:6px}ol.trace li.vendor .say{font-style:italic}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(26,95,180,.6)}70%{box-shadow:0 0 0 10px rgba(26,95,180,0)}100%{box-shadow:0 0 0 0 rgba(26,95,180,0)}}
@media (prefers-reduced-motion:reduce){ol.trace li,ol.trace li.live::before{animation:none}}
@media print{body,body.blocked-screen{background:#fff;color:#000}.noprint,header.top nav{display:none}.panel{border-color:#999;break-inside:avoid}}
"""

POLL_JS = """
<script>
(function(){var el=document.body;var url=el.getAttribute('data-poll');if(!url)return;var me=el.getAttribute('data-id');
function tick(){fetch(url,{cache:'no-store'}).then(function(r){return r.json()}).then(function(s){
if(s.latest_id&&s.latest_id!==me){location.href='/d/'+encodeURIComponent(s.latest_id);return}
var c=document.getElementById('call-status');if(c&&s.call_text!==undefined)c.textContent=s.call_text;
var ol=document.getElementById('trace');if(ol&&s.trace_count!==undefined&&s.trace_count!==ol.children.length){
fetch('/d/'+encodeURIComponent(me)+'/trace',{cache:'no-store'}).then(function(r){return r.text()}).then(function(h){
var t=document.createElement('template');t.innerHTML=h;var items=t.content.querySelectorAll('li');
for(var i=0;i<ol.children.length;i++){ol.children[i].classList.remove('live')}
for(var j=ol.children.length;j<items.length;j++){var li=items[j].cloneNode(true);li.style.setProperty('--i','0');ol.appendChild(li)}
if(items.length&&items[items.length-1].classList.contains('live')&&ol.lastElementChild){ol.lastElementChild.classList.add('live')}})}
}).catch(function(){})}
window.addEventListener('doris:changed',tick);
(function loop(){setTimeout(function(){tick();loop()},window.__dorisDeskLive?8000:1500)})();})();
</script>"""


def page(title: str, body: str, *, body_class: str = "", poll: Optional[str] = None, decision_id: Optional[str] = None,
         chain_ids: Optional[list[str]] = None) -> str:
    attrs = f' class="{esc(body_class)}"' if body_class else ""
    if poll:
        attrs += f' data-poll="{esc(poll)}" data-id="{esc(decision_id)}"'
    if chain_ids:
        attrs += f' data-chain="{esc(",".join(chain_ids))}"'
    desk_head = desk_tail = ""
    if DESK_CONFIG and poll:
        desk_head = f'<meta name="doris-desk" content="{esc(json.dumps(DESK_CONFIG))}">'
        desk_tail = '<script type="module" src="/static/desk.js"></script>'
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"{desk_head}<title>{esc(title)}</title><style>{CSS}</style></head><body{attrs}><main>"
        "<header class=\"top\"><span class=\"name\">Doris</span>"
        f"<span class=\"tag\">{esc(TAGLINE)}</span><nav><span id=\"desk-status\" class=\"quiet\"></span>"
        "<a href=\"/\">Queue</a><a href=\"/samples\">Samples</a></nav></header>"
        f"{body}</main>{POLL_JS if poll else ''}{desk_tail}</body></html>"
    )


# --- pieces ---------------------------------------------------------------------------

def pill(decision: str) -> str:
    label, cls, _ = VERDICT.get(decision, (decision, "hold", ""))
    return f'<span class="pill v-{cls}">{esc(label)}</span>'


def summary(d: dict) -> str:
    changed = d.get("bank_account_new") and d.get("bank_account_new") != d.get("bank_account_on_file")
    rows = [
        ("Claims to be", d.get("vendor_claimed") or "Unknown"),
        ("Vendor on file", d.get("vendor_id") or "None (first-time vendor)"),
        ("Pay to", d.get("payee_name") or "Not found"),
        ("Amount", None),
        ("Sender", d.get("sender_email") or d.get("sender_domain") or "Unknown"),
        ("Account on file", d.get("bank_account_on_file") or "None"),
        ("Account requested", d.get("bank_account_new") or "Same as on file"),
        ("Invoice", d.get("invoice_id")),
    ]
    cells = []
    for k, v in rows:
        value = money(d.get("amount"), d.get("currency")) if k == "Amount" else esc(v)
        cls = " changed" if (k == "Account requested" and changed) else ""
        cells.append(f'<div><div class="k">{esc(k)}</div><div class="v{cls}">{value}</div></div>')
    return f'<section class="panel"><div class="grid">{"".join(cells)}</div></section>'


def reasons(d: dict) -> str:
    items = "".join(f"<li>{esc(r)}</li>" for r in d.get("reasons") or [])
    rationale = d.get("llm_rationale")
    extra = f'<p class="quiet" style="margin:10px 0 0">Claude: {esc(rationale)}</p>' if rationale else ""
    return f'<section class="panel"><h2>Why</h2><ol class="reasons">{items or "<li>No reasons given</li>"}</ol>{extra}</section>'


def hidden_text(d: dict) -> str:
    sig = next((s for s in d.get("signals") or [] if s.get("name") == "prompt_injection" and s.get("fired")), None)
    if not sig:
        return ""
    findings = (sig.get("evidence") or {}).get("findings") or []
    quotes = "".join(
        f'<p class="hidden-text">{esc(f.get("text"))}</p><p class="quiet" style="margin:2px 0 10px">'
        f'{esc(str(f.get("kind", "")).replace("_", " "))}{", line " + esc(f.get("line")) if f.get("line") else ""} ({esc(f.get("source"))})</p>'
        for f in findings[:3] if f.get("text"))
    return (f'<section class="panel"><h2>Hidden text Doris found</h2><p class="quiet" style="margin-top:0">'
            f'A real vendor has no reason to hide instructions. Their prompt injection became our evidence.</p>{quotes}</section>')


def signals_table(d: dict) -> str:
    rows = []
    for s in d.get("signals") or []:
        cls = ' class="fired"' if s.get("fired") else ""
        label = SIGNAL_LABEL.get(s.get("name"), s.get("name"))
        rows.append(f"<tr{cls}><td><span class=\"dot\"></span>{esc(label)}</td><td>{'Fired' if s.get('fired') else 'Quiet'}</td>"
                    f"<td>{esc(s.get('weight'))}</td><td>{esc(s.get('detail'))}</td></tr>")
    return ('<section class="panel"><h2>Every check that ran</h2><div class="scroll"><table><tr><th>Check</th><th>Result</th>'
            f'<th>Weight</th><th>Detail</th></tr>{"".join(rows)}</table></div></section>')


def audit_trail(chain: list[dict], audit_ok: Optional[bool]) -> str:
    status = ('<span class="ok">Chain verified</span>' if audit_ok else
              '<span class="bad">Chain verification failed</span>' if audit_ok is False else
              '<span class="quiet">Chain not checked</span>')
    items = []
    for d in chain:
        items.append(
            f"<li>{pill(d.get('decision', ''))} <span class=\"quiet\">#{esc(d.get('seq'))} · {esc(when(d.get('created_at')))} · "
            f"{esc(d.get('decision_id'))}</span><div class=\"hash\">prev {esc(d.get('prev_hash'))}<br>hash {esc(d.get('audit_hash'))}</div></li>")
    return (f'<section class="panel"><h2>Audit trail</h2><p style="margin-top:0">{status}. Each record\'s hash covers the one before it, '
            f'so nobody can quietly rewrite this history.</p><ol class="chain">{"".join(items)}</ol></section>')


# --- the Certificate Transparency strip ------------------------------------------------

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
    return (f'<section class="panel"><h2>Certificate Transparency</h2>{"".join(parts)}'
            f'<p class="quiet" style="margin:6px 0 0">{text}</p></section>')


# --- whole screens -------------------------------------------------------------------

def callback_panel(d: dict, call_text: str, can_call: bool) -> str:
    since = when(d.get("callback_number_on_file_since"), "month")
    button = ('<form class="inline" method="post" action="/d/{0}/call"><button class="primary">Call the vendor now</button></form>'
              .format(esc(d.get("decision_id")))) if can_call else ""
    manual = ('<form class="inline" method="post" action="/d/{0}/manual_callback"><span class="quiet">Called them yourself?</span>'
              '<input type="text" name="notes" placeholder="What they said" aria-label="What the vendor said">'
              '<button name="confirmed" value="no" class="danger">They never changed banks</button>'
              '<button name="confirmed" value="yes">They confirmed the change</button></form>').format(esc(d.get("decision_id")))
    return (f'<section class="panel"><h2>Callback</h2><p style="margin-top:0">Number on file: <strong>{esc(pretty_phone(d.get("callback_number")))}</strong>'
            f'{", on file since " + esc(since) if since else ""}. Never a number from the email.</p>'
            f'<p id="call-status" class="quiet">{esc(call_text)}</p>{button}{manual}</section>')


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


def review_panel(d: dict) -> str:
    """Approving a held payment is a control, not a click: the reviewer says how they verified it, and from what."""
    new_vendor = any(s.get("name") == "new_vendor" and s.get("fired") for s in d.get("signals") or [])
    age = domain_age_days(d)
    lead = ("First-time vendor: Doris has nothing on file to check this against, and documents can be forged. "
            "Before approving, confirm it yourself using contact details you found independently." if new_vendor else
            "Before approving, confirm the payment details yourself through a channel you already trusted.")
    untrusted = (f'<p class="quiet">From this email, so untrusted: {esc(d.get("sender_email") or d.get("sender_domain") or "the sender")}'
                 " and any phone number, link or address in it.</p>")
    evidence = ""
    young = ""
    if age is not None:
        span = f"{age // 365} years" if age >= 730 else f"{age} days"
        evidence = f'<p>{esc(d.get("sender_domain"))} got its first certificate {span} before this email.</p>'
        if age < YOUNG_DOMAIN_DAYS:
            young = (f'<label style="display:block;margin-top:6px"><input type="checkbox" name="young_ok" value="yes"> '
                     f'I know {esc(d.get("sender_domain"))} is only {age} days old</label>')
    options = "".join(f'<option value="{esc(k)}">{esc(v)}</option>' for k, v in VERIFY_METHODS.items())
    return ('<section class="panel"><h2>Human review</h2>'
            f'<p style="margin-top:0">{esc(lead)}</p>{untrusted}{evidence}'
            f'<form method="post" action="/d/{esc(d.get("decision_id"))}/review">'
            '<div class="inline" style="display:flex;gap:8px;flex-wrap:wrap">'
            '<input type="text" name="reviewer" placeholder="Your name" aria-label="Reviewer" required>'
            f'<select name="method" aria-label="How you verified it"><option value="">How did you verify it?</option>{options}</select>'
            '<input type="text" name="source" placeholder="Where that number or record came from" aria-label="Source" style="min-width:260px">'
            '<input type="text" name="notes" placeholder="Notes (optional)" aria-label="Notes"></div>'
            f'{young}<div class="inline" style="display:flex;gap:8px;margin-top:8px">'
            '<button name="approve" value="yes">Approve payment</button><button name="approve" value="no" class="danger">Reject</button>'
            '</div><p class="quiet" style="margin:6px 0 0">Approving needs how you verified it and where from. Rejecting needs only your name.</p>'
            '</form></section>')


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
    for e in events or []:
        who = {"doris": "Doris", "vendor": "Vendor", "system": "Call"}.get(e.get("who"), "Call")
        add(e.get("who", "system"), who, e.get("text", ""), _clock(e["at"]))
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
    return (f'<section class="panel"><h2>What Doris did</h2><ol class="trace" id="trace">{trace_items(chain, events, live)}</ol>'
            '</section>')


def audit_links(d: dict) -> str:
    i = esc(d.get("decision_id"))
    return (f'<p class="noprint" style="margin:8px 0 0"><a href="/d/{i}/audit">Open the audit record</a> · '
            f'<a href="/d/{i}/audit.json">Download the audit packet (JSON)</a></p>')


def audit_screen(packet: dict) -> str:
    rows = "".join(
        f"<tr><td>#{esc(r['seq'])}</td><td>{esc(r['decision_id'])}</td><td class=\"hash\">{esc(r['audit_hash'])}</td>"
        f"<td>{'<span class=ok>matches</span>' if r['matches'] else '<span class=bad>does not match</span>'}</td></tr>"
        for r in packet["recomputed_here"])
    log = packet.get("detector_log_verification") or {}
    log_line = ('<span class="ok">The detector\'s whole log verifies</span>' if log.get("ok") else
                '<span class="bad">The detector\'s log does not verify</span>' if log else '<span class="quiet">Detector log not checked</span>')
    chain = packet["decisions"]
    body = (f'<section class="panel"><h2>Audit record: invoice {esc(packet["invoice_id"])}</h2><p style="margin-top:0">Outcome: '
            f'{pill(packet["outcome"] or "")} · generated {esc(when(packet["generated_at"]))}</p>'
            f'<p class="noprint"><button onclick="window.print()">Print</button> <a href="/d/{esc(chain[-1]["decision_id"])}/audit.json">Download JSON</a> · '
            f'<a href="/d/{esc(chain[-1]["decision_id"])}">Back to the decision</a></p></section>'
            + trace_panel(chain, [{"at": _ts(e["at"]), "who": e["who"], "text": e["text"]} for e in packet["call_transcript"]])
            + '<section class="panel"><h2>Every record, recomputed by this dashboard</h2>'
              f'<p style="margin-top:0">{log_line}. Each hash below was recomputed here from the Decision itself.</p>'
              f'<div class="scroll"><table><tr><th>Seq</th><th>Decision</th><th>audit_hash</th><th>Recomputed</th></tr>{rows}</table></div></section>'
            + f'<section class="panel"><h2>Check it yourself</h2><p style="margin-top:0">{esc(packet["how_to_verify"])}</p>'
              f'<pre class="hash" style="white-space:pre-wrap">{esc(packet["verify_in_python"])}</pre></section>')
    return page(f"Audit record: {packet['invoice_id']}", body)


def _ts(iso: str) -> float:
    dt = parse_ts(iso)
    return dt.timestamp() if dt else 0.0


def safe(fn, *args, **kwargs) -> str:
    """Render one panel; if its data is too strange to draw, say so instead of failing the whole screen."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001  (a demo screen must never 500 over one panel)
        import logging
        logging.getLogger("doris.render").exception("panel %s failed", getattr(fn, "__name__", fn))
        return '<section class="panel"><p class="quiet" style="margin:0">This panel could not be drawn from this decision.</p></section>'


def review_screen(d: dict, *, chain: Optional[list[dict]] = None, audit_ok: Optional[bool] = None,
                  call_text: str = "", can_call: bool = False, poll: bool = True, flash: str = "",
                  events: Optional[list[dict]] = None, live: bool = False) -> str:
    if d.get("decision") == "BLOCKED":
        return blocked_screen(d, chain=chain, audit_ok=audit_ok, poll=poll, events=events)
    label, cls, line = VERDICT.get(d.get("decision"), (d.get("decision"), "hold", ""))
    body = [f'<p class="flash">{esc(flash)}</p>' if flash else "",
            f'<section class="banner v-{cls}"><h1>{esc(label)}</h1><p>{esc(line)} Risk {float(d.get("risk_score") or 0):.2f}.</p></section>',
            safe(trace_panel, chain or [d], events, live), safe(summary, d), safe(reasons, d)]
    if d.get("decision") == "CALLBACK":
        body.append(callback_panel(d, call_text, can_call))
    if d.get("decision") == "HOLD":
        body.append(review_panel(d))
    body += [safe(hidden_text, d), safe(ct_strip, d), safe(signals_table, d), safe(audit_trail, chain or [d], audit_ok), audit_links(d)]
    url = f"/d/{d.get('decision_id')}/state" if poll else None
    return page(f"{label}: {d.get('invoice_id')}", "".join(body), poll=url, decision_id=d.get("decision_id"),
                chain_ids=[x.get("decision_id") for x in (chain or [d]) if x.get("decision_id")])


def blocked_screen(d: dict, *, chain: Optional[list[dict]] = None, audit_ok: Optional[bool] = None, poll: bool = True,
                   events: Optional[list[dict]] = None) -> str:
    headline = (d.get("reasons") or ["Blocked"])[0]
    hero = (f'<section class="blocked-hero"><h1>BLOCKED</h1><p>{esc(headline)}</p>'
            f'<p style="font-size:16px;opacity:.9">{money(d.get("amount"), d.get("currency"))} to {esc(d.get("payee_name") or "an unknown payee")} '
            f'stays put. Risk {float(d.get("risk_score") or 0):.2f}.</p></section>')
    body = (hero + safe(trace_panel, chain or [d], events) + "".join(safe(f, d) for f in (summary, reasons, hidden_text, ct_strip))
            + safe(audit_trail, chain or [d], audit_ok) + audit_links(d) + safe(signals_table, d))
    url = f"/d/{d.get('decision_id')}/state" if poll else None
    return page(f"BLOCKED: {d.get('invoice_id')}", body, body_class="blocked-screen", poll=url, decision_id=d.get("decision_id"),
                chain_ids=[x.get("decision_id") for x in (chain or [d]) if x.get("decision_id")])


def queue_screen(decisions: Iterable[dict], *, detector_ok: bool, stats: Optional[dict] = None, demo: bool = True, flash: str = "",
                 live_from: Optional[str] = None) -> str:
    rows = []
    for d in decisions:
        rows.append(f'<tr><td>{esc(when(d.get("created_at"), "short"))}</td><td><a href="/d/{esc(d.get("decision_id"))}">{esc(d.get("invoice_id"))}</a></td>'
                    f'<td>{esc(d.get("vendor_claimed") or "")}</td><td>{money(d.get("amount"), d.get("currency"))}</td>'
                    f'<td>{pill(d.get("decision", ""))}</td><td>{float(d.get("risk_score") or 0):.2f}</td></tr>')
    status = '<span class="ok">Detector connected</span>' if detector_ok else '<span class="bad">Detector not reachable</span>'
    demo_form = ('<form class="inline" method="post" action="/demo/send"><button class="primary">Send the demo bank-change email</button>'
                 '<span class="quiet">Runs the live demo email through Doris and opens its screen.</span></form>'
                 '<p class="quiet" style="margin:8px 0 0"><a href="/?live=1">Live mode</a>: this page opens each new decision the moment it arrives.</p>') if demo else ""
    door = ""
    if stats:
        door = (f'<section class="panel"><h2>Forward-to-check door</h2><p style="margin:0">{esc(stats.get("checks", 0))} emails checked, '
                f'{esc(stats.get("scam", 0))} called a scam, {esc(stats.get("suspicious", 0))} suspicious. Feedback: '
                f'{esc(stats.get("up", 0))} right, {esc(stats.get("down", 0))} wrong.</p></section>')
    body = (f'<p class="flash">{esc(flash)}</p>' if flash else "") + \
           f'<section class="panel"><p style="margin-top:0">{status}.</p>{demo_form}</section>{door}' + \
           ('<section class="panel"><h2>Decisions, newest first</h2><div class="scroll"><table><tr><th>When</th><th>Invoice</th><th>Claims to be</th>'
            f'<th>Amount</th><th>Decision</th><th>Risk</th></tr>{"".join(rows) or "<tr><td colspan=6>No decisions yet</td></tr>"}</table></div></section>')
    if live_from is not None:
        return page("Doris queue (live)", body, poll="/latest/state", decision_id=live_from)
    return page("Doris queue", body)


def samples_screen(names: list[str]) -> str:
    links = "".join(f'<li><a href="/samples/{esc(n)}">{esc(n)}</a></li>' for n in names)
    return page("Contract samples", f'<section class="panel"><h2>Contract samples</h2><p class="quiet" style="margin-top:0">Each renders '
                                    f'exactly as a live Decision would. No detector needed.</p><ul>{links}</ul></section>')


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
