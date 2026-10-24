"""The HTTP surface for Agent B. Standard library only.

    POST /check                               raw email (message/rfc822) or JSON {"text", "pdf_base64"?, "invoice_id"?}
    GET  /decisions?limit=50                  newest first
    GET  /decisions/{id}
    POST /decisions/{id}/callback_result      {"vendor_confirmed": bool, "notes": str, "called"?: str}
    POST /decisions/{id}/review               {"approve": bool, "reviewer": str, "notes": str}
    GET  /audit/verify
    GET  /vendors
    GET  /health
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .models import Attachment
from .pipeline import Doris

log = logging.getLogger("doris.api")
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"}
MAX_BODY_BYTES = 25 * 1024 * 1024  # 25 MB: a raw email plus a PDF attachment


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def make_handler(doris: Doris):
    class Handler(BaseHTTPRequestHandler):
        server_version = "doris/0.1"

        def log_message(self, fmt, *args):  # quiet, structured
            log.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: Any) -> None:
            data = json.dumps(_jsonable(body), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in CORS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                raise ValueError("invalid Content-Length header")
            if length < 0:
                raise ValueError(f"invalid Content-Length: {length}")
            if length > MAX_BODY_BYTES:
                raise ValueError(f"body too large ({length} bytes; limit {MAX_BODY_BYTES})")
            return self.rfile.read(length) if length else b""

        def do_OPTIONS(self):
            self.send_response(204)
            for k, v in CORS.items():
                self.send_header(k, v)
            self.end_headers()

        def do_GET(self):
            try:
                self._get()
            except KeyError as exc:
                self._send(404, {"error": f"no decision {exc.args[0] if exc.args else ''}"})
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                self._send(400, {"error": str(exc)[:300]})
            except Exception as exc:  # never leak a traceback to the caller
                log.exception("request failed")
                self._send(500, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"})

        def _get(self):
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            if path == "/health":
                return self._send(200, {"ok": True, "offline": doris.settings.offline, "model": doris.settings.model,
                                        "llm_available": doris.llm.available, "llm_disabled_reason": doris.llm.disabled_reason,
                                        "records": doris.audit.head()[0]})
            if path == "/decisions":
                raw_limit = (parse_qs(url.query).get("limit") or ["50"])[0]
                try:
                    limit = int(raw_limit)
                except (TypeError, ValueError):
                    return self._send(400, {"error": f'limit must be an integer, got {raw_limit!r}'})
                if limit < 1:
                    return self._send(400, {"error": "limit must be >= 1"})
                return self._send(200, doris.list_decisions(limit=min(limit, 1000)))
            m = re.fullmatch(r"/decisions/([^/]+)", path)
            if m:
                decision = doris.get_decision(m.group(1))
                return self._send(200, decision) if decision else self._send(404, {"error": f"no decision {m.group(1)}"})
            if path == "/audit/verify":
                return self._send(200, asdict(doris.verify()))
            if path == "/evaluation":
                from . import evaluate
                if not evaluate.DEFAULT_OUT.exists():
                    return self._send(404, {"error": "no evaluation yet; run: python -m detector evaluate"})
                return self._send(200, json.loads(evaluate.DEFAULT_OUT.read_text()))
            if path == "/vendors":
                return self._send(200, [asdict(v) for v in doris.vendors.list()])
            self._send(404, {"error": f"no route {path}"})

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            try:
                raw = self._body()
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if path == "/check":
                    if ctype == "application/json":
                        body = json.loads(raw.decode("utf-8"))
                        if not isinstance(body, dict):
                            return self._send(400, {"error": "JSON object expected"})
                        text = body.get("text")
                        if not isinstance(text, str) or not text.strip():
                            return self._send(400, {"error": 'JSON body needs "text" (a string: the email or forwarded message)'})
                        invoice_id = body.get("invoice_id")
                        if invoice_id is not None and (isinstance(invoice_id, bool) or not isinstance(invoice_id, (str, int, float))):
                            return self._send(400, {"error": '"invoice_id" must be a string'})
                        extra = []
                        if body.get("pdf_base64") is not None:
                            pdf = body["pdf_base64"]
                            if not isinstance(pdf, str):
                                return self._send(400, {"error": '"pdf_base64" must be a base64 string'})
                            extra.append(Attachment(body.get("pdf_filename") or "attachment.pdf", "application/pdf", base64.b64decode(pdf)))
                        return self._send(200, doris.check(text, extra_attachments=extra, invoice_id=invoice_id))
                    if not raw.strip():
                        return self._send(400, {"error": "empty body"})
                    return self._send(200, doris.check(raw))
                m = re.fullmatch(r"/decisions/([^/]+)/(callback_result|review)", path)
                if m:
                    body = json.loads(raw.decode("utf-8") or "{}")
                    if not isinstance(body, dict):
                        return self._send(400, {"error": "JSON object expected"})
                    if m.group(2) == "callback_result":
                        if "vendor_confirmed" not in body:
                            return self._send(400, {"error": '"vendor_confirmed" (bool) is required'})
                        if not isinstance(body["vendor_confirmed"], bool):
                            return self._send(400, {"error": '"vendor_confirmed" must be a boolean (true/false)'})
                        called = body.get("called")
                        if called is not None and not isinstance(called, str):
                            return self._send(400, {"error": '"called" must be a string (E.164)'})
                        return self._send(200, doris.resolve_callback(m.group(1), vendor_confirmed=body["vendor_confirmed"],
                                                                      notes=str(body.get("notes") or ""), called=called))
                    if "approve" not in body:
                        return self._send(400, {"error": '"approve" (bool) is required'})
                    if not isinstance(body["approve"], bool):
                        return self._send(400, {"error": '"approve" must be a boolean (true/false)'})
                    return self._send(200, doris.resolve_review(m.group(1), approve=body["approve"],
                                                                reviewer=str(body.get("reviewer") or ""), notes=str(body.get("notes") or "")))
                return self._send(404, {"error": f"no route {path}"})
            except KeyError as exc:
                return self._send(404, {"error": f"no decision {exc.args[0] if exc.args else ''}"})
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error) as exc:
                return self._send(400, {"error": str(exc)[:300]})
            except Exception as exc:  # never leak a traceback to the caller
                log.exception("request failed")
                return self._send(500, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"})

    return Handler


def make_server(doris: Doris, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(doris))
    server.daemon_threads = True
    return server


def serve(doris: Doris, host: str = "127.0.0.1", port: int = 8787) -> None:
    server = make_server(doris, host, port)
    print(f"Doris detector listening on http://{host}:{server.server_address[1]}  (offline={doris.settings.offline}, model={doris.settings.model})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
