"""CLI: python -m detector <command>"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .models import Attachment
from .pipeline import FIXTURES, Doris


def _doris(args) -> Doris:
    settings = Settings.from_env(**({"offline": True} if getattr(args, "offline", False) else {}))
    doris = Doris(settings)
    if not doris.vendors.list():
        added = doris.vendors.seed(FIXTURES / "vendors.json")
        print(f"(vendor table was empty; seeded {added} fixture vendors into {settings.db_path})", file=sys.stderr)
    return doris


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m detector", description="Doris: the AP fraud detector (Agent A).")
    parser.add_argument("--offline", action="store_true", help="force offline mode (no Claude, fixture CT)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="score one email (.eml or forwarded text) and print the Decision")
    p.add_argument("file")
    p.add_argument("--pdf", help="attach a PDF invoice")
    p.add_argument("--invoice-id")
    p = sub.add_parser("serve", help="HTTP API for the surfaces")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p = sub.add_parser("seed", help="load vendors from a JSON fixture")
    p.add_argument("file", nargs="?", default=str(FIXTURES / "vendors.json"))
    sub.add_parser("verify", help="verify the audit chain")
    p = sub.add_parser("decisions", help="list decisions, newest first")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("samples", help="regenerate contract/samples from the pipeline")
    p.add_argument("--out", default=None)
    p = sub.add_parser("evaluate", help="confusion matrix on the labelled test set")
    p.add_argument("--testset", default=None)
    p.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(name)s: %(message)s")

    if args.cmd == "samples":
        from .samples import make_samples
        paths = make_samples(Path(args.out)) if args.out else make_samples()
        for path in paths:
            d = json.loads(path.read_text())
            print(f"{path.name:36} {d['decision']:9} {d['risk_score']:.2f}")
        return 0
    if args.cmd == "evaluate":
        from .evaluate import run_evaluation
        report = run_evaluation(testset=Path(args.testset) if args.testset else None, out=Path(args.out) if args.out else None,
                                offline=args.offline or Settings.from_env().offline)
        print(report.render())
        return 0

    doris = _doris(args)
    try:
        if args.cmd == "check":
            path = Path(args.file)
            raw = path.read_bytes() if path.suffix.lower() == ".eml" else path.read_text()
            extra = [Attachment(Path(args.pdf).name, "application/pdf", Path(args.pdf).read_bytes())] if args.pdf else []
            decision = doris.check(raw, extra_attachments=extra, invoice_id=args.invoice_id)
            print(json.dumps(decision, indent=2, ensure_ascii=False))
            return 0
        if args.cmd == "serve":
            from .api import serve
            serve(doris, args.host, args.port)
            return 0
        if args.cmd == "seed":
            print(f"seeded {doris.vendors.seed(args.file)} new vendors")
            return 0
        if args.cmd == "verify":
            result = doris.verify()
            print(json.dumps(result.__dict__))
            return 0 if result.ok else 1
        if args.cmd == "decisions":
            for d in doris.list_decisions(limit=args.limit):
                print(f"{d['seq']:>5} {d['created_at']} {d['decision_id']} {d['decision']:9} {d['risk_score']:.2f} {d['invoice_id']}  {d['vendor_claimed'] or ''}")
            return 0
    finally:
        doris.close()
    return 2


if __name__ == "__main__":
    sys.exit(main())
