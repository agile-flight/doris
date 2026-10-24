"""Ingest: an email (.eml or forwarded text) and its PDF attachments, split into
visible text and hidden text. Hidden text never reaches the extractor or the judge;
it goes to the prompt-injection check as evidence.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Optional

import pdfplumber

from .models import Attachment, DocumentText, HiddenSpan, InboundMessage

TINY_FONT_PT = 4.0
NEAR_WHITE = 0.92

_HIDDEN_CSS = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?![.\d])|font-size\s*:\s*0*[01]?(?:\.\d+)?(?:px|pt|em)?\s*(?:;|$)"
    r"|color\s*:\s*(?:#fff(?:fff)?|white|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))",
    re.IGNORECASE,
)
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote", "pre"}


# --- colour helpers --------------------------------------------------------------

def near_white(color) -> bool:
    """True if a pdfplumber non_stroking_color is (near) white in gray, RGB or CMYK."""
    if color is None:
        return False
    try:
        parts = [float(c) for c in color]
    except TypeError:
        return False
    if len(parts) == 1:
        return parts[0] >= NEAR_WHITE
    if len(parts) == 3:
        return all(p >= NEAR_WHITE for p in parts)
    if len(parts) == 4:
        c, m, y, k = parts
        rgb = [(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)]
        return all(p >= NEAR_WHITE for p in rgb)
    return False


# --- HTML -------------------------------------------------------------------------

class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible: list[str] = []
        self.hidden: list[str] = []
        self._stack: list[bool] = []
        self._skip = 0
        self._current_hidden: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        attrs = dict(attrs)
        style = attrs.get("style") or ""
        hidden = bool(_HIDDEN_CSS.search(style)) or "hidden" in attrs or attrs.get("aria-hidden") == "true"
        parent_hidden = self._stack[-1] if self._stack else False
        self._stack.append(hidden or parent_hidden)
        if tag in _BLOCK_TAGS:
            self.visible.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        if self._stack:
            was_hidden = self._stack.pop()
            parent_hidden = self._stack[-1] if self._stack else False
            if was_hidden and not parent_hidden and self._current_hidden:
                self.hidden.append(" ".join(self._current_hidden).strip())
                self._current_hidden = []
        if tag in _BLOCK_TAGS:
            self.visible.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._stack and self._stack[-1]:
            self._current_hidden.append(text)
        else:
            self.visible.append(text + " ")


def html_to_text(html: str) -> tuple[str, list[HiddenSpan]]:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    if parser._current_hidden:
        parser.hidden.append(" ".join(parser._current_hidden).strip())
    text = re.sub(r"[ \t]+\n", "\n", "".join(parser.visible))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    spans = [HiddenSpan(source="html", reason="css_hidden", text=t) for t in parser.hidden if t]
    return text, spans


# --- PDF --------------------------------------------------------------------------

def pdf_text(data: bytes) -> DocumentText:
    """Visible text plus hidden spans, with 1-based line numbers in reading order."""
    visible_lines: list[str] = []
    hidden: list[HiddenSpan] = []
    line_no = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                x0, top0, x1, bottom0 = page.bbox
                for line in page.extract_text_lines(layout=False, strip=True, return_chars=True):
                    text = (line.get("text") or "").strip()
                    if not text:
                        continue
                    line_no += 1
                    chars = [c for c in line.get("chars", []) if (c.get("text") or "").strip()]
                    if not chars:
                        visible_lines.append(text)
                        continue
                    n = len(chars)
                    white = sum(near_white(c.get("non_stroking_color")) for c in chars)
                    tiny = sum(float(c.get("size") or 0) < TINY_FONT_PT for c in chars)
                    off = sum(c["x1"] <= x0 or c["x0"] >= x1 or c["bottom"] <= top0 or c["top"] >= bottom0 for c in chars)
                    reason = None
                    if white / n >= 0.8:
                        reason = "white_text"
                    elif tiny / n >= 0.8:
                        reason = "tiny_text"
                    elif off / n >= 0.8:
                        reason = "off_page"
                    if reason:
                        color = chars[0].get("non_stroking_color")
                        hidden.append(HiddenSpan(
                            source="pdf", reason=reason, text=text, line=line_no,
                            font_size=round(float(chars[0].get("size") or 0), 2),
                            color=[round(float(c), 3) for c in color] if color is not None else None,
                        ))
                    else:
                        visible_lines.append(text)
    except Exception as exc:  # malformed PDF: report, never crash the pipeline
        return DocumentText(visible="", hidden=[], line_count=0, error=f"{type(exc).__name__}: {exc}"[:200])
    return DocumentText(visible="\n".join(visible_lines), hidden=hidden, line_count=line_no)


# --- email ------------------------------------------------------------------------

_FWD_HEADER = re.compile(r"^\s*>?\s*(From|Subject|Date|To|Reply-To)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_GMAIL_DATE_FORMATS = ("%a, %b %d, %Y at %I:%M %p", "%a, %d %b %Y at %H:%M", "%B %d, %Y at %I:%M %p")


def _parse_loose_date(value: str) -> Optional[datetime]:
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in _GMAIL_DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _looks_like_email(msg: EmailMessage) -> bool:
    return bool(msg["From"] or msg["Subject"]) and bool(msg["Date"] or msg["Message-ID"] or msg["Content-Type"] or msg["MIME-Version"] or msg.get_payload())


def _parse_forwarded_text(text: str, now: datetime, raw_sha: str) -> InboundMessage:
    headers: dict[str, str] = {}
    first_from_end = None
    for m in _FWD_HEADER.finditer(text):
        key = m.group(1).lower()
        if key not in headers:
            headers[key] = m.group(2)
        if key == "from" and first_from_end is None:
            first_from_end = m.end()
    name, addr = parseaddr(headers.get("from", ""))
    received = _parse_loose_date(headers["date"]) if headers.get("date") else None
    body = text
    if first_from_end is not None:
        blank = re.search(r"\n\s*\n", text[first_from_end:])
        body = text[first_from_end + blank.end():] if blank else text[first_from_end:]
    return InboundMessage(
        sender_email=addr.lower() or None, sender_name=name or None,
        reply_to=parseaddr(headers.get("reply-to", ""))[1].lower() or None,
        subject=headers.get("subject"), body_text=body.strip(), body_hidden=[],
        attachments=[], received_at=received or now, raw_sha256=raw_sha, forwarded=True,
    )


def ingest(raw: bytes | str, *, now: Optional[datetime] = None) -> InboundMessage:
    """Parse an RFC 822 email or a forwarded plain-text message."""
    now = now or datetime.now(timezone.utc)
    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    msg = message_from_bytes(raw_bytes, policy=policy.default)
    if not _looks_like_email(msg):
        return _parse_forwarded_text(raw_bytes.decode("utf-8", errors="replace"), now, raw_sha)

    name, addr = parseaddr(str(msg["From"] or ""))
    reply_to = parseaddr(str(msg["Reply-To"] or ""))[1].lower() or None
    received = _parse_loose_date(str(msg["Date"])) if msg["Date"] else None

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if filename or disposition == "attachment" or ctype == "application/pdf":
            payload = part.get_payload(decode=True) or b""
            attachments.append(Attachment(filename=filename or "attachment", content_type=ctype, data=payload))
        elif ctype == "text/plain":
            text_parts.append(part.get_content())
        elif ctype == "text/html":
            html_parts.append(part.get_content())

    body_hidden: list[HiddenSpan] = []
    if text_parts:
        body_text = "\n".join(text_parts).strip()
        for html in html_parts:
            _, spans = html_to_text(html)
            body_hidden.extend(spans)
    elif html_parts:
        texts = []
        for html in html_parts:
            text, spans = html_to_text(html)
            texts.append(text)
            body_hidden.extend(spans)
        body_text = "\n".join(texts).strip()
    else:
        body_text = ""

    forwarded = bool(re.search(r"-{3,}\s*Forwarded message\s*-{3,}|^\s*Begin forwarded message", body_text, re.IGNORECASE | re.MULTILINE))
    sender_email = addr.lower() or None
    sender_name = name or None
    subject = str(msg["Subject"]) if msg["Subject"] else None
    if forwarded:
        inner = _parse_forwarded_text(body_text, now, raw_sha)
        if inner.sender_email:
            sender_email, sender_name = inner.sender_email, inner.sender_name
            subject = inner.subject or subject
            body_text = inner.body_text

    return InboundMessage(
        sender_email=sender_email, sender_name=sender_name, reply_to=reply_to, subject=subject,
        body_text=body_text, body_hidden=body_hidden, attachments=attachments,
        received_at=received or now, raw_sha256=raw_sha, forwarded=forwarded,
    )


def extract_documents(message: InboundMessage) -> list[DocumentText]:
    """Text of every PDF attachment, in order."""
    docs = []
    for att in message.attachments:
        if att.content_type == "application/pdf" or att.filename.lower().endswith(".pdf"):
            docs.append(pdf_text(att.data))
    return docs
