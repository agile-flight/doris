"""CLI: python -m surfaces serve"""
from __future__ import annotations

import argparse
import logging
import sys

from .app import Surfaces, serve_forever
from .config import Settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m surfaces", description="Doris surfaces (Agent B).")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="the dashboard (localhost) and the public hooks server")
    p.add_argument("--port", type=int)
    p.add_argument("--hooks-port", type=int)
    p.add_argument("--role", choices=["demo", "bounty", "all"], help="demo: dashboard, callback, AP inbox. bounty: the check@ door only")
    p = sub.add_parser("demo-seed", help="seed a demo detector database whose Acme record rings the judges' table")
    p.add_argument("--phone", required=True, help="E.164 number of the phone on the judges' table, e.g. +14155550123")
    p.add_argument("--db", default="data/demo.db", help="detector database to seed (use the same DORIS_DB for serve)")
    p.add_argument("--add", action="store_true", help="add missing demo records to an existing database")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(name)s: %(message)s")
    settings = Settings.from_env()
    if getattr(args, "port", None):
        settings.port = args.port
    if getattr(args, "hooks_port", None):
        settings.hooks_port = args.hooks_port
    if args.cmd == "demo-seed":
        from .demo import seed_demo
        print(seed_demo(args.phone, args.db, add=args.add))
        return 0
    if args.cmd == "serve":
        if getattr(args, "role", None):
            settings.role = args.role
        if settings.role == "bounty" and settings.auto_call:
            settings.auto_call = False
        app = Surfaces(settings)
        from . import callback, door
        if settings.role in ("demo", "all"):
            callback.wire(app)
        door.wire(app)
        from . import desk, payments, render
        if settings.role in ("demo", "all"):
            payments.wire(app)
        if desk.wire(app):
            render.DESK_CONFIG = app.desk.browser_config()
        serve_forever(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
