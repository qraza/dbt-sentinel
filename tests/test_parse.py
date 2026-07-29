"""Tests for dbt_sentinel.parse, run against captured dbt artifact fixtures.

Fixtures live in tests/fixtures/{passing,failing}/ and were captured from the
taxi-analytics-pipeline repo: `passing` is a clean `dbt build`, `failing` is the
same build with the avg_speed_mph unit fix reverted, which trips the singular
test `assert_int_trips_enriched_speed_within_bounds`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_sentinel.parse import FAILURE_STATUSES, FailingTest, parse

FIXTURES = Path(__file__).parent / "fixtures"
PASSING = FIXTURES / "passing"
FAILING = FIXTURES / "failing"

# The test we deliberately broke to capture the failing fixture.
BROKEN_TEST = "assert_int_trips_enriched_speed_within_bounds"


def test_passing_fixture_yields_no_failures() -> None:
    """A clean run has no failing tests."""
    assert parse(PASSING) == []


def test_failing_fixture_yields_failures() -> None:
    """The broken run surfaces at least one failing test."""
    failures = parse(FAILING)
    assert failures, "expected at least one failing test in the failing fixture"
    assert all(isinstance(f, FailingTest) for f in failures)


def test_every_returned_test_has_a_failure_status() -> None:
    """parse() must never return a passing test."""
    for f in parse(FAILING):
        assert f.status in FAILURE_STATUSES


def test_broken_speed_test_is_parsed_correctly() -> None:
    """The known broken test carries the right enriched context."""
    failures = parse(FAILING)
    speed = next((f for f in failures if f.test_name == BROKEN_TEST), None)
    assert speed is not None, f"{BROKEN_TEST} not found in parsed failures"

    assert speed.status == "fail"
    assert speed.failure_count and speed.failure_count > 0
    # It's a hand-written singular test, so no generic test_type / column.
    assert speed.is_generic is False
    assert speed.test_type is None
    assert speed.column_name is None
    # It guards the int_trips_enriched model.
    assert speed.model_name == "int_trips_enriched"
    assert speed.relation is not None
    assert "int_trips_enriched" in speed.relation
    # The compiled SQL dbt actually ran should be captured for grounding later.
    assert speed.compiled_sql
    assert "int_trips_enriched" in speed.compiled_sql


def test_missing_target_dir_raises() -> None:
    """A bad target path fails loudly rather than silently returning []."""
    with pytest.raises(FileNotFoundError):
        parse(FIXTURES / "does_not_exist")