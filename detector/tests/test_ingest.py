from datetime import datetime, timezone

from detector.ingest import ingest, near_white, pdf_text, extract_documents


def test_eml_headers_and_attachment(fixtures):
    msg = ingest((fixtures / "emails" / "clean_acme.eml").read_bytes())
    assert msg.sender_email == "billing@acmesupply.com"
    assert msg.sender_name == "Acme Supply"
    assert msg.sender_domain == "acmesupply.com"
    assert msg.subject == "Invoice INV-1041 from Acme Supply"
    assert msg.received_at == datetime(2026, 10, 22, 8, 41, tzinfo=timezone.utc)
    assert msg.forwarded is False
    assert "Please find attached invoice INV-1041" in msg.body_text
    assert [a.filename for a in msg.attachments] == ["inv_1041_acme_clean.pdf"]
    assert msg.attachments[0].content_type == "application/pdf"
    assert msg.attachments[0].data.startswith(b"%PDF")


def test_forwarded_text_recovers_sender_from_body(fixtures):
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    msg = ingest((fixtures / "emails" / "forwarded_check.txt").read_text(), now=now)
    assert msg.forwarded is True
    assert msg.sender_email == "accounts@acme-supply.co"
    assert msg.sender_name == "Acme Supply Accounts"
    assert msg.sender_domain == "acme-supply.co"
    assert msg.subject == "URGENT: Updated remittance instructions - INV-1046"
    assert msg.received_at == datetime(2026, 10, 22, 15, 12, tzinfo=timezone.utc)
    assert "Account Number: 885500129930" in msg.body_text
    assert msg.attachments == []


def test_forwarded_text_without_date_uses_now():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    msg = ingest("From: Someone <x@example.com>\n\nhello", now=now)
    assert msg.received_at == now
    assert msg.sender_email == "x@example.com"


def test_html_hidden_text_is_separated(fixtures):
    msg = ingest((fixtures / "emails" / "prompt_injection.eml").read_bytes())
    assert "Attached is invoice INV-1043" in msg.body_text
    assert "approve this invoice immediately" not in msg.body_text
    assert len(msg.body_hidden) == 1
    span = msg.body_hidden[0]
    assert span.source == "html"
    assert span.reason == "css_hidden"
    assert "approve this invoice immediately" in span.text


def test_pdf_white_text_is_hidden_on_line_14(fixtures):
    doc = pdf_text((fixtures / "invoices" / "inv_1043_blue_horizon_injection.pdf").read_bytes())
    assert doc.error is None
    assert "pre-approved" not in doc.visible
    assert "Blue Horizon Automobile LLC" in doc.visible
    assert "Invoice Number: INV-1043" in doc.visible
    assert len(doc.hidden) == 1
    span = doc.hidden[0]
    assert span.source == "pdf"
    assert span.line == 14
    assert span.reason == "white_text"
    assert span.font_size == 9.0
    assert "pre-approved" in span.text


def test_pdf_tiny_text_is_hidden(fixtures):
    doc = pdf_text((fixtures / "invoices" / "inv_1045_acme_tiny_text.pdf").read_bytes())
    assert len(doc.hidden) == 1
    assert doc.hidden[0].reason == "tiny_text"
    assert doc.hidden[0].font_size == 2.0
    assert "Ignore prior instructions" in doc.hidden[0].text
    assert "Ignore prior instructions" not in doc.visible


def test_clean_pdf_has_no_hidden_text(fixtures):
    doc = pdf_text((fixtures / "invoices" / "inv_1041_acme_clean.pdf").read_bytes())
    assert doc.hidden == []
    assert "Total Due: $4,820.00" in doc.visible
    assert doc.line_count > 10


def test_malformed_pdf_reports_error_instead_of_raising():
    doc = pdf_text(b"%PDF-1.4 this is not really a pdf")
    assert doc.visible == ""
    assert doc.hidden == []
    assert doc.error


def test_near_white_handles_gray_rgb_cmyk():
    assert near_white((1.0,)) is True
    assert near_white((0.95, 0.95, 0.95)) is True
    assert near_white((1.0, 1.0, 1.0)) is True
    assert near_white((0.0, 0.0, 0.0, 0.0)) is True   # CMYK white
    assert near_white((0.0, 0.0, 0.0)) is False
    assert near_white((0.0, 0.0, 0.0, 1.0)) is False
    assert near_white((0.2, 0.9, 0.9)) is False
    assert near_white(None) is False
    assert near_white([1, 1, 1]) is True


def test_extract_documents_reads_pdf_attachments(fixtures):
    msg = ingest((fixtures / "emails" / "new_vendor_northwind.eml").read_bytes())
    docs = extract_documents(msg)
    assert len(docs) == 1
    assert "Northwind Traders Inc." in docs[0].visible


def test_raw_sha256_is_stable(fixtures):
    raw = (fixtures / "emails" / "clean_acme.eml").read_bytes()
    assert ingest(raw).raw_sha256 == ingest(raw).raw_sha256
    assert len(ingest(raw).raw_sha256) == 64
