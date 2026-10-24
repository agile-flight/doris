"""The detector, seen only through its HTTP API (contract/README.md)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class DetectorError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class DetectorClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Any = None, content_type: str = "application/json") -> Any:
        data = None
        headers = {}
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("error") or e.reason
            except (ValueError, AttributeError):
                msg = str(e.reason)
            raise DetectorError(e.code, str(msg)) from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise DetectorError(503, f"detector not reachable: {e}") from None

    def health(self) -> dict:
        return self._call("GET", "/health")

    def check_raw(self, raw: bytes) -> dict:
        return self._call("POST", "/check", raw, "message/rfc822")

    def check_text(self, text: str, pdf_base64: Optional[str] = None, pdf_filename: Optional[str] = None) -> dict:
        body: dict[str, Any] = {"text": text}
        if pdf_base64:
            body.update(pdf_base64=pdf_base64, pdf_filename=pdf_filename or "attachment.pdf")
        return self._call("POST", "/check", body)

    def get(self, decision_id: str) -> dict:
        return self._call("GET", f"/decisions/{urllib.parse.quote(decision_id, safe='')}")

    def list(self, limit: int = 200) -> list[dict]:
        return self._call("GET", f"/decisions?limit={int(limit)}")

    def callback_result(self, decision_id: str, *, confirmed: bool, notes: str, called: Optional[str]) -> dict:
        body: dict[str, Any] = {"vendor_confirmed": bool(confirmed), "notes": notes}
        if called:
            body["called"] = called
        return self._call("POST", f"/decisions/{urllib.parse.quote(decision_id, safe='')}/callback_result", body)

    def review(self, decision_id: str, *, approve: bool, reviewer: str, notes: str) -> dict:
        return self._call("POST", f"/decisions/{urllib.parse.quote(decision_id, safe='')}/review",
                          {"approve": bool(approve), "reviewer": reviewer, "notes": notes})

    def vendor(self, vendor_id: Optional[str]) -> Optional[dict]:
        """The record on file for a vendor (with its payment history), or None."""
        if not vendor_id:
            return None
        return next((v for v in self._call("GET", "/vendors") if v.get("vendor_id") == vendor_id), None)

    def verify(self) -> dict:
        return self._call("GET", "/audit/verify")

    # --- chains: a Decision and the follow-ups that supersede it ----------------------

    def chain(self, decision_id: str) -> tuple[list[dict], dict]:
        """(the invoice's decisions from the first to the latest, the latest). Follows `supersedes` both ways."""
        decisions = self.list(limit=500)
        by_id = {d["decision_id"]: d for d in decisions}
        if decision_id not in by_id:
            by_id[decision_id] = self.get(decision_id)
        start = by_id[decision_id]
        back = [start]
        while back[0].get("supersedes") and back[0]["supersedes"] in by_id:
            back.insert(0, by_id[back[0]["supersedes"]])
        children = {d["supersedes"]: d for d in decisions if d.get("supersedes")}
        forward = []
        cur = start
        while cur["decision_id"] in children:
            cur = children[cur["decision_id"]]
            forward.append(cur)
        chain = back + forward
        return chain, chain[-1]
