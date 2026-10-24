"""Doris: the detection engine (Agent A).

Public surface for Agent B:
    from detector import Doris
    decision = Doris().check(raw_email_bytes)
"""
__version__ = "0.1.0"


def __getattr__(name):
    if name == "Doris":
        from .pipeline import Doris
        return Doris
    raise AttributeError(name)
