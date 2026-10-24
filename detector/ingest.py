"""Ingest: an email (.eml or forwarded text) and its PDF attachments, split into
visible text and hidden text. Hidden text never reaches the extractor or the judge;
it goes to the prompt-injection check as evidence.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Optional

import pdfplumber
from pdfminer.converter import PDFLayoutAnalyzer

from .models import Attachment, DocumentText, HiddenSpan, InboundMessage

log = logging.getLogger("doris.ingest")
TINY_FONT_PT = 4.0
NEAR_WHITE = 0.92

_HIDDEN_CSS = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0+(?:\.0+)?\s*(?:;|$)"
    r"|font-size\s*:\s*0+(?:\.0+)?\s*(?:px|pt|em)?\s*(?:;|$)"
    r"|(?<![-\w])color\s*:\s*(?:#fff\b|#ffffff\b|white\b|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\)|rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*1\s*\))"
    r"|position\s*:\s*absolute;\s*left\s*:\s*-\d{3,}",
    re.IGNORECASE,
)
_FONT_COLOR = re.compile(r"^(#fff\b|#ffffff\b|white)$", re.IGNORECASE)
_NEAR_WHITE_COLOR = re.compile(r"(?<![-\w])color\s*:\s*#(?:fefefe|fdfdfd|fcfcfc|fbfbfb)\b", re.IGNORECASE)
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote", "pre"}


# --- colour helpers --------------------------------------------------------------

def near_white(color) -> bool:
    """True if a pdfplumber non_stroking_color is (near) white in gray, RGB or CMYK."""
    if color is None:
        return False
    try:
        parts = [float(c) for c in color]
    except (TypeError, ValueError):
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

_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible: list[str] = []
        self.hidden: list[str] = []
        # Stack of (tag_name, is_hidden) so stray end tags don't pop the wrong context.
        self._stack: list[tuple[str, bool]] = []
        self._skip = 0
        self._current_hidden: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
            return
        if tag in _VOID_TAGS:
            # Void tags self-close: they inherit hidden state but don't stay on the stack.
            parent_hidden = self._stack[-1][1] if self._stack else False
            if parent_hidden and tag in _BLOCK_TAGS:
                self.visible.append("\n")
            return
        attrs = dict(attrs)
        style = attrs.get("style") or ""
        # <font color=#ffffff> is an HTML attribute, not CSS
        font_color = _FONT_COLOR.search(attrs.get("color", "") or "")
        hidden = (
            bool(_HIDDEN_CSS.search(style))
            or bool(_NEAR_WHITE_COLOR.search(style))
            or bool(font_color)
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
        )
        parent_hidden = self._stack[-1][1] if self._stack else False
        self._stack.append((tag, hidden or parent_hidden))
        if tag in _BLOCK_TAGS and not (hidden or parent_hidden):
            self.visible.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
            return
        if self._stack:
            # Only pop a matching open tag; stray end tags (</b> inside <span hidden>) are ignored.
            for i in range(len(self._stack) - 1, -1, -1):
                if self._stack[i][0] == tag:
                    was_hidden = self._stack[i][1]
                    del self._stack[i]
                    parent_hidden = self._stack[-1][1] if self._stack else False
                    if was_hidden and not parent_hidden and self._current_hidden:
                        self.hidden.append(" ".join(self._current_hidden).strip())
                        self._current_hidden = []
                    break
        if tag in _BLOCK_TAGS and not (self._stack and self._stack[-1][1]):
            self.visible.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._stack and self._stack[-1][1]:
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

class _RenderModeScanner(PDFLayoutAnalyzer):
    """Collects the bounding boxes of characters drawn with text render mode 3 (invisible).

    pdfplumber's char dicts do not carry the render mode, so this scans the page with pdfminer
    and records where the invisible characters are. `with_text`/bbox coordinates match
    pdfplumber's char `x0`/`y0`, so the two can be aligned exactly.
    """

    def __init__(self, rsrcmgr):
        super().__init__(rsrcmgr, laparams=None)
        self._render = 0
        self.invisible: set[tuple[float, float]] = set()

    def render_string(self, textstate, seq, ncs, graphicstate):
        self._render = int(getattr(textstate, "render", 0) or 0)
        super().render_string(textstate, seq, ncs, graphicstate)

    def render_char(self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate):
        adv = super().render_char(matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate)
        if self._render == 3:
            objs = getattr(self.cur_item, "_objs", None)
            if objs:
                x0, y0, _, _ = objs[-1].bbox
                self.invisible.add((round(float(x0), 1), round(float(y0), 1)))
        return adv


def _invisible_char_positions(data: bytes) -> set[tuple[float, float]]:
    """(x0, y0) positions of render-mode-3 characters, or an empty set if the scan fails."""
    positions: set[tuple[float, float]] = set()
    try:
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
        from pdfminer.pdfpage import PDFPage
        from pdfminer.pdfparser import PDFParser

        rsrcmgr = PDFResourceManager()
        device = _RenderModeScanner(rsrcmgr)
        interpreter = PDFPageInterpreter(rsrcmgr, device)
        for page in PDFPage.create_pages(PDFDocument(PDFParser(io.BytesIO(data)))):
            interpreter.process_page(page)
            positions |= device.invisible
            device.invisible = set()
    except Exception as exc:  # never let the extra scan break a document
        log.debug("render-mode scan failed: %s", exc)
    return positions


def _char_hidden_reason(ch, x0: float, top0: float, x1: float, bottom0: float,
                        invisible: Optional[set[tuple[float, float]]] = None) -> Optional[str]:
    """Why this single character is invisible to a human, or None if it is normally visible.

    Per character, not per line: white text sharing a line with visible text is still hidden,
    and a line that is 60% white does not hide the other 40%.
    """
    if ch.get("render_mode") == 3:            # render mode 3: text is never painted
        return "white_text"
    if invisible and (round(float(ch.get("x0", 0)), 1), round(float(ch.get("y0", 0)), 1)) in invisible:
        return "white_text"                   # render mode 3, found by the pdfminer scan
    if float(ch.get("size") or 0) < TINY_FONT_PT:
        return "tiny_text"
    if near_white(ch.get("non_stroking_color")):
        return "white_text"
    if ch["x1"] <= x0 or ch["x0"] >= x1 or ch["bottom"] <= top0 or ch["top"] >= bottom0:
        return "off_page"
    return None


def _colour(ch) -> Optional[list[float]]:
    color = ch.get("non_stroking_color")
    try:
        return [round(float(c), 3) for c in color]
    except (TypeError, ValueError):
        return None


def _line_runs(chars: list, reasons: list[Optional[str]]) -> list[tuple[Optional[str], str, dict]]:
    """Group consecutive characters into (reason, text, first_char) runs, rebuilding word spaces.

    `extract_text_lines` drops the space glyphs, so a space is inserted wherever the gap between
    two characters is wide enough to be a normal word space.
    """
    runs: list[list] = []
    for j, ch in enumerate(chars):
        prefix = ""
        if j:
            gap = float(ch.get("x0", 0)) - float(chars[j - 1].get("x1", 0))
            size = float(ch.get("size") or 0) or 10.0
            if gap > max(0.6, 0.2 * size):
                prefix = " "
        piece = prefix + (ch.get("text") or "")
        if runs and runs[-1][0] == reasons[j]:
            runs[-1][1] += piece
        else:
            runs.append([reasons[j], piece, ch])
    return [(r, t.strip(), c) for r, t, c in runs if t.strip()]


def pdf_text(data: bytes) -> DocumentText:
    """Visible text plus hidden spans, with 1-based line numbers in reading order.

    Characters are classified one at a time, so a line that mixes visible and hidden text is
    split into a visible part and a hidden span rather than being read as one or the other.
    """
    visible_lines: list[str] = []
    hidden: list[HiddenSpan] = []
    line_no = 0
    invisible = _invisible_char_positions(data)
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
                    reasons = [_char_hidden_reason(c, x0, top0, x1, bottom0, invisible) for c in chars]
                    runs = _line_runs(chars, reasons)
                    rebuilt = " ".join(t for _, t, _ in runs)
                    if " ".join(rebuilt.split()) != " ".join(text.split()):
                        # Could not align the per-character view to the line text: keep the line
                        # whole rather than risk inventing or dropping text.
                        if all(reasons):
                            hidden.append(HiddenSpan(source="pdf", reason=reasons[0], text=text, line=line_no,
                                                     font_size=round(float(chars[0].get("size") or 0), 2),
                                                     color=_colour(chars[0])))
                        else:
                            visible_lines.append(text)
                        continue
                    visible_parts: list[str] = []
                    for reason, run_text, first in runs:
                        if reason:
                            hidden.append(HiddenSpan(
                                source="pdf", reason=reason, text=run_text, line=line_no,
                                font_size=round(float(first.get("size") or 0), 2),
                                color=_colour(first),
                            ))
                        else:
                            visible_parts.append(run_text)
                    if visible_parts:
                        visible_lines.append(" ".join(visible_parts))
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
    outer_sender_email = addr.lower() or None  # who actually sent us this message, before any forward override

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
        # Always scan HTML parts too: a bank change may live only in the HTML body.
        html_texts = []
        for html in html_parts:
            text, spans = html_to_text(html)
            html_texts.append(text)
            body_hidden.extend(spans)
        if html_texts:
            body_text = (body_text + "\n" + "\n".join(html_texts)).strip()
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
        outer_sender_email=outer_sender_email,
    )


def extract_documents(message: InboundMessage) -> list[DocumentText]:
    """Text of every attachment, in order.

    PDFs are detected by content type, extension, or the %PDF magic bytes (so an
    application/octet-stream invoice is still read). HTML attachments are read as text too.
    """
    docs = []
    for att in message.attachments:
        name = att.filename or "attachment"
        lower, ctype = name.lower(), (att.content_type or "").lower()
        if ctype == "application/pdf" or lower.endswith(".pdf") or att.data[:5] == b"%PDF-":
            doc = pdf_text(att.data)
            doc.name, doc.kind = name, "pdf"
            docs.append(doc)
        elif ctype in ("text/html", "application/xhtml+xml") or lower.endswith((".html", ".htm")):
            text, spans = html_to_text(att.data.decode("utf-8", errors="replace"))
            docs.append(DocumentText(visible=text, hidden=spans, line_count=text.count("\n") + 1, name=name, kind="html"))
    return docs
