"""Tests for dbt_sentinel.analyze, with the Anthropic API fully mocked.

No network and no API key are needed: we build a FailureContext by hand, inject
a fake httpx client, and assert (a) the prompt actually carries the grounding
evidence, and (b) the model's reply is parsed into a structured Analysis --
including the fail-safe path when the reply isn't valid JSON.
"""

from __future__ import annotations

import json

import pytest

from dbt_sentinel.analyze import (
    Analysis,
    _parse_response,
    analyze,
    build_prompt,
)
from dbt_sentinel.context import FailureContext
from dbt_sentinel.parse import FailingTest

MARKER_SQL = "select * from int_trips_enriched where avg_speed_mph > 80  -- MARKER"


def _make_context() -> FailureContext:
    """A realistic failing-test context, no warehouse required."""
    test = FailingTest(
        unique_id="test.capstone.assert_speed",
        test_name="assert_int_trips_enriched_speed_within_bounds",
        status="fail",
        failure_count=1826500,
        message=None,
        test_type=None,  # singular test
        model_name="int_trips_enriched",
        column_name=None,
        relation="main.int_trips_enriched",
        compiled_sql=MARKER_SQL,
    )
    return FailureContext(
        test=test,
        columns=[("avg_speed_mph", "NUMBER"), ("trip_distance", "NUMBER")],
        sample_rows=[{"avg_speed_mph": 97.2, "trip_distance": 0.81}],
        sampled_count=1,
        note="Sampled 1 of 1826500 offending rows.",
    )


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None

    def json(self) -> dict:
        return {"content": [{"type": "text", "text": self._text}]}


class _FakeClient:
    """Captures the outgoing request and returns a canned reply."""

    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text
        self.last_json: dict | None = None
        self.last_headers: dict | None = None

    def post(self, url, headers=None, json=None):
        self.last_headers = headers
        self.last_json = json
        return _FakeResponse(self.reply_text)


# --- build_prompt --------------------------------------------------------


def test_prompt_contains_grounding_evidence() -> None:
    prompt = build_prompt(_make_context())
    # The compiled SQL and a real sampled value must be in the prompt, or the
    # model isn't actually grounded.
    assert MARKER_SQL in prompt
    assert "97.2" in prompt
    assert "avg_speed_mph" in prompt
    assert "assert_int_trips_enriched_speed_within_bounds" in prompt
    assert "1826500" in prompt


# --- _parse_response -----------------------------------------------------


def test_parse_valid_json() -> None:
    reply = json.dumps(
        {
            "root_cause": "600x multiplier",
            "suggested_fix": "use 60",
            "confidence": "high",
            "evidence": "600 * 0.81 / 5 = 97.2",
        }
    )
    analysis = _parse_response(reply)
    assert analysis.root_cause == "600x multiplier"
    assert analysis.confidence == "high"
    assert analysis.is_confident is True


def test_parse_json_embedded_in_prose() -> None:
    reply = 'Sure!\n{"root_cause":"x","suggested_fix":"y","confidence":"low","evidence":"z"}\nHope that helps.'
    analysis = _parse_response(reply)
    assert analysis.root_cause == "x"
    assert analysis.confidence == "low"
    assert analysis.is_confident is False


def test_parse_malformed_is_low_confidence_not_crash() -> None:
    analysis = _parse_response("the model rambled without any json")
    assert isinstance(analysis, Analysis)
    assert analysis.confidence == "low"
    assert analysis.raw  # raw text preserved for debugging


# --- analyze (mocked client) --------------------------------------------


def test_analyze_sends_grounded_request_and_parses_reply() -> None:
    reply = json.dumps(
        {
            "root_cause": "multiplier is 600, should be 60",
            "suggested_fix": "change 600 to 60",
            "confidence": "high",
            "evidence": "600 * 0.81 / 5 = 97.2 matches the sampled row",
        }
    )
    client = _FakeClient(reply)
    analysis = analyze(_make_context(), api_key="test-key", client=client)

    # The reply was parsed.
    assert analysis.confidence == "high"
    assert "600" in analysis.root_cause

    # The outgoing request was actually grounded and authenticated.
    assert client.last_headers["x-api-key"] == "test-key"
    sent_prompt = client.last_json["messages"][0]["content"]
    assert MARKER_SQL in sent_prompt
    assert client.last_json["system"]  # system prompt present


def test_analyze_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        analyze(_make_context(), client=_FakeClient("{}"))