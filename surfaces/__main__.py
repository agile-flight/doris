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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(name)s: %(message)s")
    settings = Settings.from_env()
    if getattr(args, "port", None):
        settings.port = args.port
    if getattr(args, "hooks_port", None):
        settings.hooks_port = args.hooks_port
    if args.cmd == "serve":
        app = Surfaces(settings)
        try:
            from . import callback, door  # noqa: F401  (registers hook routes and wires managers)
            callback.wire(app)
            door.wire(app)
        except ImportError:
            pass
        serve_forever(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
