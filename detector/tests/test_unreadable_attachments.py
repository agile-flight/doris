"""An invoice Doris can't read is never paid unseen: the bounty's easiest win, closed."""
import io
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest
from reportlab.pdfgen import canvas

from detector.config import Settings
from detector.pipeline import FIXTURES, Doris

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def doris(tmp_path):
    return Doris(Settings(offline=True, db_path=tmp_path / "d.db", outbox_dir=tmp_path / "o"), seed=FIXTURES / "vendors.json", now=lambda: NOW)


def acme_email(*attachments, body="Hi team, please find attached invoice INV-1060. Thanks, Dana"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = "Acme Supply <billing@acmesupply.com>", "ap@calhacksrobotics.com", "Invoice INV-1060", format_datetime(NOW)
    m.set_content(body)
    for name, ctype, data in attachments:
        main, sub = ctype.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m.as_bytes()


def scanned_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.rect(72, 500, 400, 200, fill=1)          # a picture of an invoice, no text layer
    c.showPage(); c.save()
    return buf.getvalue()


def test_a_scanned_pdf_is_held(doris):
    d = doris.check(acme_email(("inv.pdf", "application/pdf", scanned_pdf())))
    assert d["decision"] == "HOLD" and any("could not be read" in r for r in d["reasons"])


@pytest.mark.parametrize("name,ctype", [("remit.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                                        ("remit.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                                        ("remit.zip", "application/zip"), ("remit.bin", "application/octet-stream")])
def test_files_doris_cannot_open_are_held(doris, name, ctype):
    d = doris.check(acme_email((name, ctype, b"PK\x03\x04" + b"x" * 500)))
    assert d["decision"] == "HOLD"


def test_a_large_image_is_held_but_a_logo_is_not(doris):
    assert doris.check(acme_email(("scan.png", "image/png", b"\x89PNG" + b"x" * 60_000)))["decision"] == "HOLD"
    assert doris.check(acme_email(("logo.png", "image/png", b"\x89PNG" + b"x" * 4_000)))["decision"] == "PAY"


def test_calendar_and_signature_files_are_ignored(doris):
    assert doris.check(acme_email(("invite.ics", "text/calendar", b"BEGIN:VCALENDAR"),
                                  ("smime.p7s", "application/pkcs7-signature", b"0" * 900)))["decision"] == "PAY"


def test_bank_details_in_a_text_attachment_are_seen(doris):
    d = doris.check(acme_email(("remit.txt", "text/plain", b"Remit to: Acme Supply Co.\nRouting Number: 026009593\nAccount Number: 700233118841\n")))
    assert next(s for s in d["signals"] if s["name"] == "bank_account_changed")["fired"]
