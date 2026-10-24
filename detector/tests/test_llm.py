"""The Claude client never raises and never breaks the deterministic path.

These tests use fakes; they never touch the network.
"""
import anthropic
import pytest
from pydantic import BaseModel, ValidationError

from detector.config import Settings
from detector.llm import Claude


class Out(BaseModel):
    risk: float


class FakeMessage:
    def __init__(self, parsed_output=None, stop_reason=None, stop_details=None, raises=None):
        self._parsed = parsed_output
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self._raises = raises

    @property
    def parsed_output(self):
        if self._raises is not None:
            raise self._raises
        return self._parsed


class FakeMessages:
    def __init__(self, message=None, exc=None):
        self._message = message
        self._exc = exc

    def parse(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._message


class FakeBeta:
    def __init__(self, messages):
        self.messages = messages


class FakeClient:
    def __init__(self, message=None, exc=None):
        self.messages = FakeMessages(message, exc)
        self.beta = FakeBeta(self.messages)


def make_llm(client=None, exc=None, **overrides) -> Claude:
    llm = Claude(Settings(**overrides))
    if exc is not None:
        def boom():
            raise exc
        llm._get_client = boom
    elif client is not None:
        llm._get_client = lambda: client
    return llm


def test_offline_client_is_unavailable_without_calling():
    llm = Claude(Settings(offline=True))
    assert llm.available is False
    assert llm.parse(system="s", user="u", output_model=Out, effort="low") is None


def test_missing_credentials_disable_gracefully():
    llm = make_llm(exc=TypeError("Could not resolve authentication method. Expected either api_key or auth_token..."))
    assert llm.available is True
    assert llm.parse(system="s", user="u", output_model=Out, effort="low") is None
    assert llm.available is False
    assert "credentials" in (llm.disabled_reason or "")


def test_refusal_returns_none_and_records_reason():
    llm = make_llm(client=FakeClient(message=FakeMessage(stop_reason="refusal", stop_details=None)))
    assert llm.parse(system="s", user="u", output_model=Out, effort="low") is None
    assert "refusal" in (llm.last_error or "")


def test_truncated_output_validation_error_returns_none():
    # A truncated structured output surfaces as a pydantic ValidationError on parsed_output.
    err = ValidationError.from_exception_data("Out", [{"type": "missing", "loc": ("risk",), "input": {}}])
    llm = make_llm(client=FakeClient(message=FakeMessage(raises=err)))
    assert llm.parse(system="s", user="u", output_model=Out, effort="low") is None
    assert "unparseable" in (llm.last_error or "")


def test_api_error_returns_none():
    llm = make_llm(exc=anthropic.APIStatusError(
        "boom", response=type("R", (), {"status_code": 500, "request": None, "headers": {}})(), body=None))
    assert llm.parse(system="s", user="u", output_model=Out, effort="low") is None
    assert llm.last_error


def test_unexpected_exception_returns_none():
    llm = make_llm(exc=RuntimeError("unexpected"))
    assert llm.parse(system="s", user="u", output_model=Out, effort="low") is None
    assert "RuntimeError" in (llm.last_error or "")


def test_good_response_returns_parsed_model():
    llm = make_llm(client=FakeClient(message=FakeMessage(parsed_output=Out(risk=0.4))))
    out = llm.parse(system="s", user="u", output_model=Out, effort="low")
    assert isinstance(out, Out) and out.risk == 0.4


def test_fallback_beta_rejection_retries_without_betas():
    class BetaReject(FakeMessages):
        def parse(self, **kwargs):
            if "betas" in kwargs:
                raise anthropic.BadRequestError(
                    "fallbacks not supported for this model",
                    response=type("R", (), {"status_code": 400, "request": None, "headers": {}})(), body=None)
            return FakeMessage(parsed_output=Out(risk=0.7))

    client = FakeClient()
    client.messages = BetaReject()
    client.beta = FakeBeta(client.messages)
    llm = make_llm(client=client)
    out = llm.parse(system="s", user="u", output_model=Out, effort="low")
    assert out is not None and out.risk == 0.7
