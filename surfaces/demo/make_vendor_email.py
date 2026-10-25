"""Builds vendor_bank_change.eml (the live demo's bank-change attack) and routine_invoice.eml (a normal invoice to the
account on file, which Doris pays), from fictional companies whose domains
are unregistered (checked 2026-10-25), so the demo impersonates no one. Run: .venv/bin/python surfaces/demo/make_vendor_email.py"""
import io
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
LINES = [
    ("b", "Tannerfield Supply Co."), ("", "880 Embarcadero West, Oakland, CA 94607"),
    ("", "billing@tannerfieldsupply.com | (510) 555-0147"), ("", ""),
    ("b", "INVOICE"), ("", "Invoice Number: TS-20417"), ("", "Invoice Date: October 21, 2026"), ("", "Due Date: November 20, 2026"),
    ("", "Bill To: Marlowe Robotics Inc., 1450 Fourth St, Berkeley, CA 94710"), ("", ""),
    ("b", "Description                                   Qty    Unit       Amount"),
    ("", "Stainless fasteners, M8 (box of 500)          12   $185.00    $2,220.00"),
    ("", "Nitrile gloves, size L (case)                 20    $95.00    $1,900.00"),
    ("", "Freight                                        1   $700.00      $700.00"), ("", ""),
    ("", "Subtotal: $4,820.00"), ("b", "Total Due: $4,820.00"), ("", ""),
    ("", "Please note our updated banking details, effective immediately:"),
    ("", "Remit to: Tannerfield Supply Co."), ("", "Bank: Meridian Trust"),
    ("", "Routing Number: 026009593"), ("", "Account Number: 700233118841"), ("", ""),
    ("", "Thank you for your business."),
]


ROUTINE = LINES[:5] + [("", "Invoice Number: TS-20411"), ("", "Invoice Date: October 20, 2026"), ("", "Due Date: November 19, 2026")] \
    + LINES[8:18] + [("", "Remit to: Tannerfield Supply Co."), ("", "Bank: First Harbor Bank"), ("", "Routing Number: 121000358"),
                     ("", "Account Number: 400081231234"), ("", ""), ("", "Thank you for your business.")]


def pdf(lines=LINES) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    y = 740
    for style, text in lines:
        c.setFont("Helvetica-Bold" if style == "b" else "Helvetica", 12 if style == "b" else 10)
        c.drawString(60, y, text)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def main() -> None:
    msg = MIMEMultipart()
    msg["From"] = '"Tannerfield Supply Billing" <billing@tannerfieldsupply-billing.com>'
    msg["To"] = "ap@marlowerobotics.com"
    msg["Subject"] = "Updated banking details - invoice TS-20417"
    msg["Date"] = "Thu, 22 Oct 2026 09:03:00 +0000"
    msg.attach(MIMEText("Hi,\n\nWe have moved to a new treasury provider, so our bank account has changed. Please update the "
                        "account on file to the details in the attached invoice. This must be updated before Friday's "
                        "payment run.\n\nReply to this email to confirm the change.\n\nThanks,\nDana Whitfield\n"
                        "Accounts Receivable, Tannerfield Supply\n", "plain", "utf-8"))
    att = MIMEApplication(pdf(), _subtype="pdf")
    att.add_header("Content-Disposition", "attachment", filename="TS-20417_tannerfield.pdf")
    msg.attach(att)
    (HERE / "vendor_bank_change.eml").write_bytes(msg.as_bytes())
    msg = MIMEMultipart()
    msg["From"] = '"Tannerfield Supply" <billing@tannerfieldsupply.com>'
    msg["To"] = "ap@marlowerobotics.com"
    msg["Subject"] = "Invoice TS-20411 from Tannerfield Supply"
    msg["Date"] = "Tue, 20 Oct 2026 16:12:00 +0000"
    msg.attach(MIMEText("Hi team,\n\nPlease find attached invoice TS-20411 for October. Payment terms are net 30 as usual."
                        "\n\nThanks,\nDana Whitfield\nAccounts Receivable, Tannerfield Supply\n", "plain", "utf-8"))
    att = MIMEApplication(pdf(ROUTINE), _subtype="pdf")
    att.add_header("Content-Disposition", "attachment", filename="TS-20411_tannerfield.pdf")
    msg.attach(att)
    (HERE / "routine_invoice.eml").write_bytes(msg.as_bytes())


if __name__ == "__main__":
    main()
