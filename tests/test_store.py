"""Tests for dbt_sentinel.store — run history and regression classification."""

from __future__ import annotations

import pytest

from dbt_sentinel.analyze import Analysis
from dbt_sentinel.parse import FailingTest
from dbt_sentinel.store import (
    NEW,
    RECURRING,
    REGRESSED,
    classify,
    connect,
    has_been_seen,
    history,
    previous_statuses,
    record_run,
)


def _test(unique_id: str = "test.p.speed", name: str = "speed") -> FailingTest:
    return FailingTest(
        unique_id=unique_id,
        test_name=name,
        status="fail",
        failure_count=3,
        message=None,
        test_type=None,
    )


def _analysis(confidence: str = "high") -> Analysis:
    return Analysis(
        root_cause="600 should be 60",
        suggested_fix="use 60",
        confidence=confidence,
        evidence="row arithmetic",
    )


@pytest.fixture
def con(tmp_path):
    return connect(tmp_path / "history.duckdb")


def test_empty_history_has_no_previous(con) -> None:
    assert previous_statuses(con) == {}


def test_record_run_then_read_back(con) -> None:
    run_id = record_run(con, [(_test(), _analysis())], project="demo")
    assert run_id == 1

    prev = previous_statuses(con)
    assert prev == {"test.p.speed": "fail"}

    entries = history(con, "test.p.speed")
    assert len(entries) == 1
    assert entries[0].status == "fail"
    assert entries[0].failure_count == 3
    assert entries[0].confidence == "high"


def test_run_ids_increment(con) -> None:
    assert record_run(con, [(_test(), _analysis())]) == 1
    assert record_run(con, [(_test(), _analysis())]) == 2


def test_history_is_ordered_and_accumulates(con) -> None:
    record_run(con, [(_test(), _analysis())])
    record_run(con, [(_test(), _analysis("low"))])
    entries = history(con, "test.p.speed")
    assert len(entries) == 2
    assert [e.confidence for e in entries] == ["high", "low"]
    assert entries[0].run_at <= entries[1].run_at


def test_has_been_seen(con) -> None:
    assert has_been_seen(con, "test.p.speed") is False
    record_run(con, [(_test(), _analysis())])
    assert has_been_seen(con, "test.p.speed") is True


def test_classify_new_when_never_seen() -> None:
    assert classify("test.p.speed", previous={}, seen_ever=False) == NEW


def test_classify_recurring_when_failed_last_run() -> None:
    prev = {"test.p.speed": "fail"}
    assert classify("test.p.speed", previous=prev, seen_ever=True) == RECURRING


def test_classify_regressed_when_seen_before_but_not_last_run() -> None:
    assert (
        classify("test.p.speed", previous={"other.test": "fail"}, seen_ever=True)
        == REGRESSED
    )


def test_classify_end_to_end_across_runs(con) -> None:
    """new -> recurring -> (fixed) -> regressed, using real stored history."""
    t = _test()

    assert (
        classify(t.unique_id, previous_statuses(con), has_been_seen(con, t.unique_id))
        == NEW
    )
    record_run(con, [(t, _analysis())])

    assert (
        classify(t.unique_id, previous_statuses(con), has_been_seen(con, t.unique_id))
        == RECURRING
    )
    record_run(con, [(t, _analysis())])

    record_run(con, [])

    assert (
        classify(t.unique_id, previous_statuses(con), has_been_seen(con, t.unique_id))
        == REGRESSED
    )
