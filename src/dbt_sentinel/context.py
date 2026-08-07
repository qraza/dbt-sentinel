"""Gather grounding evidence for a failing dbt test.

For each :class:`~dbt_sentinel.parse.FailingTest`, this asks the warehouse for a
small, capped sample of the rows that actually caused the failure, plus their
column types. That sample -- not the model name alone -- is what lets the LLM
layer explain *why* a test failed instead of guessing.

Why we run the test's compiled SQL rather than ``SELECT * FROM <model>``:
some models are materialized *ephemeral*, so they don't exist as tables -- dbt
inlines them as CTEs. The test's compiled SQL already contains that inlined
logic and, when run, returns exactly the offending rows. So it works regardless
of how the guarded model is materialized, on any engine.

dbt sometimes stores a test's ``compiled_code`` wrapped in a count aggregate
(``select count(*) as failures ... from ( <rows> ) dbt_internal_test``). We
detect and unwrap that so we sample individual rows, not a single count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .parse import FailingTest
from .warehouse import Warehouse

# Matches dbt's execution wrapper so we can recover the inner failing-rows query.
_WRAPPER_RE = re.compile(
    r"from\s*\(\s*(?P<inner>.*)\)\s*dbt_internal_test",
    re.IGNORECASE | re.DOTALL,
)

DEFAULT_SAMPLE_LIMIT = 20


@dataclass
class FailureContext:
    """A failing test plus the concrete evidence gathered from the warehouse."""

    test: FailingTest
    columns: list[tuple[str, str]] = field(default_factory=list)  # (name, type)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    sampled_count: int = 0
    note: str = ""  # human-readable account of how the sample was obtained


def _failing_rows_sql(compiled_sql: str) -> str:
    """Return SQL that yields the offending rows, unwrapping the count form."""
    match = _WRAPPER_RE.search(compiled_sql)
    return match.group("inner").strip() if match else compiled_sql.strip()


def _jsonable(value: Any) -> Any:
    """Coerce warehouse types the JSON prompt can't hold into strings."""
    if isinstance(value, (Decimal, datetime, date)):
        return str(value)
    return value


def gather_context(
    test: FailingTest,
    warehouse: Warehouse,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> FailureContext:
    """Pull a capped sample of offending rows for one failing test.

    Args:
        test: the failing test to gather evidence for.
        warehouse: any :class:`~dbt_sentinel.warehouse.Warehouse` adapter.
        sample_limit: max rows to return -- keep small so prompts stay cheap and
            no more data than necessary leaves the warehouse.
    """
    if not test.compiled_sql:
        return FailureContext(
            test=test,
            note="No compiled SQL in the manifest for this test; cannot sample rows.",
        )

    rows_sql = _failing_rows_sql(test.compiled_sql)
    sample_sql = (
        f"select * from (\n{rows_sql}\n) as _sentinel_sample limit {sample_limit}"
    )

    try:
        result = warehouse.query(sample_sql)
    except Exception as exc:  # noqa: BLE001 - surface any engine's error verbatim
        return FailureContext(
            test=test,
            note=f"Could not run the test's compiled SQL to sample rows: {exc}",
        )

    sample_rows = [
        {name: _jsonable(value) for name, value in row.items()} for row in result.rows
    ]

    total = test.failure_count if test.failure_count is not None else "unknown"
    note = (
        f"Sampled {len(sample_rows)} of {total} offending row(s) by running the "
        f"test's compiled SQL against the {warehouse.name} warehouse."
    )
    return FailureContext(
        test=test,
        columns=result.columns,
        sample_rows=sample_rows,
        sampled_count=len(sample_rows),
        note=note,
    )