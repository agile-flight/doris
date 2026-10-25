"""Two servers in one process.

  Dashboard (127.0.0.1:8788, never exposed): the queue, the review and BLOCKED screens, review and
  manual-callback forms, the demo trigger, the sample gallery.
  Hooks (127.0.0.1:8789, the only port to expose, e.g. with Tailscale Funnel): Twilio voice webhooks,
  Doris's audio, the inbound email door and its feedback links. Every hook URL carries a secret token.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote, urlparse

from . import render
from .client import DetectorClient, DetectorError
from .config import ROOT, Settings
from .store import Store

log = logging.getLogger("doris.surfaces")
SAMPLES_DIR = ROOT / "contract" / "samples"
DEMO_EMAIL = Path(__file__).resolve().parent / "demo" / "bank_change_demo.eml"


class Surfaces:
    """Shared state for both servers."""

    def __init__(self, settings: Settings, client: Optional[DetectorClient] = None, store: Optional[Store] = None):
        self.settings = settings
        self.client = client or DetectorClient(settings.detector_url)
        self.store = store or Store(settings.data_dir / "surfaces.db")
        self.callbacks = None   # set by callback.wire()
        self.door = None        # set by door.wire()
        self.hook_routes: list = []   # (method, path regex, fn(handler, *groups)) registered by callback.py and door.py

    def route(self, method: str, pattern: str, fn) -> None:
        if not any(r[0] == method and r[1] == pattern for r in self.hook_routes):
            self.hook_routes.append((method, pattern, fn))

    def call_text(self, decision_id: str) -> str:
        call = self.store.latest_call(decision_id)
        if not call:
            return "Not called yet." if self.settings.twilio_ready else "Telephony isn't configured: call the number on file and record the answer below."
        text = {"starting": "Placing the call…", "queued": "Dialing…", "initiated": "Dialing…", "ringing": "Ringing…",
                "answered": "Doris is on the line.", "in-progress": "Doris is on the line.", "completed": "Call finished.",
                "busy": "Line busy. Try again.", "no-answer": "No answer. Try again.", "failed": "The call failed. Try again.",
                "canceled": "Call canceled.", "inconclusive": "No clear answer. Try again or record it below.",
                "saving": "Saving the vendor's answer…", "unsaved": "Doris couldn't save the answer. Record it below.",
                "resolved-elsewhere": "Already resolved.", "stale": "The last call never reported back. Try again.",
                "superseded": "Replaced by a newer call."}.get(call["status"], call["status"])
        if call.get("result"):
            text += f" Vendor said: {call['result'].rstrip('.!? ')}."
        return text

    def maybe_auto_call(self, decision: dict) -> None:
        if decision.get("decision") == "CALLBACK" and self.settings.auto_call and self.callbacks is not None:
            import time as _time
            if self.store.calls_since(_time.time() - self.settings.auto_call_cooldown) > 0:
                log.warning("auto-call skipped: another call started in the last %ss", self.settings.auto_call_cooldown)
                return
            try:
                self.callbacks.start(decision)
            except Exception as exc:  # never let a telephony problem lose the decision
                log.warning("auto-call failed: %s", exc)


def content_length(handler: BaseHTTPRequestHandler) -> int:
    """The declared body length, or -1 if it is missing, negative or not a number."""
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return -1
    return n if n >= 0 else -1


def _form(handler: BaseHTTPRequestHandler, limit: int = 100_000) -> dict[str, str]:
    length = content_length(handler)
    if length <= 0 or length > limit:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


class _Base(BaseHTTPRequestHandler):
    server_version = "doris-surfaces/0.1"
    timeout = 15          # a client that stops sending can't hold a thread
    app: Surfaces

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def send(self, status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8", headers: Optional[dict] = None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str) -> None:
        self.send(303, b"", headers={"Location": location})

    def json(self, status: int, value: Any) -> None:
        self.send(status, json.dumps(value), "application/json")

    def guarded(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except DetectorError as exc:
            status = 502 if exc.status >= 500 else exc.status
            self.send(status, render.page("Error", f'<p class="flash">The detector said: {render.esc(exc.message)}</p><p><a href="/">Back to the queue</a></p>'))
        except Exception:
            log.exception("request failed")
            self.send(500, render.page("Error", '<p class="flash">Something went wrong. Details are in the server log.</p>'))


class DashboardHandler(_Base):
    """Localhost only. The Host check stops DNS rebinding; the Origin check stops other sites posting forms."""

    def _allowed(self, post: bool) -> bool:
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if (self.headers.get("Host") or "") not in hosts:
            self.send(403, b"forbidden host", "text/plain")
            return False
        if post:
            origin = self.headers.get("Origin") or ""
            referer = self.headers.get("Referer") or ""
            ok = {f"http://{h}" for h in hosts}
            if (origin and origin not in ok) or (not origin and referer and not any(referer.startswith(o + "/") or referer == o for o in ok)):
                self.send(403, b"cross-site request refused", "text/plain")
                return False
        return True

    def do_GET(self):
        if self._allowed(False):
            self.guarded(self._get)

    def do_POST(self):
        if self._allowed(True):
            self.guarded(self._post)

    def _get(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        flash = (parse_qs(url.query).get("flash") or [""])[0][:200]
        if path == "/":
            try:
                decisions, ok = self.app.client.list(limit=50), True
            except DetectorError:
                decisions, ok = [], False
            newest = decisions[0]["decision_id"] if decisions else ""
            live = (parse_qs(url.query).get("live") or [""])[0] == "1"
            return self.send(200, render.queue_screen(decisions, detector_ok=ok, stats=self.app.store.stats(), flash=flash,
                                                      live_from=newest if live else None))
        if path == "/latest/state":
            decisions = self.app.client.list(limit=1)
            return self.json(200, {"latest_id": decisions[0]["decision_id"] if decisions else ""})
        if path == "/samples":
            return self.send(200, render.samples_screen([p.stem for p in sorted(SAMPLES_DIR.glob("*.json"))]))
        m = re.fullmatch(r"/samples/([\w\-]+)", path)
        if m:
            f = SAMPLES_DIR / f"{m.group(1)}.json"
            if not f.is_file():
                return self.send(404, render.page("Not found", "<p>No such sample.</p>"))
            d = json.loads(f.read_text())
            return self.send(200, render.review_screen(d, chain=[d], audit_ok=None, call_text="Sample: not a live decision.", poll=False))
        m = re.fullmatch(r"/d/([^/]+)/state", path)
        if m:
            chain, latest = self.app.client.chain(m.group(1))
            events = self.app.store.events([d["decision_id"] for d in chain])
            return self.json(200, {"decision_id": m.group(1), "latest_id": latest["decision_id"], "decision": latest["decision"],
                                   "call_text": self.app.call_text(m.group(1)),
                                   "trace_count": render.trace_items(chain, events).count("<li ")})
        m = re.fullmatch(r"/d/([^/]+)/trace", path)
        if m:
            chain, latest = self.app.client.chain(m.group(1))
            events = self.app.store.events([d["decision_id"] for d in chain])
            return self.send(200, render.trace_items(chain, events, live=self._call_live(latest["decision_id"])))
        m = re.fullmatch(r"/d/([^/]+)/audit(\.json)?", path)
        if m:
            chain, latest = self.app.client.chain(m.group(1))
            events = self.app.store.events([d["decision_id"] for d in chain])
            try:
                verify = self.app.client.verify()
            except DetectorError:
                verify = None
            from .auditview import packet
            pk = packet(chain, events, verify)
            if m.group(2):
                name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(pk["invoice_id"]))[:60]
                return self.send(200, json.dumps(pk, indent=2, ensure_ascii=False), "application/json; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="doris-audit-{name}.json"'})
            return self.send(200, render.audit_screen(pk))
        m = re.fullmatch(r"/d/([^/]+)", path)
        if m:
            try:
                chain, latest = self.app.client.chain(m.group(1))
            except DetectorError as exc:
                if exc.status == 404:
                    return self.send(404, render.page("Not found", "<p>No such decision.</p><p><a href=\"/\">Back to the queue</a></p>"))
                raise
            if latest["decision_id"] != m.group(1):
                return self.redirect(f"/d/{quote(latest['decision_id'])}")
            try:
                audit_ok = bool(self.app.client.verify().get("ok"))
            except DetectorError:
                audit_ok = None
            events = self.app.store.events([d["decision_id"] for d in chain])
            return self.send(200, render.review_screen(latest, chain=chain, audit_ok=audit_ok, call_text=self.app.call_text(latest["decision_id"]),
                                                      can_call=self.app.settings.twilio_ready, flash=flash, events=events,
                                                      live=self._call_live(latest["decision_id"])))
        self.send(404, render.page("Not found", "<p>Nothing here.</p>"))

    def _call_live(self, decision_id: str) -> bool:
        from .callback import LIVE
        call = self.app.store.latest_call(decision_id)
        return bool(call and call["status"] in LIVE)

    def _post(self):
        path = urlparse(self.path).path.rstrip("/")
        form = _form(self)
        if path == "/demo/send":
            decision = self.app.client.check_raw(DEMO_EMAIL.read_bytes())
            self.app.maybe_auto_call(decision)
            return self.redirect(f"/d/{quote(decision['decision_id'])}")
        m = re.fullmatch(r"/d/([^/]+)/(review|manual_callback|call)", path)
        if not m:
            return self.send(404, render.page("Not found", "<p>Nothing here.</p>"))
        decision_id, action = m.groups()
        if action == "review":
            approve = form.get("approve")
            if approve not in ("yes", "no") or not form.get("reviewer", "").strip():
                return self.redirect(f"/d/{quote(decision_id)}?flash=" + quote("Give your name and choose approve or reject."))
            notes = form.get("notes", "").strip()[:300]
            if approve == "yes":
                d = self.app.client.get(decision_id)
                method, source = form.get("method", ""), form.get("source", "").strip()[:200]
                age = render.domain_age_days(d)
                if method not in render.VERIFY_METHODS or len(source) < 3:
                    return self.redirect(f"/d/{quote(decision_id)}?flash=" + quote(
                        "To approve, say how you verified it and where that number or record came from."))
                if age is not None and age < render.YOUNG_DOMAIN_DAYS and form.get("young_ok") != "yes":
                    return self.redirect(f"/d/{quote(decision_id)}?flash=" + quote(
                        f"This sender's domain is only {age} days old. Tick the box to confirm you saw that."))
                notes = (f"Verified: {render.VERIFY_METHODS[method]}. Source: {source}."
                         + (f" Acknowledged the domain is {age} days old." if age is not None and age < render.YOUNG_DOMAIN_DAYS else "")
                         + (f" {notes}" if notes else ""))
            follow = self.app.client.review(decision_id, approve=approve == "yes", reviewer=form["reviewer"].strip()[:80], notes=notes[:500])
            return self.redirect(f"/d/{quote(follow['decision_id'])}")
        if action == "manual_callback":
            confirmed = form.get("confirmed")
            if confirmed not in ("yes", "no"):
                return self.redirect(f"/d/{quote(decision_id)}?flash=" + quote("Choose what the vendor said."))
            d = self.app.client.get(decision_id)
            notes = form.get("notes", "").strip()[:500] or ("confirmed the change" if confirmed == "yes" else "we never changed our bank")
            follow = self.app.client.callback_result(decision_id, confirmed=confirmed == "yes", notes=f"{notes} (recorded by the AP clerk)",
                                                     called=d.get("callback_number"))
            self.app.store.add_event(decision_id, "system", f"The AP clerk called the number on file and recorded: \"{notes}\"")
            return self.redirect(f"/d/{quote(follow['decision_id'])}")
        if action == "call":
            if self.app.callbacks is None or not self.app.settings.twilio_ready:
                return self.redirect(f"/d/{quote(decision_id)}?flash=" + quote("Telephony isn't configured yet; see surfaces/README.md."))
            from .callback import TelephonyError
            try:
                self.app.callbacks.start(self.app.client.get(decision_id))
            except TelephonyError as exc:
                return self.redirect(f"/d/{quote(decision_id)}?flash=" + quote(str(exc)[:180]))
            return self.redirect(f"/d/{quote(decision_id)}")


class HooksHandler(_Base):
    """Public webhooks. Routes are registered per app by callback.wire() and door.wire()."""

    def _dispatch(self, method: str):
        path = urlparse(self.path).path
        for verb, pattern, fn in self.app.hook_routes:
            m = re.fullmatch(pattern, path)
            if m and verb == method:
                return fn(self, *m.groups())
        self.send(404, b"not found", "text/plain")

    def do_GET(self):
        self.guarded(lambda: self._dispatch("GET"))

    def do_POST(self):
        self.guarded(lambda: self._dispatch("POST"))


class BoundedServer(ThreadingHTTPServer):
    """A threading server that refuses connections beyond a cap instead of spawning a thread per flood."""
    daemon_threads = True

    def __init__(self, address, handler, max_connections: int = 64):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_servers(app: Surfaces) -> tuple[ThreadingHTTPServer, ThreadingHTTPServer]:
    dash = type("Dash", (DashboardHandler,), {"app": app})
    hooks = type("Hooks", (HooksHandler,), {"app": app})
    a = BoundedServer((app.settings.host, app.settings.port), dash, app.settings.max_connections)
    b = BoundedServer((app.settings.host, app.settings.hooks_port), hooks, app.settings.max_connections)
    return a, b


def serve_forever(app: Surfaces) -> None:
    a, b = make_servers(app)
    threading.Thread(target=b.serve_forever, daemon=True).start()
    print(f"Role: {app.settings.role}   Dashboard: http://{app.settings.host}:{a.server_address[1]}   Hooks: http://{app.settings.host}:{b.server_address[1]}"
          f"   Detector: {app.settings.detector_url}   Telephony: {'ready' if app.settings.twilio_ready else 'not configured'}")
    try:
        a.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        a.server_close()
        b.server_close()
