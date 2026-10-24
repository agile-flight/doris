"""Shared test setup. Tests never reach the network or the Claude API."""
import os
from pathlib import Path

import pytest

os.environ["DORIS_OFFLINE"] = "1"

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
